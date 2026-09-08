"""
Persona 2: The Intermediate Algo Trader
Connects via TCP SDK. Simulates Pandas DataFrame bottlenecks blocking the network.
"""
import sys
import os
import asyncio
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from sdk.client import MDrapClient

async def main():
    print("Intermediate Persona: Connecting to MDRAP TCP Gateway...")
    client = MDrapClient(port=9000)
    
    buffer_queue = asyncio.Queue()
    total_received = 0
    
    def on_event(msg):
        # We NO LONGER block the network loop. We instantly drop the message into a queue.
        buffer_queue.put_nowait(msg)
        
    async def process_dataframes():
        nonlocal total_received
        local_buffer = []
        while True:
            msg = await buffer_queue.get()
            local_buffer.append(msg)
            total_received += 1
            
            if len(local_buffer) >= 2000:
                print(f"[{total_received} total] Background Task constructing DataFrame...")
                t0 = time.perf_counter()
                
                # We use asyncio.to_thread to run the heavy CPU blocking task 
                # completely outside the main asyncio network loop!
                await asyncio.to_thread(time.sleep, 1.5)
                
                t1 = time.perf_counter()
                print(f"   ✅ DataFrame built in {(t1-t0):.2f}s! Network socket remained completely unblocked!")
                local_buffer.clear()

    client.on("event", on_event)
    
    print("Listening for stream... (Watch how the server NEVER drops us now!)")
    
    # Run the SDK network loop AND our background processor concurrently
    try:
        await asyncio.gather(
            client.subscribe(),
            process_dataframes()
        )
    except asyncio.CancelledError:
        pass
    except ConnectionError:
        print("\n❌ Disconnected from Gateway! The server dropped us because we read too slowly.")

if __name__ == "__main__":
    asyncio.run(main())
