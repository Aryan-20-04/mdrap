"""
Unit tests for src/exporter.py to expand test coverage.
Tests gather_report_data, export_excel, export_csv_package, and order flow exports.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from exporter import MarketDataExporter


def test_exporter_gather_and_csv():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_exp.db")
        exporter = MarketDataExporter(db_path=db_path)

        data = exporter.gather_report_data("AAPL")
        assert "symbol" in data
        assert "bbo" in data
        assert "health" in data

        csv_dir = os.path.join(tmpdir, "csv_out")
        created_files = exporter.export_csv("AAPL", output_dir=csv_dir)
        assert len(created_files) > 0
        for f in created_files:
            assert os.path.exists(f)


def test_exporter_excel():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_exp.db")
        exporter = MarketDataExporter(db_path=db_path)

        xlsx_path = os.path.join(tmpdir, "test_model.xlsx")
        out_file = exporter.export_excel("AAPL", output_path=xlsx_path)
        assert os.path.exists(out_file)
        assert out_file.endswith(".xlsx")


def test_export_order_flow_workbook():
    with tempfile.TemporaryDirectory() as tmpdir:
        summary = {
            "symbol": "NVDA",
            "institutional_bias": "ACCUMULATING",
            "cvd": 15000.0,
            "cnd": 1800000.0,
            "aggressor_ratio_pct": 62.5,
            "total_trades": 150,
            "total_volume": 50000.0,
            "buy_volume": 32000.0,
            "sell_volume": 18000.0,
            "whale_trades": 5,
            "block_trades": 10,
            "retail_trades": 50,
            "top_participants": [
                {
                    "mpid": "GSCO",
                    "name": "Goldman Sachs",
                    "buy_volume": 10000.0,
                    "sell_volume": 2000.0,
                    "net_volume": 8000.0,
                    "net_notional": 960000.0,
                    "buy_ratio_pct": 83.3,
                    "trades": 25,
                    "whales": 2,
                    "stance": "ACCUMULATING",
                }
            ],
        }

        out_path = os.path.join(tmpdir, "flow_nvda.xlsx")
        exporter = MarketDataExporter(db_path=":memory:")
        result = exporter.export_flow_workbook(summary, symbol="NVDA", output_path=out_path)
        assert os.path.exists(result)
