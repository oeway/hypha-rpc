/**
 * Regression test for the "Cannot read properties of undefined (reading 'reset')"
 * crash in _create_message / _append_message / _set_message / _process_message.
 *
 * Scenario: the sender marks message_cache calls as heartbeat=true whenever the
 * call carries a session_id. The receiver's _object_store[key] may exist without
 * a `timer` field (e.g., session created via _get_session_store but the timer
 * setup path at _encode_promise was skipped because `timer && reject && _method_timeout`
 * was false). Pre-fix, `.timer.reset()` on undefined crashed every chunk of a
 * chunked message — observed in the browser as ~40 identical TypeErrors after
 * a disconnect, and the machine appeared stuck in a disconnected state.
 *
 * Run: node tests/rpc_message_cache_heartbeat_test.mjs
 */

import assert from "node:assert/strict";
import { RPC } from "../src/rpc.js";

function makeFakeConnection() {
  return {
    _enable_reconnect: false,
    _closed: false,
    manager_id: null,
    on_connected() {},
    on_disconnected() {},
    on_message() {},
    async emit_message() {},
  };
}

let passed = 0;
let failed = 0;
function test(name, fn) {
  try {
    fn();
    console.log(`  ok ${name}`);
    passed++;
  } catch (err) {
    console.error(`  FAIL ${name}: ${err?.message || err}`);
    failed++;
  }
}

const SESSION_KEY = "session-abc";

function withRpc(body) {
  const rpc = new RPC(makeFakeConnection(), { client_id: "test-client" });
  try {
    body(rpc);
  } finally {
    rpc.disconnect?.();
  }
}

console.log("\n── _create_message heartbeat on session without timer ──\n");

test("_create_message(heartbeat=true) does not crash when session.timer is undefined", () => {
  withRpc((rpc) => {
    // Session exists but was created without a timer (mirrors the real code path
    // where _encode_promise's `if (timer && reject && _method_timeout)` branch
    // was skipped — store.timer never assigned).
    rpc._object_store[SESSION_KEY] = { target_id: "peer", _created_at: Date.now() };
    // Must not throw
    rpc._create_message(SESSION_KEY, /* heartbeat */ true, /* overwrite */ false, null);
    // Cache entry should still be created
    assert.ok(rpc._object_store["message_cache"][SESSION_KEY], "cache entry created");
  });
});

test("_append_message(heartbeat=true) does not crash when session.timer is undefined", () => {
  withRpc((rpc) => {
    rpc._object_store[SESSION_KEY] = {};
    rpc._create_message(SESSION_KEY, false, false, null);
    // Should not throw
    rpc._append_message(SESSION_KEY, new Uint8Array([1, 2, 3]), /* heartbeat */ true, null);
    assert.equal(rpc._object_store["message_cache"][SESSION_KEY].length, 1);
  });
});

test("_set_message(heartbeat=true) does not crash when session.timer is undefined", () => {
  withRpc((rpc) => {
    rpc._object_store[SESSION_KEY] = {};
    rpc._create_message(SESSION_KEY, false, false, null);
    rpc._set_message(SESSION_KEY, 0, new Uint8Array([4, 5, 6]), /* heartbeat */ true, null);
    assert.ok(rpc._object_store["message_cache"][SESSION_KEY][0]);
  });
});

test("_process_message(heartbeat=true) on session without timer does not crash on reset", () => {
  withRpc((rpc) => {
    rpc._object_store[SESSION_KEY] = {};
    // Give it a cache entry so we don't trip over the "message does not exist" check.
    rpc._create_message(SESSION_KEY, false, false, null);
    // The regression would have been a TypeError "reading 'reset'" from
    // timer being undefined — downstream errors (e.g. msgpack unpack with
    // empty data) are unrelated.
    try {
      rpc._process_message(SESSION_KEY, /* heartbeat */ true, { ws: "w" });
    } catch (err) {
      assert.ok(
        !/reading 'reset'/.test(String(err)),
        `must not be the timer-reset TypeError: ${err.message}`,
      );
    }
  });
});

console.log("\n── session absent → still throws meaningful error ──\n");

test("_create_message(heartbeat=true) throws clear error when session is gone", () => {
  withRpc((rpc) => {
    try {
      rpc._create_message("no-such-session", /* heartbeat */ true, false, null);
      assert.fail("expected throw");
    } catch (err) {
      assert.ok(
        /session does not exist anymore/.test(String(err)),
        `expected session-missing error, got: ${err.message}`,
      );
    }
  });
});

console.log("\n── timer present IS reset ──\n");

test("_create_message with real timer calls .reset()", () => {
  withRpc((rpc) => {
    let resetCount = 0;
    rpc._object_store[SESSION_KEY] = {
      timer: { reset: () => { resetCount++; } },
    };
    rpc._create_message(SESSION_KEY, true, false, null);
    assert.equal(resetCount, 1, "reset should have been called once");
  });
});

console.log("\n── Summary ──");
console.log(`  passed=${passed}  failed=${failed}`);
if (failed > 0) process.exit(1);
