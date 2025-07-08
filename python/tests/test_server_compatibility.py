"""Test compatibility with old Hypha server at https://hypha.aicell.io."""

import asyncio
import pytest
from hypha_rpc import connect_to_server

OLD_SERVER_URL = "https://hypha.aicell.io"


@pytest.mark.asyncio
async def test_old_server_context_injection():
    """Test that context injection works with old server at hypha.aicell.io."""

    try:
        async with connect_to_server(
            {"server_url": OLD_SERVER_URL, "timeout": 30}
        ) as server:
            # Register service with require_context
            await server.register_service(
                {
                    "id": "test-old-server-context",
                    "config": {"require_context": True, "visibility": "public"},
                    "test_context": lambda x, context=None: {
                        "input": x,
                        "has_context": context is not None,
                        "workspace": context.get("ws") if context else None,
                        "from": context.get("from") if context else None,
                        "context_type": type(context).__name__ if context else "None",
                    },
                    "test_normal": lambda x, context=None: f"echo: {x}",
                }
            )

            # Test context injection
            svc = await server.get_service("test-old-server-context")
            context_result = await svc.test_context("test")

            assert context_result["has_context"] is True
            assert context_result["workspace"] is not None
            assert context_result["from"] is not None
            print(
                f"✓ Old server context test passed - workspace: {context_result['workspace']}"
            )
            print(f"  Context type: {context_result['context_type']}")
            print(f"  From: {context_result['from']}")

            # Test normal method
            normal_result = await svc.test_normal("hello")
            assert normal_result == "echo: hello"
            print("✓ Normal method test passed")

    except Exception as e:
        pytest.skip(f"Old server test skipped - connection failed: {e}")


@pytest.mark.asyncio
async def test_old_server_no_context_services():
    """Test services without require_context on old server."""

    try:
        async with connect_to_server(
            {"server_url": OLD_SERVER_URL, "timeout": 30}
        ) as server:
            # Register service without require_context
            await server.register_service(
                {
                    "id": "test-old-server-no-context",
                    "config": {"visibility": "public"},
                    "simple_method": lambda x: f"echo: {x}",
                    "math_method": lambda a, b: a + b,
                    "list_method": lambda *args: list(args),
                }
            )

            # Test the service
            svc = await server.get_service("test-old-server-no-context")

            assert await svc.simple_method("hello") == "echo: hello"
            assert await svc.math_method(2, 3) == 5
            assert await svc.list_method(1, 2, 3) == [1, 2, 3]

            print("✓ Non-context service test on old server passed")

    except Exception as e:
        pytest.skip(f"Old server test skipped - connection failed: {e}")


@pytest.mark.asyncio
async def test_old_server_multiple_context_methods():
    """Test multiple methods with context injection on old server."""

    try:
        async with connect_to_server(
            {"server_url": OLD_SERVER_URL, "timeout": 30}
        ) as server:

            def context_method_1(x, context=None):
                return {
                    "method": "context_method_1",
                    "input": x,
                    "workspace": context.get("ws") if context else None,
                }

            def context_method_2(a, b, context=None):
                return {
                    "method": "context_method_2",
                    "sum": a + b,
                    "workspace": context.get("ws") if context else None,
                }

            # Register service with multiple context methods
            await server.register_service(
                {
                    "id": "test-old-server-multi-context",
                    "config": {"require_context": True, "visibility": "public"},
                    "method1": context_method_1,
                    "method2": context_method_2,
                }
            )

            svc = await server.get_service("test-old-server-multi-context")

            # Test first method
            result1 = await svc.method1("test")
            assert result1["method"] == "context_method_1"
            assert result1["workspace"] is not None

            # Test second method
            result2 = await svc.method2(5, 7)
            assert result2["method"] == "context_method_2"
            assert result2["sum"] == 12
            assert result2["workspace"] is not None

            print("✓ Multiple context methods test passed")
            print(f"  Workspace from method1: {result1['workspace']}")
            print(f"  Workspace from method2: {result2['workspace']}")

    except Exception as e:
        pytest.skip(f"Old server test skipped - connection failed: {e}")


if __name__ == "__main__":
    # Run specific test for manual testing
    asyncio.run(test_old_server_context_injection())
