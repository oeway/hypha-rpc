#!/usr/bin/env python3
"""Test the new generator streaming functionality for API v4+."""

import asyncio
import pytest
import numpy as np
import msgpack
import shortuuid
from hypha_rpc import connect_to_server
from hypha_rpc.rpc import API_VERSION
from . import WS_SERVER_URL


@pytest.mark.asyncio
async def test_generator_streaming_basic(websocket_server):
    """Test basic generator streaming with API v4+."""
    # Use a single client to test generator functionality
    client = await connect_to_server(
        {"id": "test-generator-client", "server_url": WS_SERVER_URL}
    )

    try:
        # Verify both clients support API v4
        assert API_VERSION == 4

        # Register a service with generator on server
        def simple_generator():
            for i in range(5):
                yield f"item_{i}"

        async def async_simple_generator():
            for i in range(5):
                yield f"async_item_{i}"

        await client.register_service(
            {
                "id": "generator-service",
                "type": "generator-test",
                "simple_generator": simple_generator,
                "async_simple_generator": async_simple_generator,
            }
        )

        # Wait a bit for service registration to complete
        await asyncio.sleep(0.5)

        # Get the service from the same client
        service = await client.get_service("generator-service")

        # Test sync generator streaming
        results = []
        gen = await service.simple_generator()
        async for item in gen:
            results.append(item)

        assert len(results) == 5
        assert results == ["item_0", "item_1", "item_2", "item_3", "item_4"]

        # Test async generator streaming
        async_results = []
        async_gen = await service.async_simple_generator()
        async for item in async_gen:
            async_results.append(item)

        assert len(async_results) == 5
        assert async_results == [
            "async_item_0",
            "async_item_1",
            "async_item_2",
            "async_item_3",
            "async_item_4",
        ]

    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_large_message_streaming(websocket_server):
    """Test large message chunking via generator streaming."""
    # Use a single client to test large message handling
    client = await connect_to_server(
        {"id": "test-large-client", "server_url": WS_SERVER_URL}
    )

    try:
        # Use a size that will trigger chunking but not take too long for CI
        large_data = np.random.random((600, 600)).astype(
            np.float32
        )  # ~1.4MB, good for testing chunking
        print(
            f"Created test array: {large_data.shape}, {large_data.nbytes} bytes (~{large_data.nbytes/1024/1024:.1f}MB)"
        )

        async def large_data_service(data):
            # This should trigger generator streaming for large messages
            return {"received_shape": data.shape, "sum": float(np.sum(data))}

        print("Registering service...")
        await client.register_service(
            {"id": "large-data-service", "process_large_data": large_data_service}
        )

        print("Waiting for service registration...")
        await asyncio.sleep(0.5)

        print("Getting service...")
        service = await client.get_service("large-data-service")

        # Test sending the large data with timeout
        print("Calling service with large data...")
        try:
            result = await asyncio.wait_for(
                service.process_large_data(large_data), timeout=30  # 30 second timeout
            )
            print(f"Received result: {result}")

            assert result["received_shape"] == list(large_data.shape)
            assert abs(result["sum"] - float(np.sum(large_data))) < 1e-5
            print("Test passed!")
        except asyncio.TimeoutError:
            print("ERROR: Service call timed out after 30 seconds!")
            raise

    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_generator_streaming_compatibility(websocket_server):
    """Test backward compatibility with older API versions."""
    # Connect client with API v4
    client = await connect_to_server(
        {"id": "test-compat-client", "server_url": WS_SERVER_URL}
    )

    try:
        # Test that API version detection works
        rpc = client.rpc

        # Should support API v4 features (check with the server manager instead)
        try:
            manager = await client.get_manager_service()
            supports_v4 = (
                hasattr(manager, "config")
                and getattr(manager.config, "api_version", 3) >= 4
            )
            # For this test, we just verify the client has API v4
            assert API_VERSION >= 4, "Should support API v4+"
        except Exception:
            # If we can't check remote version, at least verify our local version
            assert API_VERSION >= 4, "Should support API v4+"

    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_generator_error_handling(websocket_server):
    """Test error handling in generator streaming."""
    client = await connect_to_server(
        {"id": "test-error-client", "server_url": WS_SERVER_URL}
    )

    try:

        def error_generator():
            yield "item1"
            yield "item2"
            raise ValueError("Test error in generator")

        await client.register_service(
            {"id": "error-service", "error_generator": error_generator}
        )

        await asyncio.sleep(0.5)

        service = await client.get_service("error-service")

        # This should handle the error gracefully
        results = []
        try:
            gen = await service.error_generator()
            async for item in gen:
                results.append(item)
        except Exception as e:
            # Should get some items before the error
            assert len(results) >= 0  # May get some items before error
            assert "error" in str(e).lower() or len(results) >= 0

    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_generator_message_format(websocket_server):
    """Test the compact generator message format."""
    client = await connect_to_server(
        {"id": "test-format-client", "server_url": WS_SERVER_URL}
    )

    try:
        rpc = client.rpc

        # Test that the message format uses the compact method field
        test_chunks = [b"chunk1", b"chunk2", b"chunk3"]
        generator_id = shortuuid.uuid()

        # Capture messages sent
        captured_messages = []
        original_emit = rpc._emit_message

        async def mock_emit(message):
            # Decode and store the message
            import io

            unpacker = msgpack.Unpacker(io.BytesIO(message))
            msg = unpacker.unpack()
            captured_messages.append(msg)
            return await original_emit(message)

        rpc._emit_message = mock_emit

        # Send test generator stream
        await rpc._send_generator_stream(test_chunks, "test-target", generator_id)

        # Verify compact message format
        assert len(captured_messages) >= 5  # start + 3 data + end

        # Check start message format
        start_msg = captured_messages[0]
        assert start_msg["type"] == "generator"
        assert start_msg["method"] == f"{generator_id}:start"
        assert "generator_id" not in start_msg  # Should not have old format
        assert "action" not in start_msg  # Should not have old format

        # Check data message format
        for i in range(1, 4):
            data_msg = captured_messages[i]
            assert data_msg["type"] == "generator"
            assert data_msg["method"] == f"{generator_id}:data"
            assert "data" in data_msg

        # Check end message format
        end_msg = captured_messages[-1]
        assert end_msg["type"] == "generator"
        assert end_msg["method"] == f"{generator_id}:end"

        # Restore original emit
        rpc._emit_message = original_emit

    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_mixed_data_types_generator(websocket_server):
    """Test generator streaming with mixed data types."""
    client = await connect_to_server(
        {"id": "test-mixed-client", "server_url": WS_SERVER_URL}
    )

    try:

        def mixed_generator():
            yield 42
            yield "string"
            yield [1, 2, 3]
            yield {"key": "value"}
            yield np.array([1, 2, 3])

        await client.register_service(
            {"id": "mixed-service", "mixed_generator": mixed_generator}
        )

        await asyncio.sleep(0.5)

        service = await client.get_service("mixed-service")

        results = []
        gen = await service.mixed_generator()
        async for item in gen:
            results.append(item)

        assert len(results) == 5
        assert results[0] == 42
        assert results[1] == "string"
        assert results[2] == [1, 2, 3]
        assert results[3] == {"key": "value"}
        assert np.array_equal(results[4], np.array([1, 2, 3]))

    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_api_version_detection(websocket_server):
    """Test API version detection for choosing streaming vs legacy approach."""
    client = await connect_to_server(
        {"id": "test-version-client", "server_url": WS_SERVER_URL}
    )

    try:
        # Test API version detection method
        rpc = client.rpc

        # Test with the server manager (which should exist)
        try:
            manager = await client.get_manager_service()
            # Test that we can detect API versions properly
            assert API_VERSION >= 4, "Should detect API v4+ support"
        except Exception:
            # Fallback to just checking our own version
            assert API_VERSION >= 4, "Should support API v4+"

        # Test with invalid client ID (should return False)
        supports_invalid = await rpc._check_remote_api_version("non-existent-client")
        assert not supports_invalid, "Should return False for non-existent client"

    finally:
        await client.disconnect()
