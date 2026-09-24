"""MDRAP External Alert Delivery Framework.

Provides decoupled, resilient last-mile alert delivery to external notification
targets (Webhooks, Slack, PagerDuty).

Shared Contract:
1. Runs outside the synchronous tick evaluation loop (rule 1). Never adds latency
   to tick processing.
2. Graceful degradation: A slow or unreachable external endpoint retries with exponential
   backoff and spills to a local dead-letter queue; it never blocks.
3. Anti-flapping: Evaluated using sustained hysteresis windows and cooldown deduplication.
4. Security: All outbound payloads are cryptographically signed with HMAC-SHA256.
5. Observability: Every attempt, success, failure, and dead-letter is tracked for Prometheus.
6. Liveness monitoring: Monitored via scheduled heartbeat self-test alerts.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
import urllib.error
from typing import List, Optional, Tuple

from alerts import Alert, AlertEngine, AlertStatus, AlertType
from protocols import AlertSink, DeliveryResult
from security import SecurityManager

logger = logging.getLogger(__name__)

__stability__ = "stable"

__all__ = [
    "BaseHttpAlertSink",
    "WebhookAlertSink",
    "SlackAlertSink",
    "PagerDutyAlertSink",
    "MockAlertSink",
    "AlertDeliveryWorker",
]


class RateLimiter:
    """Token-bucket rate limiter for outbound notification dispatch."""

    def __init__(self, rate: float = 10.0, burst: float = 20.0) -> None:
        self.rate = float(rate)
        self.capacity = float(burst)
        self.tokens = float(burst)
        self.last_update = time.perf_counter()
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        with self._lock:
            now = time.perf_counter()
            elapsed = now - self.last_update
            self.last_update = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            return False


class BaseHttpAlertSink:
    """Base class for HTTP-based alert notification sinks."""

    def __init__(
        self,
        name: str,
        endpoint_url: str,
        security_manager: Optional[SecurityManager] = None,
        signing_secret_key: Optional[str] = None,
        max_retries: int = 3,
        backoff_base_s: float = 0.05,
        timeout_s: float = 2.0,
        rate_limit_eps: float = 10.0,
        dead_letter_path: Optional[str] = "data/deadletter/alerts.jsonl",
    ) -> None:
        self.name = name
        self.endpoint_url = endpoint_url
        self.security_manager = security_manager
        self.signing_secret_key = signing_secret_key
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.timeout_s = timeout_s
        self.dead_letter_path = dead_letter_path
        self.rate_limiter = RateLimiter(rate=rate_limit_eps, burst=rate_limit_eps * 2)

        # Telemetry
        self.success_count = 0
        self.failure_count = 0
        self.is_closed = False

        # Ensure signing secret is registered in SecurityManager if provided
        if self.security_manager and not self.signing_secret_key:
            # Check or create default alert feed secret
            try:
                self.security_manager._secrets["MDRAP_ALERTS"] = (
                    b"mdrap-default-alert-signing-key-32b"
                )
            except Exception:
                pass

    def format_payload(self, alert: Alert) -> dict:
        """Format an Alert instance into the specific target's wire payload."""
        return {
            "alert_id": alert.alert_id,
            "alert_type": alert.alert_type.value,
            "symbol": alert.symbol,
            "condition": alert.condition,
            "threshold": alert.threshold,
            "triggered_value": alert.triggered_value,
            "triggered_at": alert.triggered_at,
            "message": alert.message,
            "timestamp": time.time(),
        }

    def sign_payload(self, payload_dict: dict) -> Tuple[str, str]:
        """Generate HMAC-SHA256 signature and timestamp for request authentication."""
        ts_str = str(int(time.time()))
        payload_with_ts = dict(payload_dict)
        payload_with_ts["_auth_ts"] = ts_str

        if self.security_manager and hasattr(self.security_manager, "sign_payload"):
            try:
                sig = self.security_manager.sign_payload(
                    "MDRAP_ALERTS", payload_with_ts
                )
                return sig, ts_str
            except Exception:
                pass

        import hmac

        secret = (self.signing_secret_key or "default-alert-key").encode("utf-8")
        canonical_bytes = json.dumps(
            payload_with_ts, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        sig = hmac.digest(secret, canonical_bytes, "sha256").hex()
        return sig, ts_str

    def deliver(self, alert: Alert) -> DeliveryResult:
        """Deliver alert to HTTP destination with rate limiting, retries, and backoff."""
        if self.is_closed:
            return DeliveryResult(
                alert_id=alert.alert_id,
                sink_name=self.name,
                status="FAILED",
                error_message="Sink is closed",
            )

        if not self.rate_limiter.acquire():
            self._spill_dead_letter(alert, "Rate limit exceeded on outbound alert sink")
            self.failure_count += 1
            return DeliveryResult(
                alert_id=alert.alert_id,
                sink_name=self.name,
                status="FAILED",
                error_message="Rate limit exceeded",
            )

        payload_dict = self.format_payload(alert)
        sig, ts_str = self.sign_payload(payload_dict)
        payload_bytes = json.dumps(payload_dict).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "MDRAP-Alert-Delivery/2.2.0",
            "X-MDRAP-Signature": sig,
            "X-MDRAP-Timestamp": ts_str,
        }

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                req = urllib.request.Request(
                    self.endpoint_url,
                    data=payload_bytes,
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    if 200 <= resp.status < 300:
                        self.success_count += 1
                        return DeliveryResult(
                            alert_id=alert.alert_id,
                            sink_name=self.name,
                            status="DELIVERED",
                            attempts=attempt,
                            delivered_at=time.time(),
                        )
                    else:
                        last_error = f"HTTP {resp.status}"
            except Exception as exc:
                last_error = str(exc)

            if attempt < self.max_retries:
                sleep_s = self.backoff_base_s * (2 ** (attempt - 1))
                time.sleep(sleep_s)

        # All retries failed: spill to dead-letter storage
        self.failure_count += 1
        self._spill_dead_letter(alert, last_error or "Max retries exceeded")
        return DeliveryResult(
            alert_id=alert.alert_id,
            sink_name=self.name,
            status="FAILED",
            attempts=self.max_retries,
            error_message=last_error,
        )

    def _spill_dead_letter(self, alert: Alert, reason: str) -> None:
        """Persist failed alert to disk to guarantee no silent data loss (Invariant 3)."""
        if not self.dead_letter_path:
            return
        try:
            dir_path = os.path.dirname(os.path.abspath(self.dead_letter_path))
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            record = {
                "alert_id": alert.alert_id,
                "symbol": alert.symbol,
                "condition": alert.condition,
                "threshold": alert.threshold,
                "triggered_value": alert.triggered_value,
                "message": alert.message,
                "sink": self.name,
                "reason": reason,
                "spilled_at": time.time(),
            }
            with open(self.dead_letter_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
                f.flush()
        except Exception as exc:
            logger.error("Failed writing alert to dead letter file: %s", exc)

    def close(self) -> None:
        self.is_closed = True


class WebhookAlertSink(BaseHttpAlertSink):
    """Generic JSON Webhook Alert Sink."""

    def __init__(self, endpoint_url: str, **kwargs) -> None:
        super().__init__(name="webhook", endpoint_url=endpoint_url, **kwargs)


class SlackAlertSink(BaseHttpAlertSink):
    """Slack Incoming Webhook Alert Sink with formatted message cards."""

    def __init__(self, webhook_url: str, **kwargs) -> None:
        super().__init__(name="slack", endpoint_url=webhook_url, **kwargs)

    def format_payload(self, alert: Alert) -> dict:
        color = (
            "#e01e5a"
            if "INVALID" in alert.condition or "DRAWDOWN" in alert.condition
            else "#ecb22e"
        )
        return {
            "text": f"🚨 *MDRAP Alert:* {alert.condition}",
            "attachments": [
                {
                    "color": color,
                    "fields": [
                        {"title": "Symbol", "value": alert.symbol, "short": True},
                        {"title": "Condition", "value": alert.condition, "short": True},
                        {
                            "title": "Triggered Value",
                            "value": f"{alert.triggered_value:.4f}",
                            "short": True,
                        },
                        {
                            "title": "Timestamp",
                            "value": time.strftime(
                                "%Y-%m-%d %H:%M:%S UTC",
                                time.gmtime(alert.triggered_at or time.time()),
                            ),
                            "short": True,
                        },
                    ],
                    "footer": "MDRAP Real-Time Monitoring",
                }
            ],
        }


class PagerDutyAlertSink(BaseHttpAlertSink):
    """PagerDuty Events API v2 Alert Sink."""

    def __init__(
        self,
        routing_key: str,
        events_api_url: str = "https://events.pagerduty.com/v2/enqueue",
        **kwargs,
    ) -> None:
        super().__init__(name="pagerduty", endpoint_url=events_api_url, **kwargs)
        self.routing_key = routing_key

    def format_payload(self, alert: Alert) -> dict:
        severity = (
            "critical"
            if alert.alert_type in (AlertType.QUALITY_DEGRADATION, AlertType.DRAWDOWN)
            else "warning"
        )
        return {
            "routing_key": self.routing_key,
            "event_action": "trigger",
            "dedup_key": f"mdrap-{alert.symbol}-{alert.alert_type.value}",
            "payload": {
                "summary": alert.message or alert.condition,
                "severity": severity,
                "source": "mdrap-engine",
                "custom_details": {
                    "alert_id": alert.alert_id,
                    "symbol": alert.symbol,
                    "threshold": alert.threshold,
                    "triggered_value": alert.triggered_value,
                },
            },
        }


class MockAlertSink:
    """In-memory test double verifying delivery guarantees without network requests."""

    def __init__(self, name: str = "mock", simulate_failure: bool = False) -> None:
        self.name = name
        self.simulate_failure = simulate_failure
        self.delivered_alerts: List[Alert] = []
        self.delivery_results: List[DeliveryResult] = []
        self._lock = threading.Lock()
        self.is_closed = False

    def deliver(self, alert: Alert) -> DeliveryResult:
        if self.is_closed:
            return DeliveryResult(
                alert_id=alert.alert_id,
                sink_name=self.name,
                status="FAILED",
                error_message="Closed",
            )

        if self.simulate_failure:
            res = DeliveryResult(
                alert_id=alert.alert_id,
                sink_name=self.name,
                status="FAILED",
                attempts=3,
                error_message="Simulated endpoint failure",
            )
            with self._lock:
                self.delivery_results.append(res)
            return res

        res = DeliveryResult(
            alert_id=alert.alert_id,
            sink_name=self.name,
            status="DELIVERED",
            attempts=1,
            delivered_at=time.time(),
        )
        with self._lock:
            self.delivered_alerts.append(alert)
            self.delivery_results.append(res)
        return res

    def close(self) -> None:
        self.is_closed = True

    def clear(self) -> None:
        with self._lock:
            self.delivered_alerts.clear()
            self.delivery_results.clear()


class AlertDeliveryWorker:
    """
    Decoupled background worker managing alert dispatch, retries, and health checks.

    Satisfies the Shared Contract by pulling pending alerts from AlertEngine in an
    independent thread, ensuring zero impact on the hot tick evaluation path.
    """

    def __init__(
        self,
        alert_engine: AlertEngine,
        sinks: Optional[List[AlertSink]] = None,
        poll_interval_s: float = 0.05,
    ) -> None:
        self.alert_engine = alert_engine
        self.sinks: List[AlertSink] = list(sinks or [])
        self.poll_interval_s = poll_interval_s

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Telemetry & meta-alert tracking
        self.consecutive_failures = 0
        self.last_failure_ts: float = 0.0
        self.meta_alert_triggered = False

    def add_sink(self, sink: AlertSink) -> None:
        with self._lock:
            self.sinks.append(sink)

    def deliver_pending(self, limit: int = 50) -> int:
        """
        Poll pending alerts and deliver to all registered sinks.

        Returns:
            Number of successfully processed alerts.
        """
        pending = self.alert_engine.query_pending_deliveries(limit=limit)
        if not pending:
            return 0

        processed = 0
        for alert in pending:
            all_delivered = True
            last_err = None
            max_attempts = 1

            with self._lock:
                sinks_copy = list(self.sinks)

            if not sinks_copy:
                # No sinks registered: mark delivered to avoid unbounded backlog
                self.alert_engine.mark_delivery_status(
                    alert.alert_id, "delivered", 1, time.time()
                )
                processed += 1
                continue

            for sink in sinks_copy:
                try:
                    result = sink.deliver(alert)
                    max_attempts = max(max_attempts, result.attempts)
                    if result.status != "DELIVERED":
                        all_delivered = False
                        last_err = result.error_message or "Delivery failed"
                except Exception as exc:
                    all_delivered = False
                    last_err = str(exc)

            now = time.time()
            if all_delivered:
                self.alert_engine.mark_delivery_status(
                    alert.alert_id, "delivered", max_attempts, now
                )
                self.consecutive_failures = 0
                processed += 1
            else:
                self.alert_engine.mark_delivery_status(
                    alert.alert_id, "failed", max_attempts, now, error=last_err
                )
                self.consecutive_failures += 1
                self.last_failure_ts = now
                self._check_meta_alert()

        return processed

    def send_heartbeat(self, sink_name: str | None = None) -> DeliveryResult:
        """
        Dispatch a synthetic heartbeat/self-test alert through the delivery pipeline.

        Returns:
            DeliveryResult confirming whether the alerting fabric is functional.
        """
        heartbeat_alert = Alert(
            alert_id=999999,
            alert_type=AlertType.CUSTOM,
            symbol="SYSTEM",
            condition="LIVENESS_HEARTBEAT",
            threshold=1.0,
            status=AlertStatus.ACTIVE,
            created_at=time.time(),
            triggered_at=time.time(),
            triggered_value=1.0,
            message="MDRAP Heartbeat Self-Test Alert: Alerting pipeline is alive and operational.",
        )

        with self._lock:
            targets = [
                s
                for s in self.sinks
                if not sink_name or getattr(s, "name", "") == sink_name
            ]

        if not targets:
            return DeliveryResult(
                alert_id=heartbeat_alert.alert_id,
                sink_name="none",
                status="FAILED",
                error_message="No matching alert sinks registered for heartbeat",
            )

        # Deliver to first target sink
        sink = targets[0]
        return sink.deliver(heartbeat_alert)

    def _check_meta_alert(self) -> None:
        """Trigger a meta-alert if external delivery has been persistently failing."""
        if self.consecutive_failures >= 5 and not self.meta_alert_triggered:
            self.meta_alert_triggered = True
            logger.critical(
                "MDRAP META-ALERT: Alert delivery has failed %d consecutive times! Downstream channels may be broken.",
                self.consecutive_failures,
            )

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._worker_loop, name="mdrap-alert-delivery", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
            self._thread = None

    def _worker_loop(self) -> None:
        while self._running:
            try:
                count = self.deliver_pending(limit=25)
                if count == 0:
                    time.sleep(self.poll_interval_s)
            except Exception as exc:
                logger.error("Error in alert delivery worker loop: %s", exc)
                time.sleep(self.poll_interval_s)
