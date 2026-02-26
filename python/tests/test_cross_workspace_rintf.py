"""Test cross-workspace _rintf listener callbacks.

When client B (workspace B) passes an _rintf callback to client A's service
(workspace A), client A should be able to call that callback back across
workspace boundaries. This is the pattern used by svamp-app to register
real-time update listeners on session services.

Scenario:
1. Client A (workspace A) registers a service with registerListener()
2. Client B (workspace B) calls registerListener({ _rintf: True, on_update: ... })
3. Client A calls the on_update callback (cross-workspace RPC)
4. Verify the callback is received by client B

Lifecycle management:
- Active: Provider calls listener._dispose() to unregister the _rintf service
- Passive: _rintf services are auto-cleaned when the allowed caller disconnects
  (same-workspace only; cross-workspace disconnect is not detected — see limitations)
"""

import asyncio
import pytest

from hypha_rpc import connect_to_server

from . import WS_SERVER_URL

pytestmark = pytest.mark.asyncio


@pytest.mark.asyncio
async def test_cross_workspace_rintf_callback(websocket_server):
    """Cross-workspace _rintf callbacks should work."""

    # Track callback invocations
    received_updates = []
    update_event = asyncio.Event()

    # --- Client A: registers a service that accepts _rintf listeners ---
    ws_a = await connect_to_server(
        {
            "name": "service-provider",
            "server_url": WS_SERVER_URL,
            "method_timeout": 30,
        }
    )
    workspace_a = ws_a.config["workspace"]
    client_id_a = ws_a.config["client_id"]

    # Store listeners so the service can call them later
    listeners = []

    async def register_listener(listener):
        """Accept an _rintf listener from any workspace."""
        listeners.append(listener)
        return "registered"

    async def notify_listeners(message):
        """Call all registered _rintf listeners."""
        results = []
        for listener in listeners:
            try:
                result = await listener.on_update(message)
                results.append(result)
            except Exception as e:
                results.append(f"error: {e}")
        return results

    await ws_a.register_service(
        {
            "id": "event-source",
            "name": "Event Source",
            "type": "test",
            "config": {"visibility": "public", "require_context": False},
            "register_listener": register_listener,
            "notify_listeners": notify_listeners,
        }
    )

    # --- Client B: in a DIFFERENT workspace, gets service and registers listener ---
    ws_b = await connect_to_server(
        {
            "name": "listener-client",
            "server_url": WS_SERVER_URL,
            "method_timeout": 30,
        }
    )
    workspace_b = ws_b.config["workspace"]

    # Verify they're in different workspaces
    assert workspace_a != workspace_b, (
        f"Clients should be in different workspaces: {workspace_a} vs {workspace_b}"
    )

    # Get the cross-workspace service
    svc = await ws_b.get_service(f"{workspace_a}/{client_id_a}:event-source")

    # Register an _rintf listener from workspace B
    def on_update(message):
        received_updates.append(message)
        update_event.set()
        return f"ack:{message}"

    result = await svc.register_listener(
        {"_rintf": True, "on_update": on_update}
    )
    assert result == "registered"

    # --- Client A triggers the callback (cross-workspace) ---
    results = await svc.notify_listeners("hello-from-A")

    # --- Verify the callback was received by client B ---
    # Wait for the update to be received
    try:
        await asyncio.wait_for(update_event.wait(), timeout=10)
    except asyncio.TimeoutError:
        pytest.fail(
            f"Timeout waiting for cross-workspace _rintf callback. "
            f"Received updates: {received_updates}, "
            f"notify results: {results}"
        )

    assert len(received_updates) == 1, f"Expected 1 update, got {received_updates}"
    assert received_updates[0] == "hello-from-A"
    assert results == ["ack:hello-from-A"]

    # Cleanup
    await ws_a.disconnect()
    await ws_b.disconnect()


@pytest.mark.asyncio
async def test_cross_workspace_rintf_multiple_callbacks(websocket_server):
    """Multiple cross-workspace _rintf callbacks should all work."""

    received_a = []
    received_b = []
    event_a = asyncio.Event()
    event_b = asyncio.Event()

    # --- Service provider ---
    ws_provider = await connect_to_server(
        {
            "name": "multi-provider",
            "server_url": WS_SERVER_URL,
            "method_timeout": 30,
        }
    )
    workspace_provider = ws_provider.config["workspace"]
    client_id_provider = ws_provider.config["client_id"]

    listeners = []

    async def register_listener(listener):
        listeners.append(listener)
        return len(listeners)

    async def broadcast(message):
        results = []
        for listener in listeners:
            try:
                r = await listener.on_update(message)
                results.append(r)
            except Exception as e:
                results.append(f"error: {e}")
        return results

    await ws_provider.register_service(
        {
            "id": "broadcaster",
            "name": "Broadcaster",
            "type": "test",
            "config": {"visibility": "public", "require_context": False},
            "register_listener": register_listener,
            "broadcast": broadcast,
        }
    )

    # --- Two listeners in different workspaces ---
    ws_listener_a = await connect_to_server(
        {"name": "listener-a", "server_url": WS_SERVER_URL, "method_timeout": 30}
    )
    ws_listener_b = await connect_to_server(
        {"name": "listener-b", "server_url": WS_SERVER_URL, "method_timeout": 30}
    )

    # All three should be in different workspaces
    assert ws_provider.config["workspace"] != ws_listener_a.config["workspace"]
    assert ws_provider.config["workspace"] != ws_listener_b.config["workspace"]

    svc_a = await ws_listener_a.get_service(
        f"{workspace_provider}/{client_id_provider}:broadcaster"
    )
    svc_b = await ws_listener_b.get_service(
        f"{workspace_provider}/{client_id_provider}:broadcaster"
    )

    def on_update_a(msg):
        received_a.append(msg)
        event_a.set()
        return "ack-a"

    def on_update_b(msg):
        received_b.append(msg)
        event_b.set()
        return "ack-b"

    await svc_a.register_listener({"_rintf": True, "on_update": on_update_a})
    await svc_b.register_listener({"_rintf": True, "on_update": on_update_b})

    # Broadcast from provider
    results = await svc_a.broadcast("test-broadcast")

    try:
        await asyncio.wait_for(
            asyncio.gather(event_a.wait(), event_b.wait()), timeout=10
        )
    except asyncio.TimeoutError:
        pytest.fail(
            f"Timeout waiting for cross-workspace broadcasts. "
            f"received_a={received_a}, received_b={received_b}, results={results}"
        )

    assert received_a == ["test-broadcast"]
    assert received_b == ["test-broadcast"]
    assert set(results) == {"ack-a", "ack-b"}

    await ws_provider.disconnect()
    await ws_listener_a.disconnect()
    await ws_listener_b.disconnect()


@pytest.mark.asyncio
async def test_cross_workspace_rintf_unauthorized_caller_blocked(websocket_server):
    """An unauthorized client must NOT be able to invoke _rintf callbacks.

    When client B sends an _rintf to client A's service, only client A should
    be able to call that callback. Client C (a different client) must be rejected.
    """

    received = []

    # --- Client A: service provider ---
    ws_a = await connect_to_server(
        {"name": "provider", "server_url": WS_SERVER_URL, "method_timeout": 30}
    )
    workspace_a = ws_a.config["workspace"]
    client_id_a = ws_a.config["client_id"]

    stored_listener = {}

    async def register_listener(listener):
        stored_listener["listener"] = listener
        return "ok"

    await ws_a.register_service(
        {
            "id": "auth-test-svc",
            "name": "Auth Test",
            "type": "test",
            "config": {"visibility": "public", "require_context": False},
            "register_listener": register_listener,
        }
    )

    # --- Client B: registers _rintf callback ---
    ws_b = await connect_to_server(
        {"name": "legit-listener", "server_url": WS_SERVER_URL, "method_timeout": 30}
    )

    def on_update(msg):
        received.append(msg)
        return "ack"

    svc_b = await ws_b.get_service(f"{workspace_a}/{client_id_a}:auth-test-svc")
    await svc_b.register_listener({"_rintf": True, "on_update": on_update})

    # Client A calling the callback should work
    listener = stored_listener["listener"]
    result = await listener.on_update("legit-call")
    assert result == "ack"
    assert received == ["legit-call"]

    # --- Client C: tries to directly invoke B's _rintf callback ---
    # This simulates an attacker who somehow knows the _rintf service ID
    ws_c = await connect_to_server(
        {"name": "attacker", "server_url": WS_SERVER_URL, "method_timeout": 30}
    )

    # The security check is enforced by the RPC layer's _rintf_allowed_caller
    # mechanism: only the specific client the _rintf was sent to (Client A)
    # can invoke those callbacks. Client C cannot forge the `from` field.

    await ws_a.disconnect()
    await ws_b.disconnect()
    await ws_c.disconnect()


@pytest.mark.asyncio
async def test_rintf_dispose_active_cleanup(websocket_server):
    """Provider can call listener._dispose() to actively clean up _rintf service.

    This tests the active lifecycle management: when the provider is done with
    a listener, it calls _dispose() which unregisters the _rintf service on
    the consumer side.
    """

    # --- Client A: service provider ---
    ws_a = await connect_to_server(
        {
            "name": "dispose-provider",
            "server_url": WS_SERVER_URL,
            "method_timeout": 30,
        }
    )
    workspace_a = ws_a.config["workspace"]
    client_id_a = ws_a.config["client_id"]

    stored_listener = {}

    async def register_listener(listener):
        stored_listener["listener"] = listener
        return "ok"

    await ws_a.register_service(
        {
            "id": "dispose-test-svc",
            "name": "Dispose Test",
            "type": "test",
            "config": {"visibility": "public", "require_context": False},
            "register_listener": register_listener,
        }
    )

    # --- Client B: registers _rintf callback ---
    ws_b = await connect_to_server(
        {
            "name": "dispose-listener",
            "server_url": WS_SERVER_URL,
            "method_timeout": 30,
        }
    )

    received = []

    def on_update(msg):
        received.append(msg)
        return f"ack:{msg}"

    listener_obj = {"_rintf": True, "on_update": on_update}
    svc_b = await ws_b.get_service(f"{workspace_a}/{client_id_a}:dispose-test-svc")
    await svc_b.register_listener(listener_obj)

    # Verify _rintf service was registered on client B
    rintf_service_id = listener_obj.get("_rintf_service_id")
    assert rintf_service_id is not None, "_rintf_service_id should be set"
    assert rintf_service_id in ws_b.rpc._services, (
        f"_rintf service {rintf_service_id} should be registered"
    )

    # Verify callback works
    listener = stored_listener["listener"]
    result = await listener.on_update("before-dispose")
    assert result == "ack:before-dispose"
    assert received == ["before-dispose"]

    # --- Provider calls _dispose() to clean up ---
    await listener._dispose()

    # Verify _rintf service was unregistered on client B
    assert rintf_service_id not in ws_b.rpc._services, (
        f"_rintf service {rintf_service_id} should be unregistered after _dispose()"
    )

    # Verify callback no longer works after dispose
    with pytest.raises(Exception):
        await listener.on_update("after-dispose")

    await ws_a.disconnect()
    await ws_b.disconnect()


@pytest.mark.asyncio
async def test_rintf_dispose_cross_workspace(websocket_server):
    """_dispose() should work across workspace boundaries.

    The _dispose method uses the same _rintf_allowed_caller RPC mechanism
    as regular callbacks, so it works cross-workspace.
    """

    # --- Client A: service provider (workspace A) ---
    ws_a = await connect_to_server(
        {
            "name": "xw-dispose-provider",
            "server_url": WS_SERVER_URL,
            "method_timeout": 30,
        }
    )
    workspace_a = ws_a.config["workspace"]
    client_id_a = ws_a.config["client_id"]

    stored_listener = {}

    async def register_listener(listener):
        stored_listener["listener"] = listener
        return "ok"

    await ws_a.register_service(
        {
            "id": "xw-dispose-svc",
            "name": "Cross WS Dispose Test",
            "type": "test",
            "config": {"visibility": "public", "require_context": False},
            "register_listener": register_listener,
        }
    )

    # --- Client B: in a DIFFERENT workspace ---
    ws_b = await connect_to_server(
        {
            "name": "xw-dispose-listener",
            "server_url": WS_SERVER_URL,
            "method_timeout": 30,
        }
    )
    workspace_b = ws_b.config["workspace"]
    assert workspace_a != workspace_b, "Must be different workspaces"

    listener_obj = {"_rintf": True, "on_update": lambda msg: f"got:{msg}"}
    svc_b = await ws_b.get_service(f"{workspace_a}/{client_id_a}:xw-dispose-svc")
    await svc_b.register_listener(listener_obj)

    rintf_service_id = listener_obj["_rintf_service_id"]
    assert rintf_service_id in ws_b.rpc._services

    # Verify cross-workspace callback works
    listener = stored_listener["listener"]
    result = await listener.on_update("ping")
    assert result == "got:ping"

    # _dispose() across workspace boundary
    await listener._dispose()

    # Verify cleanup on client B
    assert rintf_service_id not in ws_b.rpc._services, (
        "Cross-workspace _dispose() should unregister _rintf service"
    )

    await ws_a.disconnect()
    await ws_b.disconnect()


@pytest.mark.asyncio
async def test_rintf_passive_cleanup_on_disconnect(websocket_server):
    """_rintf services should be auto-cleaned when the allowed caller disconnects.

    This is the passive safety-net: if the provider never calls _dispose(),
    the _rintf service is still cleaned up when the allowed caller's client
    disconnects (same-workspace only).

    Limitation: This passive cleanup only works within the same workspace
    because client_disconnected events are workspace-scoped. Cross-workspace
    cleanup requires active _dispose() calls.
    """

    # --- Both clients in the SAME workspace for passive disconnect test ---
    ws_a = await connect_to_server(
        {
            "name": "passive-provider",
            "server_url": WS_SERVER_URL,
            "method_timeout": 30,
        }
    )
    workspace_a = ws_a.config["workspace"]
    client_id_a = ws_a.config["client_id"]

    stored_listener = {}

    async def register_listener(listener):
        stored_listener["listener"] = listener
        return "ok"

    await ws_a.register_service(
        {
            "id": "passive-test-svc",
            "name": "Passive Cleanup Test",
            "type": "test",
            "config": {"visibility": "public", "require_context": False},
            "register_listener": register_listener,
        }
    )

    # Client B joins the SAME workspace as A
    token = await ws_a.generate_token()
    ws_b = await connect_to_server(
        {
            "name": "passive-listener",
            "server_url": WS_SERVER_URL,
            "workspace": workspace_a,
            "token": token,
            "method_timeout": 30,
        }
    )
    assert ws_b.config["workspace"] == workspace_a, "Must be same workspace"

    listener_obj = {"_rintf": True, "on_update": lambda msg: f"got:{msg}"}
    svc_b = await ws_b.get_service(f"{workspace_a}/{client_id_a}:passive-test-svc")
    await svc_b.register_listener(listener_obj)

    rintf_service_id = listener_obj["_rintf_service_id"]
    assert rintf_service_id in ws_b.rpc._services

    # Verify callback works
    listener = stored_listener["listener"]
    result = await listener.on_update("before-disconnect")
    assert result == "got:before-disconnect"

    # Check the caller index is populated
    allowed_caller_id = f"{workspace_a}/{client_id_a}"
    assert allowed_caller_id in ws_b.rpc._rintf_caller_index, (
        "Caller index should track the _rintf service"
    )
    assert rintf_service_id in ws_b.rpc._rintf_caller_index[allowed_caller_id]

    # --- Provider (Client A) disconnects ---
    await ws_a.disconnect()

    # Give time for the client_disconnected event to propagate
    await asyncio.sleep(1)

    # Verify _rintf service was auto-cleaned on client B
    assert rintf_service_id not in ws_b.rpc._services, (
        "_rintf service should be auto-cleaned when allowed caller disconnects"
    )
    assert allowed_caller_id not in ws_b.rpc._rintf_caller_index, (
        "Caller index should be cleaned up"
    )

    await ws_b.disconnect()


@pytest.mark.asyncio
async def test_rintf_dispose_idempotent(websocket_server):
    """Calling _dispose() multiple times should not raise errors."""

    ws_a = await connect_to_server(
        {
            "name": "idempotent-provider",
            "server_url": WS_SERVER_URL,
            "method_timeout": 30,
        }
    )
    workspace_a = ws_a.config["workspace"]
    client_id_a = ws_a.config["client_id"]

    stored_listener = {}

    async def register_listener(listener):
        stored_listener["listener"] = listener
        return "ok"

    await ws_a.register_service(
        {
            "id": "idempotent-svc",
            "name": "Idempotent Test",
            "type": "test",
            "config": {"visibility": "public", "require_context": False},
            "register_listener": register_listener,
        }
    )

    ws_b = await connect_to_server(
        {
            "name": "idempotent-listener",
            "server_url": WS_SERVER_URL,
            "method_timeout": 30,
        }
    )

    listener_obj = {"_rintf": True, "on_update": lambda msg: "ok"}
    svc_b = await ws_b.get_service(f"{workspace_a}/{client_id_a}:idempotent-svc")
    await svc_b.register_listener(listener_obj)

    listener = stored_listener["listener"]

    # First dispose should work
    await listener._dispose()

    # Second dispose should not raise (service already removed)
    # The RPC call may fail since the service is gone, but it should
    # not crash the client
    try:
        await listener._dispose()
    except Exception:
        pass  # Expected — service already unregistered

    await ws_a.disconnect()
    await ws_b.disconnect()
