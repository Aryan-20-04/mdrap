"""
MDRAP Hello World Strategy
A minimal example for first-time users. 
It buys 10 shares of AAPL on the very first market tick it sees.
"""
import asyncio
import sys

# Import directly from the installed MDRAP package!
from strategy_sdk import Strategy, LiveStrategyRunner
from models import CanonicalEvent

class HelloWorldStrategy(Strategy):
    def __init__(self):
        super().__init__(name="HelloWorld", symbols=["AAPL"])
        self.has_bought = False

    def on_tick(self, event: CanonicalEvent) -> None:
        if not self.has_bought and event.instrument_id == "AAPL":
            print(f"[*] Hello MDRAP! Executing first trade: BUY 10 AAPL @ ${event.price:.2f}")
            self.buy("AAPL", 10)
            self.has_bought = True

if __name__ == "__main__":
    print("Initializing Hello World Strategy...")
    print("Make sure you are running the market server in another terminal!")
    
    strategy = HelloWorldStrategy()
    runner = LiveStrategyRunner(strategy, port=9000)
    
    try:
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        print("\nExiting. View your PnL above!")
