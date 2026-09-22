"""Regression test for differential fuzzing."""
from fuzz.fuzz_differential import run_sbe_differential_fuzz

def test_sbe_differential_fuzz_smoke():
    """Run 2,000 differential fuzz iterations on SBE frame decoder."""
    run_sbe_differential_fuzz(iterations=2000, seed=123)
