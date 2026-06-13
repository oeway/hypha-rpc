"""Test that the workspace manager proxy is refreshed after reconnection.

When the Hypha server restarts, it assigns a new manager_id. The wm proxy
returned by connect_to_server() must automatically refresh its methods to
target the new manager_id. Without this, all RPC calls (echo, get_service,
list_services, etc.) silently go to a nonexistent target and time out.

Run: pytest tests/test_wm_proxy_refresh.py -v --timeout=120
"""

import asyncio
import os
import time

import pytest

from hypha_rpc.websocket_client import connect_to_server
from . import WS_SERVER_URL


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_wm_methods_retarget_after_manager_refresh(websocket_server):
    """Regression: wm proxy methods must retarget to the new manager_id.

    The manager_refreshed retarget loop gates on hasattr(_encoded_method).
    Manager methods are retry-wrapped, so the retry wrapper must expose the
    underlying RemoteFunction's _encoded_method, and the get_service closure
    wrapper must share it too. Without this the loop matches nothing and the
    wm proxy keeps targeting the stale "*/<old_manager_id>" after a restart.
    """
    ws = await connect_to_server(
        {
            "name": "wm-retarget-test",
            "server_url": WS_SERVER_URL,
            "method_timeout": 30,
        }
    )
    old_manager_id = ws.rpc._connection.manager_id
    try:
        # A retry-wrapped manager method (echo) and the get_service closure
        # wrapper must both be retargetable.
        assert hasattr(ws.echo, "_encoded_method"), (
            "retry-wrapped manager methods must expose _encoded_method"
        )
        assert hasattr(ws.get_service, "_encoded_method"), (
            "get_service wrapper must expose _encoded_method for retargeting"
        )
        assert ws.echo._encoded_method["_rtarget"] == f"*/{old_manager_id}"
        assert ws.get_service._encoded_method["_rtarget"] == f"*/{old_manager_id}"

        # Simulate a server restart assigning a fresh manager_id, then drive the
        # manager_refreshed event. Fire twice so retargeting happens regardless
        # of whether the initial (no-op) refresh was already consumed at setup.
        ws.rpc._connection.manager_id = "regression-new-manager-id"
        ws.rpc._fire("manager_refreshed", {})
        ws.rpc._fire("manager_refreshed", {})
        await asyncio.sleep(0.2)  # let the fire-and-forget handler run

        for name in ("echo", "get_service"):
            target = getattr(ws, name)._encoded_method["_rtarget"]
            assert target == "*/regression-new-manager-id", (
                f"{name} should retarget to new manager, got {target}"
            )
    finally:
        # Restore the real manager_id so disconnect can reach the server.
        ws.rpc._connection.manager_id = old_manager_id
        await ws.disconnect()


@pytest.mark.asyncio
@pytest.mark.timeout(120)
@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="Requires real server restart with port recycling; too slow for CI",
)
async def test_wm_proxy_refreshes_after_server_restart(restartable_server):
    """Test that echo() works after the server restarts with a new manager_id."""

    # 1. Connect and verify echo works
    ws = await connect_to_server(
        {
            "name": "wm-refresh-test",
            "server_url": WS_SERVER_URL,
            "method_timeout": 30,
        }
    )

    result = await ws.echo("hello")
    assert result == "hello", f"Initial echo failed: {result}"

    initial_manager_id = ws.rpc._connection.manager_id
    print(f"Initial manager_id: {initial_manager_id}")

    # Track reconnection events
    reconnection_events = []

    def on_services_registered(info):
        reconnection_events.append(time.time())
        print(f"Services registered at {time.time()}")

    ws.rpc.on("services_registered", on_services_registered)

    # 2. Restart the server (this creates a new server with a new manager_id)
    # Use a longer stop_delay to ensure the port is freed (TIME_WAIT)
    print("Restarting server...")
    restartable_server.restart(stop_delay=2, start_timeout=30)
    print("Server restarted")

    # 3. Wait for reconnection (up to 60s)
    deadline = time.time() + 60
    while time.time() < deadline:
        if reconnection_events:
            break
        await asyncio.sleep(0.5)

    assert reconnection_events, "No reconnection event received within 60s"
    print(f"Reconnected after {time.time() - reconnection_events[0]:.1f}s")

    # Give the wm proxy refresh a moment to complete
    await asyncio.sleep(2)

    # 4. Verify the manager_id changed (new server instance)
    new_manager_id = ws.rpc._connection.manager_id
    print(f"New manager_id: {new_manager_id}")
    assert new_manager_id != initial_manager_id, (
        f"manager_id should change after server restart, "
        f"got same: {new_manager_id}"
    )

    # 5. THE CRITICAL TEST: echo() must work with the new manager_id.
    # Without the wm proxy refresh fix, this would timeout because
    # ws.echo() targets the old (nonexistent) manager_id.
    result = await ws.echo("world")
    assert result == "world", f"Echo after reconnection failed: {result}"
    print("Echo after reconnection succeeded!")

    ws.rpc._connection.disconnect("test cleanup")
