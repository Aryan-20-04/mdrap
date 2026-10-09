# MDRAP Phase 8 — Dependency and Ownership Graph

## 1. Package Dependency Hierarchy

```mermaid
graph TD
    subgraph CorePlatform ["mdrap-core (Tier 1: Foundational Engine)"]
        core_models["mdrap.models"]
        core_gateway["mdrap.gateway"]
        core_quality["mdrap.quality"]
        core_recon["mdrap.reconciliation"]
        core_ingestlog["mdrap.ingestlog"]
        core_bbo["mdrap.bbo"]
        core_depth["mdrap.depth"]
        core_shm["mdrap.shm"]
        core_storage["mdrap.storage"]
    end

    subgraph CompanionOptions ["mdrap-options (Tier 2: Companion)"]
        opt_core["mdrap_options"]
    end

    subgraph CompanionAnalytics ["mdrap-analytics (Tier 2: Companion)"]
        tca_core["mdrap_analytics"]
    end

    subgraph CompanionStrategies ["mdrap-strategies (Tier 2: Companion)"]
        strat_core["mdrap_strategies"]
    end

    subgraph CompanionVessel ["mdrap-contrib-vessel (Tier 2: Contrib)"]
        vsl_core["mdrap_vessel"]
    end

    opt_core -.->|Optional fastpath acceleration| core_models
    tca_core -->|ConsolidatedBBO reference| core_bbo
    strat_core -->|CanonicalEvent domain models| core_models
    vsl_core -.->|Optional geofence acceleration| core_models
```

## 2. Inbound & Outbound Coupling Matrix

| Subsystem | Upstream Inbound Dependencies | Downstream Outbound Dependencies | Coupling Category |
|---|---|---|---|
| `mdrap-core` | None (Autonomous engine) | Python stdlib (`math`, `mmap`, `socket`, `sqlite3`, `time`) | Autonomous Core |
| `mdrap-options` | `mdrap-core` (optional `fastpath`) | Python stdlib (`math`, `statistics`, `dataclasses`) | Unidirectional Downstream |
| `mdrap-analytics` | `mdrap-core` (`mdrap.bbo`) | Python stdlib (`math`, `hashlib`, `time`) | Unidirectional Downstream |
| `mdrap-strategies` | `mdrap-core` (`mdrap.models`, `mdrap.client`) | Python stdlib (`deque`, `json`, `time`) | Unidirectional Downstream |
| `mdrap-contrib-vessel` | `mdrap-core` (optional `fastpath`) | Python stdlib (`math`, `re`, `time`) | Unidirectional Downstream |

## 3. Dependency Invariants
1. **Zero Core-to-Companion Dependency**: `mdrap-core` contains zero import statements referencing `mdrap_options`, `mdrap_analytics`, `mdrap_strategies`, or `mdrap_vessel`.
2. **Minimal External Dependencies**: All packages require only Python 3.10+ standard library at baseline; zero mandatory external PyPI dependencies for core execution.
3. **Clean Monorepo Boundaries**: Companion packages are maintained under `packages/` with independent versioning and distribution manifests.
