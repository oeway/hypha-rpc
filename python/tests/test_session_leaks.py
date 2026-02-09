"""Tests for session memory leak prevention.

Verifies that:
1. _target_id_index stays consistent with _object_store across all deletion paths
2. Sessions are properly cleaned up on timeout, disconnect, and normal completion
3. No stale entries accumulate in the index
"""

import asyncio
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypha_rpc.rpc import RPC
from hypha_rpc.utils import MessageEmitter


class DummyConnection(MessageEmitter):
    """Minimal connection stub for testing."""

    def __init__(self):
        super().__init__(None)
        self.manager_id = None
        self._on_message_handler = None

    def on_message(self, handler):
        self._on_message_handler = handler

    def on_connected(self, handler):
        pass

    def on_disconnected(self, handler):
        pass

    async def emit_message(self, data):
        pass

    async def disconnect(self, reason=None):
        pass


def create_rpc():
    """Create an RPC instance for testing."""
    conn = DummyConnection()
    rpc = RPC(
        conn,
        client_id="test-client",
        name="test",
        workspace="test-workspace",
        server_base_url="http://localhost",
    )
    return rpc


def _count_sessions(rpc):
    """Count non-system top-level keys in _object_store."""
    return sum(1 for k in rpc._object_store if k not in ("services", "message_cache"))


def _count_index_entries(rpc):
    """Count total session keys across all target_id index entries."""
    return sum(len(v) for v in rpc._target_id_index.values())


# ─── Test _remove_from_target_id_index ────────────────────────────────


class TestRemoveFromTargetIdIndex:
    def test_removes_session_from_index(self):
        rpc = create_rpc()
        # Create a session with target_id
        store = rpc._get_session_store("sess-1", create=True)
        store["target_id"] = "client-A"
        rpc._target_id_index["client-A"] = {"sess-1"}

        rpc._remove_from_target_id_index("sess-1")
        assert "client-A" not in rpc._target_id_index

    def test_removes_one_of_many_sessions(self):
        rpc = create_rpc()
        # Two sessions for same target
        for sid in ["sess-1", "sess-2"]:
            store = rpc._get_session_store(sid, create=True)
            store["target_id"] = "client-A"
        rpc._target_id_index["client-A"] = {"sess-1", "sess-2"}

        rpc._remove_from_target_id_index("sess-1")
        assert rpc._target_id_index["client-A"] == {"sess-2"}

    def test_noop_for_missing_session(self):
        rpc = create_rpc()
        # Should not raise
        rpc._remove_from_target_id_index("nonexistent")
        assert rpc._target_id_index == {}

    def test_noop_for_session_without_target_id(self):
        rpc = create_rpc()
        store = rpc._get_session_store("sess-1", create=True)
        # No target_id set
        rpc._remove_from_target_id_index("sess-1")
        assert rpc._target_id_index == {}

    def test_handles_nested_session_id(self):
        rpc = create_rpc()
        # Create top-level session with target_id
        store = rpc._get_session_store("parent", create=True)
        store["target_id"] = "client-B"
        rpc._target_id_index["client-B"] = {"parent"}
        # Create nested session
        rpc._get_session_store("parent.child", create=True)

        # Removing nested session should clean up the top-level key
        rpc._remove_from_target_id_index("parent.child")
        assert "client-B" not in rpc._target_id_index


# ─── Test _cleanup_sessions_for_client with index ─────────────────────


class TestCleanupSessionsForClient:
    def test_cleans_up_matching_sessions(self):
        rpc = create_rpc()
        for i in range(10):
            sid = f"sess-{i}"
            store = rpc._get_session_store(sid, create=True)
            target = f"client-{i % 3}"
            store["target_id"] = target
            if target not in rpc._target_id_index:
                rpc._target_id_index[target] = set()
            rpc._target_id_index[target].add(sid)

        # Clean up client-0 sessions (sess-0, sess-3, sess-6, sess-9)
        cleaned = rpc._cleanup_sessions_for_client("client-0")
        assert cleaned == 4
        assert "client-0" not in rpc._target_id_index
        # Other clients untouched
        assert "client-1" in rpc._target_id_index
        assert "client-2" in rpc._target_id_index

    def test_returns_zero_for_unknown_client(self):
        rpc = create_rpc()
        cleaned = rpc._cleanup_sessions_for_client("nonexistent")
        assert cleaned == 0

    def test_index_empty_after_cleaning_all(self):
        rpc = create_rpc()
        for i in range(5):
            sid = f"sess-{i}"
            store = rpc._get_session_store(sid, create=True)
            store["target_id"] = "client-X"
            if "client-X" not in rpc._target_id_index:
                rpc._target_id_index["client-X"] = set()
            rpc._target_id_index["client-X"].add(sid)

        rpc._cleanup_sessions_for_client("client-X")
        assert rpc._target_id_index == {}
        assert _count_sessions(rpc) == 0


# ─── Test _delete_session_completely cleans index ─────────────────────


class TestDeleteSessionCompletelyIndex:
    def test_top_level_session_removes_index(self):
        rpc = create_rpc()
        store = rpc._get_session_store("sess-1", create=True)
        store["target_id"] = "client-A"
        rpc._target_id_index["client-A"] = {"sess-1"}

        rpc._delete_session_completely("sess-1")

        assert "sess-1" not in rpc._object_store
        assert "client-A" not in rpc._target_id_index

    def test_nested_session_removes_index(self):
        rpc = create_rpc()
        # Create parent session with target_id
        parent_store = rpc._get_session_store("parent", create=True)
        parent_store["target_id"] = "client-A"
        rpc._target_id_index["client-A"] = {"parent"}
        # Create nested session
        rpc._get_session_store("parent.child", create=True)

        rpc._delete_session_completely("parent.child")

        # Parent's target_id index should be cleaned
        assert "client-A" not in rpc._target_id_index

    def test_multiple_sessions_same_target_partial_delete(self):
        rpc = create_rpc()
        for sid in ["sess-1", "sess-2", "sess-3"]:
            store = rpc._get_session_store(sid, create=True)
            store["target_id"] = "client-A"
        rpc._target_id_index["client-A"] = {"sess-1", "sess-2", "sess-3"}

        rpc._delete_session_completely("sess-2")

        assert rpc._target_id_index["client-A"] == {"sess-1", "sess-3"}
        assert "sess-2" not in rpc._object_store

    def test_already_deleted_session_no_error(self):
        rpc = create_rpc()
        # Should not raise
        rpc._delete_session_completely("nonexistent")
        assert rpc._target_id_index == {}


# ─── Test _delete_session_safely cleans index ─────────────────────────


class TestDeleteSessionSafelyIndex:
    def test_cleans_index_on_fallback_delete(self):
        rpc = create_rpc()
        store = rpc._get_session_store("sess-1", create=True)
        store["target_id"] = "client-A"
        rpc._target_id_index["client-A"] = {"sess-1"}

        rpc._delete_session_safely("sess-1")

        assert "sess-1" not in rpc._object_store
        assert "client-A" not in rpc._target_id_index


# ─── Test timeout path cleans index ──────────────────────────────────


class TestTimeoutCleansIndex:
    def test_simulated_timeout_cleans_index(self):
        """Simulate the timeout_callback path directly."""
        rpc = create_rpc()
        session_id = "timeout-sess"
        store = rpc._get_session_store(session_id, create=True)
        store["target_id"] = "client-T"
        rpc._target_id_index["client-T"] = {session_id}

        # Simulate what timeout_callback does:
        # 1. Remove from target_id index
        # 2. Delete from object store
        if session_id in rpc._object_store:
            rpc._remove_from_target_id_index(session_id)
            del rpc._object_store[session_id]

        assert session_id not in rpc._object_store
        assert "client-T" not in rpc._target_id_index


# ─── Test _cleanup_on_disconnect clears everything ───────────────────


class TestCleanupOnDisconnect:
    def test_clears_index_completely(self):
        rpc = create_rpc()
        for i in range(20):
            sid = f"sess-{i}"
            store = rpc._get_session_store(sid, create=True)
            target = f"client-{i % 5}"
            store["target_id"] = target
            if target not in rpc._target_id_index:
                rpc._target_id_index[target] = set()
            rpc._target_id_index[target].add(sid)

        assert _count_sessions(rpc) == 20
        assert len(rpc._target_id_index) == 5

        rpc._cleanup_on_disconnect()

        assert _count_sessions(rpc) == 0
        assert rpc._target_id_index == {}

    def test_preserves_services(self):
        rpc = create_rpc()
        rpc._services["my-service"] = {"name": "test"}
        store = rpc._get_session_store("sess-1", create=True)
        store["target_id"] = "client-A"
        rpc._target_id_index["client-A"] = {"sess-1"}

        rpc._cleanup_on_disconnect()

        assert "services" in rpc._object_store
        assert "my-service" in rpc._services
        assert rpc._services["my-service"] == {"name": "test"}
        assert rpc._target_id_index == {}


# ─── Test close() cleans everything ──────────────────────────────────


class TestCloseCleanup:
    def test_close_clears_sessions_and_index(self):
        rpc = create_rpc()
        for i in range(10):
            sid = f"sess-{i}"
            store = rpc._get_session_store(sid, create=True)
            store["target_id"] = f"client-{i % 3}"
            target = f"client-{i % 3}"
            if target not in rpc._target_id_index:
                rpc._target_id_index[target] = set()
            rpc._target_id_index[target].add(sid)

        rpc.close()

        assert _count_sessions(rpc) == 0
        assert rpc._target_id_index == {}


# ─── Test index consistency under stress ─────────────────────────────


class TestIndexConsistency:
    def test_create_and_delete_many_sessions(self):
        """Create many sessions, delete them through various paths, verify index is clean."""
        rpc = create_rpc()

        # Create 100 sessions across 10 clients
        for i in range(100):
            sid = f"sess-{i}"
            store = rpc._get_session_store(sid, create=True)
            target = f"client-{i % 10}"
            store["target_id"] = target
            if target not in rpc._target_id_index:
                rpc._target_id_index[target] = set()
            rpc._target_id_index[target].add(sid)

        assert _count_sessions(rpc) == 100
        assert _count_index_entries(rpc) == 100

        # Delete first 30 via _delete_session_completely
        for i in range(30):
            rpc._delete_session_completely(f"sess-{i}")

        assert _count_sessions(rpc) == 70
        assert _count_index_entries(rpc) == 70

        # Delete next 20 via _cleanup_sessions_for_client (client-3 and client-4)
        rpc._cleanup_sessions_for_client("client-3")
        rpc._cleanup_sessions_for_client("client-4")

        remaining_sessions = _count_sessions(rpc)
        remaining_index = _count_index_entries(rpc)
        assert remaining_sessions == remaining_index

        # Delete rest via _cleanup_on_disconnect
        rpc._cleanup_on_disconnect()

        assert _count_sessions(rpc) == 0
        assert rpc._target_id_index == {}

    def test_double_delete_no_crash(self):
        """Deleting the same session twice should not crash or corrupt index."""
        rpc = create_rpc()
        store = rpc._get_session_store("sess-1", create=True)
        store["target_id"] = "client-A"
        rpc._target_id_index["client-A"] = {"sess-1"}

        rpc._delete_session_completely("sess-1")
        # Second delete should be a no-op
        rpc._delete_session_completely("sess-1")

        assert "sess-1" not in rpc._object_store
        assert rpc._target_id_index == {}

    def test_index_matches_store_invariant(self):
        """After any operation, every session in index must exist in store."""
        rpc = create_rpc()

        # Create sessions
        for i in range(50):
            sid = f"sess-{i}"
            store = rpc._get_session_store(sid, create=True)
            target = f"client-{i % 5}"
            store["target_id"] = target
            if target not in rpc._target_id_index:
                rpc._target_id_index[target] = set()
            rpc._target_id_index[target].add(sid)

        # Delete some via different paths
        rpc._delete_session_completely("sess-0")
        rpc._delete_session_safely("sess-10")
        rpc._cleanup_sessions_for_client("client-2")

        # Verify invariant: every key in index exists in store
        for target_id, session_keys in rpc._target_id_index.items():
            for sk in session_keys:
                assert sk in rpc._object_store, (
                    f"Stale index entry: {sk} in index for {target_id} "
                    f"but not in _object_store"
                )
                session = rpc._object_store[sk]
                assert isinstance(session, dict)
                assert session.get("target_id") == target_id


# ─── Test encode/decode fast path doesn't break functionality ────────


class TestEncodeFastPath:
    def test_flat_dict_roundtrip(self):
        """Fast path should produce identical results for flat primitive dicts."""
        import msgpack

        rpc = create_rpc()

        payload = {"a": 1, "b": "hello", "c": True, "d": None, "e": 3.14}
        encoded = rpc._encode(payload, session_id="test-session")
        packed = msgpack.packb(encoded)
        unpacked = msgpack.unpackb(packed, raw=False)
        decoded = rpc._decode(unpacked)

        assert decoded["a"] == 1
        assert decoded["b"] == "hello"
        assert decoded["c"] is True
        assert decoded["d"] is None
        assert decoded["e"] == 3.14

    def test_nested_primitive_dict_roundtrip(self):
        """Fast path should work with nested dicts of primitives."""
        import msgpack

        rpc = create_rpc()

        payload = {
            "level1": {"level2": {"value": 42, "name": "test"}},
            "list": [1, 2, 3],
        }
        encoded = rpc._encode(payload, session_id="test-session")
        packed = msgpack.packb(encoded)
        unpacked = msgpack.unpackb(packed, raw=False)
        decoded = rpc._decode(unpacked)

        assert decoded["level1"]["level2"]["value"] == 42
        assert decoded["list"] == [1, 2, 3]

    def test_dict_with_callable_not_fast_pathed(self):
        """Dicts containing callables should NOT use the fast path."""
        rpc = create_rpc()

        def my_func():
            pass

        payload = {"name": "test", "callback": my_func}
        encoded = rpc._encode(payload, session_id="test-session")

        # The callback should be encoded as a method reference
        assert encoded["callback"]["_rtype"] == "method"

    def test_list_of_primitive_dicts_roundtrip(self):
        """Tabular data (list of dicts) should roundtrip correctly."""
        import msgpack

        rpc = create_rpc()

        payload = [
            {"id": i, "name": f"item_{i}", "active": i % 2 == 0} for i in range(10)
        ]
        encoded = rpc._encode(payload, session_id="test-session")
        packed = msgpack.packb(encoded)
        unpacked = msgpack.unpackb(packed, raw=False)
        decoded = rpc._decode(unpacked)

        assert len(decoded) == 10
        assert decoded[0]["id"] == 0
        assert decoded[9]["name"] == "item_9"

    def test_numpy_array_not_fast_pathed(self):
        """Numpy arrays should NOT use the fast path."""
        import numpy as np

        rpc = create_rpc()

        payload = {"data": np.array([1.0, 2.0, 3.0], dtype=np.float32)}
        encoded = rpc._encode(payload, session_id="test-session")

        assert encoded["data"]["_rtype"] == "ndarray"
