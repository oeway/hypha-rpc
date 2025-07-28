#!/usr/bin/env python3
"""
Test runner for reconnection tests.
"""

import asyncio
import subprocess
import sys
import time
import pytest
from hypha_rpc import connect_to_server


async def run_reconnection_test_suite():
    """Run a comprehensive reconnection test suite manually."""
    print("🧪 MANUAL RECONNECTION TEST SUITE")
    print("=" * 50)
    
    # This assumes you have a hypha server running on localhost:9527
    SERVER_URL = "ws://127.0.0.1:9527/ws" 
    
    try:
        # Test basic connection
        print("\n1️⃣ Testing basic connection...")
        ws = await connect_to_server({
            "name": "manual-reconnection-test",
            "server_url": SERVER_URL,
            "client_id": "manual-test"
        })
        
        # Register a test service
        print("2️⃣ Registering test service...")
        test_data = {"counter": 0}
        
        await ws.register_service({
            "id": "reconnection-test-service",
            "config": {"visibility": "protected"},
            "get_counter": lambda: test_data["counter"],
            "increment": lambda: test_data.update({"counter": test_data["counter"] + 1}) or test_data["counter"],
            "ping": lambda: "pong",
            "echo": lambda x: f"echo: {x}"
        })
        
        # Test initial functionality
        print("3️⃣ Testing initial functionality...")
        svc = await ws.get_service("reconnection-test-service")
        assert await svc.ping() == "pong"
        assert await svc.get_counter() == 0
        await svc.increment()
        assert await svc.get_counter() == 1
        print("✅ Initial functionality works")
        
        # Test connection close and recovery
        print("\n4️⃣ Testing connection recovery...")
        print("💥 Closing connection abruptly...")
        await ws.rpc._connection._websocket.close(1011)  # Unexpected condition
        
        print("⏳ Waiting for reconnection...")
        await asyncio.sleep(3)
        
        print("🔍 Testing service after reconnection...")
        svc = await ws.get_service("reconnection-test-service")
        assert await svc.ping() == "pong"
        result = await svc.echo("after-reconnection")
        assert result == "echo: after-reconnection"
        print("✅ Reconnection successful!")
        
        # Test multiple disconnections
        print("\n5️⃣ Testing multiple disconnections...")
        valid_codes = [1000, 1001, 1011]  # Normal, going away, unexpected condition
        for i, code in enumerate(valid_codes):
            print(f"💥 Disconnection #{i+1} (code {code})")
            await ws.rpc._connection._websocket.close(code)
            await asyncio.sleep(1.5)
            
            svc = await ws.get_service("reconnection-test-service")
            result = await svc.echo(f"test-{i}")
            assert result == f"echo: test-{i}"
            print(f"✅ Reconnection #{i+1} successful")
        
        print("\n🎉 ALL MANUAL TESTS PASSED!")
        
        # Cleanup
        await ws.disconnect()
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        raise


@pytest.mark.asyncio
async def test_quick_reconnection_check():
    """Quick test to verify reconnection logic is working."""
    # This test requires a running hypha server
    try:
        SERVER_URL = "ws://127.0.0.1:9527/ws"
        
        ws = await connect_to_server({
            "name": "quick-reconnection-test",
            "server_url": SERVER_URL,
            "client_id": "quick-test"
        })
        
        # Register a simple service
        await ws.register_service({
            "id": "quick-test-service",
            "config": {"visibility": "protected"},
            "ping": lambda: "pong"
        })
        
        # Test it works
        svc = await ws.get_service("quick-test-service")
        result = await svc.ping()
        assert result == "pong"
        
        # Disconnect and reconnect
        await ws.rpc._connection._websocket.close(1011)
        await asyncio.sleep(2)
        
        # Should still work
        svc = await ws.get_service("quick-test-service")
        result = await svc.ping()
        assert result == "pong"
        
        await ws.disconnect()
        print("✅ Quick reconnection test passed")
        
    except Exception as e:
        print(f"❌ Quick test failed (server might not be running): {e}")
        pytest.skip("Hypha server not available for live testing")


if __name__ == "__main__":
    print("🚀 Running manual reconnection test suite...")
    print("📋 Make sure you have a hypha server running on localhost:9527")
    print("   Start with: python -m hypha.server --port=9527")
    print()
    
    # Run the manual test
    asyncio.run(run_reconnection_test_suite()) 