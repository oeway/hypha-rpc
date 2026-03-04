"""
Tests for session cleanup race conditions in hypha-rpc.

Issue: pending RPC calls were not cancelled when WebSocket permanently
disconnects (all reconnect attempts exhausted) — callers hung forever
and per-call timers leaked.
"""
import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock


# ---------------------------------------------------------------------------
# Minimal stub for the connection object expected by RPC
# ---------------------------------------------------------------------------

class _FakeConnection:
    """Minimal fake connection that exposes the attributes RPC inspects."""

    def __init__(self, *, enable_reconnect=False, closed=False, manager_id="test-manager"):
        self.manager_id = manager_id
        self._enable_reconnect = enable_reconnect
        self._closed = closed
        self._disconnected_handler = None
        self._connected_handler = None
        self._message_handler = None

    # ------------------------------------------------------------------
    # Connection protocol expected by RPC.__init__
    # ------------------------------------------------------------------
    def on_disconnected(self, handler):
        self._disconnected_handler = handler

    def on_connected(self, handler):
        self._connected_handler = handler

    def on_message(self, handler):
        self._message_handler = handler

    async def emit_message(self, data):
        pass  # no-op

    # ------------------------------------------------------------------
    # Helpers for tests
    # ------------------------------------------------------------------
    def simulate_disconnect(self, reason="simulated drop", closed=True):
        """Fire the disconnect callback as the websocket layer would."""
        self._closed = closed
        if self._disconnected_handler:
            self._disconnected_handler(reason)


# ---------------------------------------------------------------------------
# Helper: build a minimal RPC instance without a real connection
# ---------------------------------------------------------------------------

def _make_rpc(connection, loop):
    """Return an RPC instance wired to *connection* on *loop*."""
    from hypha_rpc.rpc import RPC

    rpc = RPC(
        connection=connection,
        client_id="test-client",
        workspace="test-ws",
        loop=loop,
        silent=True,
    )
    return rpc


def _inject_pending_call(rpc, session_key="call-1"):
    """Inject a fake pending outbound RPC call into the object store.

    Returns the future that the 'caller' is waiting on so the test can
    verify it gets rejected.
    """
    loop = rpc.loop
    fut = loop.create_future()

    def _resolve(value):
        if not fut.done():
            fut.set_result(value)

    def _reject(exc):
        if not fut.done():
            fut.set_exception(exc)

    # Simulate a Timer with a started flag
    fake_timer = MagicMock()
    fake_timer.started = True
    fake_timer.clear = MagicMock()

    # Simulate a heartbeat task (asyncio.Task-like)
    fake_heartbeat = MagicMock()
    fake_heartbeat.done = MagicMock(return_value=False)
    fake_heartbeat.cancel = MagicMock()

    rpc._object_store[session_key] = {
        "resolve": _resolve,
        "reject": _reject,
        "timer": fake_timer,
        "heartbeat_task": fake_heartbeat,
        "target_id": "remote-peer",
        "_created_at": __import__("time").time(),
    }
    return fut, fake_timer, fake_heartbeat


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSessionCleanupOnDisconnect(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()

    def tearDown(self):
        self.loop.close()

    # ------------------------------------------------------------------
    # 1. Without reconnect: disconnect should immediately reject calls
    # ------------------------------------------------------------------
    def test_pending_calls_rejected_when_reconnect_disabled(self):
        """Pending calls must be rejected immediately on first disconnect
        when _enable_reconnect is False."""
        conn = _FakeConnection(enable_reconnect=False, closed=False)
        rpc = _make_rpc(conn, self.loop)

        fut, timer, heartbeat = _inject_pending_call(rpc, "call-no-reconnect")

        conn.simulate_disconnect(reason="server-closed", closed=True)

        self.assertTrue(fut.done(), "Future must be resolved after disconnect")
        self.assertIsInstance(
            fut.exception(), ConnectionError,
            "Future must be rejected with ConnectionError",
        )
        timer.clear.assert_called_once()
        heartbeat.cancel.assert_called_once()

    # ------------------------------------------------------------------
    # 2. With reconnect ACTIVE: calls should NOT be rejected yet
    # ------------------------------------------------------------------
    def test_pending_calls_not_rejected_during_active_reconnect(self):
        """While reconnect is in progress (_closed=False), pending calls
        should NOT be rejected so they can succeed after reconnect."""
        conn = _FakeConnection(enable_reconnect=True, closed=False)
        rpc = _make_rpc(conn, self.loop)

        fut, timer, heartbeat = _inject_pending_call(rpc, "call-reconnecting")

        # Simulate transient disconnect with reconnect still in progress
        conn.simulate_disconnect(reason="transient-drop", closed=False)

        self.assertFalse(fut.done(), "Future must NOT be resolved while reconnecting")
        timer.clear.assert_not_called()
        heartbeat.cancel.assert_not_called()

    # ------------------------------------------------------------------
    # 3. With reconnect EXHAUSTED: calls MUST be rejected
    # ------------------------------------------------------------------
    def test_pending_calls_rejected_when_reconnect_permanently_fails(self):
        """When all reconnect retries are exhausted (_closed=True but
        _enable_reconnect still True), pending calls must be rejected to
        prevent callers from hanging forever."""
        conn = _FakeConnection(enable_reconnect=True, closed=False)
        rpc = _make_rpc(conn, self.loop)

        fut, timer, heartbeat = _inject_pending_call(rpc, "call-max-retry")

        # First disconnect: reconnect in progress — calls stay pending
        conn.simulate_disconnect(reason="first-drop", closed=False)
        self.assertFalse(fut.done(), "Future must not be settled during reconnect")

        # Second call: reconnect permanently failed (_closed=True, but
        # _enable_reconnect is still True — this is exactly the bug condition)
        conn.simulate_disconnect(reason="Max reconnection attempts exceeded", closed=True)

        self.assertTrue(fut.done(), "Future must be settled after permanent disconnect")
        self.assertIsInstance(
            fut.exception(), ConnectionError,
            "Future must be rejected with ConnectionError after permanent disconnect",
        )
        timer.clear.assert_called_once()
        heartbeat.cancel.assert_called_once()

    # ------------------------------------------------------------------
    # 4. Timer leak check: no dangling timer after rejection
    # ------------------------------------------------------------------
    def test_no_timer_leak_after_permanent_disconnect(self):
        """Per-call timers must be cleared when the connection is
        permanently closed so they cannot fire after the call is gone."""
        conn = _FakeConnection(enable_reconnect=True, closed=False)
        rpc = _make_rpc(conn, self.loop)

        _, timer1, _ = _inject_pending_call(rpc, "call-A")
        _, timer2, _ = _inject_pending_call(rpc, "call-B")

        # Permanent disconnect
        conn.simulate_disconnect(reason="gone", closed=True)

        timer1.clear.assert_called_once()
        timer2.clear.assert_called_once()

    # ------------------------------------------------------------------
    # 5. Server-refused-reconnect path (ConnectionAbortedError branch)
    # ------------------------------------------------------------------
    def test_pending_calls_rejected_when_server_refuses_reconnect(self):
        """If the server refuses to reconnect the _closed flag is set True
        and the disconnect callback is fired again.  Calls must be rejected."""
        conn = _FakeConnection(enable_reconnect=True, closed=False)
        rpc = _make_rpc(conn, self.loop)

        fut, timer, heartbeat = _inject_pending_call(rpc, "call-refused")

        # Mimic websocket_client.py line 555-567: _closed=True, then
        # _handle_disconnected called with reconnect still enabled.
        conn._closed = True
        if conn._disconnected_handler:
            conn._disconnected_handler("Server refused reconnection")

        self.assertTrue(fut.done())
        self.assertIsInstance(fut.exception(), ConnectionError)
        timer.clear.assert_called_once()


if __name__ == "__main__":
    unittest.main()
