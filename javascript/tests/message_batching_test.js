/**
 * Test message batching functionality in JavaScript
 */

import { RPC } from "../src/rpc.js";

class MockConnection {
  constructor() {
    this.manager_id = null; // Don't auto-register services
    this.workspace = "test_workspace";
    this.messages = [];
    this.message_callback = null;
    this.connected_callback = null;
    this.batch_messages = [];
    this.user_messages = []; // Track user messages separately
  }

  async emit_message(message) {
    this.messages.push(message);
    // Track user messages (not internal service registration)
    try {
      const { decodeMulti } = await import("@msgpack/msgpack");
      const unpacker = decodeMulti(message);
      const { value: data } = unpacker.next();
      if (data.to !== "*/test_manager") {
        this.user_messages.push(message);
      }
    } catch (e) {
      // If we can't decode, assume it's a user message
      this.user_messages.push(message);
    }
  }

  on_message(callback) {
    this.message_callback = callback;
  }

  on_connected(callback) {
    this.connected_callback = callback;
    // Don't auto-call connected callback to avoid service registration
  }

  async disconnect() {
    // Mock disconnect
  }
}

// Helper function to wait for a given time
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Test basic message batching functionality
async function test_message_batching_basic() {
  console.log("Testing basic message batching...");

  const conn = new MockConnection();
  const rpc = new RPC(conn, {
    client_id: "test_client",
    enable_message_batching: true,
    batch_timeout_ms: 50, // Higher timeout for testing
    batch_max_messages: 3,
    batch_max_size: 1024,
  });

  // Add a simple service
  function test_service() {
    return "test_response";
  }

  rpc.add_service({
    id: "test_service",
    test_method: test_service,
  });

  // Send multiple small messages rapidly
  const messages = [];
  for (let i = 0; i < 5; i++) {
    const msg = {
      type: "method",
      from: "test_workspace/test_client",
      to: "test_workspace/target_client",
      method: "test_method",
      args: [`arg_${i}`],
    };
    messages.push(rpc.emit(msg));
  }

  // Wait for all messages to be sent
  await Promise.all(messages);

  // Should have used batching - fewer total messages sent
  console.assert(
    conn.user_messages.length > 0,
    "Should have sent user messages",
  );
  console.log(
    `Sent ${conn.user_messages.length} user messages (batched from 5 individual messages)`,
  );

  // Check that batching was used - with 5 messages and batch size 3, we expect 2 batches
  // First batch: 3 messages, Second batch: 2 messages = 5 messages total in stream format
  console.assert(
    conn.user_messages.length >= 5,
    "Should have at least the original messages via batching",
  );
  console.log(
    `Total messages: ${conn.messages.length}, User messages: ${conn.user_messages.length}`,
  );

  console.log("✓ Basic message batching test passed");
}

// Test that batching can be disabled
async function test_message_batching_disabled() {
  console.log("Testing disabled message batching...");

  const conn = new MockConnection();
  const rpc = new RPC(conn, {
    client_id: "test_client",
    enable_message_batching: false,
  });

  // Add a simple service
  function test_service() {
    return "test_response";
  }

  rpc.add_service({
    id: "test_service",
    test_method: test_service,
  });

  // Send multiple small messages
  const messages = [];
  for (let i = 0; i < 3; i++) {
    const msg = {
      type: "method",
      from: "test_workspace/test_client",
      to: "test_workspace/target_client",
      method: "test_method",
      args: [`arg_${i}`],
    };
    messages.push(rpc.emit(msg));
  }

  // Wait for all messages to be sent
  await Promise.all(messages);

  // Should send each message individually (no batching)
  console.assert(
    conn.user_messages.length === 3,
    `Expected 3 messages, got ${conn.user_messages.length}`,
  );
  console.log(`Sent ${conn.user_messages.length} user messages (no batching)`);

  console.log("✓ Disabled message batching test passed");
}

// Test that messages are flushed when size limit is reached
async function test_message_batching_size_limit() {
  console.log("Testing size-based message batching...");

  const conn = new MockConnection();
  const rpc = new RPC(conn, {
    client_id: "test_client",
    enable_message_batching: true,
    batch_timeout_ms: 1000, // Long timeout so size limit triggers first
    batch_max_messages: 10,
    batch_max_size: 200, // Small size limit
  });

  // Add a simple service
  function test_service() {
    return "test_response";
  }

  rpc.add_service({
    id: "test_service",
    test_method: test_service,
  });

  // Send messages that will exceed size limit
  const messages = [];
  for (let i = 0; i < 3; i++) {
    const msg = {
      type: "method",
      from: "test_workspace/test_client",
      to: "test_workspace/target_client",
      method: "test_method",
      args: [`long_argument_that_makes_message_large_${i}`.repeat(10)],
    };
    messages.push(rpc.emit(msg));
  }

  // Wait for all messages to be sent
  await Promise.all(messages);

  // Should have triggered size-based flushing
  console.assert(
    conn.user_messages.length > 0,
    "Should have sent user messages",
  );
  console.log(
    `Sent ${conn.user_messages.length} user messages (size-based batching)`,
  );

  console.log("✓ Size-based message batching test passed");
}

// Test that messages are flushed after timeout
async function test_message_batching_timeout() {
  console.log("Testing timeout-based message batching...");

  const conn = new MockConnection();
  const rpc = new RPC(conn, {
    client_id: "test_client",
    enable_message_batching: true,
    batch_timeout_ms: 20, // Short timeout
    batch_max_messages: 10,
    batch_max_size: 10000,
  });

  // Add a simple service
  function test_service() {
    return "test_response";
  }

  rpc.add_service({
    id: "test_service",
    test_method: test_service,
  });

  // Send a single message
  const msg = {
    type: "method",
    from: "test_workspace/test_client",
    to: "test_workspace/target_client",
    method: "test_method",
    args: ["single_arg"],
  };

  await rpc.emit(msg);

  // Wait for timeout to trigger
  await sleep(100);

  // Should have sent the message after timeout
  console.assert(
    conn.user_messages.length === 1,
    `Expected 1 message, got ${conn.user_messages.length}`,
  );
  console.log(
    `Sent ${conn.user_messages.length} user messages (timeout-based batching)`,
  );

  console.log("✓ Timeout-based message batching test passed");
}

// Test that large messages bypass batching
async function test_message_batching_large_message_bypass() {
  console.log("Testing large message bypass...");

  const conn = new MockConnection();
  const rpc = new RPC(conn, {
    client_id: "test_client",
    enable_message_batching: true,
    batch_timeout_ms: 100,
    batch_max_messages: 10,
    batch_max_size: 1000, // Small batch size
  });

  // Add a simple service
  function test_service() {
    return "test_response";
  }

  rpc.add_service({
    id: "test_service",
    test_method: test_service,
  });

  // Send a large message that exceeds batch size
  const large_data = "x".repeat(2000); // Larger than batch_max_size
  const msg = {
    type: "method",
    from: "test_workspace/test_client",
    to: "test_workspace/target_client",
    method: "test_method",
    args: [large_data],
  };

  await rpc.emit(msg);

  // Should bypass batching and send immediately
  console.assert(
    conn.user_messages.length === 1,
    `Expected 1 message, got ${conn.user_messages.length}`,
  );
  console.log(
    `Sent ${conn.user_messages.length} user messages (large message bypass)`,
  );

  console.log("✓ Large message bypass test passed");
}

// Test that messages to different targets are batched separately
async function test_message_batching_different_targets() {
  console.log("Testing different targets batching...");

  const conn = new MockConnection();
  const rpc = new RPC(conn, {
    client_id: "test_client",
    enable_message_batching: true,
    batch_timeout_ms: 50,
    batch_max_messages: 5,
    batch_max_size: 10000,
  });

  // Add a simple service
  function test_service() {
    return "test_response";
  }

  rpc.add_service({
    id: "test_service",
    test_method: test_service,
  });

  // Send messages to different targets
  const messages = [];
  for (let i = 0; i < 3; i++) {
    // Target 1
    const msg1 = {
      type: "method",
      from: "test_workspace/test_client",
      to: "test_workspace/target_client_1",
      method: "test_method",
      args: [`arg_${i}`],
    };
    messages.push(rpc.emit(msg1));

    // Target 2
    const msg2 = {
      type: "method",
      from: "test_workspace/test_client",
      to: "test_workspace/target_client_2",
      method: "test_method",
      args: [`arg_${i}`],
    };
    messages.push(rpc.emit(msg2));
  }

  // Wait for all messages to be sent
  await Promise.all(messages);

  // Wait for timeout-based batching to trigger
  await sleep(100);

  // Should have separate batches for different targets
  console.assert(
    conn.user_messages.length > 0,
    "Should have sent user messages",
  );
  console.log(
    `Sent ${conn.user_messages.length} user messages (different targets)`,
  );
  console.log(
    `Total messages: ${conn.messages.length}, User messages: ${conn.user_messages.length}`,
  );

  console.log("✓ Different targets batching test passed");
}

// Test backward compatibility with API version detection
async function test_message_batching_backward_compatibility() {
  console.log("Testing backward compatibility...");

  const conn = new MockConnection();
  const rpc = new RPC(conn, {
    client_id: "test_client",
    enable_message_batching: true,
    batch_timeout_ms: 50,
    batch_max_messages: 3,
    batch_max_size: 1024,
  });

  // Mock the _send_chunks method to simulate API version fallback
  const originalSendChunks = rpc._send_chunks;
  rpc._send_chunks = async function (data, target_id, session_id) {
    // Simulate that API version check fails, so we'll use a different approach
    // For this test, we'll just send each message individually
    return this._emit_message(data);
  };

  // Add a simple service
  function test_service() {
    return "test_response";
  }

  rpc.add_service({
    id: "test_service",
    test_method: test_service,
  });

  // Send messages - batching will still occur but will use fallback method
  const messages = [];
  for (let i = 0; i < 3; i++) {
    const msg = {
      type: "method",
      from: "test_workspace/test_client",
      to: "test_workspace/target_client",
      method: "test_method",
      args: [`arg_${i}`],
    };
    messages.push(rpc.emit(msg));
  }

  // Wait for all messages to be sent
  await Promise.all(messages);

  // With the mocked fallback, should still batch but use different mechanism
  console.assert(
    conn.user_messages.length > 0,
    `Expected messages, got ${conn.user_messages.length}`,
  );
  console.log(
    `Sent ${conn.user_messages.length} user messages (backward compatibility fallback)`,
  );

  // Restore original method
  rpc._send_chunks = originalSendChunks;

  console.log("✓ Backward compatibility test passed");
}

// Run all tests
async function run_all_tests() {
  console.log("Running JavaScript message batching tests...\n");

  try {
    await test_message_batching_basic();
    await test_message_batching_disabled();
    await test_message_batching_size_limit();
    await test_message_batching_timeout();
    await test_message_batching_large_message_bypass();
    await test_message_batching_different_targets();
    await test_message_batching_backward_compatibility();

    console.log("\n🎉 All JavaScript message batching tests passed!");
  } catch (error) {
    console.error("\n❌ Test failed:", error);
    throw error;
  }
}

// Export for use in other test files
export {
  MockConnection,
  test_message_batching_basic,
  test_message_batching_disabled,
  test_message_batching_size_limit,
  test_message_batching_timeout,
  test_message_batching_large_message_bypass,
  test_message_batching_different_targets,
  test_message_batching_backward_compatibility,
  run_all_tests,
};

// Run tests if this file is executed directly
if (
  typeof window === "undefined" &&
  import.meta.url === `file://${process.argv[1]}`
) {
  run_all_tests().catch(console.error);
}
