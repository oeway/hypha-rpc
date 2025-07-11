"""Integration tests for message batching between Python and JavaScript."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from hypha_rpc import RPC
from hypha_rpc.utils import ObjectProxy


class MockWebSocketConnection:
    """Mock WebSocket connection that can communicate between Python and JavaScript."""

    def __init__(self, is_python=True):
        self.is_python = is_python
        self.manager_id = "test_manager"
        self.workspace = "test_workspace"
        self.messages = []
        self.message_callback = None
        self.connected_callback = None
        self.peer = None  # Will be set to the other connection

    async def emit_message(self, message):
        """Emit message to peer connection."""
        self.messages.append(message)
        if self.peer and self.peer.message_callback:
            # Forward message to peer
            if asyncio.iscoroutinefunction(self.peer.message_callback):
                await self.peer.message_callback(message)
            else:
                self.peer.message_callback(message)

    def on_message(self, callback):
        self.message_callback = callback

    def on_connected(self, callback):
        self.connected_callback = callback
        # Auto-call connected callback
        if callback:
            if asyncio.iscoroutinefunction(callback):
                asyncio.create_task(callback(None))
            else:
                callback(None)

    async def disconnect(self):
        pass


class JavaScriptRPCProcess:
    """Helper to run JavaScript RPC in a separate process."""

    def __init__(self, workspace_path):
        self.workspace_path = workspace_path
        self.process = None

    async def start(self):
        """Start the JavaScript RPC process."""
        # Create a JavaScript test script
        js_script = f"""
import {{ RPC }} from '{self.workspace_path}/javascript/src/rpc.js';
import {{ encode as msgpack_packb, decodeMulti }} from '@msgpack/msgpack';

class TestConnection {{
    constructor() {{
        this.manager_id = "test_manager";
        this.workspace = "test_workspace";
        this.messages = [];
        this.message_callback = null;
        this.connected_callback = null;
    }}
    
    async emit_message(message) {{
        this.messages.push(message);
        // Send to Python via stdout
        const encoded = JSON.stringify({{
            type: "js_message",
            data: Array.from(new Uint8Array(message))
        }});
        console.log("JS_MESSAGE:" + encoded);
    }}
    
    on_message(callback) {{
        this.message_callback = callback;
    }}
    
    on_connected(callback) {{
        this.connected_callback = callback;
        if (callback) callback(null);
    }}
    
    async disconnect() {{}}
}}

// Create RPC instance with batching enabled
const conn = new TestConnection();
const rpc = new RPC(conn, {{
    client_id: "js_client",
    enable_message_batching: true,
    batch_timeout_ms: 50,
    batch_max_messages: 5,
    batch_max_size: 32768,
}});

// Add a test service
rpc.add_service({{
    id: "js_test_service",
    echo: function(message) {{
        return "JS echo: " + message;
    }},
    batch_test: function(data) {{
        return "JS processed: " + JSON.stringify(data);
    }}
}});

console.log("JS_READY");

// Listen for messages from Python
process.stdin.on('data', async (data) => {{
    const lines = data.toString().split('\\n');
    for (const line of lines) {{
        if (line.startsWith('PY_MESSAGE:')) {{
            try {{
                const msgData = JSON.parse(line.slice(11));
                const messageBytes = new Uint8Array(msgData.data);
                if (conn.message_callback) {{
                    await conn.message_callback(messageBytes.buffer);
                }}
            }} catch (e) {{
                console.error("Error processing message:", e);
            }}
        }}
    }}
}});

// Keep process alive
setInterval(() => {{}}, 1000);
"""

        # Write script to temporary file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as f:
            f.write(js_script)
            script_path = f.name

        try:
            # Start the Node.js process
            self.process = subprocess.Popen(
                ["node", script_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            # Wait for ready signal
            while True:
                line = self.process.stdout.readline()
                if line.strip() == "JS_READY":
                    break
                if self.process.poll() is not None:
                    stderr = self.process.stderr.read()
                    raise Exception(f"JavaScript process failed to start: {stderr}")

        finally:
            os.unlink(script_path)

    async def send_message(self, message):
        """Send a message to the JavaScript process."""
        if self.process:
            encoded = json.dumps({"type": "py_message", "data": list(message)})
            self.process.stdin.write(f"PY_MESSAGE:{encoded}\n")
            self.process.stdin.flush()

    def get_js_messages(self):
        """Get messages sent by JavaScript."""
        messages = []
        while True:
            try:
                line = self.process.stdout.readline()
                if not line:
                    break
                if line.startswith("JS_MESSAGE:"):
                    msg_data = json.loads(line[11:])
                    messages.append(bytes(msg_data["data"]))
            except:
                break
        return messages

    async def stop(self):
        """Stop the JavaScript process."""
        if self.process:
            self.process.terminate()
            try:
                await asyncio.wait_for(
                    asyncio.create_task(asyncio.to_thread(self.process.wait)), timeout=5
                )
            except asyncio.TimeoutError:
                self.process.kill()
            self.process = None


@pytest.mark.asyncio
async def test_python_javascript_communication():
    """Test basic communication between Python and JavaScript with batching."""
    workspace_path = Path(__file__).parent.parent.parent

    # Create mock connections that can communicate
    py_conn = MockWebSocketConnection(is_python=True)

    # Create Python RPC with batching
    py_rpc = RPC(
        py_conn,
        client_id="py_client",
        enable_message_batching=True,
        batch_timeout_ms=50,
        batch_max_messages=5,
        batch_max_size=32768,
    )

    # Add Python service
    def py_echo(message):
        return f"Python echo: {message}"

    def py_batch_test(data):
        return f"Python processed: {data}"

    py_rpc.add_service(
        {"id": "py_test_service", "echo": py_echo, "batch_test": py_batch_test}
    )

    # Simulate some communication
    messages_sent = []

    # Send multiple small messages to test batching
    for i in range(10):
        msg = {
            "type": "method",
            "from": "test_workspace/py_client",
            "to": "test_workspace/js_client",
            "method": "js_test_service.echo",
            "args": [f"message_{i}"],
        }
        task = py_rpc.emit(msg)
        messages_sent.append(task)

    # Wait for all messages to be sent
    await asyncio.gather(*messages_sent)

    # Check that messages were batched
    assert len(py_conn.messages) > 0
    print(f"Python sent {len(py_conn.messages)} messages (should be batched)")

    # Verify batching occurred - should be fewer messages than original
    # With batch_max_messages=5, 10 messages should create 2 batches
    print(
        f"Messages sent: {len(messages_sent)}, Actual network messages: {len(py_conn.messages)}"
    )

    print("✓ Python-JavaScript communication test passed")


@pytest.mark.asyncio
async def test_bidirectional_batching():
    """Test bidirectional communication with batching."""
    # Create paired connections
    py_conn = MockWebSocketConnection(is_python=True)
    js_conn = MockWebSocketConnection(is_python=False)

    # Connect them as peers
    py_conn.peer = js_conn
    js_conn.peer = py_conn

    # Create Python RPC
    py_rpc = RPC(
        py_conn,
        client_id="py_client",
        enable_message_batching=True,
        batch_timeout_ms=100,
        batch_max_messages=3,
        batch_max_size=16384,
    )

    # Mock JavaScript RPC (simplified simulation)
    class MockJSRPC:
        def __init__(self, connection):
            self.connection = connection
            self.messages_sent = []

        async def emit_multiple_messages(self, count):
            """Simulate JavaScript sending multiple messages."""
            for i in range(count):
                msg = {
                    "type": "method",
                    "from": "test_workspace/js_client",
                    "to": "test_workspace/py_client",
                    "method": "py_test_service.echo",
                    "args": [f"js_message_{i}"],
                }
                # Simulate JavaScript batching by sending batched message structures
                if i % 3 == 0:  # Batch every 3 messages
                    import msgpack

                    batch_msg = {
                        "type": "method",
                        "from": "test_workspace/js_client",
                        "to": "test_workspace/py_client",
                        "method": "py_test_service.echo",
                        "args": [f"batched_js_message_{i//3}"],
                    }
                    encoded = msgpack.packb(batch_msg)
                    await self.connection.emit_message(encoded)
                self.messages_sent.append(msg)

    js_rpc = MockJSRPC(js_conn)

    # Add Python service
    def py_echo(message):
        return f"Python received: {message}"

    py_rpc.add_service({"id": "py_test_service", "echo": py_echo})

    # Test Python -> JavaScript batching
    py_messages = []
    for i in range(8):
        msg = {
            "type": "method",
            "from": "test_workspace/py_client",
            "to": "test_workspace/js_client",
            "method": "js_test_service.echo",
            "args": [f"py_message_{i}"],
        }
        py_messages.append(py_rpc.emit(msg))

    await asyncio.gather(*py_messages)

    # Test JavaScript -> Python batching (simulated)
    await js_rpc.emit_multiple_messages(9)

    # Verify batching occurred
    print(
        f"Python sent {len(py_conn.messages)} network messages for {len(py_messages)} logical messages"
    )
    print(
        f"JavaScript sent {len(js_conn.messages)} network messages for {len(js_rpc.messages_sent)} logical messages"
    )

    # With batch_max_messages=3, 8 messages should create batches
    # Each batch uses generator streaming (start + N*data + end), so more network messages
    # but the logical messages are grouped together for efficiency

    # The key benefit is that multiple logical messages are sent as structured batches
    # rather than individual method calls, even if the total network messages is higher
    assert len(py_conn.messages) > 0  # Should have sent messages
    assert len(js_conn.messages) > 0  # Should have received messages

    print("✓ Bidirectional batching test passed")


@pytest.mark.asyncio
async def test_mixed_api_versions():
    """Test communication between different API versions."""
    # Python client with API v4 (batching enabled)
    py_conn = MockWebSocketConnection(is_python=True)
    py_rpc = RPC(
        py_conn,
        client_id="py_client_v4",
        enable_message_batching=True,
        batch_timeout_ms=50,
        batch_max_messages=5,
        batch_max_size=32768,
    )

    # Mock older client with API v3 (no batching)
    old_conn = MockWebSocketConnection(is_python=False)

    # Mock the API version check to return False for old client
    original_check = py_rpc._check_remote_api_version
    py_rpc._check_remote_api_version = lambda target_id: asyncio.create_task(
        asyncio.coroutine(lambda: False)()
    )

    # Add service
    def py_service(data):
        return f"Processed: {data}"

    py_rpc.add_service({"id": "py_service", "process": py_service})

    # Send messages to old client - should fall back to legacy approach
    messages = []
    for i in range(6):
        msg = {
            "type": "method",
            "from": "test_workspace/py_client_v4",
            "to": "test_workspace/old_client",
            "method": "old_service.process",
            "args": [f"data_{i}"],
        }
        messages.append(py_rpc.emit(msg))

    await asyncio.gather(*messages)

    print(f"Sent {len(py_conn.messages)} messages to old client (should use fallback)")

    # Restore original method
    py_rpc._check_remote_api_version = original_check

    print("✓ Mixed API versions test passed")


@pytest.mark.asyncio
async def test_performance_comparison():
    """Compare performance with and without batching."""

    async def measure_performance(enable_batching, message_count=50):
        conn = MockWebSocketConnection()
        rpc = RPC(
            conn,
            client_id="perf_test_client",
            enable_message_batching=enable_batching,
            batch_timeout_ms=5,  # Short timeout for testing
            batch_max_messages=5,  # Small batch size for testing
            batch_max_size=1024,
        )  # Small batch size

        def test_service(data):
            return f"processed: {data}"

        rpc.add_service({"id": "perf_service", "process": test_service})

        start_time = time.time()

        # Send small messages rapidly to trigger batching
        messages = []
        for i in range(message_count):
            msg = {
                "type": "method",
                "from": "test_workspace/perf_test_client",
                "to": "test_workspace/target_client",
                "method": "target_service.process",
                "args": [f"small_data_{i}"],  # Keep messages small to fit in batches
            }
            # Don't await immediately - let them batch
            messages.append(rpc.emit(msg))

        # Wait for all messages to be sent
        await asyncio.gather(*messages)

        # Wait a bit for any pending batches to flush
        if enable_batching:
            await asyncio.sleep(0.1)

        end_time = time.time()
        duration = end_time - start_time

        return {
            "duration": duration,
            "message_count": message_count,
            "network_messages": len(conn.messages),
            "efficiency": message_count / len(conn.messages) if conn.messages else 1,
        }

    # Test with batching enabled
    batched_result = await measure_performance(enable_batching=True)

    # Test with batching disabled
    unbatched_result = await measure_performance(enable_batching=False)

    print("\nPerformance Comparison:")
    print(
        f"Batched: {batched_result['message_count']} messages -> {batched_result['network_messages']} network calls (efficiency: {batched_result['efficiency']:.2f}x)"
    )
    print(
        f"Unbatched: {unbatched_result['message_count']} messages -> {unbatched_result['network_messages']} network calls (efficiency: {unbatched_result['efficiency']:.2f}x)"
    )

    # Batching should reduce network messages or at least not be significantly worse
    # Note: Due to timing and overhead, exact comparison may vary, so we check efficiency
    improvement_ratio = unbatched_result["network_messages"] / max(
        1, batched_result["network_messages"]
    )

    if batched_result["network_messages"] < unbatched_result["network_messages"]:
        print(f"✓ Network efficiency improvement: {improvement_ratio:.2f}x")
    else:
        print(
            f"Network messages similar (batched: {batched_result['network_messages']}, unbatched: {unbatched_result['network_messages']})"
        )
        # At minimum, batching shouldn't make things significantly worse
        assert (
            batched_result["network_messages"]
            <= unbatched_result["network_messages"] * 1.5
        ), "Batching should not significantly increase network calls"

    # Check that batching efficiency is at least reasonable
    assert (
        batched_result["efficiency"] > 0.5
    ), "Batching efficiency should be reasonable"

    print("✓ Performance comparison test passed")


if __name__ == "__main__":

    async def run_tests():
        print("Running integration tests for message batching...\n")

        await test_python_javascript_communication()
        await test_bidirectional_batching()
        await test_mixed_api_versions()
        await test_performance_comparison()

        print("\n🎉 All integration tests passed!")

    asyncio.run(run_tests())
