MDRAP v4.0.0.1 re-audit - additional experiment scripts (add to the first bundle; same setup)
Run against either build:  PYTHONPATH=/path/to/<build>/src python3 <script>
(simulate/sim_loop needs MDRAP_API_KEY_SALT set.)
  e_property.py        randomized live-vs-recovered state test (300 seeds x 3 configs)   <- best regression gate
  e_snap_concurrent.py concurrent submit() latency during save_snapshot() at 10k instruments
  e_proj2.py           transient projection outage with a plain-protocol projection (missed offsets)
  e_sqlite_proj.py     real SQLiteProjection: external write-lock stall; hole invisible to metrics()
  e_new_risks.py       R1 grouped ACK window on Engine path; R2 corrupt snapshot + pruned WAL (silent loss)
  e_bytes.py           unserializable (bytes) payload poison pill
  ab.py                interleaved perf A/B helper (run alternately for each build)
  sim_loop.py          ZeroDivisionError rate in `mdrap simulate` (pre-existing flake)
Timings are relative only (shared single vCPU). Correctness results are deterministic.
