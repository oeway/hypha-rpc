"""Test that the workspace manager proxy is refreshed after reconnection.

When the Hypha server restarts, it assigns a new manager_id. The wm proxy
returned by connect_to_server() must automatically refresh its methods to
target the new manager_id. Without this, all RPC calls (echo, get_service,
list_services, etc.) silently go to a nonexistent target and time out.

Run: pytest tests/test_wm_proxy_refresh.py -v
"""

import asyncio
import time

import pytest

from hypha_rpc.websocket_client import connect_to_server
from . import WS_SERVER_URL


@pytest.mark.asyncio
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
    print("Restarting server...")
    restartable_server.restart(stop_delay=0.5, start_timeout=15)
    print("Server restarted")

    # 3. Wait for reconnection (up to 30s)
    deadline = time.time() + 30
    while time.time() < deadline:
        if reconnection_events:
            break
        await asyncio.sleep(0.5)

    assert reconnection_events, "No reconnection event received within 30s"
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
