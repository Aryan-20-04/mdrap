"""
VWAP Slicing Execution Strategy
Demonstrates executing trades against the live TCP gateway.
"""
import sys
import os
import asyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'src')))

from sdk.execution import LiveStrategyRunner
from strategy_sdk import Strategy
from models import CanonicalEvent

class VWAPSlicingStrategy(Strategy):
    """
    Executes algorithmic 'slices' of a large parent order based on the Volume-Weighted 
    Average Price (VWAP) over a trailing window.
    If the market price drops below our calculated VWAP, we aggressively buy.
    """
    def __init__(self, symbol: str = "TSLA", slice_size: int = 100):
        super().__init__(name="VWAPSlicer", symbols=[symbol])
        self.symbol = symbol
        self.slice_size = slice_size
        
        # Internal VWAP trackers
        self.cumulative_notional = 0.0
        self.cumulative_volume = 0.0
        self.current_vwap = 0.0
        
        # Risk limits
        self.max_inventory = 1000

    def on_tick(self, event: CanonicalEvent) -> None:
        if event.instrument_id != self.symbol or event.price is None or event.quantity is None:
            return
            
        # Update VWAP
        notional = event.price * event.quantity
        self.cumulative_notional += notional
        self.cumulative_volume += event.quantity
        self.current_vwap = self.cumulative_notional / self.cumulative_volume

        # Execution Logic
        pos = self.executor.get_position(self.symbol)
        
        # If live price drops 0.5% below VWAP, we consider it a discount and buy a slice
        if event.price < (self.current_vwap * 0.995):
            if pos.quantity < self.max_inventory:
                print(f"[VWAP SLICER] Buying {self.slice_size} {self.symbol} @ {event.price:.2f} (VWAP: {self.current_vwap:.2f})")
                self.buy(self.symbol, self.slice_size, price=None) # Market order
        
        # If we are holding inventory and price spikes 0.5% above VWAP, sell to capture spread
        elif event.price > (self.current_vwap * 1.005):
            if pos.quantity > 0:
                print(f"[VWAP SLICER] Taking Profit {self.slice_size} {self.symbol} @ {event.price:.2f} (VWAP: {self.current_vwap:.2f})")
                self.sell(self.symbol, self.slice_size, price=None)


if __name__ == "__main__":
    strategy = VWAPSlicingStrategy(symbol="TSLA", slice_size=50)
    runner = LiveStrategyRunner(strategy, port=9000)
    
    print("Press Ctrl+C to stop execution and view PnL report.")
    
    try:
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        pass
