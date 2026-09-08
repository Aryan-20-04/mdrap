"""
MDRAP Extreme Market Server
Blasts data into the TCP Gateway as fast as Python can process it.
WARNING: This will pin a CPU core to 100%.
"""
import sys
import os
import asyncio
import time
import random

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from gateway_tcp import TCPGatewayServer

async def main():
    server = TCPGatewayServer(host='127.0.0.1', port=9000)
    await server.start()
    
    print("🔥 EXTREME MARKET SERVER STARTED 🔥")
    print("Blasting raw TCP events with NO THROTTLING... (Press Ctrl+C to stop)")
    
    instruments = ["AAPL", "BTC/USD", "TSLA", "NVDA", "SPY"]
    
    try:
        batch_count = 0
        while True:
            for _ in range(5000):
                sym = random.choice(instruments)
                price = random.uniform(100, 500)
                ts = time.time()
                
                payload = {
                    "type": "event",
                    "instrument": sym,
                    "event_type": "TRADE",
                    "quality": "VALID",
                    "price": price,
                    "size": 100,
                    "ts": ts
                }
                await server.broadcast(payload)
                
            batch_count += 1
            # We only yield for 0.001s to prevent the async loop from completely locking up,
            # but this will easily push 10,000+ events per second over the network socket.
            await asyncio.sleep(0.001) 
            
            if batch_count % 10 == 0:
                print(f"Blasted {batch_count * 5000:,} events... Active TCP Clients: {len(server.clients)}")
                
    except KeyboardInterrupt:
        print("\n[Server] Shutdown gracefully.")
    except Exception as e:
        if "CancelledError" not in str(type(e)):
            raise
    finally:
        await server.stop()

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
