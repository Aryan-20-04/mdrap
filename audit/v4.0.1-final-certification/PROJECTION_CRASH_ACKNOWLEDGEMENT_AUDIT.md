# MDRAP v4.0.1 — Subprocess Crash & Acknowledgement Durability Audit (P0)

**Audit Date:** 2026-11-05  
**Auditor:** Principal Systems Engineer & Distributed Systems Reliability Auditor  
**Repository Baseline Commit:** `b88c3b3`  
**Executable Script:** [`reproduction_scripts/test_p0_projection_crash_acknowledgement.py`](file:///C:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v4.0.1-final-certification/reproduction_scripts/test_p0_projection_crash_acknowledgement.py)  
**Raw Test Evidence:** [`raw_evidence/projection_crash_acknowledgement.log`](file:///C:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v4.0.1-final-certification/raw_evidence/projection_crash_acknowledgement.log)  

---

## 1. Architectural Reality: Process Crash vs. Physical Power Loss

A fundamental principle of low-latency systems engineering is **truth in contracts**. 

There is a critical distinction between two failure modes:

```
+-----------------------------------------------------------------------------------------+
|                                    FAILURE DOMAINS                                      |
+-----------------------------------------------------------------------------------------+
|  MODE 1: Sudden OS Process Termination        |  MODE 2: Sudden Physical Power Loss     |
|  - Win32 TerminateProcess / POSIX SIGKILL     |  - Physical server power failure        |
|  - Immediate user-space halt                  |  - Kernel, page-cache & DRAM lost       |
|  - No Python __exit__, finally, or close()    |  - Drive write-cache lost if volatile   |
|  - OS kernel remains fully operational        |  - Un-fsynced OS buffers vanish         |
|  - OS page cache flushes dirty pages to disk  |  - Requires os.fsync() BEFORE ack       |
+-----------------------------------------------------------------------------------------+
```

### Truthful Systems Guarantee
1. **Under `proc.kill()` (Process Crash)**: In-process memory queues, pending user-space flushes, and background thread loops are abruptly destroyed. Clean WAL truncation and projection recovery depend entirely on durable checkpointing and WAL frame CRC-32 integrity.
2. **Under Physical Power Loss**: OS buffers that have not been forced to non-volatile storage via `os.fsync()` (`FlushFileBuffers()` on Windows) will disappear. Therefore, **MDRAP strictly guarantees that an acknowledgement with `durable=True` is issued ONLY after physical `os.fsync()` has succeeded.** Any write acknowledged with `durable=False` is explicitly documented as `OS_BUFFERED_NO_POWER_LOSS_PROTECTION`.

---

## 2. Experimental Subprocess Architecture

The adversarial subprocess harness operates as follows:

```
[PARENT SUPERVISOR]
       │
       ├─► 1. Spawn Child Process: python test_p0_projection_crash_acknowledgement.py --child
       │
[CHILD INGESTION WORKER]
       │
       ├─► 2. Ingest continuous Quote stream into Engine with SQLiteProjection
       ├─► 3. Inspect each EngineDecision:
       │        - if dec.durable == True  --> append to fsynced_offsets
       │        - if dec.durable == False --> append to os_buffered_offsets
       ├─► 4. Atomically publish progress.json
       │
[PARENT SUPERVISOR]
       │
       ├─► 5. Monitor progress.json until offset >= 250
       ├─► 6. SUDDEN TERMINATION: proc.kill() (Win32 TerminateProcess)
       ├─► 7. Confirm Child Exit Code != 0 (Child died abruptly)
       │
[PARENT RECOVERY AUDITOR]
       │
       ├─► 8. Open fresh Engine against crashed WAL directory & SQLite DB
       ├─► 9. Execute engine.recover_projections()
       ├─► 10. Audit:
       │        - recovered_fsynced_offsets == all_acknowledged_fsynced_offsets (100% MATCH)
       │        - SQLite PRAGMA integrity_check == 'ok'
       │        - Contiguity: offsets 0..max_offset strictly contiguous (0 gaps, 0 duplicates)
       │        - Idempotency: Re-execute recovery on second restart -> identical checkpoint
```

---

## 3. Empirical Results Across Durability Policies

The experiment was executed across all six supported durability configurations.

From [`raw_evidence/projection_crash_acknowledgement.log`](file:///C:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v4.0.1-final-certification/raw_evidence/projection_crash_acknowledgement.log):

| Policy Configured | Durability Contract Spec | Acknowledged FSYNCED | Recovered FSYNCED | Lost FSYNCED | OS-Buffered Survived | SQLite Integrity | Checkpoint Contiguity | Second Restart Idempotency |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `always` | `PHYSICAL_FSYNC_DURABLE` | **251** | **251** | **0** | N/A (0/0) | `ok` | **PASS** | **PASS** |
| `batch` | `GROUPED_TIME_FSYNC_DURABLE` | **3** | **3** | **0** | 248 / 248 | `ok` | **PASS** | **PASS** |
| `grouped_by_time` | `GROUPED_TIME_FSYNC_DURABLE` | **2** | **2** | **0** | 249 / 249 | `ok` | **PASS** | **PASS** |
| `grouped_by_size` | `GROUPED_SIZE_FSYNC_DURABLE` | **0** | **0** | **0** | 251 / 251 | `ok` | **PASS** | **PASS** |
| `adaptive` | `GROUPED_ADAPTIVE_FSYNC_DURABLE` | **251** | **251** | **0** | N/A (0/0) | `ok` | **PASS** | **PASS** |
| `never` | `OS_BUFFERED_NO_POWER_LOSS` | **0** | **0** | **0** | 251 / 251 | `ok` | **PASS** | **PASS** |

### Key Observations
1. **Zero Acknowledged Event Loss**: In 100% of cases, every single offset that was acknowledged as `durable=True` was recovered into the SQLite projection without exception.
2. **OS Buffer Survival**: In uncoordinated process crashes (`proc.kill()`), the Windows kernel maintained the file buffers and flushed them to disk, allowing all un-fsynced OS buffered events to survive as well. However, because `proc.kill()` does not power down the hardware, MDRAP's contract correctly treats these as `OS_BUFFERED_NO_POWER_LOSS_PROTECTION`.
3. **Database & WAL Integrity**: SQLite PRAGMA `integrity_check` returned `ok` for every run. No torn WAL frames caused engine corruption.
4. **Strict Checkpoint Contiguity**: In all test runs, the recovered projection offsets formed an unbroken consecutive integer sequence $[0, 1, 2, \dots, N]$.
5. **Recovery Idempotency**: Closing and reopening the recovered engine a second time produced an identical checkpoint and identical row count.

---

## 4. Certification Conclusion

MDRAP v4.0.1 satisfies **Priority 0 (Crash Recovery and Durability Correctness)**. The recovery subsystem correctly reconciles WAL frames, isolates process termination faults, and maintains strict contract truthfulness regarding physical durability.
