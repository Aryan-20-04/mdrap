# MDRAP legacy import compatibility

This opt-in distribution supplies deprecated top-level imports such as
`from models import RawEvent`. Install `mdrap-compat` only while migrating; it
depends on `mdrap-core` and `mdrap-contrib` and intentionally adds generic names
to Python's top-level import namespace. An application module with the same name
can take precedence depending on `sys.path` order.

New code should use `from mdrap.models import RawEvent`. The compatibility
distribution is intended for the 3.0 migration window and is not part of either
the core or contrib wheel.
