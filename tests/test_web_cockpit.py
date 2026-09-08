import os
import sys
import time
import urllib.request
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from web_cockpit import WebCockpitServer


def test_web_cockpit_server_lifecycle_and_endpoints():
    port = 18090
    server = WebCockpitServer(host="127.0.0.1", port=port)
    server.start(daemon=True)
    time.sleep(0.3)

    try:
        base = f"http://127.0.0.1:{port}"

        # 1. Test HTML Dashboard
        with urllib.request.urlopen(f"{base}/", timeout=3) as resp:
            assert resp.status == 200
            html = resp.read().decode("utf-8")
            assert "MDRAP" in html
            assert "Consolidated L2 Depth Ladder" in html
            assert "Cumulative Volume Delta" in html
            assert "Transaction Cost Analysis" in html

        # 2. Test JSON Snapshot API
        with urllib.request.urlopen(f"{base}/api/snapshot?symbol=AAPL", timeout=3) as resp:
            assert resp.status == 200
            import json
            data = json.loads(resp.read().decode("utf-8"))
            assert "quote" in data
            assert data["quote"]["symbol"] == "AAPL"
            assert "flow" in data
            assert "tca" in data

        # 3. Test Download Excel API
        with urllib.request.urlopen(f"{base}/api/download_workbook", timeout=5) as resp:
            assert resp.status == 200
            content = resp.read()
            assert len(content) > 1000  # valid xlsx binary
            assert content.startswith(b"PK")  # ZIP/XLSX magic bytes

        # 4. Test Paper Trading Order Placement API
        order_req = urllib.request.Request(
            f"{base}/api/order",
            data=json.dumps({
                "symbol": "AAPL",
                "side": "BUY",
                "order_type": "MARKET",
                "quantity": 100,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(order_req, timeout=3) as resp:
            assert resp.status == 200
            res = json.loads(resp.read().decode("utf-8"))
            assert res["status"] == "FILLED"
            assert res["symbol"] == "AAPL"
            assert res["quantity"] == 100
            assert res["position_qty"] == 100

        # 5. Test Positions API
        with urllib.request.urlopen(f"{base}/api/positions", timeout=3) as resp:
            assert resp.status == 200
            p_data = json.loads(resp.read().decode("utf-8"))
            assert "cash" in p_data
            assert "positions" in p_data
            assert "AAPL" in p_data["positions"]
            assert p_data["positions"]["AAPL"]["quantity"] == 100

    finally:
        server.stop()
