"""
MDRAP High-Frequency Trading (HFT) Benchmark Strategy

This script tests the absolute maximum throughput of the MDRAP SDK Event Loop.
It measures exactly how many events per second (EPS) the LiveStrategyRunner can process.
"""
import asyncio
import time
import sys

from sdk.execution import LiveStrategyRunner
from strategy_sdk import Strategy
from models import CanonicalEvent

class HFTBenchmarkStrategy(Strategy):
    def __init__(self, target_events: int = 1_000_000):
        super().__init__(name="HFT Benchmark", symbols=[])
        self.target_events = target_events
        self.events_processed = 0
        self.start_time = 0.0

    def on_tick(self, event: CanonicalEvent) -> None:
        if self.events_processed == 0:
            print(f"🚀 Receiving first event... Starting benchmark timer!")
            self.start_time = time.perf_counter()
            
        self.events_processed += 1
        
        # Every 100,000 events, print a live speed update
        if self.events_processed % 100_000 == 0:
            elapsed = time.perf_counter() - self.start_time
            eps = self.events_processed / elapsed
            print(f"⚡ Processed {self.events_processed:,} events | Speed: {eps:,.0f} EPS")
            
        # Stop the runner once we hit our target to get the final score
        if self.events_processed >= self.target_events:
            elapsed = time.perf_counter() - self.start_time
            eps = self.events_processed / elapsed
            print("\n" + "="*50)
            print(f"🏁 BENCHMARK COMPLETE!")
            print(f"Total Events: {self.events_processed:,}")
            print(f"Total Time:   {elapsed:.2f} seconds")
            print(f"Throughput:   {eps:,.0f} Events Per Second (EPS)")
            print("="*50)
            sys.exit(0)

if __name__ == "__main__":
    print("Initializing HFT Benchmark (Target: 1,000,000 events)...")
    runner = LiveStrategyRunner(HFTBenchmarkStrategy(target_events=1_000_000), port=9000)
    
    try:
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        pass
