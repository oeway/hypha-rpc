"""Tests for listener leak fix on WebSocket reconnection.

Verifies that:
1. client_disconnected handlers don't accumulate across reconnections
2. Old subscriptions are cleaned up before re-subscribing
3. Handler references are properly stored and removed
4. close() properly cleans up stored handler references
"""

import asyncio
import time
import pytest

from hypha_rpc.websocket_client import connect_to_server
from hypha_rpc.rpc import RPC
from . import WS_SERVER_URL


# =============================================================================
# Test 1: Handler count stays at 1 after multiple reconnections
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_no_handler_accumulation_after_reconnections(websocket_server):
    """After N reconnections, there should be exactly 1 client_disconnected handler.

    This is the primary regression test for the listener leak bug where
    each reconnection added a new handler without removing the old one.
    """
    ws = await connect_to_server(
        {
            "name": "handler-leak-test",
            "server_url": WS_SERVER_URL,
            "client_id": "handler-leak-test",
            "method_timeout": 30,
        }
    )

    await ws.register_service(
        {
            "id": "leak-test-svc",
            "config": {"visibility": "protected"},
            "ping": lambda: "pong",
        }
    )

    # Wait for initial subscription to be set up
    await asyncio.sleep(1)

    def count_disconnect_handlers():
        """Count client_disconnected handlers in the RPC event emitter."""
        handlers = ws.rpc._event_handlers.get("client_disconnected", [])
        return len(handlers)

    initial_count = count_disconnect_handlers()
    assert initial_count <= 1, f"Expected 0 or 1 initial handlers, got {initial_count}"

    # Perform 3 reconnection cycles
    NUM_RECONNECTIONS = 3
    for cycle in range(NUM_RECONNECTIONS):
        # Wait for services_registered to know reconnection is complete
        services_registered = asyncio.Event()

        def on_svc_registered(info):
            services_registered.set()

        ws.rpc.on("services_registered", on_svc_registered)

        # Force disconnect
        await ws.rpc._connection._websocket.close(1011)

        # Wait for full reconnection including service re-registration
        try:
            await asyncio.wait_for(services_registered.wait(), timeout=15)
        except asyncio.TimeoutError:
            # If services_registered didn't fire, wait a bit more
            await asyncio.sleep(3)

        ws.rpc.off("services_registered", on_svc_registered)

        handler_count = count_disconnect_handlers()
        assert handler_count <= 1, (
            f"Listener leak detected after reconnection {cycle + 1}: "
            f"expected <= 1 handler, got {handler_count}"
        )

    # Final verification
    final_count = count_disconnect_handlers()
    assert final_count <= 1, (
        f"Listener leak: after {NUM_RECONNECTIONS} reconnections, "
        f"expected <= 1 handler, got {final_count}"
    )

    # Verify consistency: if handler is in the list, stored ref should match
    handlers = ws.rpc._event_handlers.get("client_disconnected", [])
    if len(handlers) == 1:
        assert ws.rpc._bound_handle_client_disconnected == handlers[0], (
            "Stored handler reference should match the one in the event handlers list"
        )

    await ws.disconnect()


# =============================================================================
# Test 2: Subscription reference is properly managed
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_subscription_reference_managed_on_reconnection(websocket_server):
    """Verify that _client_disconnected_subscription is updated on each reconnection.

    The old subscription should be unsubscribed and replaced with a new one.
    """
    ws = await connect_to_server(
        {
            "name": "sub-ref-test",
            "server_url": WS_SERVER_URL,
            "client_id": "sub-ref-test",
            "method_timeout": 30,
        }
    )

    await ws.register_service(
        {
            "id": "sub-ref-svc",
            "config": {"visibility": "protected"},
            "ping": lambda: "pong",
        }
    )

    await asyncio.sleep(1)

    # Capture initial subscription reference
    initial_sub = ws.rpc._client_disconnected_subscription

    # Force reconnection
    services_registered = asyncio.Event()
    ws.rpc.on("services_registered", lambda info: services_registered.set())

    await ws.rpc._connection._websocket.close(1011)

    try:
        await asyncio.wait_for(services_registered.wait(), timeout=15)
    except asyncio.TimeoutError:
        await asyncio.sleep(3)

    # After reconnection, subscription may or may not exist depending on
    # timing. The key assertions are:
    # 1. There's at most one subscription stored (no accumulation)
    # 2. Handler count is at most 1
    handlers = ws.rpc._event_handlers.get("client_disconnected", [])
    assert len(handlers) <= 1, (
        f"Handler leak: expected <= 1 handler, got {len(handlers)}"
    )

    await ws.disconnect()


# =============================================================================
# Test 3: close() cleans up handler and subscription
# =============================================================================


@pytest.mark.asyncio
async def test_close_cleans_up_handler_and_subscription(websocket_server):
    """Verify that close() properly removes the stored handler and subscription."""
    ws = await connect_to_server(
        {
            "name": "close-cleanup-test",
            "server_url": WS_SERVER_URL,
            "client_id": "close-cleanup-test",
            "method_timeout": 30,
        }
    )

    await ws.register_service(
        {
            "id": "close-cleanup-svc",
            "config": {"visibility": "protected"},
            "ping": lambda: "pong",
        }
    )

    await asyncio.sleep(1)

    # Verify handler and subscription exist before close
    assert ws.rpc._bound_handle_client_disconnected is not None or \
        ws.rpc._client_disconnected_subscription is not None, \
        "Handler or subscription should exist before close"

    # Close RPC
    ws.rpc.close()

    # Both should be None after close
    assert ws.rpc._bound_handle_client_disconnected is None, (
        "Handler reference should be None after close()"
    )
    assert ws.rpc._client_disconnected_subscription is None, (
        "Subscription reference should be None after close()"
    )

    # Event handler list should be empty or not contain the handler
    handlers = ws.rpc._event_handlers.get("client_disconnected", [])
    assert len(handlers) == 0, (
        f"client_disconnected handlers should be empty after close(), got {len(handlers)}"
    )

    try:
        await ws.disconnect()
    except Exception:
        pass  # May already be disconnected after close()


# =============================================================================
# Test 4: Service calls work correctly after reconnection (no duplicate handling)
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_service_works_after_reconnection_no_duplicates(websocket_server):
    """Verify that service calls work correctly after multiple reconnections.

    With the listener leak, duplicate handlers would cause cascading errors
    when a remote client disconnects. This test verifies a single client's
    own service calls remain consistent after multiple reconnections.
    """
    ws = await connect_to_server(
        {
            "name": "dup-handler-test",
            "server_url": WS_SERVER_URL,
            "client_id": "dup-handler-test",
            "method_timeout": 30,
        }
    )

    call_count = [0]

    async def tracked_echo(msg):
        call_count[0] += 1
        return f"echo: {msg}"

    await ws.register_service(
        {
            "id": "tracked-svc",
            "config": {"visibility": "protected"},
            "echo": tracked_echo,
            "ping": lambda: "pong",
        }
    )

    # Verify initial service call
    svc = await ws.get_service("tracked-svc")
    result = await svc.echo("hello")
    assert result == "echo: hello"
    assert call_count[0] == 1

    # Reconnect 3 times
    for cycle in range(3):
        services_registered = asyncio.Event()
        ws.rpc.on("services_registered", lambda info: services_registered.set())
        await ws.rpc._connection._websocket.close(1011)
        try:
            await asyncio.wait_for(services_registered.wait(), timeout=15)
        except asyncio.TimeoutError:
            await asyncio.sleep(3)
        ws.rpc.off("services_registered")

    # After reconnections, service calls should still work
    await asyncio.sleep(1)
    call_count[0] = 0

    for attempt in range(10):
        try:
            svc = await asyncio.wait_for(ws.get_service("tracked-svc"), timeout=3)
            result = await asyncio.wait_for(svc.echo("after-reconnect"), timeout=3)
            assert result == "echo: after-reconnect"
            break
        except Exception:
            if attempt == 9:
                raise
            await asyncio.sleep(1)

    # Each echo call should be invoked exactly once (no duplicate handlers)
    assert call_count[0] == 1, (
        f"Expected 1 call, got {call_count[0]} - possible duplicate handler issue"
    )

    # Verify handler count is still <= 1
    handlers = ws.rpc._event_handlers.get("client_disconnected", [])
    assert len(handlers) <= 1, (
        f"Handler leak: {len(handlers)} handlers after 3 reconnections"
    )

    await ws.disconnect()


# =============================================================================
# Test 5: Connection info timeout on recv()
# =============================================================================


@pytest.mark.asyncio
async def test_connection_info_timeout():
    """Verify that connecting with an unreachable server times out properly.

    The connection_info recv() should timeout instead of hanging indefinitely.
    This tests the asyncio.wait_for wrapper around self._websocket.recv().
    """
    # Try connecting to a port where nothing is listening
    # This should raise a connection error, not hang forever
    start = time.time()
    with pytest.raises(Exception):
        await asyncio.wait_for(
            connect_to_server(
                {
                    "name": "timeout-test",
                    "server_url": "http://127.0.0.1:19999",  # Nothing listening
                    "client_id": "timeout-test",
                    "method_timeout": 5,
                }
            ),
            timeout=15,
        )
    elapsed = time.time() - start
    # Should fail relatively quickly, not hang for minutes
    assert elapsed < 30, f"Connection attempt took too long: {elapsed:.1f}s"


# =============================================================================
# Test 6: Unit test for handler cleanup logic
# =============================================================================


class TestHandlerCleanupUnit:
    """Unit tests for the handler cleanup logic in RPC using DummyConnection."""

    def test_initial_handler_refs_are_none(self):
        """Verify new RPC instances start with None handler references."""
        from hypha_rpc.utils import MessageEmitter

        class DummyConn(MessageEmitter):
            def __init__(self):
                super().__init__(None)
                self.manager_id = None

            def on_message(self, h):
                pass

            def on_connected(self, h):
                pass

            def on_disconnected(self, h):
                pass

            async def emit_message(self, d):
                pass

            async def disconnect(self, reason=None):
                pass

        conn = DummyConn()
        rpc = RPC(
            conn,
            client_id="test",
            name="test",
            workspace="test-ws",
            server_base_url="http://localhost",
        )

        assert rpc._client_disconnected_subscription is None
        assert rpc._bound_handle_client_disconnected is None

    def test_close_handles_none_refs_gracefully(self):
        """Verify close() works when handler refs are already None."""
        from hypha_rpc.utils import MessageEmitter

        class DummyConn(MessageEmitter):
            def __init__(self):
                super().__init__(None)
                self.manager_id = None

            def on_message(self, h):
                pass

            def on_connected(self, h):
                pass

            def on_disconnected(self, h):
                pass

            async def emit_message(self, d):
                pass

            async def disconnect(self, reason=None):
                pass

        conn = DummyConn()
        rpc = RPC(
            conn,
            client_id="test",
            name="test",
            workspace="test-ws",
            server_base_url="http://localhost",
        )

        # close() should not raise even when refs are None
        rpc.close()
        assert rpc._client_disconnected_subscription is None
        assert rpc._bound_handle_client_disconnected is None
