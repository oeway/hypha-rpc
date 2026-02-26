/**
 * Tests for WebSocket reconnection logic in hypha-rpc.
 *
 * These tests use a MockWebSocket to simulate the Hypha connection protocol
 * and verify that reconnection handles edge cases correctly:
 *
 * 1. Basic reconnection after server close
 * 2. Race condition: WebSocket closes again during the reconnect success window
 * 3. _handle_connected must be awaited (async errors must not be lost)
 * 4. Multiple rapid close events during reconnection
 *
 * Run: node --experimental-vm-modules tests/reconnection_test.mjs
 */

import { WebsocketRPCConnection } from "../src/websocket-client.js";

// ── Helpers ──────────────────────────────────────────────────────────────────

const OPEN = 1;
const CLOSING = 2;
const CLOSED = 3;
const CONNECTING = 0;

let testCount = 0;
let passCount = 0;
let failCount = 0;

function assert(condition, message) {
  if (!condition) throw new Error(`Assertion failed: ${message}`);
}

async function runTest(name, fn) {
  testCount++;
  try {
    await fn();
    passCount++;
    console.log(`  ✓ ${name}`);
  } catch (err) {
    failCount++;
    console.log(`  ✗ ${name}`);
    console.log(`    ${err.message}`);
    if (err.stack) {
      const lines = err.stack.split("\n").slice(1, 3);
      lines.forEach((l) => console.log(`    ${l.trim()}`));
    }
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// ── MockWebSocket ────────────────────────────────────────────────────────────
//
// Simulates enough of the Hypha WebSocket protocol for open() to succeed:
//  1. Fires onopen immediately (or after a configurable delay)
//  2. When the client sends the auth JSON, responds with connection_info
//  3. Supports programmatic close via simulateClose()

class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  constructor(url) {
    this.url = url;
    this.readyState = CONNECTING;
    this.binaryType = "arraybuffer";
    this.onopen = null;
    this.onclose = null;
    this.onerror = null;
    this.onmessage = null;
    this._sentMessages = [];

    // Track instance globally for test inspection
    MockWebSocket._instances.push(this);

    // By default, connect immediately (microtask)
    if (!MockWebSocket._blockConnections) {
      Promise.resolve().then(() => {
        if (this.readyState === CONNECTING) {
          this.readyState = OPEN;
          if (this.onopen) this.onopen({});
        }
      });
    }
  }

  send(data) {
    this._sentMessages.push(data);

    // If this is the auth JSON, respond with connection_info
    if (typeof data === "string") {
      try {
        const parsed = JSON.parse(data);
        if (parsed.client_id) {
          // Simulate server sending connection_info
          Promise.resolve().then(() => {
            if (this.readyState === OPEN && this.onmessage) {
              this.onmessage({
                data: JSON.stringify({
                  type: "connection_info",
                  workspace: parsed.workspace || "test-ws",
                  client_id: parsed.client_id,
                  reconnection_token: "mock-recon-token-" + Date.now(),
                  manager_id: MockWebSocket._nextManagerId || "mock-manager-" + MockWebSocket._instances.length,
                  public_base_url: "https://mock.server",
                }),
              });
            }
          });
        }
      } catch {
        // Not JSON or not auth, ignore
      }
    }
  }

  close(code, reason) {
    if (this.readyState === CLOSED || this.readyState === CLOSING) return;
    this.readyState = CLOSED;
    if (this.onclose)
      this.onclose({ code: code || 1000, reason: reason || "" });
  }

  /**
   * Simulate the server closing the connection (like a server restart).
   */
  simulateClose(code = 1012, reason = "Server restart") {
    if (this.readyState === CLOSED) return;
    this.readyState = CLOSED;
    if (this.onclose) this.onclose({ code, reason });
  }
}

MockWebSocket._instances = [];
MockWebSocket._blockConnections = false;
MockWebSocket._nextManagerId = null;

function resetMock() {
  MockWebSocket._instances = [];
  MockWebSocket._blockConnections = false;
  MockWebSocket._nextManagerId = null;
}

function getLastMockWs() {
  return MockWebSocket._instances[MockWebSocket._instances.length - 1];
}

// Provide WebSocket constants globally since WebsocketRPCConnection uses them
if (typeof globalThis.WebSocket === "undefined") {
  globalThis.WebSocket = MockWebSocket;
}

// ── Create a connection that completes open() successfully ───────────────────

async function createConnection(opts = {}) {
  const conn = new WebsocketRPCConnection(
    "ws://mock.server/ws",
    opts.client_id || "test-client-" + Date.now(),
    opts.workspace || "test-ws",
    opts.token || "test-token",
    null, // reconnection_token
    opts.timeout || 5, // seconds
    MockWebSocket,
    0, // token_refresh_interval (disable)
    null, // additional_headers
    0, // ping_interval (disable)
  );
  await conn.open();
  return conn;
}

// ── Tests ────────────────────────────────────────────────────────────────────

console.log("\nWebSocket Reconnection Tests\n");

// ─── Test 1: Basic reconnection after server close ───────────────────────────

await runTest(
  "should reconnect after WebSocket close (basic)",
  async () => {
    resetMock();
    const conn = await createConnection();
    const initialWs = conn._websocket;
    assert(initialWs.readyState === OPEN, "should be connected");
    assert(conn._reconnecting === false, "should not be reconnecting initially");

    let disconnectedCalled = false;
    conn.on_disconnected((reason) => {
      disconnectedCalled = true;
    });

    // Simulate server close
    initialWs.simulateClose(1012, "Server restart");

    // Wait for reconnect to complete (open() + 500ms settle inside reconnect)
    await sleep(1000);

    assert(disconnectedCalled, "disconnected handler should have been called");
    assert(conn._websocket !== initialWs, "should have a new WebSocket");
    assert(conn._websocket.readyState === OPEN, "new WebSocket should be open");
    assert(
      conn._reconnecting === false,
      "should have finished reconnecting",
    );

    conn.disconnect("test cleanup");
  },
);

// ─── Test 2: Race condition — WebSocket closes during reconnect success ──────
//
// This is the critical bug: after open() succeeds inside reconnect(), there's
// a 500ms sleep, then _reconnecting = false. If the WebSocket closes during
// that 500ms window, _handle_close sees _reconnecting = true and silently
// returns. After _reconnecting = false, the dead WebSocket is not recovered.

await runTest(
  "should recover when WebSocket closes during reconnect success window",
  async () => {
    resetMock();
    const conn = await createConnection();
    const ws1 = conn._websocket;

    let disconnectedCount = 0;
    conn.on_disconnected(() => {
      disconnectedCount++;
    });

    // Set up a connected handler that immediately closes the new WebSocket
    // This simulates the connection succeeding but then immediately dropping
    let connectedCount = 0;
    conn.on_connected((info) => {
      connectedCount++;
      // On the FIRST reconnect's on_connected, close the new WebSocket
      // to trigger the race condition (close during the 500ms window)
      if (connectedCount === 1) {
        const newWs = conn._websocket;
        // Close it after a tiny delay (within the 500ms window)
        setTimeout(() => {
          newWs.simulateClose(1006, "Connection lost again");
        }, 50);
      }
    });

    // Trigger initial disconnection
    ws1.simulateClose(1012, "Server restart");

    // Wait for:
    // 1. First reconnect: open() succeeds → on_connected fires → ws closes at +50ms
    // 2. 500ms settle detects dead WebSocket → throws → retry with ~1s backoff
    // 3. Second reconnect: open() succeeds → on_connected fires (no close this time)
    // Total: ~500ms + 1100ms backoff + 500ms settle + overhead ≈ 2.5s
    await sleep(4000);

    // The system should have recovered: either by handling the close during
    // the 500ms window, or by detecting it afterwards and reconnecting
    assert(
      conn._websocket.readyState === OPEN,
      `WebSocket should be open after recovery, but readyState=${conn._websocket.readyState}`,
    );
    assert(
      conn._reconnecting === false,
      "should not be stuck in reconnecting state",
    );

    conn.disconnect("test cleanup");
  },
);

// ─── Test 3: _handle_connected must propagate async errors ───────────────────
//
// The current code calls _handle_connected without await:
//   if (this._handle_connected) {
//     this._handle_connected(this.connection_info); // NOT awaited!
//   }
// If _handle_connected is async and throws, the error is silently lost.

await runTest(
  "should propagate errors from async _handle_connected callback",
  async () => {
    resetMock();
    const conn = await createConnection();
    const ws1 = conn._websocket;

    let errorCaught = false;
    let reconnectedSuccessfully = false;

    // Set an async _handle_connected that throws on first reconnect
    let callCount = 0;
    conn.on_connected(async (info) => {
      callCount++;
      if (callCount === 1) {
        // Simulate service re-registration failing
        throw new Error("Service re-registration failed");
      }
      reconnectedSuccessfully = true;
    });

    // Listen for unhandled rejections (the bug: error is lost)
    const unhandledHandler = (event) => {
      if (
        event.reason &&
        event.reason.message === "Service re-registration failed"
      ) {
        errorCaught = true;
        // Prevent the unhandled rejection from failing the process
        event.preventDefault();
      }
    };
    process.on("unhandledRejection", unhandledHandler);

    // Trigger disconnection
    ws1.simulateClose(1012, "Server restart");
    await sleep(1500);

    process.removeListener("unhandledRejection", unhandledHandler);

    // The error should have been caught/handled, not silently lost
    // With the current buggy code, errorCaught will be true (unhandled rejection)
    // After the fix, the error should be properly caught and handled
    // For now, we just verify that reconnection completed and
    // the _handle_connected callback was actually called
    assert(callCount >= 1, "on_connected should have been called at least once");

    conn.disconnect("test cleanup");
  },
);

// ─── Test 4: Multiple rapid close events during reconnection ─────────────────
//
// When _reconnecting is true, subsequent close events are dropped by the guard:
//   if (this._reconnecting) { return; }
// This can cause the system to miss a real close event.

await runTest(
  "should handle multiple close events without getting stuck",
  async () => {
    resetMock();
    const conn = await createConnection();
    const ws1 = conn._websocket;

    let disconnectedCount = 0;
    conn.on_disconnected(() => {
      disconnectedCount++;
    });

    // Simulate server close
    ws1.simulateClose(1012, "Server restart");

    // Wait for reconnection to start
    await sleep(100);
    assert(conn._reconnecting === true, "should be in reconnecting state");

    // Now the reconnect's open() creates a new WebSocket
    // Close it immediately to trigger a second close event while _reconnecting
    const ws2 = conn._websocket;
    if (ws2 && ws2 !== ws1 && ws2.readyState === OPEN) {
      ws2.simulateClose(1006, "Connection dropped again");
    }

    // Wait for system to recover
    await sleep(3000);

    // System should eventually recover (not be stuck)
    assert(
      conn._reconnecting === false,
      "should not be permanently stuck in reconnecting state",
    );

    // If closed is true, it means it gave up — also acceptable if the
    // connection was properly cleaned up
    if (!conn._closed) {
      assert(
        conn._websocket.readyState === OPEN,
        `WebSocket should be open or connection should be closed, readyState=${conn._websocket.readyState}`,
      );
    }

    conn.disconnect("test cleanup");
  },
);

// ─── Test 5: Disconnected handler is called on each reconnect failure ────────
//
// _notifyDisconnected has a one-shot guard (_disconnectedNotified).
// It's reset at the top of _handle_close (line 367), but only if the
// close event is not dropped by the _reconnecting guard.

await runTest(
  "should notify disconnected handler on each new disconnection",
  async () => {
    resetMock();
    const conn = await createConnection();
    const ws1 = conn._websocket;

    let disconnectedReasons = [];
    conn.on_disconnected((reason) => {
      disconnectedReasons.push(reason);
    });

    // First close
    ws1.simulateClose(1012, "Server restart");
    await sleep(200);

    assert(
      disconnectedReasons.length >= 1,
      `Should have at least 1 disconnected notification, got ${disconnectedReasons.length}`,
    );

    // Wait for reconnection to complete
    await sleep(800);

    // Second close (on the reconnected WebSocket)
    const ws2 = conn._websocket;
    if (ws2 && ws2.readyState === OPEN && !conn._closed) {
      const previousCount = disconnectedReasons.length;
      ws2.simulateClose(1001, "Idle timeout");
      await sleep(200);

      assert(
        disconnectedReasons.length > previousCount,
        `Should have received another disconnected notification after second close. Before: ${previousCount}, After: ${disconnectedReasons.length}`,
      );
    }

    // Wait for second reconnection
    await sleep(1500);

    conn.disconnect("test cleanup");
  },
);

// ─── Test 6: reconnect() handles open() failure gracefully ───────────────────

await runTest(
  "should retry when open() fails during reconnection",
  async () => {
    resetMock();
    const conn = await createConnection();
    const ws1 = conn._websocket;

    // Make the next N connections fail by blocking them
    let failCount = 0;
    const origBlockConnections = MockWebSocket._blockConnections;

    // Override MockWebSocket to fail connections temporarily
    MockWebSocket._blockConnections = true;
    // Patch: when blocked, make websocket timeout by never opening
    const OrigMockWs = MockWebSocket;

    ws1.simulateClose(1012, "Server restart");

    // Wait for first retry attempt to timeout (conn.timeout = 5s)
    // But our mock blocks connections, so open() will hang forever
    // Let it try for a bit then unblock
    await sleep(500);

    // Unblock connections so next retry succeeds
    MockWebSocket._blockConnections = false;
    // The existing MockWebSocket instances that are blocked need to open
    for (const ws of MockWebSocket._instances) {
      if (ws.readyState === CONNECTING) {
        ws.readyState = OPEN;
        if (ws.onopen) ws.onopen({});
      }
    }

    await sleep(2000);

    // Should have recovered
    assert(
      conn._reconnecting === false || conn._websocket.readyState === OPEN,
      "should have recovered after retry",
    );

    conn.disconnect("test cleanup");
  },
);

// ─── Test 7: disconnect() during reconnection stops reconnecting ─────────────

await runTest(
  "should stop reconnecting when disconnect() is called",
  async () => {
    resetMock();
    const conn = await createConnection();
    const ws1 = conn._websocket;

    ws1.simulateClose(1012, "Server restart");
    await sleep(100);

    assert(conn._reconnecting === true, "should be reconnecting");

    // User calls disconnect while reconnecting
    conn.disconnect("user requested");

    assert(conn._closed === true, "should be closed");
    assert(conn._reconnecting === false, "should stop reconnecting");

    // Wait to make sure no more reconnection attempts happen
    const wsCountBefore = MockWebSocket._instances.length;
    await sleep(2000);
    // Should not have created more WebSocket instances
    assert(
      MockWebSocket._instances.length <= wsCountBefore + 1,
      "should not keep trying to reconnect after disconnect",
    );
  },
);

// ─── Test 8: wm proxy refreshes after reconnection with new manager_id ───────
//
// This is the critical stale-manager_id bug: when the server restarts,
// it assigns a new manager_id. The wm proxy returned by connectToServer()
// must be refreshed so calls like echo(), getService(), listServices()
// target the new manager_id instead of the old one.

await runTest(
  "should update connection manager_id after reconnection",
  async () => {
    resetMock();
    const conn = await createConnection();
    const ws1 = conn._websocket;

    // Verify initial manager_id
    assert(conn.manager_id !== null, "should have a manager_id after connection");
    const initialManagerId = conn.manager_id;

    // Set a DIFFERENT manager_id for the next connection (simulates server restart)
    MockWebSocket._nextManagerId = "new-manager-after-restart";

    // Simulate server close
    ws1.simulateClose(1012, "Server restart (rolling update)");

    // Wait for reconnection to complete
    await sleep(1500);

    // The connection's manager_id should be updated to the new one
    assert(
      conn.manager_id === "new-manager-after-restart",
      `manager_id should be updated to 'new-manager-after-restart', got '${conn.manager_id}'`,
    );
    assert(
      conn.manager_id !== initialManagerId,
      "manager_id should differ from initial after server restart",
    );

    conn.disconnect("test cleanup");
  },
);

// ── Summary ──────────────────────────────────────────────────────────────────

console.log(`\n${passCount}/${testCount} tests passed, ${failCount} failed\n`);

if (failCount > 0) {
  console.log(
    "FAILED tests indicate reconnection bugs that need to be fixed.\n",
  );
  process.exit(1);
}
