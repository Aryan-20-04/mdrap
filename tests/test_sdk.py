"""
Tests for MDRAP Quant-Ready Python SDK and TCP Gateway.
"""
import os
import sys
import asyncio
import threading
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from models import CanonicalEvent, EventType, QualityStatus
from gateway_tcp import TCPGatewayServer
from client import MDrapClient

def test_gateway_connection_and_broadcast():
    async def _run():
        server = TCPGatewayServer(port=9099)
        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.1)  # allow bind
        
        client = MDrapClient(port=9099)
        connected = await client.connect()
        assert connected is True
        
        events_received = []
        def on_event(msg):
            events_received.append(msg)
            
        client.on("event", on_event)
            
        # Start read loop
        task = asyncio.create_task(client._read_loop())
        
        # Broadcast an event
        evt = CanonicalEvent(
            event_id="test-1",
            instrument_id="AAPL",
            event_type=EventType.TRADE,
            exchange_timestamp=1000.0,
            receive_timestamp=1000.1,
            processing_timestamp=1000.2,
            source="FEED",
            sequence_number=1,
            raw_id="raw-1",
            price=150.0,
            quantity=100.0
        )
        evt.quality_status = QualityStatus.VALID
        
        payload = {
            "type": "event",
            "instrument": evt.instrument_id,
            "price": evt.price,
            "quality": evt.quality_status.value
        }
        await server.broadcast(payload)
        
        # Give it a moment to propagate
        await asyncio.sleep(0.1)
        
        assert len(events_received) == 1
        assert events_received[0]["instrument"] == "AAPL"
        assert events_received[0]["price"] == 150.0
        assert events_received[0]["quality"] == "VALID"
        
        await client.close()
        task.cancel()
        
        await server.stop()
        server_task.cancel()

    asyncio.run(_run())

def test_sdk_dataframe_conversion():
    client = MDrapClient()
    events = [
        {"ts": 1000.0, "instrument": "AAPL", "price": 150.0, "size": 100},
        {"ts": 1001.0, "instrument": "AAPL", "price": 151.0, "size": 200},
    ]
    try:
        df = client.to_dataframe(events)
        assert len(df) == 2
        assert "instrument" in df.columns
        assert df.iloc[0]["price"] == 150.0
    except ImportError:
        # Pandas not installed
        pass
