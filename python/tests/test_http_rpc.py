"""Test HTTP RPC transport for hypha-rpc standalone tests.

These tests use a local Hypha server to ensure deterministic behavior,
avoiding load-balancer routing issues with remote multi-instance servers.
"""

import pytest
import numpy as np
import asyncio
from hypha_rpc import connect_to_server
from . import WS_SERVER_URL


# Local server URL (started by websocket_server fixture)
SERVER_URL = WS_SERVER_URL


class TestHTTPObjectTransmission:
    """Test HTTP transport with complex objects and callbacks."""

    @pytest.mark.asyncio
    async def test_http_numpy_array_transmission(self, websocket_server):
        """Test transmitting numpy arrays over HTTP transport."""
        # Service provider via WebSocket
        ws_server = await connect_to_server(
            {
                "server_url": SERVER_URL,
                "client_id": "numpy-provider-ws-test",
            }
        )

        try:
            workspace = ws_server.config["workspace"]

            # Register service that works with numpy arrays
            await ws_server.register_service(
                {
                    "id": "numpy-service",
                    "name": "Numpy Service",
                    "config": {"visibility": "public"},
                    "process_array": lambda arr: {
                        "shape": list(arr.shape),  # Convert to list for JSON
                        "dtype": str(arr.dtype),
                        "sum": float(np.sum(arr)),
                        "mean": float(np.mean(arr)),
                        "result_array": arr * 2,  # Return modified array
                    },
                    "reshape": lambda arr, shape: np.reshape(arr, shape),
                }
            )

            token = await ws_server.generate_token()

            # HTTP client connects
            http_server = await connect_to_server(
                {
                    "server_url": SERVER_URL,
                    "workspace": workspace,
                    "client_id": "numpy-consumer-http-test",
                    "transport": "http",
                    "token": token,
                }
            )

            try:
                service = await http_server.get_service(
                    f"{ws_server.config['client_id']}:numpy-service"
                )

                # Test 1: Send and receive numpy array
                test_array = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.float32)
                result = await service.process_array(test_array)

                assert result["shape"] == [2, 3]
                assert result["dtype"] == "float32"
                assert result["sum"] == 21.0
                assert result["mean"] == 3.5

                result_array = result["result_array"]
                assert isinstance(result_array, np.ndarray)
                assert np.array_equal(result_array, test_array * 2)

                # Test 2: Large array
                large_array = np.random.rand(100, 100)
                result2 = await service.process_array(large_array)
                assert result2["shape"] == [100, 100]

                # Test 3: Reshape operation
                flat_array = np.arange(12)
                reshaped = await service.reshape(flat_array, (3, 4))
                assert reshaped.shape == (3, 4)
                assert np.array_equal(reshaped, np.arange(12).reshape(3, 4))

            finally:
                await http_server.disconnect()

        finally:
            await ws_server.disconnect()

    @pytest.mark.asyncio
    async def test_http_nested_objects_transmission(self, websocket_server):
        """Test transmitting nested complex objects over HTTP."""
        ws_server = await connect_to_server(
            {
                "server_url": SERVER_URL,
                "client_id": "nested-provider-ws-test",
            }
        )

        try:
            workspace = ws_server.config["workspace"]

            # Service that handles nested objects
            await ws_server.register_service(
                {
                    "id": "nested-service",
                    "name": "Nested Object Service",
                    "config": {"visibility": "public"},
                    "process_nested": lambda data: {
                        "received_keys": list(data.keys()),
                        "array_sum": (
                            float(np.sum(data["array"])) if "array" in data else 0
                        ),
                        "nested_count": len(data.get("nested", {}).get("items", [])),
                        "echo": data,
                    },
                }
            )

            token = await ws_server.generate_token()

            http_server = await connect_to_server(
                {
                    "server_url": SERVER_URL,
                    "workspace": workspace,
                    "client_id": "nested-consumer-http-test",
                    "transport": "http",
                    "token": token,
                }
            )

            try:
                service = await http_server.get_service(
                    f"{ws_server.config['client_id']}:nested-service"
                )

                # Complex nested structure
                test_data = {
                    "string": "test",
                    "number": 42,
                    "array": np.array([1, 2, 3, 4, 5]),
                    "nested": {
                        "items": [1, 2, 3],
                        "metadata": {
                            "name": "test_item",
                            "values": [10, 20, 30],
                        },
                    },
                    "list_of_arrays": [
                        np.array([1, 2]),
                        np.array([3, 4]),
                    ],
                }

                result = await service.process_nested(test_data)

                assert set(result["received_keys"]) == set(test_data.keys())
                assert result["array_sum"] == 15.0
                assert result["nested_count"] == 3

                # Verify echo preserves structure
                echo = result["echo"]
                assert echo["string"] == "test"
                assert echo["number"] == 42
                assert np.array_equal(echo["array"], test_data["array"])
                assert echo["nested"]["metadata"]["name"] == "test_item"

            finally:
                await http_server.disconnect()

        finally:
            await ws_server.disconnect()

    @pytest.mark.asyncio
    async def test_http_callbacks_basic(self, websocket_server):
        """Test basic callback functionality over HTTP transport."""
        ws_server = await connect_to_server(
            {
                "server_url": SERVER_URL,
                "client_id": "callback-provider-ws-test",
            }
        )

        try:
            workspace = ws_server.config["workspace"]

            # Service that uses callbacks
            async def call_multiple_times(callback, count):
                results = []
                for i in range(count):
                    result = await callback(i)
                    results.append(result)
                return results

            async def process_with_progress(data, progress_callback):
                for i in range(len(data)):
                    await progress_callback({"step": i, "total": len(data)})
                return sum(data)

            await ws_server.register_service(
                {
                    "id": "callback-service",
                    "name": "Callback Service",
                    "config": {"visibility": "public"},
                    "call_multiple_times": call_multiple_times,
                    "process_with_progress": process_with_progress,
                }
            )

            token = await ws_server.generate_token()

            http_server = await connect_to_server(
                {
                    "server_url": SERVER_URL,
                    "workspace": workspace,
                    "client_id": "callback-consumer-http-test",
                    "transport": "http",
                    "token": token,
                }
            )

            try:
                service = await http_server.get_service(
                    f"{ws_server.config['client_id']}:callback-service"
                )

                # Test 1: Simple callback
                callback_results = []

                def test_callback(value):
                    callback_results.append(value)
                    return value * 2

                results = await service.call_multiple_times(test_callback, 5)
                assert len(callback_results) == 5
                assert callback_results == [0, 1, 2, 3, 4]
                assert results == [0, 2, 4, 6, 8]

                # Test 2: Progress callback
                progress_updates = []

                def progress_callback(info):
                    progress_updates.append(info)

                test_data = [10, 20, 30, 40]
                result = await service.process_with_progress(
                    test_data, progress_callback
                )
                assert result == 100
                assert len(progress_updates) == 4
                assert progress_updates[0] == {"step": 0, "total": 4}
                assert progress_updates[-1] == {"step": 3, "total": 4}

            finally:
                await http_server.disconnect()

        finally:
            await ws_server.disconnect()

    @pytest.mark.asyncio
    async def test_http_async_callbacks(self, websocket_server):
        """Test async callback functionality over HTTP transport."""
        ws_server = await connect_to_server(
            {
                "server_url": SERVER_URL,
                "client_id": "async-callback-provider-ws-test",
            }
        )

        try:
            workspace = ws_server.config["workspace"]

            # Service with async callback support
            async def process_async_callback(items, async_callback):
                results = []
                for item in items:
                    result = await async_callback(item)
                    results.append(result)
                return results

            await ws_server.register_service(
                {
                    "id": "async-callback-service",
                    "name": "Async Callback Service",
                    "config": {"visibility": "public"},
                    "process_async": process_async_callback,
                }
            )

            token = await ws_server.generate_token()

            http_server = await connect_to_server(
                {
                    "server_url": SERVER_URL,
                    "workspace": workspace,
                    "client_id": "async-callback-consumer-http-test",
                    "transport": "http",
                    "token": token,
                }
            )

            try:
                service = await http_server.get_service(
                    f"{ws_server.config['client_id']}:async-callback-service"
                )

                # Async callback
                async def async_transform(value):
                    await asyncio.sleep(0.01)  # Simulate async work
                    return value**2

                test_items = [1, 2, 3, 4, 5]
                results = await service.process_async(test_items, async_transform)
                assert results == [1, 4, 9, 16, 25]

            finally:
                await http_server.disconnect()

        finally:
            await ws_server.disconnect()

    @pytest.mark.asyncio
    async def test_http_callback_with_numpy(self, websocket_server):
        """Test callbacks that pass numpy arrays over HTTP."""
        ws_server = await connect_to_server(
            {
                "server_url": SERVER_URL,
                "client_id": "numpy-callback-provider-ws-test",
            }
        )

        try:
            workspace = ws_server.config["workspace"]

            # Service that sends arrays to callbacks
            async def transform_batch(arrays, transform_callback):
                results = []
                for arr in arrays:
                    result = await transform_callback(arr)
                    results.append(result)
                return results

            await ws_server.register_service(
                {
                    "id": "numpy-callback-service",
                    "name": "Numpy Callback Service",
                    "config": {"visibility": "public"},
                    "transform_batch": transform_batch,
                }
            )

            token = await ws_server.generate_token()

            http_server = await connect_to_server(
                {
                    "server_url": SERVER_URL,
                    "workspace": workspace,
                    "client_id": "numpy-callback-consumer-http-test",
                    "transport": "http",
                    "token": token,
                }
            )

            try:
                service = await http_server.get_service(
                    f"{ws_server.config['client_id']}:numpy-callback-service"
                )

                # Callback that processes numpy arrays
                def array_processor(arr):
                    return {
                        "sum": float(np.sum(arr)),
                        "modified": arr * 3,
                    }

                test_arrays = [
                    np.array([1, 2, 3]),
                    np.array([4, 5, 6]),
                    np.array([7, 8, 9]),
                ]

                results = await service.transform_batch(test_arrays, array_processor)

                assert len(results) == 3
                assert results[0]["sum"] == 6.0
                assert results[1]["sum"] == 15.0
                assert results[2]["sum"] == 24.0

                assert np.array_equal(results[0]["modified"], np.array([3, 6, 9]))
                assert np.array_equal(results[1]["modified"], np.array([12, 15, 18]))
                assert np.array_equal(results[2]["modified"], np.array([21, 24, 27]))

            finally:
                await http_server.disconnect()

        finally:
            await ws_server.disconnect()

    @pytest.mark.asyncio
    async def test_http_binary_data_transmission(self, websocket_server):
        """Test transmitting raw binary data over HTTP."""
        ws_server = await connect_to_server(
            {
                "server_url": SERVER_URL,
                "client_id": "binary-provider-ws-test",
            }
        )

        try:
            workspace = ws_server.config["workspace"]

            # Service that handles binary data
            await ws_server.register_service(
                {
                    "id": "binary-service",
                    "name": "Binary Service",
                    "config": {"visibility": "public"},
                    "process_binary": lambda data: {
                        "length": len(data),
                        "first_bytes": data[:10],
                        "reversed": bytes(reversed(data)),
                    },
                    "concat_binary": lambda parts: b"".join(parts),
                }
            )

            token = await ws_server.generate_token()

            http_server = await connect_to_server(
                {
                    "server_url": SERVER_URL,
                    "workspace": workspace,
                    "client_id": "binary-consumer-http-test",
                    "transport": "http",
                    "token": token,
                }
            )

            try:
                service = await http_server.get_service(
                    f"{ws_server.config['client_id']}:binary-service"
                )

                # Test 1: Send binary data
                test_data = b"Hello, World! This is binary data."
                result = await service.process_binary(test_data)

                assert result["length"] == len(test_data)
                assert result["first_bytes"] == test_data[:10]
                assert result["reversed"] == bytes(reversed(test_data))

                # Test 2: Multiple binary chunks
                parts = [b"Part1", b"Part2", b"Part3"]
                concatenated = await service.concat_binary(parts)
                assert concatenated == b"Part1Part2Part3"

            finally:
                await http_server.disconnect()

        finally:
            await ws_server.disconnect()


class TestHTTPManagerService:
    """Test HTTP transport interaction with workspace manager service."""

    @pytest.mark.asyncio
    async def test_http_manager_service_basic_access(self, websocket_server):
        """Test that HTTP clients can access the manager service."""
        server = await connect_to_server(
            {
                "server_url": SERVER_URL,
                "client_id": "http-manager-test",
                "transport": "http",
            }
        )

        try:
            # The server object IS the manager service wrapper
            assert server is not None
            assert hasattr(server, "get_service")
            assert hasattr(server, "list_services")
            assert hasattr(server, "register_service")

            # Verify we can call manager service methods
            services = await server.list_services()
            assert isinstance(services, list)

            print(f"✓ HTTP manager service accessible, found {len(services)} services")

        finally:
            await server.disconnect()

    @pytest.mark.asyncio
    async def test_http_manager_service_registration(self, websocket_server):
        """Test service registration via HTTP manager service."""
        server = await connect_to_server(
            {
                "server_url": SERVER_URL,
                "client_id": "http-registration-test",
                "transport": "http",
            }
        )

        try:
            # Register a test service via manager
            await server.register_service(
                {
                    "id": "http-test-service",
                    "name": "HTTP Test Service",
                    "config": {"visibility": "public"},
                    "echo": lambda x: f"HTTP Echo: {x}",
                    "add": lambda a, b: a + b,
                }
            )

            # Verify the service was registered by listing services
            # Service IDs include workspace prefix: workspace/client_id:service_id
            services = await server.list_services()
            service_ids = [s.get("id") for s in services]
            ws = server.config.get("workspace", "")
            full_service_id = f"{ws}/{server.config['client_id']}:http-test-service"
            assert full_service_id in service_ids

            # Get and test the service
            svc = await server.get_service("http-test-service")
            result = await svc.echo("test")
            assert result == "HTTP Echo: test"

            add_result = await svc.add(5, 3)
            assert add_result == 8

            print("✓ HTTP service registration and retrieval working")

        finally:
            await server.disconnect()

    @pytest.mark.asyncio
    async def test_http_cross_workspace_manager_access(self, websocket_server):
        """Test that HTTP clients in their own workspace can access the manager."""
        # Connect without specifying workspace (will be assigned a user workspace)
        server = await connect_to_server(
            {
                "server_url": SERVER_URL,
                "client_id": "http-cross-ws-test",
                "transport": "http",
            }
        )

        try:
            workspace = server.config.get("workspace")
            print(f"Connected to workspace: {workspace}")

            # Even if we're in a user workspace, we should access the manager
            # This uses the */manager-id:default format internally
            services = await server.list_services()
            assert isinstance(services, list)

            # Register service in our workspace
            await server.register_service(
                {
                    "id": "cross-ws-service",
                    "config": {"visibility": "public"},
                    "test": lambda: "ok",
                }
            )

            # Verify via manager (service IDs include workspace prefix)
            services_after = await server.list_services()
            service_ids = [s.get("id") for s in services_after]
            full_service_id = f"{workspace}/{server.config['client_id']}:cross-ws-service"
            assert full_service_id in service_ids

            print(f"✓ Cross-workspace manager access from {workspace}")

        finally:
            await server.disconnect()

    @pytest.mark.asyncio
    async def test_http_manager_get_service_info(self, websocket_server):
        """Test getting detailed service info via HTTP manager."""
        server = await connect_to_server(
            {
                "server_url": SERVER_URL,
                "client_id": "http-service-info-test",
                "transport": "http",
            }
        )

        try:
            # Register a service with metadata
            await server.register_service(
                {
                    "id": "detailed-service",
                    "name": "Detailed Service",
                    "description": "A service with detailed metadata",
                    "config": {"visibility": "public"},
                    "version": "1.0.0",
                    "methods": lambda: ["echo", "transform"],
                    "echo": lambda x: x,
                    "transform": lambda x: x.upper() if isinstance(x, str) else x,
                }
            )

            # Get service info (service IDs include workspace prefix)
            services = await server.list_services()
            our_service = None
            ws = server.config.get("workspace", "")
            full_service_id = f"{ws}/{server.config['client_id']}:detailed-service"

            for svc_info in services:
                if svc_info.get("id") == full_service_id:
                    our_service = svc_info
                    break

            assert our_service is not None
            assert our_service.get("name") == "Detailed Service"
            assert our_service.get("description") == "A service with detailed metadata"

            # Test the service works
            svc = await server.get_service("detailed-service")
            assert await svc.echo("test") == "test"
            assert await svc.transform("hello") == "HELLO"

            print("✓ HTTP manager service info retrieval working")

        finally:
            await server.disconnect()

    @pytest.mark.asyncio
    async def test_http_manager_service_with_token(self, websocket_server):
        """Test HTTP manager access with authentication token."""
        # First, connect via WebSocket to get a token
        ws_server = await connect_to_server(
            {
                "server_url": SERVER_URL,
                "client_id": "token-provider-test",
            }
        )

        try:
            workspace = ws_server.config["workspace"]
            token = await ws_server.generate_token()

            # Now connect via HTTP with the token
            http_server = await connect_to_server(
                {
                    "server_url": SERVER_URL,
                    "workspace": workspace,
                    "client_id": "token-consumer-http-test",
                    "transport": "http",
                    "token": token,
                }
            )

            try:
                # Should have manager access
                services = await http_server.list_services()
                assert isinstance(services, list)

                # Register a service
                await http_server.register_service(
                    {
                        "id": "token-auth-service",
                        "config": {"visibility": "public"},
                        "test": lambda: "authenticated",
                    }
                )

                # Verify
                svc = await http_server.get_service("token-auth-service")
                result = await svc.test()
                assert result == "authenticated"

                print("✓ HTTP manager access with token working")

            finally:
                await http_server.disconnect()

        finally:
            await ws_server.disconnect()

    @pytest.mark.asyncio
    async def test_http_manager_list_services_filtering(self, websocket_server):
        """Test listing services with different filters via HTTP."""
        server = await connect_to_server(
            {
                "server_url": SERVER_URL,
                "client_id": "http-list-filter-test",
                "transport": "http",
            }
        )

        try:
            # Register multiple services
            for i in range(3):
                await server.register_service(
                    {
                        "id": f"filter-test-{i}",
                        "config": {"visibility": "public"},
                        "index": lambda idx=i: idx,
                    }
                )

            # List all services
            all_services = await server.list_services()
            our_services = [s for s in all_services if "filter-test" in s.get("id", "")]
            assert len(our_services) >= 3

            print(
                f"✓ HTTP manager service filtering, found {len(our_services)} test services"
            )

        finally:
            await server.disconnect()


class TestHTTPReconnection:
    """Test HTTP transport reconnection behavior."""

    @pytest.mark.asyncio
    async def test_http_reconnection_restores_services(self, websocket_server):
        """Test that HTTP stream reconnection re-registers services.

        Simulates a stream disconnect by closing the underlying HTTP client,
        which forces the stream loop to reconnect. After reconnection, the
        connection_info is received again, _handle_connected is called, and
        services should be re-registered and usable.
        """
        server = await connect_to_server(
            {
                "server_url": SERVER_URL,
                "client_id": "http-reconnect-test",
                "transport": "http",
            }
        )

        try:
            # Register a service
            await server.register_service(
                {
                    "id": "reconnect-service",
                    "config": {"visibility": "public"},
                    "ping": lambda: "pong",
                    "echo": lambda x: x,
                }
            )

            # Verify service works before disconnect
            svc = await server.get_service("reconnect-service")
            assert await svc.ping() == "pong"
            assert await svc.echo("hello") == "hello"
            print("Initial service working")

            # Track reconnection events
            reconnection_events = []
            original_handle_connected = server.rpc._connection._handle_connected

            async def tracking_handle_connected(info):
                reconnection_events.append(info)
                print(f"Reconnected to workspace: {info.get('workspace')}")
                if original_handle_connected:
                    await original_handle_connected(info)

            server.rpc._connection._handle_connected = tracking_handle_connected

            # Force stream disconnection by closing the underlying HTTP client.
            # This causes the active GET stream to fail, triggering the
            # reconnection logic in _stream_loop.
            conn = server.rpc._connection
            if conn._http_client:
                await conn._http_client.aclose()
                conn._http_client = None

            # Wait for reconnection (stream loop should detect the error,
            # create a new HTTP client, and reconnect)
            max_wait = 20
            start = asyncio.get_event_loop().time()
            while (
                asyncio.get_event_loop().time() - start < max_wait
                and len(reconnection_events) == 0
            ):
                await asyncio.sleep(0.5)

            assert len(reconnection_events) > 0, (
                f"Should have received reconnection event within {max_wait}s"
            )

            # Give time for services to be re-registered
            await asyncio.sleep(2)

            # Verify service works after reconnection
            success = False
            for attempt in range(10):
                try:
                    svc = await asyncio.wait_for(
                        server.get_service("reconnect-service"), timeout=5.0
                    )
                    result = await asyncio.wait_for(svc.ping(), timeout=5.0)
                    if result == "pong":
                        print(f"Service working after reconnection (attempt {attempt + 1})")
                        success = True
                        break
                except Exception as e:
                    print(f"Attempt {attempt + 1}: {type(e).__name__}: {e}")
                    await asyncio.sleep(1)

            assert success, "Service should work after HTTP stream reconnection"

            # Also test echo still works
            result = await svc.echo("after-reconnect")
            assert result == "after-reconnect"
            print("Echo working after reconnection")

        finally:
            await server.disconnect()

    @pytest.mark.asyncio
    async def test_http_reconnection_preserves_workspace(self, websocket_server):
        """Test that workspace is preserved across HTTP reconnections."""
        server = await connect_to_server(
            {
                "server_url": SERVER_URL,
                "client_id": "http-ws-preserve-test",
                "transport": "http",
            }
        )

        try:
            original_workspace = server.config.get("workspace")
            assert original_workspace is not None

            # Force stream disconnection
            conn = server.rpc._connection
            original_manager_id = conn.manager_id

            if conn._http_client:
                await conn._http_client.aclose()
                conn._http_client = None

            # Wait for reconnection
            await asyncio.sleep(5)

            # Workspace should be the same after reconnection
            new_workspace = conn._workspace
            assert new_workspace == original_workspace, (
                f"Workspace changed: {original_workspace} -> {new_workspace}"
            )

            # manager_id should be updated (new session on server)
            # but workspace should remain the same
            print(
                f"Workspace preserved: {new_workspace}, "
                f"manager_id: {original_manager_id} -> {conn.manager_id}"
            )

            # Verify we can still list services (uses workspace manager)
            services = await server.list_services()
            assert isinstance(services, list)
            print(f"Listed {len(services)} services after reconnection")

        finally:
            await server.disconnect()

    @pytest.mark.asyncio
    async def test_http_reconnection_cross_transport(self, websocket_server):
        """Test that an HTTP client can call a WebSocket service after reconnection."""
        # Service provider via WebSocket
        ws_server = await connect_to_server(
            {
                "server_url": SERVER_URL,
                "client_id": "ws-provider-reconnect-test",
            }
        )

        try:
            workspace = ws_server.config["workspace"]
            token = await ws_server.generate_token()

            # Register service on WebSocket client
            await ws_server.register_service(
                {
                    "id": "ws-service",
                    "name": "WS Service",
                    "config": {"visibility": "public"},
                    "add": lambda a, b: a + b,
                }
            )

            # HTTP client connects to same workspace
            http_server = await connect_to_server(
                {
                    "server_url": SERVER_URL,
                    "workspace": workspace,
                    "client_id": "http-consumer-reconnect-test",
                    "transport": "http",
                    "token": token,
                }
            )

            try:
                # Verify cross-transport call works
                svc = await http_server.get_service(
                    f"{ws_server.config['client_id']}:ws-service"
                )
                assert await svc.add(3, 4) == 7
                print("Cross-transport call working before reconnection")

                # Force HTTP stream disconnection
                conn = http_server.rpc._connection
                if conn._http_client:
                    await conn._http_client.aclose()
                    conn._http_client = None

                # Wait for reconnection
                await asyncio.sleep(5)

                # Cross-transport call should still work after reconnection
                success = False
                for attempt in range(10):
                    try:
                        svc = await asyncio.wait_for(
                            http_server.get_service(
                                f"{ws_server.config['client_id']}:ws-service"
                            ),
                            timeout=5.0,
                        )
                        result = await asyncio.wait_for(svc.add(10, 20), timeout=5.0)
                        if result == 30:
                            print(
                                f"Cross-transport call working after reconnection "
                                f"(attempt {attempt + 1})"
                            )
                            success = True
                            break
                    except Exception as e:
                        print(f"Attempt {attempt + 1}: {type(e).__name__}: {e}")
                        await asyncio.sleep(1)

                assert success, "Cross-transport call should work after reconnection"

            finally:
                await http_server.disconnect()

        finally:
            await ws_server.disconnect()


if __name__ == "__main__":
    # Allow running tests directly
    pytest.main([__file__, "-v", "-s"])
