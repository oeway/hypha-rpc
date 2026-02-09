"""Benchmark for RPC encode/decode performance.

Measures time spent in:
  - _encode() traversal
  - msgpack.packb() serialization
  - msgpack.unpackb() deserialization
  - _decode() traversal

Across multiple payload types:
  1. Flat dict of primitives (common API response)
  2. Nested dicts/lists (structured data)
  3. Dict with numpy arrays (scientific data)
  4. Large list of dicts (tabular data)
  5. Session cleanup with many sessions (target_id lookup)
"""

import asyncio
import io
import time
import statistics
import sys
import os

import msgpack
import numpy as np

# Add parent to path so we can import hypha_rpc
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypha_rpc.rpc import RPC
from hypha_rpc.utils import MessageEmitter


class DummyConnection(MessageEmitter):
    """Minimal connection stub for benchmarking."""

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
    """Create an RPC instance for benchmarking."""
    conn = DummyConnection()
    rpc = RPC(
        conn,
        client_id="bench-client",
        name="bench",
        workspace="bench-workspace",
        server_base_url="http://localhost",
    )
    return rpc


# ─── Payload generators ───────────────────────────────────────────────


def make_flat_primitives(n=100):
    """Flat dict with n string/int/float/bool keys."""
    d = {}
    for i in range(n):
        d[f"key_{i}"] = i
        d[f"str_{i}"] = f"value_{i}"
        d[f"float_{i}"] = i * 1.1
        d[f"bool_{i}"] = i % 2 == 0
    return d


def make_nested(depth=5, breadth=4):
    """Nested dict structure."""
    if depth == 0:
        return {"value": 42, "name": "leaf", "active": True}
    return {f"child_{i}": make_nested(depth - 1, breadth) for i in range(breadth)}


def make_numpy_payload(n_arrays=10, array_size=1000):
    """Dict containing numpy arrays."""
    d = {}
    for i in range(n_arrays):
        d[f"array_{i}"] = np.random.randn(array_size).astype(np.float32)
        d[f"label_{i}"] = f"data_{i}"
    return d


def make_tabular(n_rows=500):
    """List of dicts simulating tabular data."""
    return [
        {
            "id": i,
            "name": f"item_{i}",
            "value": i * 3.14,
            "active": i % 3 == 0,
            "tags": [f"tag_{j}" for j in range(3)],
        }
        for i in range(n_rows)
    ]


def make_mixed_payload():
    """Mix of primitives, nested, and arrays."""
    return {
        "metadata": {
            "version": "1.0",
            "count": 100,
            "nested": {"a": {"b": {"c": "deep"}}},
        },
        "items": [{"id": i, "val": i * 2.5} for i in range(50)],
        "matrix": np.eye(100, dtype=np.float32),
        "flags": {f"flag_{i}": i % 2 == 0 for i in range(20)},
    }


# ─── Benchmark runner ─────────────────────────────────────────────────


def bench_encode_decode(rpc, payload, label, iterations=200):
    """Benchmark encode, packb, unpackb, decode for a payload."""
    encode_times = []
    pack_times = []
    unpack_times = []
    decode_times = []
    total_times = []

    for _ in range(iterations):
        # Encode
        t0 = time.perf_counter()
        encoded = rpc._encode(payload, session_id="bench-session")
        t1 = time.perf_counter()

        # Pack
        packed = msgpack.packb(encoded)
        t2 = time.perf_counter()

        # Unpack
        unpacked = msgpack.unpackb(packed, raw=False)
        t3 = time.perf_counter()

        # Decode
        decoded = rpc._decode(unpacked)
        t4 = time.perf_counter()

        encode_times.append(t1 - t0)
        pack_times.append(t2 - t1)
        unpack_times.append(t3 - t2)
        decode_times.append(t4 - t3)
        total_times.append(t4 - t0)

    # Drop first 10 iterations (warmup)
    warmup = min(10, iterations // 5)
    encode_times = encode_times[warmup:]
    pack_times = pack_times[warmup:]
    unpack_times = unpack_times[warmup:]
    decode_times = decode_times[warmup:]
    total_times = total_times[warmup:]

    payload_size = len(msgpack.packb(rpc._encode(payload, session_id="bench-session")))

    print(f"\n{'=' * 65}")
    print(f"  {label}")
    print(f"  Payload size: {payload_size:,} bytes | Iterations: {iterations}")
    print(f"{'=' * 65}")
    print(
        f"  {'Step':<20} {'Mean (us)':>10} {'Median (us)':>12} {'Stdev (us)':>12} {'% of total':>10}"
    )
    print(f"  {'-' * 64}")

    total_mean = statistics.mean(total_times)
    for name, times in [
        ("_encode()", encode_times),
        ("msgpack.packb()", pack_times),
        ("msgpack.unpackb()", unpack_times),
        ("_decode()", decode_times),
        ("TOTAL", total_times),
    ]:
        mean = statistics.mean(times)
        median = statistics.median(times)
        stdev = statistics.stdev(times) if len(times) > 1 else 0
        pct = (mean / total_mean * 100) if total_mean > 0 else 0
        print(
            f"  {name:<20} {mean * 1e6:>10.1f} {median * 1e6:>12.1f} {stdev * 1e6:>12.1f} {pct:>9.1f}%"
        )

    return {
        "label": label,
        "payload_size": payload_size,
        "encode_mean_us": statistics.mean(encode_times) * 1e6,
        "pack_mean_us": statistics.mean(pack_times) * 1e6,
        "unpack_mean_us": statistics.mean(unpack_times) * 1e6,
        "decode_mean_us": statistics.mean(decode_times) * 1e6,
        "total_mean_us": statistics.mean(total_times) * 1e6,
    }


def bench_session_cleanup(n_sessions=1000, iterations=50):
    """Benchmark session cleanup with target_id lookup."""
    rpc = create_rpc()

    def populate_sessions():
        """Populate sessions with target_id and update the index."""
        for i in range(n_sessions):
            session_id = f"session-{i}"
            if session_id not in rpc._object_store:
                store = rpc._get_session_store(session_id, create=True)
                target_id = f"client-{i % 10}"
                store["target_id"] = target_id
                # Also populate the target_id index
                if target_id not in rpc._target_id_index:
                    rpc._target_id_index[target_id] = set()
                rpc._target_id_index[target_id].add(session_id)

    populate_sessions()

    times = []
    for _ in range(iterations):
        # Re-populate since cleanup removes them
        populate_sessions()

        t0 = time.perf_counter()
        rpc._cleanup_sessions_for_client("client-0")
        t1 = time.perf_counter()
        times.append(t1 - t0)

    warmup = min(5, iterations // 5)
    times = times[warmup:]

    print(f"\n{'=' * 65}")
    print(f"  Session Cleanup (n_sessions={n_sessions})")
    print(f"  Iterations: {iterations}")
    print(f"{'=' * 65}")
    mean = statistics.mean(times)
    median = statistics.median(times)
    stdev = statistics.stdev(times) if len(times) > 1 else 0
    print(
        f"  Mean: {mean * 1e6:.1f} us | Median: {median * 1e6:.1f} us | Stdev: {stdev * 1e6:.1f} us"
    )

    return {"cleanup_mean_us": mean * 1e6, "n_sessions": n_sessions}


def bench_on_message(rpc, payload, label, iterations=200):
    """Benchmark the full _on_message path (unpack + decode)."""
    # Pre-encode and pack the payload to simulate an incoming message
    encoded = rpc._encode(payload, session_id="bench-session")
    main_message = {
        "type": "method",
        "from": "remote-client",
        "to": "bench-client",
        "method": "test",
    }
    extra_data = {"args": encoded}
    packed = msgpack.packb(main_message) + msgpack.packb(extra_data)

    times = []
    received = []

    # Capture the fired event
    def handler(data):
        received.append(data)

    rpc.on("method", handler)

    for _ in range(iterations):
        received.clear()
        t0 = time.perf_counter()
        rpc._on_message(packed)
        t1 = time.perf_counter()
        times.append(t1 - t0)

    rpc.off("method", handler)

    warmup = min(10, iterations // 5)
    times = times[warmup:]

    print(f"\n{'=' * 65}")
    print(f"  _on_message() full path: {label}")
    print(f"  Message size: {len(packed):,} bytes | Iterations: {iterations}")
    print(f"{'=' * 65}")
    mean = statistics.mean(times)
    median = statistics.median(times)
    stdev = statistics.stdev(times) if len(times) > 1 else 0
    print(
        f"  Mean: {mean * 1e6:.1f} us | Median: {median * 1e6:.1f} us | Stdev: {stdev * 1e6:.1f} us"
    )

    return {"on_message_mean_us": mean * 1e6, "message_size": len(packed)}


def main():
    print("=" * 65)
    print("  Hypha RPC Encode/Decode Benchmark")
    print("=" * 65)
    print(f"  Python: {sys.version}")
    print(f"  msgpack: {msgpack.version}")
    print(f"  numpy: {np.__version__}")

    # Check if msgpack C extension is active
    try:
        from msgpack._cmsgpack import Packer as CPacker

        print("  msgpack backend: C extension")
    except ImportError:
        print("  msgpack backend: pure Python (SLOW)")

    rpc = create_rpc()
    results = []

    # 1. Flat primitives
    payload = make_flat_primitives(100)
    results.append(bench_encode_decode(rpc, payload, "1. Flat primitives (400 keys)"))

    # 2. Nested dicts
    payload = make_nested(depth=4, breadth=4)
    results.append(
        bench_encode_decode(rpc, payload, "2. Nested dicts (depth=4, breadth=4)")
    )

    # 3. Numpy arrays
    payload = make_numpy_payload(10, 1000)
    results.append(
        bench_encode_decode(rpc, payload, "3. Numpy arrays (10x 1000 floats)")
    )

    # 4. Tabular data
    payload = make_tabular(500)
    results.append(bench_encode_decode(rpc, payload, "4. Tabular data (500 rows)"))

    # 5. Mixed payload
    payload = make_mixed_payload()
    results.append(bench_encode_decode(rpc, payload, "5. Mixed payload"))

    # 6. Session cleanup
    bench_session_cleanup(n_sessions=1000)

    # 7. Full _on_message path
    bench_on_message(rpc, make_flat_primitives(100), "Flat primitives via _on_message")
    bench_on_message(rpc, make_tabular(500), "Tabular data via _on_message")

    # Summary table
    print(f"\n{'=' * 65}")
    print("  SUMMARY: Time breakdown (mean, microseconds)")
    print(f"{'=' * 65}")
    print(
        f"  {'Payload':<30} {'encode':>8} {'packb':>8} {'unpackb':>8} {'decode':>8} {'TOTAL':>8}"
    )
    print(f"  {'-' * 70}")
    for r in results:
        print(
            f"  {r['label'][:30]:<30} "
            f"{r['encode_mean_us']:>8.0f} "
            f"{r['pack_mean_us']:>8.0f} "
            f"{r['unpack_mean_us']:>8.0f} "
            f"{r['decode_mean_us']:>8.0f} "
            f"{r['total_mean_us']:>8.0f}"
        )


if __name__ == "__main__":
    main()
