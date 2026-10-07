# MDRAP

MDRAP is an early-stage Python library for normalizing market events, evaluating data quality, reconciling observations, and recording canonical events, quarantine outcomes, and lineage.

**Readiness: NOT READY for production ingestion.** The pipeline’s default in-memory writer queue is not a durable acknowledgement boundary. There is no server-owned feed supervisor, no wired adapter-to-WebSocket path, and the current wheel has not been validated across the advertised Python and operating-system matrix. See [the verified gap report](docs/verified-gap-report.md).

## Install

Python 3.10 or newer is required. Install the core engine with:

```bash
python -m pip install mdrap-core
```

The `mdrap-core` wheel installs the `mdrap` package. Install `mdrap-contrib` for the CLI and peripheral integrations; feature dependencies are available as extras such as `mdrap-contrib[api]`.

### Package migration

Replace `pip install mdrap` with `pip install mdrap-core mdrap-contrib` when you need the previous full CLI and integration set. For a core-only deployment, install just `mdrap-core`. The import path remains `mdrap`; peripheral modules and the `mdrap` command require `mdrap-contrib`.

For a temporary top-level import migration, install `mdrap-compat` explicitly. It restores imports such as `from models import RawEvent` and intentionally adds generic module names that can collide with application code. Migrate to `mdrap.*` imports and remove `mdrap-compat` after that transition.

## Process an event

```python
from mdrap import Pipeline, RawEvent, Store

store = Store(":memory:")
pipeline = Pipeline(store=store, async_writer=False, flush_interval_s=0)

event = RawEvent(
    source="EXAMPLE",
    payload={
        "instrument": "AAPL",
        "event_type": "TRADE",
        "exchange_ts": 1_800_000_000.0,
        "sequence": 1,
        "price": 200.0,
        "quantity": 10.0,
    },
)
result = pipeline.process_one(event)
pipeline.finish()
store.close()
```

This demonstrates the current in-process API. It does not establish crash durability or production suitability. The [quickstart](docs/quickstart.md) has installation and API instructions.

## Current boundaries

- The `mdrap-core` distribution owns the stable engine API. The separately version-matched `mdrap-contrib` distribution supplies peripheral modules and the `mdrap` command. Source-tree compatibility shims are excluded from both wheels.

### Migrating legacy imports

The installed wheel no longer provides generic top-level modules. Update imports such as `from models import RawEvent` and `from pipeline import Pipeline` to `from mdrap.models import RawEvent` and `from mdrap.pipeline import Pipeline`; prefer the supported facade (`from mdrap import Pipeline, RawEvent, Store`) when those names are sufficient. Flat imports may continue to work from a source checkout during migration, but they are not part of the installed package API.
- `mdrap.adapters` defines an adapter protocol and includes a reference adapter. There is no supervisor that owns adapter lifecycle or connects adapters to the HTTP server.
- Feed registration stores metadata and reports `REGISTERED_NOT_RUNNING`. The WebSocket endpoint is not connected to live server ingestion.
- SQLite stores projections. The optional binary journal is not the source of truth for pipeline acknowledgements.
- API startup outside demo mode requires a persistent `MDRAP_API_KEY_SALT` value. See [`.env.example`](.env.example).
- Native acceleration is optional and platform-specific. No native performance claim is made here.

## Run

```bash
mdrap version
mdrap --help
python -m pytest
```

The default pytest configuration excludes tests marked `slow` and `network`; run those separately when their platform and external-service requirements are available.

## Engineering status

See [the audit revalidation](docs/verified-gap-report.md) and [the roadmap](MDRAP%20v3.0.0%20Ruthless%20Audit%20and%20Roadmap.md). The repository is being migrated incrementally. Durability, security hardening, portability, end-to-end feed operation, and reproducible performance still require separate implementation and evidence.

## License

MIT. See [LICENSE](LICENSE).
