"""Memory leak tests using weakref and garbage collection validation.

These tests verify that objects are actually garbage collected after cleanup,
not just removed from dictionaries. This catches hidden reference leaks.
"""

import asyncio
import gc
import inspect
import sys
import os
import weakref
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypha_rpc.rpc import RPC
from hypha_rpc.utils import MessageEmitter
from hypha_rpc import connect_to_server
from . import WS_SERVER_URL


class DummyConnection(MessageEmitter):
    """Minimal connection stub for testing."""

    def __init__(self):
        super().__init__(None)
        self.manager_id = None
        self._on_message_handler = None
        self._connected = False

    def on_message(self, handler):
        self._on_message_handler = handler

    def on_connected(self, handler):
        self._on_connected_handler = handler
        # Note: Don't call handler here - it will be called by open()

    def on_disconnected(self, handler):
        self._on_disconnected_handler = handler

    async def emit_message(self, data):
        pass

    async def open(self):
        self._connected = True
        if hasattr(self, '_on_connected_handler') and self._on_connected_handler:
            # Properly await the async handler to avoid creating orphaned coroutines
            await self._on_connected_handler({"manager_id": None})

    async def disconnect(self, reason=None):
        self._connected = False
        if hasattr(self, '_on_disconnected_handler') and self._on_disconnected_handler:
            # Properly handle async/sync handlers
            if inspect.iscoroutinefunction(self._on_disconnected_handler):
                await self._on_disconnected_handler()
            else:
                self._on_disconnected_handler()


def create_rpc():
    """Create an RPC instance for testing.

    Returns:
        tuple: (rpc, connection) - Both must be cleaned up by caller
    """
    conn = DummyConnection()
    rpc = RPC(
        conn,
        client_id="test-client",
        name="test",
        workspace="test-workspace",
        server_base_url="http://localhost",
    )
    return rpc, conn


def assert_collected(weak_refs, timeout=2.0):
    """Assert that all weak references have been garbage collected.

    Args:
        weak_refs: Single weakref or list of weakrefs
        timeout: Maximum seconds to wait for collection

    Raises:
        AssertionError: If any object is still alive after GC
    """
    if not isinstance(weak_refs, list):
        weak_refs = [weak_refs]

    # Try garbage collection multiple times with delays
    for attempt in range(10):
        gc.collect()
        gc.collect()  # Double collect to handle cycles

        # Check if all refs are dead WITHOUT creating strong references
        # Using all() with generator avoids holding refs in a list
        all_dead = all(ref() is None for ref in weak_refs)
        if all_dead:
            return  # Success!

        if attempt < 9:
            # Wait a bit before retrying (objects may have finalizers)
            import time
            time.sleep(timeout / 10)

    # Failed - some objects still alive
    # Only NOW create strong refs for error message
    alive_objs = [ref() for ref in weak_refs if ref() is not None]
    raise AssertionError(
        f"Memory leak detected: {len(alive_objs)} object(s) not garbage collected. "
        f"Types: {[type(obj).__name__ for obj in alive_objs]}"
    )


# ========================================================================
# Test RPC Instance Cleanup
# ========================================================================


class TestRPCInstanceGC:
    """Test that RPC instances are garbage collected after cleanup."""

    def test_rpc_collected_after_close(self):
        """RPC instance should be GC'd after close()."""
        rpc, conn = create_rpc()
        weak_rpc = weakref.ref(rpc)

        # Close and delete
        rpc.close()
        del rpc
        del conn  # Also delete connection to avoid holding references

        # Verify collection
        assert_collected(weak_rpc)

    @pytest.mark.asyncio
    async def test_rpc_collected_after_disconnect(self):
        """RPC instance should be GC'd after disconnect()."""
        rpc, conn = create_rpc()
        weak_rpc = weakref.ref(rpc)

        # Disconnect and delete
        await rpc.disconnect()
        del rpc
        del conn  # Also delete connection to avoid holding references

        # Verify collection
        assert_collected(weak_rpc)

    def test_multiple_rpc_instances_collected(self):
        """Multiple RPC instances should all be GC'd."""
        rpcs = [create_rpc() for _ in range(5)]
        weak_refs = [weakref.ref(rpc) for rpc in rpcs]

        # Close all
        for rpc in rpcs:
            rpc.close()
        del rpcs

        # Verify all collected
        assert_collected(weak_refs)

    @pytest.mark.asyncio
    async def test_rpc_with_services_collected(self):
        """RPC with registered services should be GC'd."""
        rpc = create_rpc()
        weak_rpc = weakref.ref(rpc)

        # Register a service
        await rpc.register_service({
            "id": "test-service",
            "name": "Test Service",
            "echo": lambda x: x
        })

        # Disconnect and delete
        await rpc.disconnect()
        del rpc

        # Verify collection
        assert_collected(weak_rpc)


# ========================================================================
# Test Connection Cleanup
# ========================================================================


class TestConnectionGC:
    """Test that connection objects are garbage collected."""

    def test_connection_collected_after_disconnect(self):
        """Connection should be GC'd after disconnect."""
        conn = DummyConnection()
        weak_conn = weakref.ref(conn)

        del conn

        assert_collected(weak_conn)

    @pytest.mark.asyncio
    async def test_connection_with_rpc_collected(self):
        """Both connection and RPC should be GC'd together."""
        conn = DummyConnection()
        rpc = RPC(
            conn,
            client_id="test",
            name="test",
            workspace="test",
            server_base_url="http://localhost"
        )

        weak_conn = weakref.ref(conn)
        weak_rpc = weakref.ref(rpc)

        # Disconnect and delete both
        await rpc.disconnect()
        del rpc
        del conn

        # Both should be collected
        assert_collected([weak_conn, weak_rpc])


# ========================================================================
# Test Session Cleanup
# ========================================================================


class TestSessionGC:
    """Test that sessions don't leak after cleanup."""

    def test_session_objects_collected(self):
        """Session store objects should be GC'd after deletion."""
        rpc = create_rpc()

        # Create sessions with objects
        session_objects = []
        for i in range(10):
            store = rpc._get_session_store(f"sess-{i}", create=True)
            obj = {"data": f"session-{i}"}
            store["obj"] = obj
            session_objects.append(weakref.ref(obj))

        # Delete sessions
        rpc._cleanup_on_disconnect()

        # Force delete the RPC instance
        del rpc

        # Sessions should be collected
        assert_collected(session_objects)

    @pytest.mark.asyncio
    async def test_timeout_sessions_collected(self):
        """Sessions that timeout should be GC'd."""
        rpc = create_rpc()

        # Create session with object
        store = rpc._get_session_store("timeout-sess", create=True)
        session_obj = {"timeout": True}
        store["obj"] = session_obj
        weak_obj = weakref.ref(session_obj)

        # Simulate timeout cleanup
        rpc._remove_from_target_id_index("timeout-sess")
        if "timeout-sess" in rpc._object_store:
            del rpc._object_store["timeout-sess"]

        del session_obj

        # Should be collected
        assert_collected(weak_obj)


# ========================================================================
# Test Callback Cleanup
# ========================================================================


class TestCallbackGC:
    """Test that callbacks don't create reference cycles."""

    def test_callback_collected_after_service_unregister(self):
        """Callbacks should be GC'd after service unregistration."""
        rpc = create_rpc()

        # Create callback with closure
        captured_data = {"value": 42}
        weak_data = weakref.ref(captured_data)

        def callback(x):
            return captured_data["value"] + x

        weak_callback = weakref.ref(callback)

        # Register service with callback
        service_id = rpc._object_id("test-service")
        rpc._object_store[service_id] = {
            "id": "test-service",
            "callback": callback
        }

        # Unregister and delete
        if service_id in rpc._object_store:
            del rpc._object_store[service_id]
        del callback
        del captured_data

        # Both should be collected
        assert_collected([weak_callback, weak_data])

    def test_nested_callbacks_collected(self):
        """Nested callbacks should not create cycles."""
        rpc = create_rpc()

        callbacks = []
        weak_refs = []

        # Create chain of callbacks
        for i in range(5):
            data = {"level": i}
            weak_refs.append(weakref.ref(data))

            def make_callback(d):
                return lambda: d["level"]

            cb = make_callback(data)
            callbacks.append(cb)
            weak_refs.append(weakref.ref(cb))

        # Store in RPC
        rpc._object_store["callbacks"] = callbacks

        # Clean up
        del rpc._object_store["callbacks"]
        del callbacks

        # All should be collected
        assert_collected(weak_refs)


# ========================================================================
# Test Service Cleanup
# ========================================================================


class TestServiceGC:
    """Test that services are properly garbage collected."""

    @pytest.mark.asyncio
    async def test_service_collected_after_unregister(self):
        """Service objects should be GC'd after unregistration."""
        rpc = create_rpc()

        # Create service object
        service_impl = {
            "id": "test-service",
            "echo": lambda x: x,
            "data": {"important": "stuff"}
        }
        weak_service = weakref.ref(service_impl)

        # Register
        service_id = await rpc.register_service(service_impl)

        # Unregister
        await rpc.unregister_service(service_id)

        del service_impl

        # Should be collected
        assert_collected(weak_service)


# ========================================================================
# Test Integration with Real Connections (requires running server)
# ========================================================================


class TestIntegrationGC:
    """Integration tests with real server connections."""

    @pytest.mark.asyncio
    async def test_client_collected_after_disconnect(self, websocket_server):
        """Full client should be GC'd after disconnect."""
        client = await connect_to_server({
            "server_url": WS_SERVER_URL,
            "client_id": "gc-test-client",
        })

        weak_client = weakref.ref(client)
        weak_rpc = weakref.ref(client.rpc)

        # Disconnect
        await client.disconnect()
        del client

        # Should be collected
        assert_collected([weak_client, weak_rpc])

    @pytest.mark.asyncio
    async def test_service_with_callbacks_collected(self, websocket_server):
        """Service with callbacks should not leak after disconnect."""
        client = await connect_to_server({
            "server_url": WS_SERVER_URL,
            "client_id": "callback-gc-test",
        })

        # Create service with callbacks
        callback_data = {"count": 0}
        weak_data = weakref.ref(callback_data)

        def increment():
            callback_data["count"] += 1
            return callback_data["count"]

        weak_callback = weakref.ref(increment)

        await client.register_service({
            "id": "callback-service",
            "increment": increment
        })

        # Disconnect and clean up
        await client.disconnect()
        del client
        del increment
        del callback_data

        # Should be collected
        assert_collected([weak_data, weak_callback])


# ========================================================================
# Test Memory Usage Growth
# ========================================================================


class TestMemoryGrowth:
    """Test that memory doesn't grow with repeated operations."""

    @pytest.mark.asyncio
    async def test_repeated_connections_no_growth(self, websocket_server):
        """Repeated connect/disconnect shouldn't accumulate memory."""
        import tracemalloc

        tracemalloc.start()

        # Baseline
        gc.collect()
        baseline = tracemalloc.take_snapshot()

        # Perform many connect/disconnect cycles
        for i in range(10):
            client = await connect_to_server({
                "server_url": WS_SERVER_URL,
                "client_id": f"mem-growth-test-{i}",
            })
            await client.disconnect()
            del client
            gc.collect()

        # Check memory after cycles
        gc.collect()
        after = tracemalloc.take_snapshot()

        # Compare memory usage
        stats = after.compare_to(baseline, 'lineno')

        # Get total size increase
        total_increase = sum(stat.size_diff for stat in stats)

        tracemalloc.stop()

        # Should be minimal growth (< 100KB for 10 cycles)
        # Some growth is expected due to caching, but not proportional to cycles
        assert total_increase < 100 * 1024, (
            f"Memory grew by {total_increase / 1024:.1f}KB after 10 cycles. "
            f"Possible memory leak!"
        )

    def test_repeated_service_registration_no_growth(self):
        """Repeated service register/unregister shouldn't leak."""
        rpc = create_rpc()

        # Track a service object
        service = {"id": "test", "echo": lambda x: x}
        weak_service = weakref.ref(service)

        # Register and unregister many times
        for i in range(100):
            sid = rpc._object_id(f"service-{i}")
            rpc._object_store[sid] = service
            del rpc._object_store[sid]

        # Original service should still be alive (we hold reference)
        assert weak_service() is not None

        # Delete our reference
        del service

        # Now should be collected
        assert_collected(weak_service)


# ========================================================================
# Utilities
# ========================================================================


def test_assert_collected_helper():
    """Test the assert_collected helper itself."""
    obj = {"test": "data"}
    weak = weakref.ref(obj)

    # Should still be alive
    assert weak() is not None

    # Delete and verify collection works
    del obj
    assert_collected(weak)

    # Should be dead now
    assert weak() is None


def test_assert_collected_fails_on_leak():
    """Test that assert_collected raises on actual leaks."""
    # Create a global reference (won't be collected)
    global _leaked_obj
    _leaked_obj = {"leaked": True}
    weak = weakref.ref(_leaked_obj)

    # Should raise AssertionError
    with pytest.raises(AssertionError, match="Memory leak detected"):
        assert_collected(weak)

    # Clean up
    del _leaked_obj
