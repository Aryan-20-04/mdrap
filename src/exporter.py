"""
MDRAP Institutional Financial Model & Excel/CSV Exporter.

Translates canonical real-time market data, consolidated L2 order books,
multi-venue VWAP slippage curves, and data quality audits into production-grade
Microsoft Excel (.xlsx) workbooks and CSV report packages for quantitative
researchers, risk desks, and algorithmic execution analysts.
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import datetime
from typing import List, Optional

from analytics import MarketAnalytics
from bbo import BBOEngine
from depth import ConsolidatedDepthEngine
from models import CanonicalEvent, EventType, QualityStatus
from pipeline import Pipeline
from simulator import FeedSimulator, SimulatorConfig
from storage import Store


class MarketDataExporter:
    """
    Exports comprehensive MDRAP market microstructure data into styled
    multi-tab Excel workbooks (.xlsx) and structured CSV packages.
    """

    def __init__(self, db_path: str = "data/mdrap.db"):
        self.db_path = db_path
        self._ensure_data_dir()

    def _ensure_data_dir(self) -> None:
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        os.makedirs("data/reports", exist_ok=True)

    def _ensure_populated_data(self, symbol: str) -> Store:
        """Ensure database has events for symbol; if empty, run a quick simulation."""
        store = Store(self.db_path)
        cnts = store.counts()
        canonical_count = sum(cnts.values())

        if canonical_count == 0:
            pipe = Pipeline(store=store)
            sim = FeedSimulator(SimulatorConfig(seed=42, num_events=2500))
            for raw, _label in sim.generate():
                pipe.process_one(raw)
            pipe.finish()

        return store

    def gather_report_data(self, symbol: str = "AAPL") -> dict:
        """
        Gather all analytical data points for the requested symbol.
        """
        store = self._ensure_populated_data(symbol)
        try:
            sym_clean = symbol.upper().replace("-", "/")

            # 1. Gather depth engine state & VWAP curves
            depth_eng = ConsolidatedDepthEngine()
            bbo_eng = BBOEngine()
            analytics_eng = MarketAnalytics(ohlcv_interval_s=5.0)

            # Replay canonical events for symbol from store
            raw_rows = store.latest(symbol, limit=2000)
            if not raw_rows and "/" in symbol:
                raw_rows = store.latest(symbol.replace("/", "-"), limit=2000)
            if not raw_rows:
                # Query latest events for AAPL by default
                raw_rows = store.latest("AAPL", limit=1000)

            for d in raw_rows:
                try:
                    ev_type_str = d.get("event_type", "TRADE")
                    ev_type = (
                        EventType.QUOTE if ev_type_str == "QUOTE" else EventType.TRADE
                    )
                    st_str = d.get("quality_status", "VALID")
                    st = (
                        QualityStatus.INVALID
                        if st_str == "INVALID"
                        else (
                            QualityStatus.SUSPICIOUS
                            if st_str == "SUSPICIOUS"
                            else QualityStatus.VALID
                        )
                    )
                    ev = CanonicalEvent(
                        event_id=d.get("event_id", ""),
                        instrument_id=d.get("instrument_id", symbol),
                        event_type=ev_type,
                        exchange_timestamp=float(d.get("exchange_timestamp", 0.0)),
                        receive_timestamp=float(d.get("receive_timestamp", 0.0)),
                        processing_timestamp=float(d.get("processing_timestamp", 0.0)),
                        source=str(d.get("source", "")),
                        sequence_number=int(d.get("sequence_number", 0)),
                        price=d.get("price"),
                        quantity=d.get("quantity"),
                        bid_price=d.get("bid_price"),
                        bid_size=d.get("bid_size"),
                        ask_price=d.get("ask_price"),
                        ask_size=d.get("ask_size"),
                        quality_status=st,
                    )
                    depth_eng.observe(ev)
                    bbo_eng.observe(ev)
                    analytics_eng.observe(ev)
                except Exception:
                    pass

            ladder = depth_eng.current_ladder(symbol) or depth_eng.current_ladder(
                sym_clean
            )
            bbo_obj = bbo_eng.current_bbo(symbol) or bbo_eng.current_bbo(sym_clean)
            sizes = [1.0, 5.0, 10.0, 25.0, 50.0]
            curve = depth_eng.current_vwap_curve(symbol, sizes) or (
                ladder.compute_vwap_curve(sizes) if ladder else None
            )

            # Analytics
            analytics_summary = analytics_eng.full_summary()
            candles = [
                c
                for c in analytics_summary.get("ohlcv", [])
                if c.get("instrument_id") in (symbol, sym_clean)
            ]
            spreads = [
                s
                for s in analytics_summary.get("spreads", [])
                if s.get("instrument_id") in (symbol, sym_clean)
            ]
            volatility = [
                v
                for v in analytics_summary.get("volatility", [])
                if v.get("instrument_id") in (symbol, sym_clean)
            ]

            # Feed health & quality statistics
            health = store.feed_health()
            quarantine = store.quarantine_sample(limit=50)
            cnts = store.counts()
            total_canonical = sum(cnts.values())
            cur = store.conn.execute("SELECT COUNT(*) FROM quarantine")
            total_quarantine = cur.fetchone()[0] if cur else 0

            return {
                "symbol": symbol,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "bbo": bbo_obj,
                "ladder": ladder,
                "vwap_curve": curve,
                "candles": candles,
                "spreads": spreads[0] if spreads else {},
                "volatility": volatility[0] if volatility else {},
                "health": health,
                "quarantine": quarantine,
                "total_canonical": total_canonical,
                "total_quarantine": total_quarantine,
            }
        finally:
            store.close()

    def export_excel(
        self,
        symbol: str = "AAPL",
        output_path: Optional[str] = None,
        auto_open: bool = False,
    ) -> str:
        """
        Generate a multi-tab, professionally styled Microsoft Excel (.xlsx) workbook.
        """
        try:
            import openpyxl
            from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
            from openpyxl.utils import get_column_letter
        except ImportError:
            # Fallback to CSV if openpyxl is not installed
            csv_files = self.export_csv(symbol)
            return csv_files[0] if csv_files else ""

        data = self.gather_report_data(symbol)
        clean_sym = symbol.replace("/", "_").replace("-", "_")
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")

        if output_path is None:
            output_path = os.path.abspath(
                f"data/reports/MDRAP_{clean_sym}_{timestamp_str}.xlsx"
            )
        else:
            output_path = os.path.abspath(output_path)

        wb = openpyxl.Workbook()
        # Remove default sheet
        wb.remove(wb.active)

        # Common styling definitions
        font_title = Font(name="Segoe UI", size=14, bold=True, color="1E293B")
        font_sec = Font(name="Segoe UI", size=11, bold=True, color="0F172A")
        font_hdr = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
        font_cell = Font(name="Segoe UI", size=10, color="1E293B")
        font_bold = Font(name="Segoe UI", size=10, bold=True, color="1E293B")

        fill_hdr = PatternFill(
            start_color="1E293B", end_color="1E293B", fill_type="solid"
        )
        fill_subhdr = PatternFill(
            start_color="334155", end_color="334155", fill_type="solid"
        )
        fill_bid = PatternFill(
            start_color="ECFDF5", end_color="ECFDF5", fill_type="solid"
        )
        fill_ask = PatternFill(
            start_color="FEF2F2", end_color="FEF2F2", fill_type="solid"
        )
        _fill_accent = PatternFill(
            start_color="EFF6FF", end_color="EFF6FF", fill_type="solid"
        )

        font_bid = Font(name="Segoe UI", size=10, color="065F46", bold=True)
        font_ask = Font(name="Segoe UI", size=10, color="991B1B", bold=True)

        border_thin = Border(
            left=Side(style="thin", color="E2E8F0"),
            right=Side(style="thin", color="E2E8F0"),
            top=Side(style="thin", color="E2E8F0"),
            bottom=Side(style="thin", color="E2E8F0"),
        )

        align_center = Alignment(horizontal="center", vertical="center")
        _align_right = Alignment(horizontal="right", vertical="center")
        align_left = Alignment(horizontal="left", vertical="center")

        # ==========================================
        # TAB 1: EXECUTIVE OVERVIEW
        # ==========================================
        ws1 = wb.create_sheet(title="Executive Overview")
        ws1.views.sheetView[0].showGridLines = True

        ws1["A1"] = f"MDRAP Institutional Market Summary: {symbol}"
        ws1["A1"].font = font_title
        ws1["A2"] = f"Generated: {data['timestamp']} | Database: {self.db_path}"
        ws1["A2"].font = Font(name="Segoe UI", size=9, italic=True, color="64748B")

        bbo = data.get("bbo")
        ladder = data.get("ladder")

        overview_metrics = [
            ("Target Instrument", symbol, "@"),
            ("National Best Bid (NBBO)", bbo.best_bid if bbo else 0.0, "$#,##0.00"),
            ("National Best Ask (NBBO)", bbo.best_ask if bbo else 0.0, "$#,##0.00"),
            ("Consolidated Mid-Price", bbo.mid_price if bbo else 0.0, "$#,##0.00"),
            ("Quoted Spread ($)", bbo.spread if bbo else 0.0, "$#,##0.00"),
            (
                "Quoted Spread (bps)",
                (bbo.spread / bbo.mid_price * 10000.0)
                if (bbo and bbo.mid_price)
                else 0.0,
                '0.00" bps"',
            ),
            (
                "Micro-Price (VWAP Mid)",
                ladder.micro_price if ladder else 0.0,
                "$#,##0.00",
            ),
            (
                "Order Flow Imbalance (OFI)",
                ladder.imbalance_ratio if ladder else 0.0,
                "+0.00;-0.00;0.00",
            ),
            (
                "Cross-Market Arbitrage",
                "DETECTED (CROSS)" if (bbo and bbo.is_crossed) else "NONE (HEALTHY)",
                "@",
            ),
            (
                "Total Bid Book Notional ($)",
                ladder.total_bid_notional if ladder else 0.0,
                "$#,##0.00",
            ),
            (
                "Total Ask Book Notional ($)",
                ladder.total_ask_notional if ladder else 0.0,
                "$#,##0.00",
            ),
            ("Total Processed Canonical Events", data["total_canonical"], "#,##0"),
            ("Total Quarantined Events", data["total_quarantine"], "#,##0"),
        ]

        ws1.append([])
        row_idx = 4
        ws1.cell(row=row_idx, column=1, value="Metric Description").fill = fill_hdr
        ws1.cell(row=row_idx, column=1).font = font_hdr
        ws1.cell(row=row_idx, column=2, value="Telemetry Value").fill = fill_hdr
        ws1.cell(row=row_idx, column=2).font = font_hdr

        for desc, val, num_fmt in overview_metrics:
            row_idx += 1
            c1 = ws1.cell(row=row_idx, column=1, value=desc)
            c2 = ws1.cell(row=row_idx, column=2, value=val)
            c1.font = font_cell
            c1.border = border_thin
            c2.font = font_bold
            c2.border = border_thin
            c2.number_format = num_fmt

        # ==========================================
        # TAB 2: VWAP SLIPPAGE MODEL (TCA)
        # ==========================================
        ws2 = wb.create_sheet(title="VWAP Slippage Model")
        ws2.views.sheetView[0].showGridLines = True

        ws2["A1"] = f"Institutional VWAP & Slippage Execution Walk: {symbol}"
        ws2["A1"].font = font_title
        ws2["A2"] = "Simulating book walks across multi-venue coalesced order depth"
        ws2["A2"].font = Font(name="Segoe UI", size=9, italic=True, color="64748B")

        curve = data.get("vwap_curve")
        if curve:
            # BUY SIDE
            ws2["A4"] = "BUY SIDE (Walking Asks)"
            ws2["A4"].font = font_sec

            buy_headers = [
                "Order Size",
                "Fill Size",
                "Expected VWAP",
                "Slippage ($)",
                "Slippage (bps)",
                "Eff Spread (bps)",
                "Fill %",
                "Venue Attribution",
            ]
            for col_i, h in enumerate(buy_headers, 1):
                cell = ws2.cell(row=5, column=col_i, value=h)
                cell.fill = fill_hdr
                cell.font = font_hdr
                cell.alignment = align_center

            curr_row = 6
            for s in curve.buy_slices:
                venues_str = ", ".join(
                    f"{v}: {q:.1f}" for v, q in s.venue_breakdown.items()
                )
                fill_pct = (s.filled_size / s.target_size) if s.target_size else 0.0
                ws2.cell(
                    row=curr_row, column=1, value=s.target_size
                ).number_format = "#,##0.00"
                ws2.cell(
                    row=curr_row, column=2, value=s.filled_size
                ).number_format = "#,##0.00"
                ws2.cell(
                    row=curr_row, column=3, value=s.vwap_price
                ).number_format = "$#,##0.00"
                ws2.cell(
                    row=curr_row, column=4, value=s.slippage_dollars
                ).number_format = "+$#,##0.00;-$#,##0.00;$0.00"
                ws2.cell(
                    row=curr_row, column=5, value=s.slippage_bps
                ).number_format = '0.00" bps"'
                ws2.cell(
                    row=curr_row, column=6, value=s.effective_spread_bps
                ).number_format = '0.00" bps"'
                ws2.cell(row=curr_row, column=7, value=fill_pct).number_format = "0.0%"
                ws2.cell(
                    row=curr_row, column=8, value=venues_str
                ).alignment = align_left

                for ci in range(1, 9):
                    ws2.cell(row=curr_row, column=ci).border = border_thin
                    ws2.cell(row=curr_row, column=ci).font = font_cell
                curr_row += 1

            # SELL SIDE
            curr_row += 2
            ws2.cell(
                row=curr_row, column=1, value="SELL SIDE (Walking Bids)"
            ).font = font_sec
            curr_row += 1

            sell_headers = [
                "Order Size",
                "Fill Size",
                "Expected VWAP",
                "Slippage ($)",
                "Slippage (bps)",
                "Eff Spread (bps)",
                "Fill %",
                "Venue Attribution",
            ]
            for col_i, h in enumerate(sell_headers, 1):
                cell = ws2.cell(row=curr_row, column=col_i, value=h)
                cell.fill = fill_subhdr
                cell.font = font_hdr
                cell.alignment = align_center

            curr_row += 1
            for s in curve.sell_slices:
                venues_str = ", ".join(
                    f"{v}: {q:.1f}" for v, q in s.venue_breakdown.items()
                )
                fill_pct = (s.filled_size / s.target_size) if s.target_size else 0.0
                ws2.cell(
                    row=curr_row, column=1, value=s.target_size
                ).number_format = "#,##0.00"
                ws2.cell(
                    row=curr_row, column=2, value=s.filled_size
                ).number_format = "#,##0.00"
                ws2.cell(
                    row=curr_row, column=3, value=s.vwap_price
                ).number_format = "$#,##0.00"
                ws2.cell(
                    row=curr_row, column=4, value=s.slippage_dollars
                ).number_format = "+$#,##0.00;-$#,##0.00;$0.00"
                ws2.cell(
                    row=curr_row, column=5, value=s.slippage_bps
                ).number_format = '0.00" bps"'
                ws2.cell(
                    row=curr_row, column=6, value=s.effective_spread_bps
                ).number_format = '0.00" bps"'
                ws2.cell(row=curr_row, column=7, value=fill_pct).number_format = "0.0%"
                ws2.cell(
                    row=curr_row, column=8, value=venues_str
                ).alignment = align_left

                for ci in range(1, 9):
                    ws2.cell(row=curr_row, column=ci).border = border_thin
                    ws2.cell(row=curr_row, column=ci).font = font_cell
                curr_row += 1

            # LIQUIDITY DEPTH BANDS
            curr_row += 2
            ws2.cell(
                row=curr_row, column=1, value="LIQUIDITY DEPTH BANDS (USD Available)"
            ).font = font_sec
            curr_row += 1

            band_headers = [
                "Depth Band",
                "Bid Liquidity ($)",
                "Ask Liquidity ($)",
                "Total Liquidity ($)",
                "Imbalance",
            ]
            for col_i, h in enumerate(band_headers, 1):
                cell = ws2.cell(row=curr_row, column=col_i, value=h)
                cell.fill = fill_hdr
                cell.font = font_hdr
                cell.alignment = align_center

            curr_row += 1
            for bname, band in [
                ("±10 bps (0.10%)", curve.depth_10bps),
                ("±50 bps (0.50%)", curve.depth_50bps),
                ("±100 bps (1.00%)", curve.depth_100bps),
            ]:
                bid_n, ask_n = band
                tot_n = bid_n + ask_n
                imb = (bid_n - ask_n) / tot_n if tot_n > 0 else 0.0

                ws2.cell(row=curr_row, column=1, value=bname).font = font_bold
                ws2.cell(
                    row=curr_row, column=2, value=bid_n
                ).number_format = "$#,##0.00"
                ws2.cell(
                    row=curr_row, column=3, value=ask_n
                ).number_format = "$#,##0.00"
                ws2.cell(
                    row=curr_row, column=4, value=tot_n
                ).number_format = "$#,##0.00"
                ws2.cell(
                    row=curr_row, column=5, value=imb
                ).number_format = "+0.00;-0.00;0.00"

                for ci in range(1, 6):
                    ws2.cell(row=curr_row, column=ci).border = border_thin
                    ws2.cell(row=curr_row, column=ci).font = font_cell
                curr_row += 1

        # ==========================================
        # TAB 3: CONSOLIDATED L2 ORDER BOOK
        # ==========================================
        ws3 = wb.create_sheet(title="Consolidated L2 Depth")
        ws3.views.sheetView[0].showGridLines = True

        ws3["A1"] = f"Consolidated Multi-Venue Order Book Depth: {symbol}"
        ws3["A1"].font = font_title
        ws3["A2"] = (
            "Price ladder coalesced across Binance, Coinbase, Kraken, OKX, Bybit"
        )
        ws3["A2"].font = Font(name="Segoe UI", size=9, italic=True, color="64748B")

        depth_headers = [
            "Bid Venue",
            "Cum Bid Size",
            "Bid Size",
            "Bid Price",
            " | ",
            "Ask Price",
            "Ask Size",
            "Cum Ask Size",
            "Ask Venue",
        ]

        for col_i, h in enumerate(depth_headers, 1):
            cell = ws3.cell(row=4, column=col_i, value=h)
            cell.fill = fill_hdr
            cell.font = font_hdr
            cell.alignment = align_center

        if ladder:
            bids = ladder.aggregated_bids[:15]
            asks = ladder.aggregated_asks[:15]
            max_depth = max(len(bids), len(asks))

            for r in range(max_depth):
                row_num = 5 + r
                # Bids
                if r < len(bids):
                    b = bids[r]
                    v_str = ", ".join(b.venue_sizes.keys())
                    c_v = ws3.cell(row=row_num, column=1, value=v_str)
                    c_cb = ws3.cell(row=row_num, column=2, value=b.cumulative_size)
                    c_s = ws3.cell(row=row_num, column=3, value=b.total_size)
                    c_p = ws3.cell(row=row_num, column=4, value=b.price)

                    c_v.font = font_cell
                    c_cb.font = font_cell
                    c_s.font = font_bid
                    c_p.font = font_bid

                    c_cb.number_format = "#,##0.00"
                    c_s.number_format = "#,##0.00"
                    c_p.number_format = "$#,##0.00"
                    c_p.fill = fill_bid
                else:
                    ws3.cell(row=row_num, column=1, value="")
                    ws3.cell(row=row_num, column=2, value="")
                    ws3.cell(row=row_num, column=3, value="")
                    ws3.cell(row=row_num, column=4, value="")

                # Separator
                ws3.cell(row=row_num, column=5, value="|").alignment = align_center

                # Asks
                if r < len(asks):
                    a = asks[r]
                    v_str = ", ".join(a.venue_sizes.keys())
                    c_p = ws3.cell(row=row_num, column=6, value=a.price)
                    c_s = ws3.cell(row=row_num, column=7, value=a.total_size)
                    c_ca = ws3.cell(row=row_num, column=8, value=a.cumulative_size)
                    c_v = ws3.cell(row=row_num, column=9, value=v_str)

                    c_p.font = font_ask
                    c_s.font = font_ask
                    c_ca.font = font_cell
                    c_v.font = font_cell

                    c_p.number_format = "$#,##0.00"
                    c_s.number_format = "#,##0.00"
                    c_ca.number_format = "#,##0.00"
                    c_p.fill = fill_ask
                else:
                    ws3.cell(row=row_num, column=6, value="")
                    ws3.cell(row=row_num, column=7, value="")
                    ws3.cell(row=row_num, column=8, value="")
                    ws3.cell(row=row_num, column=9, value="")

                for ci in range(1, 10):
                    ws3.cell(row=row_num, column=ci).border = border_thin

        # ==========================================
        # TAB 4: OHLCV MARKET CANDLES
        # ==========================================
        ws4 = wb.create_sheet(title="OHLCV Market Candles")
        ws4.views.sheetView[0].showGridLines = True

        ws4["A1"] = f"5-Second OHLCV Market Aggregation: {symbol}"
        ws4["A1"].font = font_title
        ws4["A2"] = "Aggregated trade candlesticks ready for Excel charting"
        ws4["A2"].font = Font(name="Segoe UI", size=9, italic=True, color="64748B")

        candle_headers = [
            "Bucket Time",
            "Interval (s)",
            "Open ($)",
            "High ($)",
            "Low ($)",
            "Close ($)",
            "Volume",
            "Trades",
        ]
        for col_i, h in enumerate(candle_headers, 1):
            cell = ws4.cell(row=4, column=col_i, value=h)
            cell.fill = fill_hdr
            cell.font = font_hdr
            cell.alignment = align_center

        candles = data.get("candles", [])
        c_row = 5
        for c in candles[:200]:
            t_str = datetime.fromtimestamp(c.get("bucket_start", 0)).strftime(
                "%H:%M:%S"
            )
            ws4.cell(row=c_row, column=1, value=t_str).alignment = align_center
            ws4.cell(
                row=c_row, column=2, value=c.get("interval_s", 5.0)
            ).number_format = "#,##0"
            ws4.cell(
                row=c_row, column=3, value=c.get("open")
            ).number_format = "$#,##0.00"
            ws4.cell(
                row=c_row, column=4, value=c.get("high")
            ).number_format = "$#,##0.00"
            ws4.cell(
                row=c_row, column=5, value=c.get("low")
            ).number_format = "$#,##0.00"
            ws4.cell(
                row=c_row, column=6, value=c.get("close")
            ).number_format = "$#,##0.00"
            ws4.cell(
                row=c_row, column=7, value=c.get("volume")
            ).number_format = "#,##0.00"
            ws4.cell(
                row=c_row, column=8, value=c.get("event_count")
            ).number_format = "#,##0"

            for ci in range(1, 9):
                ws4.cell(row=c_row, column=ci).border = border_thin
                ws4.cell(row=c_row, column=ci).font = font_cell
            c_row += 1

        # ==========================================
        # TAB 5: DATA QUALITY & SLA AUDIT
        # ==========================================
        ws5 = wb.create_sheet(title="Data Quality & Audit")
        ws5.views.sheetView[0].showGridLines = True

        ws5["A1"] = f"Institutional Quality & Feed SLA Audit: {symbol}"
        ws5["A1"].font = font_title
        ws5["A2"] = "Feed reliability scores and quarantined invalid events"
        ws5["A2"].font = Font(name="Segoe UI", size=9, italic=True, color="64748B")

        ws5["A4"] = "Feed Reliability Status"
        ws5["A4"].font = font_sec

        h_headers = [
            "Feed Source",
            "State",
            "Reliability Score",
            "Total Events",
            "Invalid Events",
            "Last Seen",
        ]
        for col_i, h in enumerate(h_headers, 1):
            cell = ws5.cell(row=5, column=col_i, value=h)
            cell.fill = fill_hdr
            cell.font = font_hdr
            cell.alignment = align_center

        h_row = 6
        health = data.get("health", [])
        for h in health:
            score = h.get("score", h.get("reliability_score", 1.0))
            st_str = "HEALTHY" if score >= 0.90 else "DEGRADED"
            ws5.cell(row=h_row, column=1, value=h.get("source")).font = font_bold
            ws5.cell(row=h_row, column=2, value=st_str).alignment = align_center
            ws5.cell(row=h_row, column=3, value=score).number_format = "0.00%"
            ws5.cell(
                row=h_row, column=4, value=h.get("total", h.get("total_events", 0))
            ).number_format = "#,##0"
            ws5.cell(
                row=h_row, column=5, value=h.get("invalid", h.get("invalid_events", 0))
            ).number_format = "#,##0"
            t_up = h.get("updated_at")
            last_seen = (
                datetime.fromtimestamp(t_up).strftime("%H:%M:%S")
                if t_up
                else str(h.get("last_seen", "-"))
            )
            ws5.cell(row=h_row, column=6, value=last_seen).alignment = align_center

            for ci in range(1, 7):
                ws5.cell(row=h_row, column=ci).border = border_thin
                ws5.cell(row=h_row, column=ci).font = font_cell
            h_row += 1

        # Quarantine Audit Sample
        quarantine = data.get("quarantine", [])
        if quarantine:
            h_row += 2
            ws5.cell(
                row=h_row, column=1, value="Quarantine Audit Sample (Invalid Events)"
            ).font = font_sec
            h_row += 1

            q_headers = [
                "Event ID",
                "Instrument",
                "Source",
                "Status",
                "Violation Reasons",
                "Timestamp",
            ]
            for col_i, h in enumerate(q_headers, 1):
                cell = ws5.cell(row=h_row, column=col_i, value=h)
                cell.fill = fill_subhdr
                cell.font = font_hdr
                cell.alignment = align_center

            h_row += 1
            for q in quarantine[:30]:
                ws5.cell(
                    row=h_row, column=1, value=str(q.get("event_id", ""))
                ).font = font_cell
                ws5.cell(
                    row=h_row, column=2, value=str(q.get("instrument_id", ""))
                ).alignment = align_center
                ws5.cell(
                    row=h_row, column=3, value=str(q.get("source", ""))
                ).font = font_bold
                st_cell = ws5.cell(
                    row=h_row, column=4, value=str(q.get("quality_status", ""))
                )
                st_cell.alignment = align_center
                st_cell.font = font_ask
                ws5.cell(
                    row=h_row, column=5, value=str(q.get("reasons", ""))
                ).alignment = align_left
                t_rec = q.get("receive_timestamp")
                rec_str = (
                    datetime.fromtimestamp(t_rec).strftime("%H:%M:%S") if t_rec else "-"
                )
                ws5.cell(row=h_row, column=6, value=rec_str).alignment = align_center

                for ci in range(1, 7):
                    ws5.cell(row=h_row, column=ci).border = border_thin
                    ws5.cell(row=h_row, column=ci).font = font_cell
                h_row += 1

        # Auto-fit column widths across all sheets
        for sheet in wb.worksheets:
            for col in sheet.columns:
                max_len = 0
                for cell in col:
                    v = str(cell.value or "")
                    if len(v) > max_len:
                        max_len = len(v)
                col_letter = get_column_letter(col[0].column)
                sheet.column_dimensions[col_letter].width = max(max_len + 3, 12)

        wb.save(output_path)

        if auto_open and sys.platform == "win32":
            try:
                os.startfile(output_path)
            except Exception:
                pass

        return output_path

    def export_csv(
        self,
        symbol: str = "AAPL",
        output_dir: Optional[str] = None,
    ) -> List[str]:
        """
        Generate standalone CSV files for quantitative backtesting.
        """
        data = self.gather_report_data(symbol)
        clean_sym = symbol.replace("/", "_").replace("-", "_")
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")

        if output_dir is None:
            output_dir = os.path.abspath(
                f"data/reports/csv_{clean_sym}_{timestamp_str}"
            )
        else:
            output_dir = os.path.abspath(output_dir)

        os.makedirs(output_dir, exist_ok=True)
        generated_files = []

        # 1. Overview CSV
        overview_path = os.path.join(output_dir, "overview.csv")
        bbo = data.get("bbo")
        ladder = data.get("ladder")
        with open(overview_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["metric", "value"])
            w.writerow(["symbol", symbol])
            w.writerow(["timestamp", data["timestamp"]])
            w.writerow(["best_bid", bbo.best_bid if bbo else 0.0])
            w.writerow(["best_ask", bbo.best_ask if bbo else 0.0])
            w.writerow(["spread", bbo.spread if bbo else 0.0])
            w.writerow(["micro_price", ladder.micro_price if ladder else 0.0])
            w.writerow(["ofi", ladder.imbalance_ratio if ladder else 0.0])
            w.writerow(["total_canonical", data["total_canonical"]])
            w.writerow(["total_quarantine", data["total_quarantine"]])
        generated_files.append(overview_path)

        # 2. VWAP Slices CSV
        vwap_path = os.path.join(output_dir, "vwap_slippage.csv")
        curve = data.get("vwap_curve")
        with open(vwap_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "side",
                    "target_size",
                    "filled_size",
                    "vwap_price",
                    "slippage_dollars",
                    "slippage_bps",
                    "effective_spread_bps",
                    "fill_pct",
                    "venue_breakdown",
                ]
            )
            if curve:
                for s in curve.buy_slices:
                    fill_pct = (
                        (s.filled_size / s.target_size * 100.0)
                        if s.target_size
                        else 0.0
                    )
                    v_str = ";".join(f"{k}={v}" for k, v in s.venue_breakdown.items())
                    w.writerow(
                        [
                            "BUY",
                            s.target_size,
                            s.filled_size,
                            s.vwap_price,
                            s.slippage_dollars,
                            s.slippage_bps,
                            s.effective_spread_bps,
                            round(fill_pct, 2),
                            v_str,
                        ]
                    )
                for s in curve.sell_slices:
                    fill_pct = (
                        (s.filled_size / s.target_size * 100.0)
                        if s.target_size
                        else 0.0
                    )
                    v_str = ";".join(f"{k}={v}" for k, v in s.venue_breakdown.items())
                    w.writerow(
                        [
                            "SELL",
                            s.target_size,
                            s.filled_size,
                            s.vwap_price,
                            s.slippage_dollars,
                            s.slippage_bps,
                            s.effective_spread_bps,
                            round(fill_pct, 2),
                            v_str,
                        ]
                    )
        generated_files.append(vwap_path)

        # 3. L2 Depth Ladder CSV
        depth_path = os.path.join(output_dir, "l2_depth.csv")
        with open(depth_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["side", "level", "price", "size", "cum_size", "venues"])
            if ladder:
                for idx, b in enumerate(ladder.aggregated_bids):
                    w.writerow(
                        [
                            "BID",
                            idx + 1,
                            b.price,
                            b.total_size,
                            b.cumulative_size,
                            ";".join(b.venue_sizes.keys()),
                        ]
                    )
                for idx, a in enumerate(ladder.aggregated_asks):
                    w.writerow(
                        [
                            "ASK",
                            idx + 1,
                            a.price,
                            a.total_size,
                            a.cumulative_size,
                            ";".join(a.venue_sizes.keys()),
                        ]
                    )
        generated_files.append(depth_path)

        # 4. OHLCV Candles CSV
        candles_path = os.path.join(output_dir, "ohlcv_candles.csv")
        with open(candles_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "bucket_start",
                    "interval_s",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "event_count",
                ]
            )
            for c in data.get("candles", []):
                w.writerow(
                    [
                        c.get("bucket_start"),
                        c.get("interval_s"),
                        c.get("open"),
                        c.get("high"),
                        c.get("low"),
                        c.get("close"),
                        c.get("volume"),
                        c.get("event_count"),
                    ]
                )
        generated_files.append(candles_path)

        # 5. Feed Health CSV
        health_path = os.path.join(output_dir, "feed_health.csv")
        with open(health_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "source",
                    "reliability_score",
                    "total_events",
                    "invalid_events",
                    "last_seen",
                ]
            )
            for h in data.get("health", []):
                t_up = h.get("updated_at")
                last_seen = (
                    datetime.fromtimestamp(t_up).strftime("%H:%M:%S")
                    if t_up
                    else str(h.get("last_seen", "-"))
                )
                w.writerow(
                    [
                        h.get("source"),
                        h.get("score", h.get("reliability_score", 1.0)),
                        h.get("total", h.get("total_events", 0)),
                        h.get("invalid", h.get("invalid_events", 0)),
                        last_seen,
                    ]
                )
        generated_files.append(health_path)

        # 6. Quarantine CSV
        quarantine_path = os.path.join(output_dir, "quarantine_sample.csv")
        with open(quarantine_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "event_id",
                    "instrument_id",
                    "source",
                    "quality_status",
                    "reasons",
                    "receive_timestamp",
                ]
            )
            for q in data.get("quarantine", []):
                w.writerow(
                    [
                        q.get("event_id"),
                        q.get("instrument_id"),
                        q.get("source"),
                        q.get("quality_status"),
                        q.get("reasons"),
                        q.get("receive_timestamp"),
                    ]
                )
        generated_files.append(quarantine_path)

        return generated_files

    def export_tca_workbook(
        self,
        tca_report: dict,
        symbol: str = "AAPL",
        output_path: Optional[str] = None,
        auto_open: bool = False,
    ) -> str:
        """
        Generate an audit-grade Best Execution & TCA Microsoft Excel (.xlsx) report.
        """
        try:
            import openpyxl
            from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
            from openpyxl.utils import get_column_letter
        except ImportError:
            return ""

        clean_sym = symbol.replace("/", "_").replace("-", "_")
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")

        if output_path is None:
            output_path = os.path.abspath(
                f"data/reports/MDRAP_TCA_{clean_sym}_{timestamp_str}.xlsx"
            )
        else:
            output_path = os.path.abspath(output_path)

        wb = openpyxl.Workbook()
        wb.remove(wb.active)

        font_title = Font(name="Segoe UI", size=14, bold=True, color="1E293B")
        font_hdr = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
        font_cell = Font(name="Segoe UI", size=10, color="1E293B")
        font_bold = Font(name="Segoe UI", size=10, bold=True, color="1E293B")
        font_improved = Font(name="Segoe UI", size=10, color="065F46", bold=True)
        font_disimproved = Font(name="Segoe UI", size=10, color="991B1B", bold=True)

        fill_hdr = PatternFill(
            start_color="1E293B", end_color="1E293B", fill_type="solid"
        )
        fill_improved = PatternFill(
            start_color="ECFDF5", end_color="ECFDF5", fill_type="solid"
        )
        fill_disimproved = PatternFill(
            start_color="FEF2F2", end_color="FEF2F2", fill_type="solid"
        )
        border_thin = Border(
            left=Side(style="thin", color="E2E8F0"),
            right=Side(style="thin", color="E2E8F0"),
            top=Side(style="thin", color="E2E8F0"),
            bottom=Side(style="thin", color="E2E8F0"),
        )
        align_center = Alignment(horizontal="center", vertical="center")

        # TAB 1: EXECUTIVE SUMMARY & REGULATORY COMPLIANCE
        ws1 = wb.create_sheet(title="Executive Summary")
        ws1.views.sheetView[0].showGridLines = True
        ws1["A1"] = f"MDRAP Best Execution & TCA Audit Report: {symbol}"
        ws1["A1"].font = font_title
        ws1["A2"] = (
            f"Regulatory Compliance Benchmark (SEC Rule 606 & MiFID II RTS 28) | Merkle Proof: {tca_report.get('merkle_root', '')[:24]}..."
        )
        ws1["A2"].font = Font(name="Segoe UI", size=9, italic=True, color="64748B")

        summary_rows = [
            (
                "Execution Quality Score",
                f"{tca_report.get('overall_quality_score', 0):.1f} / 100",
                "Composite execution benchmark vs true consolidated NBBO",
            ),
            (
                "Regulatory Compliance Verdict",
                tca_report.get("compliance_status", "COMPLIANT"),
                "SEC Rule 605/606 & MiFID II RTS 27/28 Best Execution standard",
            ),
            (
                "Cryptographic Merkle Root",
                tca_report.get("merkle_root", ""),
                "Immutable SHA-256 tamper-evident verification hash",
            ),
            (
                "Total Orders Evaluated",
                f"{tca_report.get('total_trades', 0):,}",
                "Executed fills matched to contemporary microsecond quotes",
            ),
            (
                "Total Executed Shares",
                f"{tca_report.get('total_shares', 0):,}",
                "Cumulative share volume",
            ),
            (
                "Total Traded Notional ($)",
                f"${tca_report.get('total_notional', 0):,.2f}",
                "Gross traded dollar volume",
            ),
            (
                "Mean Slippage vs Arrival",
                f"{tca_report.get('mean_slippage_bps', 0):.2f} bps",
                "Average basis points slipped vs arrival price",
            ),
            (
                "p50 Median Slippage",
                f"{tca_report.get('p50_slippage_bps', 0):.2f} bps",
                "Median execution slippage",
            ),
            (
                "p95 Tail Slippage",
                f"{tca_report.get('p95_slippage_bps', 0):.2f} bps",
                "95th percentile slippage",
            ),
            (
                "Effective Spread",
                f"{tca_report.get('mean_effective_spread_bps', 0):.2f} bps",
                "2 * |Price - Midpoint|",
            ),
            (
                "Quoted NBBO Spread",
                f"{tca_report.get('mean_quoted_spread_bps', 0):.2f} bps",
                "Consolidated prevailing bid-ask spread",
            ),
            (
                "Price Improvement Rate",
                f"{tca_report.get('price_improvement_rate_pct', 0):.1f}%",
                f"{tca_report.get('price_improvement_count', 0)} orders filled inside the spread",
            ),
            (
                "Total Price Improvement ($)",
                f"${tca_report.get('total_price_improvement_usd', 0):,.2f}",
                "Total money saved vs prevailing quote",
            ),
            (
                "Total Slippage Cost ($)",
                f"${tca_report.get('total_slippage_cost_usd', 0):,.2f}",
                "Total execution drag",
            ),
        ]

        ws1.cell(row=4, column=1, value="Metric").fill = fill_hdr
        ws1.cell(row=4, column=1).font = font_hdr
        ws1.cell(row=4, column=2, value="Result").fill = fill_hdr
        ws1.cell(row=4, column=2).font = font_hdr
        ws1.cell(row=4, column=3, value="Description / Audit Rule").fill = fill_hdr
        ws1.cell(row=4, column=3).font = font_hdr

        for r_idx, (m_lbl, m_val, m_desc) in enumerate(summary_rows, start=5):
            c1 = ws1.cell(row=r_idx, column=1, value=m_lbl)
            c2 = ws1.cell(row=r_idx, column=2, value=m_val)
            c3 = ws1.cell(row=r_idx, column=3, value=m_desc)
            c1.font = font_bold
            c2.font = font_cell
            c3.font = font_cell
            c1.border = border_thin
            c2.border = border_thin
            c3.border = border_thin

        # TAB 2: BROKER SCORECARD
        ws2 = wb.create_sheet(title="Broker Scorecard")
        ws2.views.sheetView[0].showGridLines = True
        ws2["A1"] = f"Broker & Venue Execution Quality Scorecard: {symbol}"
        ws2["A1"].font = font_title
        ws2["A2"] = (
            "Comparative routing quality, PFOF markup detection, and price improvement ranking"
        )
        ws2["A2"].font = Font(name="Segoe UI", size=9, italic=True, color="64748B")

        b_headers = [
            "Broker / Execution Desk",
            "Orders",
            "Volume (Shares)",
            "Notional ($)",
            "Avg Slippage (bps)",
            "Avg Eff Spread (bps)",
            "Improvement Rate",
            "Total Improvement ($)",
            "Slippage Cost ($)",
            "Score",
            "Rating",
        ]
        for col_i, h in enumerate(b_headers, 1):
            cell = ws2.cell(row=4, column=col_i, value=h)
            cell.fill = fill_hdr
            cell.font = font_hdr
            cell.alignment = align_center

        for r_idx, sc in enumerate(tca_report.get("broker_scorecards", []), start=5):
            ws2.cell(row=r_idx, column=1, value=sc.get("broker")).font = font_bold
            ws2.cell(
                row=r_idx, column=2, value=sc.get("orders")
            ).number_format = "#,##0"
            ws2.cell(
                row=r_idx, column=3, value=sc.get("shares")
            ).number_format = "#,##0"
            ws2.cell(
                row=r_idx, column=4, value=sc.get("notional")
            ).number_format = "$#,##0.00"
            ws2.cell(
                row=r_idx, column=5, value=sc.get("avg_slippage_bps")
            ).number_format = '0.00" bps"'
            ws2.cell(
                row=r_idx, column=6, value=sc.get("avg_eff_spread_bps")
            ).number_format = '0.00" bps"'
            ws2.cell(
                row=r_idx, column=7, value=sc.get("improvement_rate_pct", 0) / 100.0
            ).number_format = "0.0%"
            ws2.cell(
                row=r_idx, column=8, value=sc.get("total_improvement_usd")
            ).number_format = "$#,##0.00"
            ws2.cell(
                row=r_idx, column=9, value=sc.get("slippage_cost_usd")
            ).number_format = "$#,##0.00"
            ws2.cell(row=r_idx, column=10, value=sc.get("score")).number_format = "0.0"
            ws2.cell(row=r_idx, column=11, value=sc.get("rating")).font = font_bold

            for ci in range(1, 12):
                ws2.cell(row=r_idx, column=ci).border = border_thin

        # TAB 3: EXECUTION LOG
        ws3 = wb.create_sheet(title="Execution Audit Log")
        ws3.views.sheetView[0].showGridLines = True
        ws3["A1"] = f"Microsecond Execution Fill Audit Log: {symbol}"
        ws3["A1"].font = font_title

        log_headers = [
            "Trade ID",
            "Side",
            "Exec Price",
            "Shares",
            "Arrival Price",
            "NBBO Bid",
            "NBBO Ask",
            "Spread (bps)",
            "Slippage (bps)",
            "Improvement ($)",
            "Broker",
            "Venue",
            "Score",
            "Merkle Leaf Hash",
        ]
        for col_i, h in enumerate(log_headers, 1):
            cell = ws3.cell(row=3, column=col_i, value=h)
            cell.fill = fill_hdr
            cell.font = font_hdr
            cell.alignment = align_center

        for r_idx, m in enumerate(tca_report.get("metrics", [])[:500], start=4):
            ws3.cell(row=r_idx, column=1, value=m.get("trade_id")).font = font_cell
            ws3.cell(row=r_idx, column=2, value=m.get("side")).font = font_bold
            ws3.cell(
                row=r_idx, column=3, value=m.get("price")
            ).number_format = "$#,##0.0000"
            ws3.cell(row=r_idx, column=4, value=m.get("shares")).number_format = "#,##0"
            ws3.cell(
                row=r_idx, column=5, value=m.get("arrival_price")
            ).number_format = "$#,##0.0000"
            ws3.cell(
                row=r_idx, column=6, value=m.get("bid")
            ).number_format = "$#,##0.0000"
            ws3.cell(
                row=r_idx, column=7, value=m.get("ask")
            ).number_format = "$#,##0.0000"
            ws3.cell(
                row=r_idx, column=8, value=m.get("quoted_spread_bps")
            ).number_format = "0.00"
            ws3.cell(
                row=r_idx, column=9, value=m.get("slippage_bps")
            ).number_format = "0.00"

            imp_cell = ws3.cell(
                row=r_idx, column=10, value=m.get("price_improvement_usd")
            )
            imp_cell.number_format = "$#,##0.00"
            if m.get("is_improved"):
                imp_cell.fill = fill_improved
                imp_cell.font = font_improved
            elif m.get("is_disimproved"):
                imp_cell.fill = fill_disimproved
                imp_cell.font = font_disimproved

            ws3.cell(row=r_idx, column=11, value=m.get("broker")).font = font_cell
            ws3.cell(row=r_idx, column=12, value=m.get("venue")).font = font_cell
            ws3.cell(row=r_idx, column=13, value=m.get("score")).number_format = "0.0"
            ws3.cell(
                row=r_idx, column=14, value=m.get("merkle_hash", "")[:16] + "..."
            ).font = font_cell

            for ci in range(1, 15):
                ws3.cell(row=r_idx, column=ci).border = border_thin

        for sheet in wb.worksheets:
            for col in sheet.columns:
                max_len = max(len(str(cell.value or "")) for cell in col)
                col_letter = get_column_letter(col[0].column)
                sheet.column_dimensions[col_letter].width = max(max_len + 3, 12)

        wb.save(output_path)
        if auto_open and sys.platform == "win32":
            os.system(f'start "" "{output_path}"')

        return output_path

    def export_flow_workbook(
        self,
        flow_summary: dict,
        symbol: str = "AAPL",
        output_path: Optional[str] = None,
        auto_open: bool = False,
    ) -> str:
        """
        Generate an Institutional Order Flow & Cumulative Volume Delta (CVD) Excel workbook.
        """
        try:
            import openpyxl
            from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
            from openpyxl.utils import get_column_letter
        except ImportError:
            return ""

        if hasattr(flow_summary, "summary"):
            if hasattr(flow_summary, "symbol"):
                symbol = flow_summary.symbol
            flow_summary = flow_summary.summary()

        clean_sym = symbol.replace("/", "_").replace("-", "_")
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")

        if output_path is None:
            output_path = os.path.abspath(
                f"data/reports/MDRAP_FLOW_{clean_sym}_{timestamp_str}.xlsx"
            )
        else:
            output_path = os.path.abspath(output_path)

        wb = openpyxl.Workbook()
        wb.remove(wb.active)

        font_title = Font(name="Segoe UI", size=14, bold=True, color="1E293B")
        font_hdr = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
        font_cell = Font(name="Segoe UI", size=10, color="1E293B")
        font_bold = Font(name="Segoe UI", size=10, bold=True, color="1E293B")
        font_buy = Font(name="Segoe UI", size=10, color="065F46", bold=True)
        font_sell = Font(name="Segoe UI", size=10, color="991B1B", bold=True)

        fill_hdr = PatternFill(
            start_color="1E293B", end_color="1E293B", fill_type="solid"
        )
        fill_accum = PatternFill(
            start_color="ECFDF5", end_color="ECFDF5", fill_type="solid"
        )
        fill_distrib = PatternFill(
            start_color="FEF2F2", end_color="FEF2F2", fill_type="solid"
        )
        border_thin = Border(
            left=Side(style="thin", color="E2E8F0"),
            right=Side(style="thin", color="E2E8F0"),
            top=Side(style="thin", color="E2E8F0"),
            bottom=Side(style="thin", color="E2E8F0"),
        )
        align_center = Alignment(horizontal="center", vertical="center")

        # TAB 1: ORDER FLOW & CVD DASHBOARD
        ws1 = wb.create_sheet(title="Order Flow Overview")
        ws1.views.sheetView[0].showGridLines = True
        ws1["A1"] = f"MDRAP Institutional Order Flow & CVD: {symbol}"
        ws1["A1"].font = font_title
        ws1["A2"] = (
            f"Lee-Ready (1991) Aggressor Classification | Stance: {flow_summary.get('institutional_bias', '')}"
        )
        ws1["A2"].font = Font(name="Segoe UI", size=9, italic=True, color="64748B")

        summary_rows = [
            (
                "Institutional Flow Bias",
                flow_summary.get("institutional_bias", "BALANCED"),
            ),
            (
                "Cumulative Volume Delta (CVD)",
                f"{flow_summary.get('cvd', 0):+,.2f} shares",
            ),
            ("Cumulative Notional Delta (CND)", f"${flow_summary.get('cnd', 0):+,.2f}"),
            (
                "Aggressor Ratio (% Buyer Initiated)",
                f"{flow_summary.get('aggressor_ratio_pct', 50):.1f}%",
            ),
            ("Total Trade Count", f"{flow_summary.get('total_trades', 0):,}"),
            (
                "Total Traded Volume",
                f"{flow_summary.get('total_volume', 0):,.2f} shares",
            ),
            (
                "Buyer-Initiated Volume (Lifting Ask)",
                f"{flow_summary.get('buy_volume', 0):,.2f} shares",
            ),
            (
                "Seller-Initiated Volume (Hitting Bid)",
                f"{flow_summary.get('sell_volume', 0):,.2f} shares",
            ),
            (
                "Whale / Institutional Block Trades",
                f"{flow_summary.get('whale_trades', 0) + flow_summary.get('block_trades', 0):,}",
            ),
            (
                "Retail Sized Trades (<100 shares)",
                f"{flow_summary.get('retail_trades', 0):,}",
            ),
        ]

        ws1.cell(row=4, column=1, value="Order Flow Metric").fill = fill_hdr
        ws1.cell(row=4, column=1).font = font_hdr
        ws1.cell(row=4, column=2, value="Current Session Value").fill = fill_hdr
        ws1.cell(row=4, column=2).font = font_hdr

        for r_idx, (m_lbl, m_val) in enumerate(summary_rows, start=5):
            c1 = ws1.cell(row=r_idx, column=1, value=m_lbl)
            c2 = ws1.cell(row=r_idx, column=2, value=m_val)
            c1.font = font_bold
            c2.font = font_cell
            c1.border = border_thin
            c2.border = border_thin

        # TAB 2: PARTICIPANT ATTRIBUTION
        ws2 = wb.create_sheet(title="Broker Attribution")
        ws2.views.sheetView[0].showGridLines = True
        ws2["A1"] = f"Market Participant (MPID) Accumulation vs Distribution: {symbol}"
        ws2["A1"].font = font_title

        p_headers = [
            "MPID",
            "Participant Name",
            "Buy Vol",
            "Sell Vol",
            "Net Delta (Shares)",
            "Net Notional ($)",
            "Buy Ratio",
            "Trades",
            "Whales",
            "Stance",
        ]
        for col_i, h in enumerate(p_headers, 1):
            cell = ws2.cell(row=3, column=col_i, value=h)
            cell.fill = fill_hdr
            cell.font = font_hdr
            cell.alignment = align_center

        for r_idx, p in enumerate(flow_summary.get("top_participants", []), start=4):
            ws2.cell(row=r_idx, column=1, value=p.get("mpid")).font = font_bold
            ws2.cell(row=r_idx, column=2, value=p.get("name")).font = font_cell
            ws2.cell(
                row=r_idx, column=3, value=p.get("buy_volume")
            ).number_format = "#,##0"
            ws2.cell(
                row=r_idx, column=4, value=p.get("sell_volume")
            ).number_format = "#,##0"

            delta_cell = ws2.cell(row=r_idx, column=5, value=p.get("net_volume"))
            delta_cell.number_format = "+#,##0;-#,##0;0"
            if p.get("net_volume", 0) > 0:
                delta_cell.font = font_buy
            elif p.get("net_volume", 0) < 0:
                delta_cell.font = font_sell

            ws2.cell(
                row=r_idx, column=6, value=p.get("net_notional")
            ).number_format = "$#,##0.00"
            ws2.cell(
                row=r_idx, column=7, value=p.get("buy_ratio_pct", 50) / 100.0
            ).number_format = "0.0%"
            ws2.cell(row=r_idx, column=8, value=p.get("trades")).number_format = "#,##0"
            ws2.cell(row=r_idx, column=9, value=p.get("whales")).number_format = "#,##0"

            stance_cell = ws2.cell(row=r_idx, column=10, value=p.get("stance"))
            stance_cell.font = font_bold
            if p.get("stance") == "ACCUMULATING":
                stance_cell.fill = fill_accum
                stance_cell.font = font_buy
            elif p.get("stance") == "DISTRIBUTING":
                stance_cell.fill = fill_distrib
                stance_cell.font = font_sell

            for ci in range(1, 11):
                ws2.cell(row=r_idx, column=ci).border = border_thin

        # TAB 3: BLOCK TRADES
        ws3 = wb.create_sheet(title="Whale & Block Log")
        ws3.views.sheetView[0].showGridLines = True
        ws3["A1"] = f"Whale & Institutional Block Trades: {symbol}"
        ws3["A1"].font = font_title

        b_headers = [
            "Trade ID",
            "Side",
            "Price",
            "Size",
            "Notional ($)",
            "Category",
            "Broker MPID",
            "Venue",
        ]
        for col_i, h in enumerate(b_headers, 1):
            cell = ws3.cell(row=3, column=col_i, value=h)
            cell.fill = fill_hdr
            cell.font = font_hdr
            cell.alignment = align_center

        for r_idx, b in enumerate(flow_summary.get("recent_blocks", []), start=4):
            ws3.cell(row=r_idx, column=1, value=b.get("trade_id")).font = font_cell
            ws3.cell(row=r_idx, column=2, value=b.get("side")).font = font_bold
            ws3.cell(
                row=r_idx, column=3, value=b.get("price")
            ).number_format = "$#,##0.0000"
            ws3.cell(row=r_idx, column=4, value=b.get("size")).number_format = "#,##0"
            ws3.cell(
                row=r_idx, column=5, value=b.get("notional")
            ).number_format = "$#,##0.00"
            ws3.cell(row=r_idx, column=6, value=b.get("category")).font = font_bold
            ws3.cell(row=r_idx, column=7, value=b.get("broker")).font = font_cell
            ws3.cell(row=r_idx, column=8, value=b.get("venue")).font = font_cell

            for ci in range(1, 9):
                ws3.cell(row=r_idx, column=ci).border = border_thin

        for sheet in wb.worksheets:
            for col in sheet.columns:
                max_len = max(len(str(cell.value or "")) for cell in col)
                col_letter = get_column_letter(col[0].column)
                sheet.column_dimensions[col_letter].width = max(max_len + 3, 12)

        wb.save(output_path)
        if auto_open and sys.platform == "win32":
            os.system(f'start "" "{output_path}"')

        return output_path
