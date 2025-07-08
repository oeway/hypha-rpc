#!/usr/bin/env python3
"""Test that context injection works after reverting changes."""

import asyncio
from hypha_rpc import connect_to_server

async def test_revert():
    """Test that context injection works with old server after revert."""
    
    print("Testing compatibility after reverting changes...")
    
    try:
        # Connect to the old server
        server = await connect_to_server({
            "server_url": "https://hypha.aicell.io",
            "timeout": 30
        })
        print("✓ Successfully connected to old server")
        
        # Register a simple service that requires context
        await server.register_service({
            "id": "test-revert",
            "config": {"require_context": True, "visibility": "public"},
            "test": lambda x, context=None: {
                "input": x,
                "has_context": context is not None,
                "workspace": context.get("ws") if context else None
            }
        })
        print("✓ Registered service with require_context")
        
        # Get the service and test it
        svc = await server.get_service("test-revert")
        result = await svc.test("hello")
        
        print(f"Result: {result}")
        
        if result["has_context"]:
            print("✅ SUCCESS: Context injection works after revert!")
            print(f"Workspace: {result['workspace']}")
        else:
            print("❌ FAILED: Context injection not working")
            
        await server.disconnect()
        print("✓ Disconnected")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_revert())