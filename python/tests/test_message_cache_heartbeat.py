"""Regression test for the "Cannot read properties of undefined (reading 'reset')"
crash in _create_message / _append_message / _set_message / _process_message.

Scenario: the sender marks message_cache calls as heartbeat=True whenever the
call carries a session_id. The receiver's _object_store[key] may exist without
a `timer` field (e.g., session created via _get_session_store but the timer
setup path at _encode_promise was skipped because
`timer and reject and _method_timeout` was false). Pre-fix, `.timer.reset()`
on a missing key crashed every chunk of a chunked message — observed in the
browser as ~40 identical TypeErrors after a disconnect.
"""

import os
import sys

import msgpack
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypha_rpc.rpc import RPC
from hypha_rpc.utils import MessageEmitter


class _StubConnection(MessageEmitter):
    """Minimal connection stub — enough for RPC.__init__ to complete."""

    def __init__(self):
        super().__init__(None)
        self.manager_id = None

    def on_message(self, handler):
        pass

    def on_connected(self, handler):
        pass

    def on_disconnected(self, handler):
        pass

    async def emit_message(self, data):
        pass


@pytest.fixture
def rpc():
    conn = _StubConnection()
    instance = RPC(
        conn,
        client_id="test-client",
        name="test",
        workspace="test-workspace",
        server_base_url="http://localhost",
        silent=True,
    )
    yield instance


SESSION_KEY = "session-abc"


def test_create_message_heartbeat_without_timer(rpc):
    """Session exists but has no timer — must not crash."""
    rpc._object_store[SESSION_KEY] = {"target_id": "peer"}
    rpc._create_message(SESSION_KEY, heartbeat=True, overwrite=False)
    assert SESSION_KEY in rpc._object_store["message_cache"]


def test_append_message_heartbeat_without_timer(rpc):
    rpc._object_store[SESSION_KEY] = {}
    rpc._create_message(SESSION_KEY, heartbeat=False, overwrite=False)
    rpc._append_message(SESSION_KEY, b"\x01\x02\x03", heartbeat=True)
    assert len(rpc._object_store["message_cache"][SESSION_KEY]) == 1


def test_set_message_heartbeat_without_timer(rpc):
    rpc._object_store[SESSION_KEY] = {}
    rpc._create_message(SESSION_KEY, heartbeat=False, overwrite=False)
    rpc._set_message(SESSION_KEY, 0, b"\x04\x05\x06", heartbeat=True)
    assert rpc._object_store["message_cache"][SESSION_KEY][0] == b"\x04\x05\x06"


def test_process_message_heartbeat_without_timer(rpc):
    """Must not raise an AttributeError about `.reset` — downstream msgpack
    errors on empty data are unrelated to this regression."""
    rpc._object_store[SESSION_KEY] = {}
    # Feed a valid msgpack payload so we only exercise the heartbeat path.
    payload = msgpack.packb({"type": "noop"})
    rpc._create_message(SESSION_KEY, heartbeat=False, overwrite=False)
    rpc._set_message(SESSION_KEY, 0, payload, heartbeat=False)
    try:
        rpc._process_message(SESSION_KEY, heartbeat=True, context={"ws": "w"})
    except AttributeError as err:
        pytest.fail(f"regression: timer.reset AttributeError re-surfaced: {err}")
    except Exception:
        # Downstream processing may still fail for other reasons; we only care
        # that the timer-reset crash is gone.
        pass


def test_create_message_session_missing_still_raises(rpc):
    """Guard must still raise a meaningful error when the session is gone."""
    with pytest.raises(Exception, match="session does not exist anymore"):
        rpc._create_message("no-such-session", heartbeat=True)


def test_create_message_with_real_timer_calls_reset(rpc):
    """Happy path: a real timer's reset() is still called."""

    class FakeTimer:
        def __init__(self):
            self.count = 0

        def reset(self):
            self.count += 1

    timer = FakeTimer()
    rpc._object_store[SESSION_KEY] = {"timer": timer}
    rpc._create_message(SESSION_KEY, heartbeat=True, overwrite=False)
    assert timer.count == 1
