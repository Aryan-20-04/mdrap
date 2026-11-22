"""MDRAP Observer — Institutional Read-Only Market Data Cockpit & Sidecar Monitor.

Exclusively uses documented MDRAP public interfaces:
- `mdrap.shm.SHMReader` for ultra-low latency shared memory IPC.
- `mdrap.service.StreamClient` for asynchronous TCP streaming.
"""

__version__ = "1.0.0"
