# MDRAP CI Cross-Platform Remediation — Agent 1 Report: SHM Epoch, Stream and Client Correctness

## 1. Domain Ownership & Executive Summary
- **Agent Role**: Shared Memory Epoch, Stream Semantics & Client Correctness
- **Primary Files**:
  - `src/mdrap/shm.py`
  - `src/mdrap/client.py`
- **Tests Covered**:
  - `tests/test_shm.py`
  - `tests/test_failure_modes.py`
  - `tests/test_shm_decoupled.py`
  - `tests/test_shm_fuzz.py`
  - `tests/test_shm_publisher_crash.py`
  - `tests/test_phase2_roadmap_verification.py`
- **Status**: PASSED / VERIFIED (46 passed, 1 skipped, 0 failed)

## 2. Issues Diagnosed & Root Cause Analysis

### Issue 1: Darwin `st_nlink == 0` False Invalidation
- **Symptom**: On macOS Darwin, every newly attached `SHMReader` reported `check_epoch_valid() == False`, invalidating its state immediately upon creation. Tests such as `test_shm_long_name_attachment_and_cleanup`, `test_epoch_mutation_detection`, and `test_shm_writer_close_poisons_epoch_instantly` failed on Darwin.
- **Root Cause**: On Darwin/XNU kernel, POSIX shared memory descriptors opened via `shm_open` are Mach kernel objects that do not link to real directory filesystem inodes. Consequently, `os.fstat(fd).st_nlink` evaluates to `0` at all times, even for live, unlinked segments. The check `if os.fstat(self.shm._fd).st_nlink == 0:` was executing unconditionally across all platforms, instantly failing on macOS.
- **Remediation**: Gated the `st_nlink == 0` check to `platform.system() == "Linux"`. For macOS, writer closure is signaled via zero-syscall memory poisoning (`struct.pack_into("<Q", self.shm.buf, 16, 0)`) and stale heartbeat detection.

### Issue 2: Violation of Zero-Syscall Steady-State Contract
- **Symptom**: `test_2_2_epoch_probe_avoids_syscall_storm` failed on macOS because `_probe_active_epoch` was called during steady state.
- **Root Cause**: A Darwin periodic probe was firing every 100ms (`(now - self._last_epoch_probe) >= 0.1`), calling `_probe_active_epoch()` in steady state when mocked or unmocked. Furthermore, `self._last_epoch_probe` was initialized to `0.0` in `SHMReader.__init__`, causing the very first call to probe externally.
- **Remediation**: Removed periodic Darwin-specific external probing on steady-state reads. Initialized `self._last_epoch_probe = time.time()`. Fast in-memory offset 16 reads remain strictly zero-syscall. External segment probing is reserved exclusively for explicit `force_probe=True` or when heartbeat is stale.

### Issue 3: Repeated Spurious `EPOCH_CHANGE` Yields and Stream Count Doubling
- **Symptom**:
  - `test_shm_stream_generator_sub_microsecond`: 100 events returned instead of 50.
  - `test_slow_reader_overrun_detection`: 20 events returned instead of 10.
  - `test_publisher_crash_during_stream_spin`: first event lacked `"seq"`.
  - `test_publisher_crash_mid_write_torn_slot`: first event lacked `"seq"`.
  - `test_mdrap_client_with_shm_end_to_end`: 0 events instead of 10.
  - `test_client_streaming_epoch_auto_recovery`: 0 events instead of 1.
- **Root Cause**: In `SHMReader.stream()`, `self.check_epoch_valid(force_probe=True)` was called on stream startup. Because it returned `False` on macOS, it yielded an initial `{"type": "EPOCH_CHANGE", ...}` control frame with no `"seq"` field. When `old_epoch == self.epoch_id`, this frame was emitted falsely. In `MDRAPClient.stream()`, seeing `EPOCH_CHANGE` triggered reader re-attachment and loop reset, resetting stream cursors and causing event counts to double (50 + 50 = 100, 10 + 10 = 20) or client timeout.
- **Remediation**:
  1. Only emit `EPOCH_CHANGE` when an actual epoch change occurs (`self.epoch_id != old_epoch` and `self.epoch_id != 0`).
  2. Use zero-syscall `check_epoch_valid(force_probe=False)` on stream startup and loop iterations.
  3. Ensure data generators yield valid market data ticks as first events when writer is healthy.

## 3. Verification Evidence
Executed full suite:
`pytest tests/test_shm.py tests/test_failure_modes.py tests/test_shm_decoupled.py tests/test_shm_fuzz.py tests/test_shm_publisher_crash.py tests/test_phase2_roadmap_verification.py -v`
Result: **46 passed, 1 skipped, 0 failed in 3.92s**.
