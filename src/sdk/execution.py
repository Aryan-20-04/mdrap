"""
Live Algorithmic Execution Testing Sandbox
Hooks into MDrapClient to run algorithmic strategies against live market data streams.
"""
import sys
import os
import asyncio
from typing import Dict, Any, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'src')))

from sdk.client import MDrapClient
from strategy_sdk import Strategy, Order, PaperExecutor, OrderStatus
from models import CanonicalEvent, EventType, QualityStatus

class LiveStrategyRunner:
    """
    Orchestrates execution of an algorithmic strategy over the LIVE TCP Gateway stream.
    """
    def __init__(self, strategy: Strategy, host: str = "127.0.0.1", port: int = 9000):
        self.strategy = strategy
        self.client = MDrapClient(host=host, port=port)
        self.events_processed = 0

    async def run(self):
        """Runs the strategy continuously until stopped."""
        self.strategy.on_start()
        
        # Async Queue for processing to avoid network blocking
        self._queue = asyncio.Queue()
        
        def on_event_callback(msg: Dict[str, Any]):
            self._queue.put_nowait(msg)

        self.client.on("event", on_event_callback)
        
        print(f"Starting LiveStrategyRunner for {self.strategy.name} on TCP port {self.client.port}...")
        
        # Run network and strategy concurrently
        try:
            await asyncio.gather(
                self.client.subscribe(),
                self._process_queue()
            )
        except asyncio.CancelledError:
            pass
        except KeyboardInterrupt:
            pass
        finally:
            self.strategy.on_stop()
            await self.client.close()
            self._print_performance()

    async def _process_queue(self):
        """Pulls events from the background queue and feeds them to the strategy."""
        while True:
            msg = await self._queue.get()
            self.events_processed += 1
            
            # Reconstruct CanonicalEvent from the raw dict
            evt = CanonicalEvent(
                event_id="live",
                instrument_id=msg.get("instrument", "UNKNOWN"),
                event_type=EventType[msg.get("event_type", "TRADE")],
                exchange_timestamp=msg.get("ts", 0.0),
                receive_timestamp=msg.get("ts", 0.0),
                processing_timestamp=0.0,
                source="live_gateway",
                sequence_number=None,
                price=msg.get("price"),
                quantity=msg.get("size"),
                quality_status=QualityStatus.VALID,
                bid_price=msg.get("bid"),
                ask_price=msg.get("ask")
            )

            # Feed the strategy
            if evt.event_type == EventType.QUOTE:
                self.strategy.on_quote(evt)
            elif evt.event_type == EventType.TRADE:
                self.strategy.on_tick(evt)
                
            self._queue.task_done()

    def _print_performance(self):
        print(f"\n--- Strategy Execution Summary: {self.strategy.name} ---")
        summary = self.strategy.performance_summary()
        print(f"Events Processed: {self.events_processed:,}")
        print(f"Total Trades:     {summary.get('total_trades')}")
        print(f"Win Rate:         {summary.get('win_rate_pct'):.2f}%")
        print(f"Avg Slippage:     {summary.get('avg_slippage_bps'):.2f} bps")
        print(f"Realized PnL:    ${summary.get('realized_pnl'):,.2f}")
        print(f"Final Equity:    ${summary.get('final_equity'):,.2f}")
        print(f"Return:           {summary.get('return_pct'):.2f}%")
        print("--------------------------------------------------")
