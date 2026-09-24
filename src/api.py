"""
MDRAP Commercial Production REST & WebSocket API Service (FastAPI).

Provides enterprise-grade, secure, self-hosted API endpoints for:
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
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import os
import secrets
import sys
import time
from typing import Any, Dict, List, Optional, Set

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    Security,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Ensure src is in python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bbo import BBOEngine
from depth import ConsolidatedDepthEngine
from models import CanonicalEvent, EventType, QualityStatus, RawEvent
from pipeline import Pipeline
from reconciliation import ReliabilityTracker
from security import (
    AccessDenied,
    ClientEntitlement,
    Role,
    SecurityManager,
    _ROLE_HIERARCHY,
)
from storage import Store
from watchdog import SourceWatchdog

__stability__ = "beta"

logger = logging.getLogger("mdrap.api")


# ---------------------------------------------------------------------------
# Pydantic Schemas for Requests & Responses
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str = "healthy"
    version: str = "2.2.0"
    uptime_seconds: float
    engine: str
    active_feeds_count: int
    db: Dict[str, Any]
    shm: Dict[str, Any]
    watchdog: Dict[str, Any]


class FeedRegisterRequest(BaseModel):
    source: str = Field(..., description="Feed source name (e.g. BINANCE, POLYGON, SIMULATOR)")
    provider: str = Field("simulator", description="Provider type: crypto, polygon, databento, simulator")
    symbols: List[str] = Field(default_factory=lambda: ["BTC/USD"], description="List of ticker symbols")
    secret: Optional[str] = Field(None, description="Optional HMAC pre-shared key or feed secret")
    config: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Provider specific options")


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
    expires_at: Optional[float] = Field(None, description="Optional unix timestamp expiration")


class CreateKeyResponse(BaseModel):
    token: str = Field(..., description="Raw secret API key (shown ONCE, never retrievable again)")
    key_prefix: str
    client_id: str
    role: str
    created_at: float
    message: str = "Store this API key securely. It will not be shown again."


class KeyItem(BaseModel):
    key_prefix: str
    client_id: str
    role: str
    is_active: bool
    created_at: float
    expires_at: Optional[float] = None


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
    ):
        self.db_path = db_path or os.environ.get("MDRAP_DB_PATH", "data/mdrap.db")
        self.store = store or Store(self.db_path)
        self.security_manager = security_manager or SecurityManager(store=self.store)
        self.reliability = ReliabilityTracker()
        self.watchdog = SourceWatchdog(reliability=self.reliability, silence_threshold_s=3.0)
        self.bbo = BBOEngine(quote_ttl_s=10.0, watchdog=self.watchdog)
        self.depth = ConsolidatedDepthEngine(depth_ttl_s=10.0, watchdog=self.watchdog)
        self.pipeline = Pipeline(
            store=self.store,
            reliability=self.reliability,
            bbo=self.bbo,
            watchdog=self.watchdog,
        )

        self.start_time = time.time()
        self.active_feeds: Dict[str, Dict[str, Any]] = {
            "SIMULATOR": {
                "source": "SIMULATOR",
                "provider": "simulator",
                "status": "ACTIVE",
                "symbols": ["AAPL", "BTC/USD", "ETH/USD"],
                "events_count": 0,
                "dropped_count": 0,
            }
        }

        # WebSocket subscribers: socket -> set of uppercase symbols (empty set = ALL)
        self.subscribers: Dict[WebSocket, Set[str]] = {}
        self._lock = asyncio.Lock()

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
        sym = str(event_data.get("instrument_id") or event_data.get("symbol") or "").upper()
        dead_sockets = []

        # Copy keys to avoid mutation during iteration
        sockets = list(self.subscribers.keys())
        for ws in sockets:
            sub_syms = self.subscribers.get(ws, set())
            if not sub_syms or "ALL" in sub_syms or sym in sub_syms:
                try:
                    await ws.send_json(event_data)
                except Exception:
                    dead_sockets.append(ws)

        for ws in dead_sockets:
            self.subscribers.pop(ws, None)


# ---------------------------------------------------------------------------
# FastAPI Factory & Lifespan
# ---------------------------------------------------------------------------

def create_app(
    db_path: Optional[str] = None,
    state: Optional[AppState] = None,
) -> FastAPI:
    """Create and configure the production FastAPI application."""
    effective_db = db_path or os.environ.get("MDRAP_DB_PATH", "data/mdrap.db")
    app_state = state or AppState(db_path=effective_db)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup logic
        logger.info("MDRAP Core Commercial API starting on %s", app_state.db_path)
        yield
        # Shutdown logic
        logger.info("MDRAP Core Commercial API stopping...")
        if hasattr(app_state.store, "commit"):
            try:
                app_state.store.commit()
            except Exception:
                pass

    app = FastAPI(
        title="MDRAP Core Commercial API",
        version="2.2.0",
        description=(
            "Production REST and WebSocket API for the Market Data Reliability & Acceleration Platform. "
            "Delivers self-hosted market data quality validation, cross-feed reconciliation, "
            "and tamper-evident cryptographic audit verification."
        ),
        lifespan=lifespan,
    )

    # Attach state
    app.state.mdrap = app_state

    # CORS configuration (secure defaults)
    # Note: allow_credentials=True with allow_origins=["*"] is invalid per the
    # CORS specification. When wildcard origins are used, credentials are disabled.
    # Set MDRAP_CORS_ORIGINS to explicit domains to enable credentialed requests.
    cors_env = os.environ.get("MDRAP_CORS_ORIGINS", "").strip()
    allowed_origins = [o.strip() for o in cors_env.split(",") if o.strip()]
    is_wildcard = not allowed_origins or allowed_origins == ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if is_wildcard else allowed_origins,
        allow_credentials=not is_wildcard,
        allow_methods=["*"],
        allow_headers=["*"],
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

        # 3. Check query param ?token=
        query_token = request.query_params.get("token")
        if query_token:
            return query_token.strip()

        return None

    def require_role(required_role: Role):
        """FastAPI dependency enforcing RBAC hierarchy (VIEWER <= OPERATOR <= ADMIN)."""

        def dependency(
            request: Request,
            token: Optional[str] = Depends(get_token_from_request),
        ) -> ClientEntitlement:
            sec_mgr: SecurityManager = request.app.state.mdrap.security_manager
            if not token:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required: Missing API key in X-API-Key or Authorization header",
                )

            ent = sec_mgr.get_entitlement(token, active_only=True)
            if not ent:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or inactive API key",
                )

            actor_role = ent.role if isinstance(ent.role, Role) else Role[str(ent.role)]
            if _ROLE_HIERARCHY.get(actor_role, 0) < _ROLE_HIERARCHY.get(required_role, 99):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        f"Insufficient permissions: Action requires role '{required_role.value}', "
                        f"but your key has role '{actor_role.value}'"
                    ),
                )
            return ent

        return dependency

    # -----------------------------------------------------------------------
    # API Routers
    # -----------------------------------------------------------------------
    router = APIRouter(prefix="/v1")

    # 1. Health & Telemetry
    @router.get("/health", response_model=HealthResponse, tags=["Health"])
    def get_health(request: Request):
        st: AppState = request.app.state.mdrap
        uptime = round(time.time() - st.start_time, 2)
        states = st.watchdog.source_states()

        return HealthResponse(
            status="healthy",
            version="2.2.0",
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
                "healthy_sources": len([s for s, state in states.items() if state == "HEALTHY"]),
                "total_monitored": len(states),
                "sources": states,
            },
        )

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
            "status": "ACTIVE",
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
            "status": "ok",
            "message": f"Feed '{src}' successfully registered and activated",
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

    # 3. Canonical Events Query
    @router.get("/events", tags=["Events"])
    def query_events(
        request: Request,
        instrument_id: Optional[str] = Query(None, description="Ticker symbol filter"),
        limit: int = Query(100, ge=1, le=1000),
        since_ts: Optional[float] = Query(None, description="Unix timestamp minimum"),
        status_filter: Optional[str] = Query(None, alias="status", description="VALID or SUSPICIOUS"),
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
            items = [r for r in items if r.get("instrument_id", "").upper() == instrument_id.upper()]
        return items

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
            raise HTTPException(status_code=404, detail=f"No BBO quote available for instrument '{sym}'")
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
        ladder_obj = st.depth.current_ladder(sym)
        ladder = ladder_obj.to_dict() if ladder_obj else None
        if not ladder:
            ladder = {"instrument_id": sym, "bids": [], "asks": [], "timestamp": time.time()}
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
            "version": "2.2.0",
            "durability": getattr(st.store, "durability", "balanced"),
            "supported_venues": [
                "BINANCE", "COINBASE", "KRAKEN", "OKX", "BYBIT",
                "NASDAQ", "CME", "NYSE", "BATS", "IEX", "NSE"
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
        role = Role[req.role.upper()] if req.role.upper() in Role.__members__ else Role.VIEWER
        raw_token = f"mdrap_live_{secrets.token_urlsafe(24)}"

        ent = st.security_manager.register_api_key(
            client_id=req.client_id,
            role=role,
            token=raw_token,
            expires_at=req.expires_at,
        )
        st.security_manager.log_audit(
            action="API_KEY_CREATED",
            actor=_auth.client_id,
            role=_auth.role,
            details=f"Created API key for client '{req.client_id}' with role '{role.value}'",
        )
        return CreateKeyResponse(
            token=raw_token,
            key_prefix=ent.key_prefix,
            client_id=ent.client_id,
            role=ent.role.value if hasattr(ent.role, "value") else str(ent.role),
            created_at=ent.created_at,
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
                client_id=k.client_id,
                role=k.role.value if hasattr(k.role, "value") else str(k.role),
                is_active=k.is_active,
                created_at=k.created_at,
                expires_at=k.expires_at,
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
        revoked = st.security_manager.revoke_api_key(key_id)
        if not revoked:
            # Try finding by exact prefix or token_hash
            for k in st.security_manager.list_api_keys():
                if k.key_prefix == key_id or (k.token_hash and k.token_hash.startswith(key_id)):
                    st.security_manager.revoke_api_key(k.token_hash)
                    revoked = True
                    break

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
        await websocket.accept()
        st: AppState = websocket.app.state.mdrap

        # 1. Authenticate WebSocket Connection
        token = websocket.query_params.get("token")
        client_ent = None

        if token:
            client_ent = st.security_manager.get_entitlement(token, active_only=True)

        # Allow initial handshake auth message if query param not provided
        if not client_ent:
            try:
                init_msg = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
                auth_data = {}
                try:
                    auth_data = json.loads(init_msg)
                except Exception:
                    if init_msg.startswith("AUTH "):
                        auth_data = {"token": init_msg.split()[1]}

                tok = auth_data.get("token") or auth_data.get("auth")
                if tok:
                    client_ent = st.security_manager.get_entitlement(tok, active_only=True)
            except Exception:
                pass

        if not client_ent:
            await websocket.send_json({"type": "ERROR", "error": "Unauthorized: Missing or invalid API key"})
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        # 2. Register Client Subscription
        subscribed_symbols: Set[str] = set()
        st.subscribers[websocket] = subscribed_symbols
        await websocket.send_json({
            "type": "ACK",
            "message": f"Connected to MDRAP Stream. Role: {client_ent.role.value if hasattr(client_ent.role, 'value') else client_ent.role}",
            "client_id": client_ent.client_id,
        })

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
                    for s in syms:
                        subscribed_symbols.add(s.upper())
                    await websocket.send_json({
                        "type": "SUBSCRIPTION_UPDATE",
                        "subscribed": list(subscribed_symbols),
                    })

                elif action in ("UNSUB", "UNSUBSCRIBE"):
                    syms = cmd_data.get("symbols", [])
                    if isinstance(syms, str):
                        syms = [syms]
                    for s in syms:
                        subscribed_symbols.discard(s.upper())
                    await websocket.send_json({
                        "type": "SUBSCRIPTION_UPDATE",
                        "subscribed": list(subscribed_symbols),
                    })

                elif action in ("PING",):
                    await websocket.send_json({"type": "PONG", "timestamp": time.time()})

                else:
                    await websocket.send_json({
                        "type": "ERROR",
                        "error": f"Unknown command '{action}'. Supported: SUB, UNSUB, PING",
                    })

        except WebSocketDisconnect:
            pass
        finally:
            st.subscribers.pop(websocket, None)

    app.include_router(router)
    return app


# Module-level default application instance for ASGI servers (e.g. uvicorn api:app)
app = create_app()
