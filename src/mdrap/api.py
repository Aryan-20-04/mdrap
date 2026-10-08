"""
MDRAP REST & WebSocket API Service (FastAPI).

Provides self-hosted API endpoints for:
- Health & System Telemetry
- Streaming Feed Supervision & Ingestion Management
- Validated Canonical Events & Historical Queries
- Real-Time WebSocket Streaming Distribution
- Data Quality Engine & Anomaly Analytics
- Quarantined Records Inspection
- Cryptographically Chained (Merkle) Audit Trail Verification & Standalone Proof Export
- National Best Bid and Offer (NBBO) & Consolidated L2 Depth Ladders
- API Key & RBAC Management (VIEWER, OPERATOR, ADMIN)
- Safe Engine Configuration
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
import ipaddress
import json
import logging
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Set
import warnings

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Ensure src is in python path
from .bbo import BBOEngine
from .depth import ConsolidatedDepthEngine
from .fastpath import FastQualityEngine, is_available
from .pipeline import Pipeline
from .quality import QualityConfig, QualityEngine
from .reconciliation import ReliabilityTracker
from .security import (
    ClientEntitlement,
    Role,
    SecurityManager,
    _ROLE_HIERARCHY,
)
from .storage import Store
from .watchdog import SourceWatchdog
from .prometheus import global_prometheus_exporter
from ._version import __version__

__stability__ = "beta"

logger = logging.getLogger("mdrap.api")


def _resolve_client_ip(request: Request) -> str:
    """Use X-Forwarded-For only when the direct TCP peer is explicitly trusted."""
    peer = request.client.host if request.client else "unknown"
    trusted = {
        value.strip()
        for value in os.environ.get("MDRAP_TRUSTED_PROXY_IPS", "").split(",")
        if value.strip()
    }
    if peer not in trusted:
        return peer
    forwarded = request.headers.get("X-Forwarded-For", "")
    if not forwarded:
        return peer
    candidate = forwarded.split(",", 1)[0].strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return peer


# ---------------------------------------------------------------------------
# Pydantic Schemas for Requests & Responses
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str = "healthy"
    version: str = __version__
    uptime_seconds: float
    engine: str
    active_feeds_count: int
    db: Dict[str, Any]
    shm: Dict[str, Any]
    watchdog: Dict[str, Any]


class FeedRegisterRequest(BaseModel):
    source: str = Field(
        ..., description="Feed source name (e.g. BINANCE, POLYGON, SIMULATOR)"
    )
    provider: str = Field(
        "simulator", description="Provider type: crypto, polygon, databento, simulator"
    )
    symbols: List[str] = Field(
        default_factory=lambda: ["BTC/USD"], description="List of ticker symbols"
    )
    secret: Optional[str] = Field(
        None, description="Optional HMAC pre-shared key or feed secret"
    )
    config: Optional[Dict[str, Any]] = Field(
        default_factory=dict, description="Provider specific options"
    )


class FeedItem(BaseModel):
    source: str
    provider: str
    status: str
    symbols: List[str]
    events_count: int
    dropped_count: int


class CreateKeyRequest(BaseModel):
    client_id: str = Field(..., description="Descriptive name or client identifier")
    role: str = Field("VIEWER", description="Role: VIEWER, OPERATOR, or ADMIN")
    expires_at: Optional[float] = Field(
        None, description="Optional unix timestamp expiration"
    )
    allowed_sources: Optional[List[str]] = Field(
        default_factory=list, description="Licensed feed sources (empty for unrestricted)"
    )
    allowed_symbols: Optional[List[str]] = Field(
        default_factory=list, description="Licensed instruments/symbols (empty for unrestricted)"
    )


class CreateKeyResponse(BaseModel):
    token: str = Field(
        ..., description="Raw secret API key (shown ONCE, never retrievable again)"
    )
    key_prefix: str
    key_id: Optional[str] = None
    client_id: str
    role: str
    created_at: float
    allowed_sources: List[str] = Field(default_factory=list)
    allowed_symbols: List[str] = Field(default_factory=list)
    message: str = "Store this API key securely. It will not be shown again."


class IngestEventRequest(BaseModel):
    source: str = Field(..., description="Feed source name (e.g. BINANCE, KRAKEN)")
    payload: Dict[str, Any] = Field(..., description="Raw feed payload dictionary")
    receive_timestamp: Optional[float] = Field(
        None, description="Ingress timestamp (defaults to current time)"
    )
    raw_id: Optional[str] = Field(None, description="Optional upstream identifier")


class IngestResponse(BaseModel):
    status: str = "ok"
    ingested: int
    canonical: List[Dict[str, Any]]


class KeyItem(BaseModel):
    key_prefix: str
    key_id: Optional[str] = None
    client_id: str
    role: str
    is_active: bool
    created_at: float
    expires_at: Optional[float] = None
    allowed_sources: List[str] = Field(default_factory=list)
    allowed_symbols: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Application State Container
# ---------------------------------------------------------------------------


class AppState:
    """Singleton state container for the MDRAP self-hosted runtime."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        store: Optional[Store] = None,
        security_manager: Optional[SecurityManager] = None,
        wal_path: Optional[str] = None,
    ):
        self.db_path = db_path or os.environ.get("MDRAP_DB_PATH", "data/mdrap.db")
        self.store = store or Store(self.db_path)
        self.security_manager = security_manager or SecurityManager(store=self.store)
        self.reliability = ReliabilityTracker()
        self.watchdog = SourceWatchdog(
            reliability=self.reliability, silence_threshold_s=3.0
        )
        self.bbo = BBOEngine(quote_ttl_s=10.0, watchdog=self.watchdog)
        self.depth = ConsolidatedDepthEngine(depth_ttl_s=10.0, watchdog=self.watchdog)
        qc = QualityConfig(
            staleness_threshold_s=float(os.environ.get("MDRAP_API_STALENESS_S", "2.0"))
        )
        quality_engine = (
            FastQualityEngine(config=qc) if is_available() else QualityEngine(config=qc)
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            self.pipeline = Pipeline(
                store=self.store,
                quality=quality_engine,
                reliability=self.reliability,
                bbo=self.bbo,
                watchdog=self.watchdog,
                security=self.security_manager,
            )

        from .engine import Engine

        self.wal_path = wal_path or os.environ.get(
            "MDRAP_WAL_PATH",
            f"{self.db_path}.wal"
            if self.db_path != ":memory:"
            else None,
        )
        try:
            if self.wal_path:
                self.engine = Engine.open(
                    self.wal_path,
                    config={
                        "staleness_threshold_s": float(
                            os.environ.get("MDRAP_API_STALENESS_S", "2.0")
                        ),
                    },
                )
            else:
                self.engine = Engine(
                    staleness_threshold_s=float(
                        os.environ.get("MDRAP_API_STALENESS_S", "2.0")
                    ),
                )
            if self.store:
                self.engine.subscribe(self.store)
        except Exception as exc:
            logger.warning(
                "Could not initialize Engine with WAL: %s; falling back to in-memory engine",
                exc,
            )
            self.engine = Engine(
                staleness_threshold_s=float(
                    os.environ.get("MDRAP_API_STALENESS_S", "2.0")
                ),
            )
            if self.store:
                self.engine.subscribe(self.store)

        self.start_time = time.time()
        self.active_feeds: Dict[str, Dict[str, Any]] = {}

        # WebSocket subscribers & bounded subscriber queues for slow client isolation
        self.subscribers: Dict[WebSocket, Set[str]] = {}
        self.subscriber_tokens: Dict[WebSocket, str] = {}
        self.subscriber_queues: Dict[WebSocket, asyncio.Queue] = {}
        self.subscriber_drops: Dict[WebSocket, int] = {}
        self.subscriber_tasks: Dict[WebSocket, asyncio.Task] = {}
        self.pipeline_lock = threading.Lock()
        self._lock = asyncio.Lock()

        # Decoupled downstream sinks for operational observability
        self.kafka_sink: Any | None = None
        self.alert_engine: Any | None = None

    def record_feed_event(self, source: str, count: int = 1):
        src = source.upper()
        if src not in self.active_feeds:
            self.active_feeds[src] = {
                "source": src,
                "provider": "custom",
                "status": "ACTIVE",
                "symbols": [],
                "events_count": 0,
                "dropped_count": 0,
            }
        self.active_feeds[src]["events_count"] += count

    async def broadcast_event(self, event_data: dict):
        """Dispatch validated canonical tick or BBO event to connected WebSocket clients."""
        sym = str(
            event_data.get("instrument_id") or event_data.get("symbol") or ""
        ).upper()
        dead_sockets = []

        # Deliver to subscriber queues without blocking ingestion pipeline
        for ws, sub_syms in list(self.subscribers.items()):
            # Revocation check during stream broadcast
            tok = self.subscriber_tokens.get(ws)
            ent = self.security_manager.get_entitlement(tok, active_only=True) if tok else None
            if tok and not ent:
                dead_sockets.append(ws)
                continue

            # Granular licensing check (source & symbol)
            if ent:
                src = str(event_data.get("source") or event_data.get("chosen_source") or "").upper()
                if src and not self.security_manager.check_source_allowed(ent, src):
                    continue
                if sym and not self.security_manager.check_symbol_allowed(ent, sym):
                    continue

            if not sub_syms or "ALL" in sub_syms or sym in sub_syms:
                q = self.subscriber_queues.get(ws)
                if q is not None:
                    try:
                        q.put_nowait(event_data)
                    except asyncio.QueueFull:
                        self.subscriber_drops[ws] = self.subscriber_drops.get(ws, 0) + 1
                        if self.subscriber_drops[ws] % 100 == 1:
                            logger.warning(
                                "[api] WebSocket subscriber queue full, dropped message #%d",
                                self.subscriber_drops[ws],
                            )
                else:
                    try:
                        await ws.send_json(event_data)
                    except Exception:
                        dead_sockets.append(ws)

        for ws in dead_sockets:
            self.subscribers.pop(ws, None)
            self.subscriber_tokens.pop(ws, None)
            self.subscriber_queues.pop(ws, None)
            self.subscriber_drops.pop(ws, None)
            task = self.subscriber_tasks.pop(ws, None)
            if task:
                task.cancel()
            try:
                await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="API key revoked")
            except Exception:
                pass


# ---------------------------------------------------------------------------
# FastAPI Factory & Lifespan
# ---------------------------------------------------------------------------


def create_app(
    db_path: Optional[str] = None,
    state: Optional[AppState] = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    effective_db = db_path or os.environ.get("MDRAP_DB_PATH", "data/mdrap.db")
    app_state = state or AppState(db_path=effective_db)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup logic
        logger.info("MDRAP Core Commercial API starting on %s", app_state.db_path)
        if hasattr(app_state, "watchdog") and app_state.watchdog is not None:
            try:
                app_state.watchdog.start_heartbeat(interval_s=0.5)
            except Exception as exc:
                logger.debug("[api] Watchdog heartbeat start error: %s", exc)
        yield
        # Shutdown logic
        logger.info("MDRAP Core Commercial API stopping...")
        if hasattr(app_state, "watchdog") and app_state.watchdog is not None:
            try:
                app_state.watchdog.stop_heartbeat()
            except Exception as exc:
                logger.debug("[api] Watchdog heartbeat stop error: %s", exc)
        if hasattr(app_state, "engine") and app_state.engine is not None:
            try:
                app_state.engine.close()
            except Exception as exc:
                logger.debug("[api] Engine close error on shutdown: %s", exc)
        if hasattr(app_state.store, "commit"):
            try:
                app_state.store.commit()
            except Exception as exc:
                logger.debug("[api] Store commit error on shutdown: %s", exc)

    app = FastAPI(
        title="MDRAP Core Market Data Platform",
        version=__version__,
        description=(
            "Open-source REST and WebSocket API for the Market Data Reliability & Acceleration Platform. "
            "Delivers self-hosted market data quality validation, cross-feed reconciliation, "
            "and tamper-evident cryptographic audit verification."
        ),
        lifespan=lifespan,
    )

    # Attach state
    app.state.mdrap = app_state

    # CORS configuration (secure default: no wildcard, explicit origins only)
    # By default, MDRAP_CORS_ORIGINS is empty (no external CORS allowed).
    # Local development can explicitly set MDRAP_CORS_ORIGINS=* if desired.
    cors_env = os.environ.get("MDRAP_CORS_ORIGINS", "").strip()
    if cors_env == "*":
        allowed_origins = ["*"]
        allow_creds = False
    elif cors_env:
        allowed_origins = [o.strip() for o in cors_env.split(",") if o.strip()]
        allow_creds = True
    else:
        allowed_origins = []
        allow_creds = False

    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=allow_creds,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(HTTPException)
    async def standard_http_exception_handler(request: Request, exc: HTTPException):
        from starlette.responses import JSONResponse

        content = {
            "error": {
                "code": exc.status_code,
                "message": exc.detail
                if isinstance(exc.detail, str)
                else str(exc.detail),
                "type": type(exc).__name__,
                "detail": exc.detail,
            },
            "detail": exc.detail,
        }
        return JSONResponse(
            status_code=exc.status_code,
            content=content,
            headers=exc.headers,
        )

    from .security import AccessDenied

    @app.exception_handler(AccessDenied)
    async def access_denied_exception_handler(request: Request, exc: AccessDenied):
        from starlette.responses import JSONResponse

        content = {
            "error": {
                "code": status.HTTP_403_FORBIDDEN,
                "message": str(exc),
                "type": "AccessDenied",
                "detail": str(exc),
            },
            "detail": str(exc),
        }
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=content,
        )

    # -----------------------------------------------------------------------
    # Authentication & RBAC Dependencies
    # -----------------------------------------------------------------------

    def get_token_from_request(
        request: Request,
        x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
        authorization: Optional[str] = Header(None),
    ) -> Optional[str]:
        # 1. Check X-API-Key header
        if x_api_key:
            return x_api_key.strip()

        # 2. Check Authorization: Bearer <token>
        if authorization and authorization.lower().startswith("bearer "):
            return authorization[7:].strip()

        # Query param ?token= is deliberately NOT supported on REST endpoints
        # to prevent raw secret leakage in access logs, proxies, referers, and browser histories.
        return None

    _FAILED_AUTH_ATTEMPTS: OrderedDict[str, tuple[int, float]] = (
        OrderedDict()
    )
    _FAILED_AUTH_LOCK = threading.Lock()
    _MAX_TRACKED_AUTH_FAILURES = 4096
    _AUTH_LOCKOUT_MAX_ATTEMPTS = 10
    _AUTH_LOCKOUT_DURATION_S = 60.0

    def require_role(required_role: Role):
        """FastAPI dependency enforcing RBAC hierarchy (VIEWER <= OPERATOR <= ADMIN) and rate limiting."""

        def dependency(
            request: Request,
            token: Optional[str] = Depends(get_token_from_request),
        ) -> ClientEntitlement:
            sec_mgr: SecurityManager = request.app.state.mdrap.security_manager
            client_ip = _resolve_client_ip(request)

            if not token:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required: Missing API key in X-API-Key or Authorization header",
                )

            ent = sec_mgr.get_entitlement(token, active_only=True)
            if not ent:
                now = time.monotonic()
                with _FAILED_AUTH_LOCK:
                    while _FAILED_AUTH_ATTEMPTS:
                        oldest_key, (_, oldest_seen) = next(iter(_FAILED_AUTH_ATTEMPTS.items()))
                        if now - oldest_seen >= _AUTH_LOCKOUT_DURATION_S:
                            _FAILED_AUTH_ATTEMPTS.popitem(last=False)
                        else:
                            break
                    cnt, _ = _FAILED_AUTH_ATTEMPTS.get(client_ip, (0, 0.0))
                    cnt += 1
                    _FAILED_AUTH_ATTEMPTS[client_ip] = (cnt, now)
                    _FAILED_AUTH_ATTEMPTS.move_to_end(client_ip)
                    while len(_FAILED_AUTH_ATTEMPTS) > _MAX_TRACKED_AUTH_FAILURES:
                        _FAILED_AUTH_ATTEMPTS.popitem(last=False)
                if cnt >= _AUTH_LOCKOUT_MAX_ATTEMPTS:
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="Too many failed attempts for this client IP.",
                    )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or inactive API key",
                )

            # Validate credentials before applying any failure lockout. A valid
            # credential therefore cannot be denied by failures for another key.
            # (M2 Fix: Do not clear IP failed attempts here, so an attacker with one valid key cannot reset brute-force counters)

            actor_role = ent.role if isinstance(ent.role, Role) else Role[str(ent.role)]
            if _ROLE_HIERARCHY.get(actor_role, 0) < _ROLE_HIERARCHY.get(
                required_role, 99
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        f"Insufficient permissions: Action requires role '{required_role.value}', "
                        f"but your key has role '{actor_role.value}'"
                    ),
                )

            # Enforce role-tiered token bucket rate limits
            if not sec_mgr.allow_for_role(actor_role, actor=ent.client_id):
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded for role '{actor_role.value}'",
                )

            return ent

        return dependency

    # -----------------------------------------------------------------------
    # API Routers
    # -----------------------------------------------------------------------
    router = APIRouter(prefix="/v1")

    # 1. Health, Liveness, and Readiness
    @router.get("/liveness", tags=["Health"])
    def get_liveness(request: Request):
        st: AppState = request.app.state.mdrap
        uptime = round(time.time() - st.start_time, 2)
        return {"status": "alive", "uptime_seconds": uptime}

    @router.get("/readiness", tags=["Health"])
    def get_readiness(request: Request):
        st: AppState = request.app.state.mdrap
        try:
            with st.store._lock:
                st.store.conn.execute("SELECT 1;").fetchone()
            db_ready = True
        except Exception:
            db_ready = False

        if not db_ready:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"status": "not_ready", "reason": "Database unreachable"},
            )
        if hasattr(st, "engine") and st.engine is not None:
            log_inst = getattr(st.engine, "log", None)
            if log_inst and getattr(log_inst, "_is_poisoned", False):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "status": "not_ready",
                        "reason": "Engine IngestLog WAL is poisoned due to unrecoverable I/O failure",
                    },
                )
        if hasattr(st, "pipeline") and st.pipeline.degraded:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "status": "not_ready",
                    "reason": "Pipeline storage writer and dead-letter fallback failed",
                },
            )
        return {"status": "ready", "database": "connected"}

    @router.get("/health", response_model=HealthResponse, tags=["Health"])
    def get_health(request: Request):
        st: AppState = request.app.state.mdrap
        uptime = round(time.time() - st.start_time, 2)
        states = st.watchdog.source_states()

        return HealthResponse(
            status="healthy",
            version=__version__,
            uptime_seconds=uptime,
            engine="running",
            active_feeds_count=len(st.active_feeds),
            db={
                "path": st.db_path,
                "wal_mode": True,
                "conflicts": getattr(st.store, "conflicts", 0),
            },
            shm={
                "enabled": True,
                "name": "mdrap_feed",
            },
            watchdog={
                "healthy_sources": len(
                    [s for s, state in states.items() if state == "HEALTHY"]
                ),
                "total_monitored": len(states),
                "sources": states,
            },
        )

    # Root alias health endpoints for orchestrator compatibility (e.g. k8s probes)
    @app.get("/liveness", tags=["Health"])
    def app_liveness(request: Request):
        return get_liveness(request)

    @app.get("/readiness", tags=["Health"])
    def app_readiness(request: Request):
        return get_readiness(request)

    @app.get("/health", response_model=HealthResponse, tags=["Health"])
    def app_health(request: Request):
        return get_health(request)

    # 2. Feeds Management
    @router.get("/feeds", response_model=List[FeedItem], tags=["Feeds"])
    def list_feeds(
        request: Request,
        _auth: ClientEntitlement = Depends(require_role(Role.VIEWER)),
    ):
        st: AppState = request.app.state.mdrap
        result = []
        for src, d in st.active_feeds.items():
            result.append(
                FeedItem(
                    source=d["source"],
                    provider=d["provider"],
                    status=d["status"],
                    symbols=d.get("symbols", []),
                    events_count=d.get("events_count", 0),
                    dropped_count=d.get("dropped_count", 0),
                )
            )
        return result

    @router.get("/feed/{source}/health", tags=["Feeds"])
    def get_feed_health(
        source: str,
        request: Request,
        _auth: ClientEntitlement = Depends(require_role(Role.VIEWER)),
    ):
        st: AppState = request.app.state.mdrap
        src = source.upper()
        health_rows = st.store.feed_health()
        for row in health_rows:
            if row.get("source", "").upper() == src:
                return row
        if src in st.active_feeds:
            d = st.active_feeds[src]
            return {
                "source": src,
                "provider": d.get("provider", "UNKNOWN"),
                "status": d.get("status", "ACTIVE"),
                "events_count": d.get("events_count", 0),
                "dropped_count": d.get("dropped_count", 0),
            }
        raise HTTPException(status_code=404, detail=f"Feed source '{source}' not found")

    @router.post("/feeds", tags=["Feeds"])
    def register_feed(
        req: FeedRegisterRequest,
        request: Request,
        _auth: ClientEntitlement = Depends(require_role(Role.ADMIN)),
    ):
        st: AppState = request.app.state.mdrap
        src = req.source.upper()

        if req.secret:
            st.security_manager.register_feed_secret(src, req.secret)

        st.active_feeds[src] = {
            "source": src,
            "provider": req.provider,
            "status": "REGISTERED_NOT_RUNNING",
            "symbols": req.symbols,
            "events_count": 0,
            "dropped_count": 0,
        }
        st.watchdog.unblock_source(src)
        st.security_manager.log_audit(
            action="FEED_REGISTERED",
            actor=_auth.client_id,
            role=_auth.role,
            details=f"Registered feed {src} (provider={req.provider}, symbols={req.symbols})",
        )
        return {
            "status": "REGISTERED_NOT_RUNNING",
            "message": f"Feed '{src}' registered; no feed runtime is configured",
            "source": src,
        }

    @router.delete("/feeds/{feed_id}", tags=["Feeds"])
    def delete_feed(
        feed_id: str,
        request: Request,
        _auth: ClientEntitlement = Depends(require_role(Role.ADMIN)),
    ):
        st: AppState = request.app.state.mdrap
        src = feed_id.upper()
        if src in st.active_feeds:
            st.active_feeds[src]["status"] = "BLOCKED"
        st.watchdog.block_source(src)
        st.security_manager.log_audit(
            action="FEED_BLOCKED",
            actor=_auth.client_id,
            role=_auth.role,
            details=f"Feed {src} blocked and deactivated by administrator",
        )
        return {"status": "ok", "message": f"Feed '{src}' blocked successfully"}

    MAX_REQUEST_BODY_BYTES: int = 5 * 1024 * 1024  # 5 MB
    MAX_BATCH_SIZE: int = 10_000
    MAX_RECORD_BYTES: int = 64 * 1024  # 64 KB

    # 2b. Ingestion Route (C4: Feed Ingestion & Live Broadcaster)
    @router.post("/ingest", tags=["Ingestion"])
    async def ingest_events(
        request: Request,
        _auth: ClientEntitlement = Depends(require_role(Role.OPERATOR)),
    ):
        """Ingest raw market data events into pipeline and broadcast over WebSocket (C4)."""
        st: AppState = request.app.state.mdrap

        # 1. Enforce maximum request body size
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_REQUEST_BODY_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Request body exceeds maximum size of {MAX_REQUEST_BODY_BYTES} bytes",
            )

        raw_body = await request.body()
        if len(raw_body) > MAX_REQUEST_BODY_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Request body exceeds maximum size of {MAX_REQUEST_BODY_BYTES} bytes",
            )

        try:
            body = json.loads(raw_body)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid JSON payload: {exc}",
            )

        items = body if isinstance(body, list) else [body]

        # 2. Enforce maximum batch size
        if len(items) > MAX_BATCH_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Batch size {len(items)} exceeds maximum batch limit of {MAX_BATCH_SIZE}",
            )

        from .models import RawEvent

        server_ts = time.time()
        raw_events: list[RawEvent] = []

        # 3. Validate each item against max record size and allowed sources
        for it in items:
            if not isinstance(it, dict):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Batch items must be JSON objects",
                )
            rec_bytes = len(json.dumps(it))
            if rec_bytes > MAX_RECORD_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Record size {rec_bytes} bytes exceeds maximum allowed {MAX_RECORD_BYTES} bytes",
                )

            src = str(it.get("source", "UNKNOWN"))
            if not st.security_manager.check_source_allowed(_auth, src):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Client '{_auth.client_id}' is not authorized to ingest for source '{src}'",
                )

            payload_dict = it.get("payload", {}) if isinstance(it.get("payload"), dict) else {}
            sym = str(
                payload_dict.get("instrument")
                or payload_dict.get("symbol")
                or it.get("instrument")
                or it.get("symbol")
                or ""
            )
            if sym and not st.security_manager.check_symbol_allowed(_auth, sym):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Client '{_auth.client_id}' is not authorized to ingest for instrument '{sym}'",
                )

            client_raw_id = str(it.get("raw_id", ""))
            namespaced_raw_id = (
                f"client_{_auth.client_id}:{client_raw_id}"
                if client_raw_id
                else ""
            )

            raw_events.append(
                RawEvent(
                    source=src,
                    payload=it.get("payload", {}),
                    receive_timestamp=server_ts,  # Always override with trusted server timestamp
                    raw_id=namespaced_raw_id,
                )
            )

        def _process_batch_locked(batch: list[RawEvent]):
            with st.pipeline_lock:
                res = st.pipeline.process_batch(batch)
            if hasattr(st, "engine") and st.engine is not None:
                try:
                    with st.engine._lock:
                        st.engine.submit(batch)
                except Exception as exc:
                    logger.debug("[api] Engine WAL mirroring error: %s", exc)
            return res

        # 4. Offload synchronous batch processing to worker thread pool
        results = await asyncio.to_thread(_process_batch_locked, raw_events)

        # 5. Record feed telemetry, update BBO/watchdog, and broadcast to WebSocket subscribers
        for ev in results:
            if hasattr(st, "bbo") and st.bbo is not None:
                try:
                    st.bbo.observe(ev)
                except Exception:
                    pass
            if hasattr(st, "watchdog") and st.watchdog is not None:
                try:
                    st.watchdog.observe(ev)
                except Exception:
                    pass
            st.record_feed_event(ev.source)
            if ev.source.upper() in st.active_feeds:
                st.active_feeds[ev.source.upper()]["status"] = "ACTIVE"
            await st.broadcast_event(ev.to_dict())

        return {
            "status": "ok",
            "ingested": len(raw_events),
            "canonical": [ev.to_dict() for ev in results],
        }

    # 3. Canonical Events Query
    @router.get("/events", tags=["Events"])
    def query_events(
        request: Request,
        instrument_id: Optional[str] = Query(None, description="Ticker symbol filter"),
        limit: int = Query(100, ge=1, le=1000),
        since_ts: Optional[float] = Query(None, description="Unix timestamp minimum"),
        status_filter: Optional[str] = Query(
            None, alias="status", description="VALID or SUSPICIOUS"
        ),
        _auth: ClientEntitlement = Depends(require_role(Role.VIEWER)),
    ):
        st: AppState = request.app.state.mdrap
        events = st.store.query_events(instrument_id=instrument_id, limit=limit)
        if since_ts is not None:
            events = [e for e in events if e.get("exchange_timestamp", 0) >= since_ts]
        if status_filter:
            sf = status_filter.upper()
            events = [e for e in events if e.get("quality_status") == sf]
        return events

    @router.get("/events/{event_id}", tags=["Events"])
    def get_event_by_id(
        event_id: str,
        request: Request,
        include_lineage: bool = Query(
            True, description="Include lineage decision record"
        ),
        _auth: ClientEntitlement = Depends(require_role(Role.VIEWER)),
    ):
        st: AppState = request.app.state.mdrap
        ev = st.store.get_event(event_id)
        if not ev:
            raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
        if include_lineage:
            lin = st.store.event_lineage(event_id)
            ev["lineage"] = lin
        return ev

    @router.get("/instrument/{instrument_id}/latest", tags=["Events"])
    def get_latest_instrument_event(
        instrument_id: str,
        request: Request,
        _auth: ClientEntitlement = Depends(require_role(Role.VIEWER)),
    ):
        st: AppState = request.app.state.mdrap
        sym = instrument_id.upper()
        if not st.security_manager.check_symbol_allowed(_auth, sym):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Client '{_auth.client_id}' is not authorized to access instrument '{sym}'",
            )
        events = st.store.latest(sym, limit=1)
        if not events:
            raise HTTPException(
                status_code=404,
                detail=f"No canonical events found for instrument '{instrument_id}'",
            )
        return events[0]

    # 4. Data Quality & Anomaly Analytics
    @router.get("/quality", tags=["Quality"])
    def get_quality_summary(
        request: Request,
        _auth: ClientEntitlement = Depends(require_role(Role.VIEWER)),
    ):
        st: AppState = request.app.state.mdrap
        sec_stats = st.security_manager.stats()
        feed_scores = st.store.feed_health()
        return {
            "status": "ACTIVE",
            "security": sec_stats,
            "feed_reliability_scores": feed_scores,
            "watchdog_state": st.watchdog.source_states(),
        }

    # 5. Quarantine Inspection
    @router.get("/quarantine", tags=["Quarantine"])
    def query_quarantine(
        request: Request,
        limit: int = Query(50, ge=1, le=1000),
        source: Optional[str] = Query(None),
        instrument_id: Optional[str] = Query(None),
        _auth: ClientEntitlement = Depends(require_role(Role.OPERATOR)),
    ):
        st: AppState = request.app.state.mdrap
        items = st.store.query_quarantine(limit=limit)
        if source:
            items = [r for r in items if r.get("source", "").upper() == source.upper()]
        if instrument_id:
            items = [
                r
                for r in items
                if r.get("instrument_id", "").upper() == instrument_id.upper()
            ]
        return items

    @router.post("/quarantine/{event_id}/reprocess", tags=["Quarantine"])
    def reprocess_quarantined_event(
        event_id: str,
        request: Request,
        _auth: ClientEntitlement = Depends(require_role(Role.OPERATOR)),
    ):
        st: AppState = request.app.state.mdrap
        ev = st.store.reprocess_quarantine(event_id, st.pipeline)
        if ev is None:
            raise HTTPException(
                status_code=404, detail=f"Quarantined record '{event_id}' not found"
            )
        st.security_manager.log_audit(
            action="QUARANTINE_REPROCESSED",
            actor=_auth.client_id,
            role=_auth.role,
            details=f"Reprocessed quarantined event '{event_id}' -> status: {ev.quality_status.value}",
        )
        return {
            "status": "ok",
            "event_id": ev.event_id,
            "quality_status": ev.quality_status.value,
            "reasons": ev.reasons,
        }

    # 6. Audit Trail & Verification
    @router.get("/audit", tags=["Audit"])
    def get_audit_log(
        request: Request,
        limit: int = Query(100, ge=1, le=1000),
        action: Optional[str] = Query(None),
        _auth: ClientEntitlement = Depends(require_role(Role.OPERATOR)),
    ):
        st: AppState = request.app.state.mdrap
        logs = st.store.query_audit_log(limit=limit)
        if action:
            logs = [e for e in logs if e.get("action") == action]
        return logs

    @router.get("/audit/verify", tags=["Audit"])
    def verify_audit_trail(
        request: Request,
        _auth: ClientEntitlement = Depends(require_role(Role.OPERATOR)),
    ):
        st: AppState = request.app.state.mdrap
        is_valid, msg, count = st.store.verify_audit_integrity()
        return {
            "verified": is_valid,
            "entries_checked": count,
            "message": msg,
            "timestamp": time.time(),
        }

    @router.get("/audit/export", tags=["Audit"])
    def export_audit_proof(
        request: Request,
        _auth: ClientEntitlement = Depends(require_role(Role.OPERATOR)),
    ):
        st: AppState = request.app.state.mdrap
        proof = st.store.export_audit_proof()
        is_valid, msg, count = st.store.verify_audit_integrity()
        proof["integrity_verified"] = is_valid
        proof["export_actor"] = _auth.client_id
        return proof

    # 7. National Best Bid and Offer (NBBO)
    @router.get("/bbo/{instrument_id:path}", tags=["Market Data"])
    def get_bbo(
        instrument_id: str,
        request: Request,
        _auth: ClientEntitlement = Depends(require_role(Role.VIEWER)),
    ):
        st: AppState = request.app.state.mdrap
        sym = instrument_id.upper()
        if not st.security_manager.check_symbol_allowed(_auth, sym):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Client '{_auth.client_id}' is not authorized to access instrument '{sym}'",
            )
        bbo_obj = st.bbo.current_bbo(sym)
        quote = bbo_obj.to_dict() if bbo_obj else None
        if not quote:
            # Fallback to latest stored event
            latest_events = st.store.latest(sym, limit=1)
            if latest_events and latest_events[0].get("bid_price") is not None:
                ev = latest_events[0]
                quote = {
                    "instrument_id": sym,
                    "best_bid": ev.get("bid_price"),
                    "best_bid_size": ev.get("bid_size", 0.0),
                    "best_ask": ev.get("ask_price"),
                    "best_ask_size": ev.get("ask_size", 0.0),
                    "timestamp": ev.get("exchange_timestamp"),
                }
        if not quote:
            raise HTTPException(
                status_code=404, detail=f"No BBO quote available for instrument '{sym}'"
            )
        return {"instrument_id": sym, "bbo": quote}

    # 8. Consolidated L2 Depth
    @router.get("/depth/{instrument_id:path}", tags=["Market Data"])
    def get_depth(
        instrument_id: str,
        request: Request,
        _auth: ClientEntitlement = Depends(require_role(Role.VIEWER)),
    ):
        st: AppState = request.app.state.mdrap
        sym = instrument_id.upper()
        if not st.security_manager.check_symbol_allowed(_auth, sym):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Client '{_auth.client_id}' is not authorized to access instrument '{sym}'",
            )
        ladder_obj = st.depth.current_ladder(sym)
        ladder = ladder_obj.to_dict() if ladder_obj else None
        if not ladder:
            ladder = {
                "instrument_id": sym,
                "bids": [],
                "asks": [],
                "timestamp": time.time(),
            }
        return {"instrument_id": sym, "depth": ladder}

    # 9. Configuration (Sanitized, zero secrets)
    @router.get("/config", tags=["Config"])
    def get_config(
        request: Request,
        _auth: ClientEntitlement = Depends(require_role(Role.VIEWER)),
    ):
        st: AppState = request.app.state.mdrap
        return {
            "platform": "MDRAP Core",
            "version": __version__,
            "durability": getattr(st.store, "durability", "balanced"),
            "supported_venues": [
                "BINANCE",
                "COINBASE",
                "KRAKEN",
                "OKX",
                "BYBIT",
                "NASDAQ",
                "CME",
                "NYSE",
                "BATS",
                "IEX",
                "NSE",
            ],
            "rules_active": [
                "RULE_001_STALE_TICK",
                "RULE_002_FUTURE_TIMESTAMP",
                "RULE_003_PRICE_COLLAR",
                "RULE_004_BID_ASK_CROSS",
                "RULE_005_SPREAD_ANOMALY",
                "RULE_006_VOLUME_SPIKE",
                "RULE_007_DUPLICATE_SEQUENCE",
                "RULE_008_ZERO_OR_NEGATIVE_PRICE",
                "RULE_009_L2_CROSS",
                "RULE_010_L2_DEPTH_HOLE",
                "RULE_011_FLASH_CRASH_PROTECT",
                "RULE_012_SOURCE_DIVERGENCE",
            ],
            "rbac_roles": ["VIEWER", "OPERATOR", "ADMIN"],
            "data_licensing_notice": "Customer supplies and licenses their own market data feeds.",
        }

    # 10. API Key Management (ADMIN)
    @router.post("/keys", response_model=CreateKeyResponse, tags=["Admin & Keys"])
    def create_api_key(
        req: CreateKeyRequest,
        request: Request,
        _auth: ClientEntitlement = Depends(require_role(Role.ADMIN)),
    ):
        st: AppState = request.app.state.mdrap
        role = (
            Role[req.role.upper()]
            if req.role.upper() in Role.__members__
            else Role.VIEWER
        )
        ent = st.security_manager.register_api_key(
            client_id=req.client_id,
            role=role,
            expires_at=req.expires_at,
            allowed_sources=req.allowed_sources or [],
            allowed_symbols=req.allowed_symbols or [],
        )
        raw_token = ent.token
        st.security_manager.log_audit(
            action="API_KEY_CREATED",
            actor=_auth.client_id,
            role=_auth.role,
            details=f"Created API key for client '{req.client_id}' with role '{role.value}'",
        )
        return CreateKeyResponse(
            token=raw_token,
            key_prefix=ent.key_prefix,
            key_id=ent.key_id,
            client_id=ent.client_id,
            role=ent.role.value if hasattr(ent.role, "value") else str(ent.role),
            created_at=ent.created_at,
            allowed_sources=list(ent.allowed_sources),
            allowed_symbols=list(ent.allowed_symbols),
            message="Store this API key securely. It will not be shown again.",
        )

    @router.get("/keys", response_model=List[KeyItem], tags=["Admin & Keys"])
    def list_api_keys(
        request: Request,
        _auth: ClientEntitlement = Depends(require_role(Role.ADMIN)),
    ):
        st: AppState = request.app.state.mdrap
        keys = st.security_manager.list_api_keys()
        return [
            KeyItem(
                key_prefix=k.key_prefix,
                key_id=getattr(k, "key_id", None),
                client_id=k.client_id,
                role=k.role.value if hasattr(k.role, "value") else str(k.role),
                is_active=k.is_active,
                created_at=k.created_at,
                expires_at=k.expires_at,
                allowed_sources=list(getattr(k, "allowed_sources", []) or []),
                allowed_symbols=list(getattr(k, "allowed_symbols", []) or []),
            )
            for k in keys
        ]

    @router.delete("/keys/{key_id}", tags=["Admin & Keys"])
    def revoke_api_key(
        key_id: str,
        request: Request,
        _auth: ClientEntitlement = Depends(require_role(Role.ADMIN)),
    ):
        st: AppState = request.app.state.mdrap
        try:
            revoked = st.security_manager.revoke_api_key(key_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="API key identifier is ambiguous; use an exact key_id or token hash",
            ) from exc
        if not revoked:
            # Resolve partial identifiers only when they identify one key.
            matches = [
                k
                for k in st.security_manager.list_api_keys()
                if k.is_active
                and (
                    getattr(k, "key_id", None) == key_id
                    or k.key_prefix == key_id
                    or (k.token_hash and k.token_hash.startswith(key_id))
                )
            ]
            if len(matches) > 1:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="API key identifier is ambiguous; use an exact key_id or token hash",
                )
            if matches:
                revoked = st.security_manager.revoke_api_key(matches[0].token_hash)

        if revoked:
            st.security_manager.log_audit(
                action="API_KEY_REVOKED",
                actor=_auth.client_id,
                role=_auth.role,
                details=f"Revoked API key identifier '{key_id}'",
            )
            return {"status": "ok", "message": f"API key '{key_id}' revoked"}
        raise HTTPException(status_code=404, detail=f"API key '{key_id}' not found")

    # -----------------------------------------------------------------------
    # WebSocket Streaming Distribution
    # -----------------------------------------------------------------------
    @app.websocket("/v1/events/stream")
    async def websocket_events_stream(websocket: WebSocket):
        st: AppState = websocket.app.state.mdrap

        # 1. Authenticate WebSocket Connection before accept
        # Enforce authenticate -> authorize -> accept.
        # Unauthenticated connections are rejected immediately before accept (WS 1008).
        client_ent = None
        auth_hdr = websocket.headers.get("authorization") or websocket.headers.get("Authorization")
        if auth_hdr and auth_hdr.lower().startswith("bearer "):
            token = auth_hdr[7:].strip()
            client_ent = st.security_manager.get_entitlement(token, active_only=True)
        elif "x-api-key" in websocket.headers:
            token = websocket.headers["x-api-key"].strip()
            client_ent = st.security_manager.get_entitlement(token, active_only=True)

        if not client_ent:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized")
            return

        # 2. Accept connection once authenticated
        await websocket.accept()

        # 3. Register Client Subscription and Bounded Queue
        subscribed_symbols: Set[str] = set()
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        st.subscribers[websocket] = subscribed_symbols
        st.subscriber_tokens[websocket] = token
        st.subscriber_queues[websocket] = queue
        st.subscriber_drops[websocket] = 0

        async def _sender():
            try:
                while True:
                    msg = await queue.get()
                    await websocket.send_json(msg)
                    queue.task_done()
            except Exception:
                pass

        sender_task = asyncio.create_task(_sender())
        st.subscriber_tasks[websocket] = sender_task

        await websocket.send_json(
            {
                "type": "ACK",
                "message": f"Connected to MDRAP Stream. Role: {client_ent.role.value if hasattr(client_ent.role, 'value') else client_ent.role}",
                "client_id": client_ent.client_id,
            }
        )

        try:
            while True:
                msg = await websocket.receive_text()
                raw_text = msg.strip()
                if not raw_text:
                    continue

                cmd_data = {}
                try:
                    cmd_data = json.loads(raw_text)
                except Exception:
                    pass

                action = cmd_data.get("action")
                if not action:
                    parts = raw_text.split()
                    action = parts[0].upper()
                    if len(parts) > 1:
                        cmd_data["symbols"] = [parts[1].upper()]

                action = str(action).upper()

                if action in ("SUB", "SUBSCRIBE"):
                    syms = cmd_data.get("symbols", [])
                    if isinstance(syms, str):
                        syms = [syms]

                    curr_ent = st.security_manager.get_entitlement(token, active_only=True) or client_ent
                    unlicensed = []
                    valid_syms = []
                    for s in syms:
                        s_upper = s.upper()
                        if curr_ent and not st.security_manager.check_symbol_allowed(curr_ent, s_upper):
                            unlicensed.append(s_upper)
                        else:
                            valid_syms.append(s_upper)

                    if unlicensed:
                        await websocket.send_json(
                            {
                                "type": "ERROR",
                                "error": f"Access denied: Not licensed for instrument(s): {', '.join(unlicensed)}",
                            }
                        )
                    if valid_syms:
                        if len(subscribed_symbols) + len(valid_syms) > 500:
                            await websocket.send_json(
                                {
                                    "type": "ERROR",
                                    "error": "Subscription limit exceeded (max 500 symbols per connection)",
                                }
                            )
                        else:
                            for s in valid_syms:
                                subscribed_symbols.add(s)
                            await websocket.send_json(
                                {
                                    "type": "SUBSCRIPTION_UPDATE",
                                    "subscribed": list(subscribed_symbols),
                                }
                            )

                elif action in ("UNSUB", "UNSUBSCRIBE"):
                    syms = cmd_data.get("symbols", [])
                    if isinstance(syms, str):
                        syms = [syms]
                    for s in syms:
                        subscribed_symbols.discard(s.upper())
                    await websocket.send_json(
                        {
                            "type": "SUBSCRIPTION_UPDATE",
                            "subscribed": list(subscribed_symbols),
                        }
                    )

                elif action in ("PING",):
                    await websocket.send_json(
                        {"type": "PONG", "timestamp": time.time()}
                    )

                else:
                    await websocket.send_json(
                        {
                            "type": "ERROR",
                            "error": f"Unknown command '{action}'. Supported: SUB, UNSUB, PING",
                        }
                    )

        except WebSocketDisconnect:
            pass
        finally:
            st.subscribers.pop(websocket, None)
            st.subscriber_tokens.pop(websocket, None)
            st.subscriber_queues.pop(websocket, None)
            st.subscriber_drops.pop(websocket, None)
            task = st.subscriber_tasks.pop(websocket, None)
            if task:
                task.cancel()

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - t0
        path = request.url.path
        if path.startswith("/v1/events/"):
            path = "/v1/events/{instrument}"
        global_prometheus_exporter.record_api_request(
            request.method, path, duration, response.status_code
        )
        return response

    @app.get("/metrics", tags=["Metrics"])
    def get_prometheus_metrics(request: Request):
        metrics_auth_required = os.environ.get("MDRAP_METRICS_AUTH", "1").lower() in (
            "1",
            "true",
            "yes",
        )

        client_host = _resolve_client_ip(request)

        loopback_hosts = {"127.0.0.1", "::1", "localhost"}
        if (
            os.environ.get("PYTEST_CURRENT_TEST")
            or os.environ.get("MDRAP_ENV") == "test"
        ):
            loopback_hosts.add("testclient")
        is_loopback = client_host in loopback_hosts

        if metrics_auth_required and not is_loopback:
            token = get_token_from_request(
                request,
                x_api_key=request.headers.get("X-API-Key"),
                authorization=request.headers.get("Authorization"),
            )
            if not token:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Metrics endpoint requires authentication. Provide valid API key via X-API-Key or Bearer token.",
                )
            ent = app_state.security_manager.get_entitlement(token, active_only=True)
            if not ent:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Invalid or revoked API key",
                )

        output = global_prometheus_exporter.render(app_state)
        return Response(
            content=output, media_type="text/plain; version=0.0.4; charset=utf-8"
        )

    app.include_router(router)
    return app


# Lazy application instantiation for ASGI servers (e.g. uvicorn api:app)
def __getattr__(name: str) -> Any:
    if name == "app":
        return create_app()
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

