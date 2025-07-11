#!/usr/bin/env python3
"""Simple test to verify server connectivity"""

import asyncio
import sys
from pathlib import Path

# Add current package to path
sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

async def test_connection():
    try:
        from hypha_rpc import connect_to_server
        
        print("Attempting to connect to server...")
        client = await connect_to_server({"server_url": "ws://localhost:9527", "client_id": "test_client"})
        print("Successfully connected!")
        
        # Test basic functionality
        def test_service():
            return "Hello from test service"
        
        await client.register_service({
            "id": "test_service",
            "config": {"visibility": "public"},
            "hello": test_service
        })
        
        service = await client.get_service("test_service")
        result = await service.hello()
        print(f"Service call result: {result}")
        
        await client.disconnect()
        print("Test completed successfully!")
        
    except Exception as e:
        print(f"Test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_connection()) 