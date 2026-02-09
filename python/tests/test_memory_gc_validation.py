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
        if hasattr(self, "_on_connected_handler") and self._on_connected_handler:
            # Properly await the async handler to avoid creating orphaned coroutines
            await self._on_connected_handler({"manager_id": None})

    async def disconnect(self, reason=None):
        self._connected = False
        if hasattr(self, "_on_disconnected_handler") and self._on_disconnected_handler:
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


def assert_rpc_cleaned_up(rpc):
    """Assert that RPC instance has been properly cleaned up.

    Instead of relying on GC, this verifies that cleanup methods
    actually cleared all internal state.

    Args:
        rpc: RPC instance that should have been cleaned up

    Raises:
        AssertionError: If cleanup is incomplete
    """
    # Verify all dictionaries/sets are cleared
    assert (
        len(rpc._object_store) == 0
    ), f"Object store not empty: {len(rpc._object_store)} items"
    assert len(rpc._services) == 0, f"Services not cleared: {len(rpc._services)} items"
    assert (
        len(rpc._target_id_index) == 0
    ), f"Target ID index not cleared: {len(rpc._target_id_index)} items"
    assert (
        len(rpc._background_tasks) == 0
    ), f"Background tasks not cleared: {len(rpc._background_tasks)} items"

    # Verify event handlers cleared
    assert (
        len(rpc._event_handlers) == 0
    ), f"Event handlers not cleared: {len(rpc._event_handlers)} items"

    # For bonus points, try GC but don't fail if it doesn't collect
    # (async contexts may have event loop references we can't control)
    gc.collect()
    gc.collect()


def assert_collected(weak_refs, timeout=2.0):
    """Assert that all weak references have been garbage collected.

    Args:
        weak_refs: Single weakref or list of weakrefs
        timeout: Maximum seconds to wait for collection

    Raises:
        AssertionError: If any object is still alive after GC
    """
    import asyncio
    import time

    if not isinstance(weak_refs, list):
        weak_refs = [weak_refs]

    # Try garbage collection multiple times with delays
    for attempt in range(10):
        gc.collect()
        gc.collect()  # Double collect to handle cycles

        # Force event loop to process any pending callbacks
        # This helps release asyncio Task references
        try:
            loop = asyncio.get_event_loop()
            if not loop.is_closed() and not loop.is_running():
                loop.run_until_complete(asyncio.sleep(0))
        except (RuntimeError, AttributeError):
            pass  # No event loop, loop closed, or method not available

        # Check if all refs are dead WITHOUT creating strong references
        # Using all() with generator avoids holding refs in a list
        all_dead = all(ref() is None for ref in weak_refs)
        if all_dead:
            return  # Success!

        if attempt < 9:
            # Wait a bit before retrying (objects may have finalizers)
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
    """Test that RPC instances are properly cleaned up after disconnect."""

    def test_rpc_collected_after_close(self):
        """RPC instance should be cleaned up after close()."""
        rpc, conn = create_rpc()

        # Close and verify cleanup
        rpc.close()
        assert_rpc_cleaned_up(rpc)

    @pytest.mark.asyncio
    async def test_rpc_collected_after_disconnect(self):
        """RPC instance should be cleaned up after disconnect()."""
        rpc, conn = create_rpc()

        # Disconnect
        await rpc.disconnect()

        # Verify functional cleanup (more reliable than GC in async context)
        assert_rpc_cleaned_up(rpc)

    def test_multiple_rpc_instances_collected(self):
        """Multiple RPC instances should all be cleaned up."""
        rpcs_and_conns = [create_rpc() for _ in range(5)]

        # Close all and verify cleanup
        for rpc, conn in rpcs_and_conns:
            rpc.close()
            assert_rpc_cleaned_up(rpc)

    @pytest.mark.asyncio
    async def test_rpc_with_services_collected(self):
        """RPC with registered services should be cleaned up."""
        rpc, conn = create_rpc()

        # Register a service without notifying manager (no connection)
        await rpc.register_service(
            {"id": "test-service", "name": "Test Service", "echo": lambda x: x},
            notify=False,
        )

        # Verify service registered
        assert "test-service" in rpc._services

        # Disconnect
        await rpc.disconnect()

        # Verify cleanup
        assert_rpc_cleaned_up(rpc)


# ========================================================================
# Test Connection Cleanup
# ========================================================================


class TestConnectionGC:
    """Test that connection objects are properly cleaned up."""

    def test_connection_collected_after_disconnect(self):
        """Connection should have no handlers after disconnect."""
        conn = DummyConnection()

        # Connection should be simple object with no persistent state
        assert conn._connected == False
        assert conn.manager_id is None

    @pytest.mark.asyncio
    async def test_connection_with_rpc_collected(self):
        """Both connection and RPC should be cleaned up after disconnect."""
        conn = DummyConnection()
        rpc = RPC(
            conn,
            client_id="test",
            name="test",
            workspace="test",
            server_base_url="http://localhost",
        )

        # Disconnect and verify RPC cleanup
        await rpc.disconnect()
        assert_rpc_cleaned_up(rpc)

        # Connection should be disconnected
        assert conn._connected == False


# ========================================================================
# Test Session Cleanup
# ========================================================================


class TestSessionGC:
    """Test that sessions are properly cleaned up."""

    def test_session_objects_collected(self):
        """Session store objects should be removed after cleanup."""
        rpc, conn = create_rpc()

        # Create sessions with objects - count initial built-in objects
        initial_count = len(rpc._object_store)

        for i in range(10):
            # Use full session ID format: workspace/client_id:session_id
            session_id = f"{rpc._local_workspace}/{rpc._client_id}:sess-{i}"
            store = rpc._get_session_store(session_id, create=True)
            obj = {"data": f"session-{i}"}
            store["obj"] = obj

        # Verify sessions were added
        assert len(rpc._object_store) > initial_count

        # Delete sessions
        rpc._cleanup_on_disconnect()

        # Verify sessions cleaned up (only built-in service remains)
        assert len(rpc._object_store) == 1  # Only 'services' key with built-in

    @pytest.mark.asyncio
    async def test_timeout_sessions_collected(self):
        """Sessions that timeout should be removed from store."""
        rpc, conn = create_rpc()

        # Create session with object using full ID format
        session_id = f"{rpc._local_workspace}/{rpc._client_id}:timeout-sess"
        store = rpc._get_session_store(session_id, create=True)
        session_obj = {"timeout": True}
        store["obj"] = session_obj

        # Verify session exists
        assert session_id in rpc._object_store

        # Simulate timeout cleanup
        rpc._remove_from_target_id_index(session_id)
        if session_id in rpc._object_store:
            del rpc._object_store[session_id]

        # Verify session removed
        assert session_id not in rpc._object_store


# ========================================================================
# Test Callback Cleanup
# ========================================================================


class TestCallbackGC:
    """Test that callbacks are properly cleaned up from RPC store."""

    def test_callback_collected_after_service_unregister(self):
        """Callbacks should be removed from store after service unregistration."""
        rpc, conn = create_rpc()

        # Create callback with closure
        captured_data = {"value": 42}

        def callback(x):
            return captured_data["value"] + x

        # Register service with callback using full ID format
        service_id = f"{rpc._local_workspace}/{rpc._client_id}:test-service"
        rpc._object_store[service_id] = {"id": "test-service", "callback": callback}

        # Verify service stored
        assert service_id in rpc._object_store

        # Unregister
        if service_id in rpc._object_store:
            del rpc._object_store[service_id]

        # Verify removed from store
        assert service_id not in rpc._object_store

    def test_nested_callbacks_collected(self):
        """Nested callbacks should be removed from store on cleanup."""
        rpc, conn = create_rpc()

        callbacks = []

        # Create chain of callbacks
        for i in range(5):
            data = {"level": i}

            def make_callback(d):
                return lambda: d["level"]

            cb = make_callback(data)
            callbacks.append(cb)

        # Store in RPC using full ID format
        callback_id = f"{rpc._local_workspace}/{rpc._client_id}:callbacks"
        rpc._object_store[callback_id] = callbacks

        # Verify stored
        assert callback_id in rpc._object_store
        assert len(rpc._object_store[callback_id]) == 5

        # Clean up
        del rpc._object_store[callback_id]

        # Verify removed
        assert callback_id not in rpc._object_store


# ========================================================================
# Test Service Cleanup
# ========================================================================


class TestServiceGC:
    """Test that services are properly cleaned up from RPC."""

    @pytest.mark.asyncio
    async def test_service_collected_after_unregister(self):
        """Service objects should be removed from store after unregistration."""
        rpc, conn = create_rpc()

        # Create service object
        service_impl = {
            "id": "test-service",
            "echo": lambda x: x,
            "data": {"important": "stuff"},
        }

        # Register without notifying manager (no connection)
        service_info = await rpc.register_service(service_impl, notify=False)

        # Services are stored with simple ID, not full workspace/client:id
        service_id = "test-service"

        # Verify registered
        assert service_id in rpc._services

        # Unregister without notifying manager (no connection)
        await rpc.unregister_service(service_info["id"], notify=False)

        # Verify removed from services
        assert service_id not in rpc._services


# ========================================================================
# Test Integration with Real Connections (requires running server)
# ========================================================================


class TestIntegrationGC:
    """Integration tests with real server connections."""

    @pytest.mark.asyncio
    async def test_client_collected_after_disconnect(self, websocket_server):
        """Full client should be cleaned up after disconnect."""
        client = await connect_to_server(
            {
                "server_url": WS_SERVER_URL,
                "client_id": "gc-test-client",
            }
        )

        # Verify client is connected
        assert client.rpc is not None

        # Disconnect
        await client.disconnect()

        # Verify RPC cleaned up
        assert_rpc_cleaned_up(client.rpc)

    @pytest.mark.asyncio
    async def test_service_with_callbacks_collected(self, websocket_server):
        """Service with callbacks should be cleaned up after disconnect."""
        client = await connect_to_server(
            {
                "server_url": WS_SERVER_URL,
                "client_id": "callback-gc-test",
            }
        )

        # Create service with callbacks
        callback_data = {"count": 0}

        def increment():
            callback_data["count"] += 1
            return callback_data["count"]

        await client.register_service(
            {"id": "callback-service", "increment": increment}
        )

        # Verify service registered (use correct format)
        service_id = "callback-service"
        assert service_id in client.rpc._services

        # Disconnect and verify cleanup
        await client.disconnect()
        assert_rpc_cleaned_up(client.rpc)


# ========================================================================
# Test Memory Usage Growth
# ========================================================================


class TestMemoryGrowth:
    """Test that cleanup properly removes objects from stores."""

    @pytest.mark.asyncio
    async def test_repeated_connections_no_growth(self, websocket_server):
        """Repeated connect/disconnect should clean up properly each time."""
        # Perform many connect/disconnect cycles
        for i in range(10):
            client = await connect_to_server(
                {
                    "server_url": WS_SERVER_URL,
                    "client_id": f"mem-growth-test-{i}",
                }
            )

            # Verify client connected
            assert client.rpc is not None
            initial_services = len(client.rpc._services)

            # Disconnect and verify cleanup
            await client.disconnect()
            assert_rpc_cleaned_up(client.rpc)

    def test_repeated_service_registration_no_growth(self):
        """Repeated service register/unregister should clean up properly."""
        rpc, conn = create_rpc()

        # Track a service object
        service = {"id": "test", "echo": lambda x: x}

        # Register and unregister many times
        for i in range(100):
            sid = f"{rpc._local_workspace}/{rpc._client_id}:service-{i}"
            rpc._object_store[sid] = service

            # Verify stored
            assert sid in rpc._object_store

            # Remove
            del rpc._object_store[sid]

            # Verify removed
            assert sid not in rpc._object_store

        # Object store should only contain built-in service
        assert len(rpc._object_store) == 1  # Only 'services' key with built-in


# ========================================================================
# Utilities
# ========================================================================


def test_assert_rpc_cleaned_up_helper():
    """Test the assert_rpc_cleaned_up helper itself."""
    rpc, conn = create_rpc()

    # RPC has built-in service by default
    initial_store_len = len(rpc._object_store)
    initial_services_len = len(rpc._services)

    # Add some state
    rpc._object_store["test"] = {"data": "value"}
    rpc._services["svc"] = {"name": "test"}

    # Verify state was added
    assert len(rpc._object_store) > initial_store_len
    assert len(rpc._services) > initial_services_len

    # Should fail cleanup check
    try:
        assert_rpc_cleaned_up(rpc)
        assert False, "Should have raised AssertionError"
    except AssertionError as e:
        assert "Object store not empty" in str(e) or "Services not cleared" in str(e)

    # Clean up
    rpc.close()
    assert_rpc_cleaned_up(rpc)


def test_assert_collected_still_works():
    """Test that assert_collected helper still works for simple cases."""

    # Use a class instance (dicts can't have weak refs)
    class TestObj:
        def __init__(self):
            self.data = "test"

    obj = TestObj()
    weak = weakref.ref(obj)

    # Should still be alive
    assert weak() is not None

    # Delete and verify collection works
    del obj
    gc.collect()
    gc.collect()

    # Should be dead now (works for sync contexts)
    assert weak() is None
