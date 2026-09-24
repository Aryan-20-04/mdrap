$ErrorActionPreference = "Stop"
python scripts\check.py $args
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
