@echo off
setlocal enabledelayedexpansion

echo ======================================================================
echo              MDRAP PLATFORM FULL FEATURE VERIFICATION
echo ======================================================================
echo.

echo [1/10] Running Pytest Suite (184 Tests)...
python -m pytest tests/ -v
if errorlevel 1 goto :fail

echo.
echo [2/10] Running 6-Stage Platform Verification...
python cli.py test
if errorlevel 1 goto :fail

echo.
echo [3/10] Testing Ingestion and FastPath (2,000 Events)...
python cli.py run --events 2000 --fastpath
if errorlevel 1 goto :fail

echo.
echo [4/10] Querying Source Health...
python cli.py query health
if errorlevel 1 goto :fail

echo.
echo [5/10] Inspecting Quarantined Events...
python cli.py query quarantine 5
if errorlevel 1 goto :fail

echo.
echo [6/10] Inspecting Consolidated L2 Depth Book (AAPL)...
python cli.py depth AAPL
if errorlevel 1 goto :fail

echo.
echo [7/10] Computing Real-Time VWAP Slippage Curve (AAPL)...
python cli.py vwap AAPL --sizes 1 5 10 25 50
if errorlevel 1 goto :fail

echo.
echo [8/10] Generating Market Analytics Summary...
python cli.py analytics summary
if errorlevel 1 goto :fail

echo.
echo [9/10] Inspecting Raw Event Archive...
python cli.py archive
if errorlevel 1 goto :fail

echo.
echo [10/10] Running High-Throughput Ingestion Benchmark (50,000 Events)...
python cli.py benchmark --events 50000 --fastpath
if errorlevel 1 goto :fail

echo.
echo ======================================================================
echo        ALL 10 VERIFICATION STAGES PASSED SUCCESSFULLY (100%%)
echo ======================================================================
goto :eof

:fail
echo.
echo ======================================================================
echo [ERROR] A verification stage failed. Check the output above.
echo ======================================================================
exit /b 1
