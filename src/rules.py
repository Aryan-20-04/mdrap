"""MDRAP User Rule Registry and Decorator.

Supports user-defined quality rules allocated to bitmask range 32..63.
Evaluated in Python post-native pass with zero overhead when no rules are registered.

Rules can be registered via:
    1. ``@register_rule(bit=N)`` decorator (in-process)
    2. ``importlib.metadata.entry_points(group="mdrap.quality_rules")`` (third-party packages)
"""

from __future__ import annotations

import sys

from dataclasses import dataclass
from typing import Callable, Optional
from models import CanonicalEvent, QualityStatus, Reason

USER_BIT_MIN = 32
USER_BIT_MAX = 63


@dataclass(frozen=True, slots=True)
class RuleDefinition:
    """Formalized metadata definition for canonical and user-defined quality rules."""

    rule_id: str
    name: str
    severity: QualityStatus
    stage: str
    description: str
    trigger_condition: str


RULE_REGISTRY_METADATA: dict[str, RuleDefinition] = {
    "MD001": RuleDefinition(
        rule_id="MD001",
        name=Reason.DUPLICATE.value,
        severity=QualityStatus.INVALID,
        stage="Sequence",
        description="Duplicate sequence number or event ID already observed within window",
        trigger_condition="Bit set in sequence window bitmap or duplicate key in unsequenced LRU",
    ),
    "MD002": RuleDefinition(
        rule_id="MD002",
        name=Reason.SEQUENCE_GAP.value,
        severity=QualityStatus.SUSPICIOUS,
        stage="Sequence",
        description="Missing sequence numbers indicating packet drop or channel disconnect",
        trigger_condition="seq - last_seq > 1 and not reorder-repaired within window",
    ),
    "MD003": RuleDefinition(
        rule_id="MD003",
        name=Reason.TS_IMPLAUSIBLE.value,
        severity=QualityStatus.SUSPICIOUS,
        stage="Timestamp",
        description="Exchange timestamp implausibly ahead of local receive timestamp",
        trigger_condition="exchange_timestamp > receive_timestamp + max_future_skew_s",
    ),
    "MD004": RuleDefinition(
        rule_id="MD004",
        name=Reason.STALE.value,
        severity=QualityStatus.SUSPICIOUS,
        stage="Timestamp",
        description="Timestamp latency exceeds maximum permissible staleness threshold",
        trigger_condition="receive_timestamp - exchange_timestamp > staleness_threshold_s",
    ),
    "MD005": RuleDefinition(
        rule_id="MD005",
        name=Reason.CROSSED_QUOTE.value,
        severity=QualityStatus.INVALID,
        stage="Book",
        description="Bid price greater than or equal to ask price violating book arbitrage",
        trigger_condition="bid_price >= ask_price and (bid_price > 0 and ask_price > 0)",
    ),
    "MD006": RuleDefinition(
        rule_id="MD006",
        name=Reason.PRICE_ANOMALY.value,
        severity=QualityStatus.SUSPICIOUS,
        stage="Statistical",
        description="Price movement exceeds statistical rolling volatility corridor",
        trigger_condition="abs(price - rolling_mean) > N * rolling_stddev",
    ),
    "MD007": RuleDefinition(
        rule_id="MD007",
        name=Reason.SCHEMA_VIOLATION.value,
        severity=QualityStatus.INVALID,
        stage="Schema",
        description="Schema validation failure or non-finite values in numeric fields",
        trigger_condition="Missing mandatory field or NaN/Inf or negative price/size without allow_negative",
    ),
    "MD008": RuleDefinition(
        rule_id="MD008",
        name=Reason.OUT_OF_ORDER.value,
        severity=QualityStatus.SUSPICIOUS,
        stage="Sequence",
        description="Decreasing or non-monotonic sequence number or exchange timestamp",
        trigger_condition="seq < last_seq without duplicate flag, or exchange_ts < last_ts",
    ),
    "MD009": RuleDefinition(
        rule_id="MD009",
        name=Reason.MALFORMED.value,
        severity=QualityStatus.INVALID,
        stage="Schema",
        description="Malformed message payload or invalid field format during gateway parsing",
        trigger_condition="Gateway normalization JSON parsing failure or corrupt byte framing",
    ),
    "MD010": RuleDefinition(
        rule_id="MD010",
        name=Reason.CIRCUIT_FILTER_BREACH.value,
        severity=QualityStatus.SUSPICIOUS,
        stage="Price",
        description="NSE/BSE daily price band circuit filter breach (+/- 10%)",
        trigger_condition="abs(price - base_price) / base_price > 0.10",
    ),
    "MD011": RuleDefinition(
        rule_id="MD011",
        name=Reason.VOLATILITY_INTERRUPTION.value,
        severity=QualityStatus.SUSPICIOUS,
        stage="Price",
        description="Deutsche Boerse dynamic price corridor halt (+/- 5%)",
        trigger_condition="abs(price - dynamic_reference) / dynamic_reference > 0.05",
    ),
    "MD012": RuleDefinition(
        rule_id="MD012",
        name=Reason.SPECIAL_QUOTE_INDICATION.value,
        severity=QualityStatus.SUSPICIOUS,
        stage="Price",
        description="Tokyo Stock Exchange Tokuhai quote indication (+/- 8%)",
        trigger_condition="abs(quote - previous_quote) / previous_quote > 0.08",
    ),
    "MD013": RuleDefinition(
        rule_id="MD013",
        name=Reason.CROSS_FEED_DISAGREEMENT.value,
        severity=QualityStatus.SUSPICIOUS,
        stage="Reconciliation",
        description="Consensus divergence between redundant feeds for the same event",
        trigger_condition="Feed price differs from consensus BBO by > reconciliation tolerance",
    ),
    "MD014": RuleDefinition(
        rule_id="MD014",
        name=Reason.RATE_LIMITED.value,
        severity=QualityStatus.INVALID,
        stage="Rate",
        description="Ingress rate limit exceeded for sending source",
        trigger_condition="Ingress token bucket exhausted for source identifier",
    ),
    "MD015": RuleDefinition(
        rule_id="MD015",
        name=Reason.SECURITY_REJECT.value,
        severity=QualityStatus.INVALID,
        stage="Security",
        description="Security gate rejection (sanitizer, HMAC, or auth failure)",
        trigger_condition="API key invalid, signature verification failure, or injection payload detected",
    ),
}

_NAME_TO_RULE: dict[str, RuleDefinition] = {
    r.name: r for r in RULE_REGISTRY_METADATA.values()
}


def get_rule_by_id(rule_id: str) -> Optional[RuleDefinition]:
    """Retrieve rule definition by standard rule ID (e.g. 'MD001')."""
    return RULE_REGISTRY_METADATA.get(rule_id.upper())


def get_rule_by_name(name: str) -> Optional[RuleDefinition]:
    """Retrieve rule definition by reason name (e.g. 'DUPLICATE')."""
    return _NAME_TO_RULE.get(name.upper())


def list_registered_definitions() -> list[RuleDefinition]:
    """List all registered canonical quality rule definitions."""
    return list(RULE_REGISTRY_METADATA.values())


@dataclass(slots=True)
class UserRule:
    bit: int
    name: str
    description: str
    severity: QualityStatus
    func: Callable[[CanonicalEvent], bool]


_USER_RULES: dict[int, UserRule] = {}
_USER_RULE_NAMES: set[str] = set()


def register_rule(
    bit: int,
    name: str,
    description: str = "",
    severity: QualityStatus = QualityStatus.SUSPICIOUS,
):
    """Decorator to register a user-defined quality rule in bitmask range 32..63."""
    if not (USER_BIT_MIN <= bit <= USER_BIT_MAX):
        raise ValueError(
            f"User rule bit {bit} out of range [{USER_BIT_MIN}, {USER_BIT_MAX}]. "
            "Bits 0..15 are core platform rules, 16..31 are reserved."
        )

    clean_name = name.strip().upper()

    def decorator(fn: Callable[[CanonicalEvent], bool]):
        if bit in _USER_RULES:
            raise ValueError(
                f"Rule bit {bit} is already registered to '{_USER_RULES[bit].name}'"
            )
        if clean_name in _USER_RULE_NAMES:
            raise ValueError(f"Rule name '{clean_name}' is already registered")

        rule = UserRule(
            bit=bit,
            name=clean_name,
            description=description,
            severity=severity,
            func=fn,
        )
        _USER_RULES[bit] = rule
        _USER_RULE_NAMES.add(clean_name)
        return fn

    return decorator


def unregister_rule(bit: int) -> None:
    """Unregister a user-defined rule by bit."""
    rule = _USER_RULES.pop(bit, None)
    if rule:
        _USER_RULE_NAMES.discard(rule.name)


def clear_user_rules() -> None:
    """Clear all registered user rules (useful in test teardown)."""
    _USER_RULES.clear()
    _USER_RULE_NAMES.clear()


def get_user_rules() -> list[UserRule]:
    """Get all currently registered user rules."""
    return list(_USER_RULES.values())


def evaluate_user_rules(event: CanonicalEvent) -> CanonicalEvent:
    """Evaluate registered user rules against CanonicalEvent post-native pass."""
    if not _USER_RULES:
        return event

    for rule in _USER_RULES.values():
        try:
            violation = rule.func(event)
            if violation:
                # Monotonic status escalation
                if rule.severity == QualityStatus.INVALID:
                    event.quality_status = QualityStatus.INVALID
                elif (
                    rule.severity == QualityStatus.SUSPICIOUS
                    and event.quality_status != QualityStatus.INVALID
                ):
                    event.quality_status = QualityStatus.SUSPICIOUS

                if rule.name not in event.reasons:
                    event.reasons.append(rule.name)
        except Exception:
            # Bad user rule never crashes the pipeline; mark schema violation
            if "USER_RULE_ERROR" not in event.reasons:
                event.reasons.append("USER_RULE_ERROR")

    return event


def discover_quality_rules() -> int:
    """Discover and register third-party quality rules from entry points.

    Third-party packages register rules under the ``mdrap.quality_rules``
    entry point group. Each entry point should resolve to a callable that,
    when called with no arguments, registers its rules via ``@register_rule``.

    Returns:
        Number of entry point modules successfully loaded.
    """
    loaded = 0
    try:
        if sys.version_info >= (3, 10):
            from importlib.metadata import entry_points

            eps = entry_points(group="mdrap.quality_rules")
        else:
            import importlib_metadata  # type: ignore[import-untyped]

            eps = importlib_metadata.entry_points().get("mdrap.quality_rules", [])

        for ep in eps:
            try:
                initializer = ep.load()
                if callable(initializer):
                    initializer()
                loaded += 1
            except Exception:
                pass
    except Exception:
        pass
    return loaded


__stability__ = "stable"
