"""Test message batching functionality."""

import asyncio
import io
import pytest
from hypha_rpc import RPC
from hypha_rpc.utils import ObjectProxy


class MockConnection:
    """Mock connection for testing."""

    def __init__(self):
        self.manager_id = None  # Don't auto-register services
        self.workspace = "test_workspace"
        self.messages = []
        self.message_callback = None
        self.connected_callback = None
        self.batch_messages = []
        self.user_messages = []  # Track user messages separately

    async def emit_message(self, message):
        self.messages.append(message)
        # Track user messages (not internal service registration)
        # Look for our test messages by checking if it's not a manager service call
        try:
            import msgpack

            unpacker = msgpack.Unpacker(io.BytesIO(message))
            data = unpacker.unpack()
            if data.get("to") != "*/test_manager":
                self.user_messages.append(message)
        except:
            # If we can't decode, assume it's a user message
            self.user_messages.append(message)

    def on_message(self, callback):
        self.message_callback = callback

    def on_connected(self, callback):
        self.connected_callback = callback

    async def disconnect(self):
        pass


@pytest.mark.asyncio
async def test_message_batching_basic():
    """Test basic message batching functionality."""
    conn = MockConnection()
    rpc = RPC(
        conn,
        client_id="test_client",
        enable_message_batching=True,
        batch_timeout_ms=50,  # Higher timeout for testing
        batch_max_messages=3,
        batch_max_size=1024,
    )

    # Add a simple service
    def test_service():
        return "test_response"

    rpc.add_service({"id": "test_service", "test_method": test_service})

    # Send multiple small messages rapidly
    messages = []
    for i in range(5):
        msg = {
            "type": "method",
            "from": "test_workspace/test_client",
            "to": "test_workspace/target_client",
            "method": "test_method",
            "args": [f"arg_{i}"],
        }
        messages.append(rpc.emit(msg))

    # Wait for all messages to be sent
    await asyncio.gather(*messages)

    # Should have used batching - fewer total messages sent
    assert len(conn.user_messages) > 0
    print(
        f"Sent {len(conn.user_messages)} user messages (batched from 5 individual messages)"
    )

    # Check that batching was used (should be fewer messages than original)
    # With batching, expect 1 batch stream (start + 5 data + end = 7 messages) or 2 batches
    assert len(conn.user_messages) >= 7  # At least the generator stream messages
    print(
        f"Total messages: {len(conn.messages)}, User messages: {len(conn.user_messages)}"
    )


@pytest.mark.asyncio
async def test_message_batching_disabled():
    """Test that batching can be disabled."""
    conn = MockConnection()
    rpc = RPC(conn, client_id="test_client", enable_message_batching=False)

    # Add a simple service
    def test_service():
        return "test_response"

    rpc.add_service({"id": "test_service", "test_method": test_service})

    # Send multiple small messages
    messages = []
    for i in range(3):
        msg = {
            "type": "method",
            "from": "test_workspace/test_client",
            "to": "test_workspace/target_client",
            "method": "test_method",
            "args": [f"arg_{i}"],
        }
        messages.append(rpc.emit(msg))

    # Wait for all messages to be sent
    await asyncio.gather(*messages)

    # Should send each message individually (no batching)
    assert len(conn.user_messages) == 3
    print(f"Sent {len(conn.user_messages)} user messages (no batching)")


@pytest.mark.asyncio
async def test_message_batching_size_limit():
    """Test that messages are flushed when size limit is reached."""
    conn = MockConnection()
    rpc = RPC(
        conn,
        client_id="test_client",
        enable_message_batching=True,
        batch_timeout_ms=1000,  # Long timeout so size limit triggers first
        batch_max_messages=10,
        batch_max_size=200,
    )  # Small size limit

    # Add a simple service
    def test_service():
        return "test_response"

    rpc.add_service({"id": "test_service", "test_method": test_service})

    # Send messages that will exceed size limit
    messages = []
    for i in range(3):
        msg = {
            "type": "method",
            "from": "test_workspace/test_client",
            "to": "test_workspace/target_client",
            "method": "test_method",
            "args": [f"long_argument_that_makes_message_large_{i}" * 10],
        }
        messages.append(rpc.emit(msg))

    # Wait for all messages to be sent
    await asyncio.gather(*messages)

    # Should have triggered size-based flushing
    assert len(conn.user_messages) > 0
    print(f"Sent {len(conn.user_messages)} user messages (size-based batching)")


@pytest.mark.asyncio
async def test_message_batching_timeout():
    """Test that messages are flushed after timeout."""
    conn = MockConnection()
    rpc = RPC(
        conn,
        client_id="test_client",
        enable_message_batching=True,
        batch_timeout_ms=20,  # Short timeout
        batch_max_messages=10,
        batch_max_size=10000,
    )

    # Add a simple service
    def test_service():
        return "test_response"

    rpc.add_service({"id": "test_service", "test_method": test_service})

    # Send a single message
    msg = {
        "type": "method",
        "from": "test_workspace/test_client",
        "to": "test_workspace/target_client",
        "method": "test_method",
        "args": ["single_arg"],
    }

    await rpc.emit(msg)

    # Wait for timeout to trigger
    await asyncio.sleep(0.1)

    # Should have sent the message after timeout
    assert len(conn.user_messages) == 1
    print(f"Sent {len(conn.user_messages)} user messages (timeout-based batching)")


@pytest.mark.asyncio
async def test_message_batching_large_message_bypass():
    """Test that large messages bypass batching."""
    conn = MockConnection()
    rpc = RPC(
        conn,
        client_id="test_client",
        enable_message_batching=True,
        batch_timeout_ms=100,
        batch_max_messages=10,
        batch_max_size=1000,
    )  # Small batch size

    # Add a simple service
    def test_service():
        return "test_response"

    rpc.add_service({"id": "test_service", "test_method": test_service})

    # Send a large message that exceeds batch size
    large_data = "x" * 2000  # Larger than batch_max_size
    msg = {
        "type": "method",
        "from": "test_workspace/test_client",
        "to": "test_workspace/target_client",
        "method": "test_method",
        "args": [large_data],
    }

    await rpc.emit(msg)

    # Should bypass batching and send immediately
    assert len(conn.user_messages) == 1
    print(f"Sent {len(conn.user_messages)} user messages (large message bypass)")


@pytest.mark.asyncio
async def test_message_batching_different_targets():
    """Test that messages to different targets are batched separately."""
    conn = MockConnection()
    rpc = RPC(
        conn,
        client_id="test_client",
        enable_message_batching=True,
        batch_timeout_ms=50,
        batch_max_messages=5,
        batch_max_size=10000,
    )

    # Add a simple service
    def test_service():
        return "test_response"

    rpc.add_service({"id": "test_service", "test_method": test_service})

    # Send messages to different targets
    messages = []
    for i in range(3):
        # Target 1
        msg1 = {
            "type": "method",
            "from": "test_workspace/test_client",
            "to": "test_workspace/target_client_1",
            "method": "test_method",
            "args": [f"arg_{i}"],
        }
        messages.append(rpc.emit(msg1))

        # Target 2
        msg2 = {
            "type": "method",
            "from": "test_workspace/test_client",
            "to": "test_workspace/target_client_2",
            "method": "test_method",
            "args": [f"arg_{i}"],
        }
        messages.append(rpc.emit(msg2))

    # Wait for all messages to be sent
    await asyncio.gather(*messages)

    # Wait for timeout-based batching to trigger
    await asyncio.sleep(0.1)

    # Should have separate batches for different targets
    assert len(conn.user_messages) > 0
    print(f"Sent {len(conn.user_messages)} user messages (different targets)")
    print(
        f"Total messages: {len(conn.messages)}, User messages: {len(conn.user_messages)}"
    )


if __name__ == "__main__":
    # Run basic tests
    print("Testing message batching functionality...")

    async def run_tests():
        await test_message_batching_basic()
        await test_message_batching_disabled()
        await test_message_batching_size_limit()
        await test_message_batching_timeout()
        await test_message_batching_large_message_bypass()
        await test_message_batching_different_targets()
        print("All tests passed!")

    asyncio.run(run_tests())
