/**
 * Unit test for rpc.js session cleanup race condition fix.
 *
 * Tests that pending RPC calls are rejected when reconnection permanently fails
 * (_closed=true), even though _enable_reconnect is still true.
 *
 * Run with: node tests/rpc_session_cleanup_test.mjs
 */

import assert from "node:assert/strict";
import { RPC } from "../src/rpc.js";

// Minimal fake connection matching the interface asserted in RPC constructor
function makeFakeConnection({ enableReconnect = false, closed = false } = {}) {
  let _connectedHandler = null;
  let _disconnectedHandler = null;
  return {
    _enable_reconnect: enableReconnect,
    _closed: closed,
    manager_id: null,
    on_connected(cb) {
      _connectedHandler = cb;
    },
    on_disconnected(handler) {
      _disconnectedHandler = handler;
    },
    on_message() {},
    async emit_message() {},
    // Helpers for tests
    connect() {
      if (_connectedHandler) _connectedHandler(null);
    },
    triggerDisconnect(reason) {
      if (_disconnectedHandler) _disconnectedHandler(reason);
    },
  };
}

let passed = 0;
let failed = 0;

async function test(name, fn) {
  try {
    await fn();
    console.log(`  ✓ ${name}`);
    passed++;
  } catch (e) {
    console.error(`  ✗ ${name}`);
    console.error(`    ${e.message}`);
    failed++;
  }
}

console.log("RPC session cleanup on disconnect:");

await test(
  "pending call is rejected when _closed=true even if _enable_reconnect=true",
  async () => {
    const conn = makeFakeConnection({ enableReconnect: true, closed: false });
    const rpc = new RPC(conn, {
      client_id: "test-client-1",
      silent: true,
    });
    conn.connect();

    // Enqueue a fake pending call
    let rejectCalled = false;
    let rejectReason = null;
    rpc._object_store["fake-session-1"] = {
      resolve: () => {},
      reject: (reason) => {
        rejectCalled = true;
        rejectReason = String(reason);
      },
      timer: null,
      heartbeat: null,
    };

    // Simulate: reconnection permanently failed → _closed flips to true
    conn._closed = true;
    conn.triggerDisconnect("Max reconnection attempts exceeded");

    assert.ok(rejectCalled, "Pending call should have been rejected");
    assert.ok(
      rejectReason && rejectReason.includes("Connection lost"),
      `Reject reason should mention connection lost, got: ${rejectReason}`
    );

    clearInterval(rpc._sessionSweepInterval);
  }
);

await test(
  "pending call is NOT rejected during transient disconnect when reconnect enabled and not closed",
  async () => {
    const conn = makeFakeConnection({ enableReconnect: true, closed: false });
    const rpc = new RPC(conn, {
      client_id: "test-client-2",
      silent: true,
    });
    conn.connect();

    let rejectCalled = false;
    rpc._object_store["fake-session-2"] = {
      resolve: () => {},
      reject: () => { rejectCalled = true; },
      timer: null,
      heartbeat: null,
    };

    // Transient disconnect — _closed is still false
    conn._closed = false;
    conn.triggerDisconnect("network blip");

    assert.ok(
      !rejectCalled,
      "Pending call should NOT be rejected during transient disconnect"
    );

    clearInterval(rpc._sessionSweepInterval);
  }
);

await test(
  "pending call is rejected immediately when reconnect is disabled",
  async () => {
    const conn = makeFakeConnection({ enableReconnect: false, closed: false });
    const rpc = new RPC(conn, {
      client_id: "test-client-3",
      silent: true,
    });
    conn.connect();

    let rejectCalled = false;
    rpc._object_store["fake-session-3"] = {
      resolve: () => {},
      reject: () => { rejectCalled = true; },
      timer: null,
      heartbeat: null,
    };

    conn.triggerDisconnect("server closed");

    assert.ok(
      rejectCalled,
      "Pending call should be rejected when reconnect disabled"
    );

    clearInterval(rpc._sessionSweepInterval);
  }
);

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
