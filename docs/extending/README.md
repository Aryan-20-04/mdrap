# MDRAP Extension Architecture

The Market Data Reliability & Acceleration Platform (MDRAP) is built on a modular, zero-dependency extension architecture. Extensions decouple core validation and reconciliation logic from venue-specific ingest formats, storage technologies, authorization systems, and downstream publication mechanisms.

MDRAP adheres to a pure Python, standard-library-first design:
- **Zero Heavy Frameworks**: No ORM, no web framework dependencies in the core pipeline; data structures use native dataclasses with `__slots__ = True` for memory efficiency.
- **Protocol-First Interfaces**: Interfaces are defined using `typing.Protocol` with `@runtime_checkable`, enabling structural duck-typing without tight inheritance coupling.
- **Dynamic Plugin Discovery**: Third-party plugins are discovered at runtime via standard library `importlib.metadata.entry_points()`.
- **System Invariants**:
  - **Monotonic Status Escalation**: Data quality priority is strictly `INVALID` > `SUSPICIOUS` > `VALID`. An event's status can escalate but can never be downgraded.
  - **Quarantine, Never Drop**: Malformed or anomalous market data is never silently discarded; payloads and lineage are recorded in evidentiary quarantine.
  - **Deterministic Generation**: Sequence and event identifiers use `itertools.count()`, avoiding non-deterministic `uuid.uuid4()` overhead.

---

## Architecture Overview

```mermaid
flowchart LR
    subgraph Ingestion ["1. Feed Ingestion"]
        A["External Venue"] -->|"Raw Wire"| B["FeedAdapter<br/>(Protocol)"]
        B -->|"RawEvent"| C["Gateway / Ingest"]
    end

    subgraph Processing ["2. Pipeline Core"]
        C --> D["QualityEngine"]
        D <-->|"Bits 32-63"| E["Quality Rules<br/>(@register_rule)"]
        D -->|"CanonicalEvent"| F["Reconciler"]
    end

    subgraph Persistence ["3. Persistence Tier"]
        F -->|"Batched Events"| G["StorageBackend<br/>(Protocol)"]
        G --> H[("Store / DB")]
    end

    subgraph Distribution ["4. Distribution Tier"]
        F -->|"Broadcast"| I["OutputSink<br/>(Protocol)"]
        I --> J["Kafka / SHM / TCP / WS"]
    end

    subgraph Security ["5. Security Tier"]
        K["AuthProvider<br/>(Protocol)"] -.->|"RBAC / Audit"| C
        K -.->|"RBAC / Audit"| G
        K -.->|"RBAC / Audit"| I
    end
```

---

## Extension Points Summary

MDRAP provides 6 primary extension points:

| # | Extension Point | Protocol / Decorator | Entry Point Group | Description | Sub-Guide |
|---|-----------------|----------------------|-------------------|-------------|-----------|
| 1 | **Feed Adapter** | [`FeedAdapter`](src/adapters/__init__.py) | `mdrap.adapters` | Connects exchange feeds, multicast lines, and proprietary binary sockets to emit [`RawEvent`](src/mdrap/models.py). | [Feed Adapter Guide](docs/extending/feed-adapter.md) |
| 2 | **Storage Backend** | [`StorageBackend`](src/mdrap/protocols.py) | Custom factory | Swappable persistence engine for canonical ticks, quarantine records, lineage trails, and BBO snapshots. | [Storage Backend Guide](docs/extending/storage-backend.md) |
| 3 | **Quality Rules** | [`@register_rule`](src/mdrap/rules.py) | `mdrap.quality_rules` | Custom user-defined quality checks in bitmask range `32..63` executed post-native pass. | [Quality Rules Guide](docs/extending/quality-rules.md) |
| 4 | **Auth Provider** | [`AuthProvider`](src/mdrap/protocols.py) | Custom factory | Pluggable authentication, RBAC enforcement (`VIEWER` < `OPERATOR` < `ADMIN`), and tamper-evident audit logging. | [Auth Provider Guide](docs/extending/auth-provider.md) |
| 5 | **Output Sink** | [`OutputSink`](src/mdrap/protocols.py) | `mdrap.output_sinks` | Real-time fanout sink for downstream distribution across messaging buses (Kafka, RabbitMQ, ZeroMQ, SHM). | [Output Sink Guide](docs/extending/output-sink.md) |
| 6 | **Alert Sink** | [`AlertSink`](src/mdrap/protocols.py) | `mdrap.alert_sinks` | Resilient external notifications to Webhooks, Slack, and PagerDuty with rate limiting, retries, and DLQ. | [Alert Sink Guide](docs/extending/alert-sink.md) |

---

## Extension Discovery Mechanism

MDRAP discovers installed plugins using Python standard library metadata entry points:

```python
import sys

def discover_plugins(group_name: str) -> dict[str, type]:
    """Discover plugins registered under a pyproject.toml entry-point group."""
    if sys.version_info >= (3, 10):
        from importlib.metadata import entry_points
        eps = entry_points(group=group_name)
    else:
        import importlib_metadata  # type: ignore
        eps = importlib_metadata.entry_points().get(group_name, [])

    plugins = {}
    for ep in eps:
        try:
            plugin_cls = ep.load()
            plugins[ep.name] = plugin_cls
        except Exception:
            pass  # Fault isolation: broken plugin does not crash registry
    return plugins
```

Extensions register themselves in their respective `pyproject.toml` file:

```toml
[project.entry-points."mdrap.adapters"]
custom_exchange = "my_package.adapters:CustomExchangeAdapter"

[project.entry-points."mdrap.quality_rules"]
rule_volume_spike = "my_package.rules:register_volume_rules"
```

---

## Flat Module Structure

MDRAP uses a flat module layout under [`src/`](src/). Never import from nested package paths like `mdrap.core.models`. All extensions and scripts import directly from the top-level modules:

```python
# Correct
from models import CanonicalEvent, EventType, QualityStatus, RawEvent, Reason
from storage import Store
from pipeline import Pipeline
from security import ClientEntitlement, Role, SecurityManager

# Incorrect
from mdrap.core.models import CanonicalEvent  # Do not use!
```

---

## Key Source Code References

- [`src/adapters/__init__.py`](src/adapters/__init__.py): [`FeedAdapter`](src/adapters/__init__.py) protocol definition and discovery routine.
- [`src/adapters/template.py`](src/adapters/template.py): Reference implementation of a custom venue feed adapter.
- [`src/mdrap/protocols.py`](src/mdrap/protocols.py): Protocol definitions for [`StorageBackend`](src/mdrap/protocols.py), [`AuthProvider`](src/mdrap/protocols.py), [`QualityEvaluator`](src/mdrap/protocols.py), and [`OutputSink`](src/mdrap/protocols.py).
- [`src/mdrap/rules.py`](src/mdrap/rules.py): User quality rule registry and [`@register_rule`](src/mdrap/rules.py) decorator.
- [`src/mdrap/models.py`](src/mdrap/models.py): Canonical domain types ([`RawEvent`](src/mdrap/models.py), [`CanonicalEvent`](src/mdrap/models.py), [`QualityStatus`](src/mdrap/models.py), [`Reason`](src/mdrap/models.py)).
- [`src/mdrap/pipeline.py`](src/mdrap/pipeline.py): Core [`Pipeline`](src/mdrap/pipeline.py) orchestrator and flush lifecycle.
- [`src/mdrap/storage.py`](src/mdrap/storage.py): Default SQLite persistence engine implementation [`Store`](src/mdrap/storage.py).
- [`src/mdrap/security.py`](src/mdrap/security.py): Cryptographic [`SecurityManager`](src/mdrap/security.py) and RBAC role definitions.
- [`src/mdrap/service.py`](src/mdrap/service.py): Background daemon [`MarketDataDaemon`](src/mdrap/service.py) coordinating broadcast distribution.
