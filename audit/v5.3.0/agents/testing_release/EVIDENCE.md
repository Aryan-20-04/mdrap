# Testing, Build & Release Engineering — Exact Evidence

## 1. Assertion Swallowed in `test_fastpath.py`
- File: `tests/test_fastpath.py:81-86`
  ```python
  try:
      assert res_py.quality_status == res_c.quality_status
  except Exception:
      pass
  ```

## 2. Rubber Stamp Gate in `gen_claims.py`
- File: `scripts/gen_claims.py:155-157`
  ```python
  current_claim = match.group(2).strip()
  if args.check:
      print(f"[PASSED] Verified claims manifest for {claim_str}.")
  ```

## 3. `-march=native` in Wheel Flags
- File: `src/mdrap/build_fastpath.py:152`
  ```python
  cmd = [compiler, "-O3", "-march=native", *_get_arch_flags(), ...]
  ```\n