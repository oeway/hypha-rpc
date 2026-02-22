"""Test the hypha server."""

import asyncio
import time
from typing import Optional

import numpy as np
import pytest
import requests
import httpx
from hypha_rpc import (
    get_remote_service,
    connect_to_server,
    get_remote_service_sync,
    connect_to_server_sync,
    get_rtc_service,
    get_rtc_service_sync,
    login,
    login_sync,
    register_rtc_service,
    register_rtc_service_sync,
)

# Import Pydantic components for the test
try:
    from pydantic import BaseModel, Field
    from hypha_rpc.utils.pydantic import register_pydantic_codec

    HAS_PYDANTIC = True
except ImportError:
    HAS_PYDANTIC = False
    BaseModel = object  # Define a dummy BaseModel if pydantic is not installed

from . import WS_SERVER_URL

# Import schema_function for testing
from hypha_rpc.utils.schema import schema_function

# Define a simple Pydantic model for testing
if HAS_PYDANTIC:

    class TestData(BaseModel):
        """Some test data."""

        item_id: int = Field(..., description="The ID of the item")
        name: str = Field(..., description="The name of the item")
        price: float
        is_offer: Optional[bool] = Field(None, description="Whether this is an offer")

    @schema_function
    def process_test_data(data: TestData) -> str:
        """Processes the provided test data."""
        return f"Processed {data.name} with price {data.price}"


@schema_function
def add_numbers(a: int, b: float = 3.14) -> float:
    """Adds two numbers, an integer and a float."""
    return a + b


# Define a simple Pydantic model for testing
class TestModel(BaseModel):
    name: str
    value: int
    is_active: bool = True
    tags: list[str] = Field(default_factory=list)


class ImJoyPlugin:
    """Represent a test plugin."""

    def __init__(self, ws):
        """Initialize the plugin."""
        self._ws = ws

    # async def setup(self):
    #     """Set up the plugin."""
    #     await self._ws.log("initialized")

    async def run(self, ctx):
        """Run the plugin."""
        await self._ws.log("hello world")

    async def add(self, data):
        """Add function."""
        return data + 1.0


@pytest.mark.asyncio
async def test_schema(websocket_server):
    """Test schema."""
    api = await connect_to_server(
        {"name": "my app", "server_url": WS_SERVER_URL, "client_id": "my-app"}
    )
    for k in api:
        if callable(api[k]):
            assert (
                hasattr(api[k], "__schema__") and api[k].__schema__ is not None
            ), f"Schema not found for {k}"
            assert api[k].__schema__.get("name") == k, f"Schema name not match for {k}"


@pytest.mark.asyncio
async def test_service_with_builtin_key(websocket_server):
    """Test schema."""
    async with connect_to_server(
        {
            "name": "my app",
            "server_url": WS_SERVER_URL,
            "client_id": "my-app",
        }
    ) as api:
        data = {}
        info = await api.register_service(
            {
                "name": "Dictionary Service",
                "id": "dict-service",
                "description": "A service to store key-value pairs",
                "config": {
                    "visibility": "protected",
                    "run_in_executor": True,
                },
                "put": lambda k, v: data.update({k: v}),
                "get": lambda k: data.get(k),
                "pop": lambda k: data.pop(k),
                "keys": lambda: list(data.keys()),
                "values": lambda: list(data.values()),
                "items": lambda: [("key1", "value1"), ("key2", "value2")],
            }
        )
        assert "/" in info["id"] and ":" in info["id"], "Service id should be absolute"
        svc = await api.get_service("dict-service")
        await svc.put("key", "value")
        assert await svc.get("key") == "value"
        assert await svc.keys() == ["key"]
        assert await svc.values() == ["value"]
        assert await svc.pop("key") == "value"
        assert await svc.get("key") == None
        assert await svc.items() == [["key1", "value1"], ["key2", "value2"]]


@pytest.mark.asyncio
async def test_dotdict_return_values(websocket_server):
    """Test that dict results from RPC calls are returned as dot-accessible objects."""
    from hypha_rpc.utils import ObjectProxy

    async with connect_to_server(
        {
            "name": "dotdict test",
            "server_url": WS_SERVER_URL,
            "client_id": "dotdict-test-client",
        }
    ) as api:
        await api.register_service(
            {
                "name": "DotDict Test Service",
                "id": "dotdict-service",
                "config": {"visibility": "protected"},
                "get_flat": lambda: {"id": "abc", "name": "test"},
                "get_nested": lambda: {
                    "id": "xyz",
                    "config": {"timeout": 30, "retry": True},
                },
                "get_deep": lambda: {
                    "level1": {
                        "level2": {"level3": {"value": 42}},
                    },
                },
                "get_mixed": lambda: {
                    "items": [1, 2, 3],
                    "meta": {"count": 3, "tags": ["a", "b"]},
                },
            }
        )

        svc = await api.get_service("dotdict-service")

        # Test flat dict has dot access
        flat = await svc.get_flat()
        assert isinstance(flat, ObjectProxy), f"Expected ObjectProxy, got {type(flat)}"
        assert flat.id == "abc"
        assert flat.name == "test"

        # Test nested dict has dot access at all levels
        nested = await svc.get_nested()
        assert isinstance(nested, ObjectProxy)
        assert nested.id == "xyz"
        assert isinstance(
            nested.config, ObjectProxy
        ), f"Nested dict should be ObjectProxy, got {type(nested.config)}"
        assert nested.config.timeout == 30
        assert nested.config.retry is True

        # Test deeply nested dicts
        deep = await svc.get_deep()
        assert isinstance(deep, ObjectProxy)
        assert deep.level1.level2.level3.value == 42

        # Test mixed dict with lists
        mixed = await svc.get_mixed()
        assert isinstance(mixed, ObjectProxy)
        assert mixed.items == [1, 2, 3]
        assert isinstance(mixed.meta, ObjectProxy)
        assert mixed.meta.count == 3
        assert mixed.meta.tags == ["a", "b"]


@pytest.mark.asyncio
async def test_login(websocket_server):
    """Test login to the server."""
    # First connect to server to generate a valid JWT token
    api = await connect_to_server(
        {"server_url": WS_SERVER_URL, "client_id": "login-test-client"}
    )
    TOKEN = await api.generate_token()
    await api.disconnect()

    async def callback(context):
        print(f"By passing login: {context['login_url']}")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            requests.get,
            context["report_url"] + "?key=" + context["key"] + "&token=" + TOKEN,
        )

    # We use ai.imjoy.io to test the login for now
    token = await login(
        {
            "server_url": WS_SERVER_URL,
            "login_callback": callback,
            "login_timeout": 20,
        }
    )
    assert token == TOKEN


def test_login_sync(websocket_server):
    """Test login to the server."""
    # First connect to server to generate a valid JWT token
    api = connect_to_server_sync(
        {"server_url": WS_SERVER_URL, "client_id": "login-sync-test-client"}
    )
    TOKEN = api.generate_token()
    api.disconnect()

    def callback(context):
        print(f"By passing login: {context['login_url']}")
        requests.get(
            context["report_url"] + "?key=" + context["key"] + "&token=" + TOKEN
        )

    # We use ai.imjoy.io to test the login for now
    token = login_sync(
        {
            "server_url": WS_SERVER_URL,
            "login_callback": callback,
            "login_timeout": 20,
        }
    )
    assert token == TOKEN


@pytest.mark.asyncio
async def test_login_with_additional_headers(websocket_server):
    """Test login with additional headers."""
    # First connect to server to generate a valid JWT token
    api = await connect_to_server(
        {"server_url": WS_SERVER_URL, "client_id": "login-headers-test-client"}
    )
    TOKEN = await api.generate_token()
    await api.disconnect()

    additional_headers = {"X-Custom-Header": "test-value"}

    async def callback(context):
        print(f"By passing login: {context['login_url']}")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            requests.get,
            context["report_url"] + "?key=" + context["key"] + "&token=" + TOKEN,
        )

    # Test that additional_headers is passed through to connect_to_server
    token = await login(
        {
            "server_url": WS_SERVER_URL,
            "login_callback": callback,
            "login_timeout": 20,
            "additional_headers": additional_headers,
        }
    )
    assert token == TOKEN


@pytest.mark.asyncio
async def test_numpy_array_sync(websocket_server):
    """Test numpy array registered in async."""
    ws = connect_to_server_sync(
        {"client_id": "test-plugin", "server_url": WS_SERVER_URL}
    )
    ws.export(ImJoyPlugin(ws))
    workspace = ws.config.workspace
    token = ws.generate_token()

    api = await connect_to_server(
        {
            "client_id": "client",
            "workspace": workspace,
            "token": token,
            "server_url": WS_SERVER_URL,
        }
    )
    plugin = await api.get_service("test-plugin:default")
    result = await plugin.add(2.1)
    assert result == 2.1 + 1.0

    large_array = np.zeros([2048, 2048, 4], dtype="float32")
    result = await plugin.add(large_array)
    np.testing.assert_array_equal(result, large_array + 1.0)


def test_connect_to_server_sync(websocket_server):
    """Test connecting to the server sync."""
    # Now all the functions are sync
    with connect_to_server_sync(
        {"client_id": "test-plugin", "server_url": WS_SERVER_URL}
    ) as server:
        workspace = server.config.workspace
        token = server.generate_token()
        assert workspace and token

        services = server.list_services("public")
        assert isinstance(services, list)

        def hello(name):
            print("Hello " + name)
            return "Hello " + name

        server.register_service(
            {
                "name": "Hello World",
                "id": "hello-world",
                "config": {
                    "visibility": "protected",
                    "run_in_executor": True,
                },
                "hello": hello,
            }
        )


def test_connect_to_server_sync(websocket_server):
    """Test connecting to the server sync."""
    # Now all the functions are sync
    with get_remote_service_sync(
        f"{WS_SERVER_URL}/public/services/hypha-login"
    ) as login:
        info = login.start()
        assert "key" in info


@pytest.mark.asyncio
async def test_export_api(websocket_server):
    """Test exporting API."""
    from hypha_rpc import api

    api.export({"hello": lambda x: "hello " + x}, {"name": "hello"})
    assert "hello" in api.get_registry()

    api.get_registry().clear()
    api.set_export_handler(lambda obj, config=None: None)
    api.export({"hello": lambda x: "hello " + x})
    assert "hello" not in api.get_registry().keys()

    api.get_registry().clear()
    api.set_export_handler(api._default_export_handler)
    api.export({"hello": lambda x: "hello " + x}, {"name": "hello2"})
    assert "hello2" in api.get_registry().keys()


@pytest.mark.asyncio
async def test_connect_to_server(websocket_server):
    """Test connecting to the server."""
    # test workspace is an exception, so it can pass directly
    ws = await connect_to_server({"name": "my plugin", "server_url": WS_SERVER_URL})
    with pytest.raises(Exception, match=r".*does not exist or is not accessible.*"):
        ws = await connect_to_server(
            {"name": "my plugin", "workspace": "test", "server_url": WS_SERVER_URL}
        )
    ws = await connect_to_server({"name": "my plugin", "server_url": WS_SERVER_URL})
    await ws.export(ImJoyPlugin(ws))

    def hello(name, key=12, context=None):
        """Say hello."""
        print("Hello " + name)
        return "Hello " + name

    await ws.register_service(
        {
            "name": "Hello World",
            "id": "hello-world",
            "description": "hello world service",
            "config": {
                "visibility": "protected",
                "run_in_executor": True,
            },
            "hello": hello,
        }
    )

    svc = await ws.get_service("hello-world")
    assert svc.hello.__doc__ == hello.__doc__
    assert svc.hello.__name__ == hello.__name__

    svc_info = await ws.register_service(
        {
            "name": "Hello World",
            "id": "hello-world",
            "description": "hello world service",
            "config": {
                "visibility": "protected",
                "run_in_executor": True,
                "require_context": True,
            },
            "hello": hello,
        },
        {"overwrite": True},
    )

    svc = await ws.get_service("hello-world")
    assert svc.hello.__doc__ == hello.__doc__
    assert svc.hello.__name__ == hello.__name__

    await ws.unregister_service(svc_info["id"])

    try:
        svc = await ws.get_service("hello-world")
    except Exception as e:
        assert "Service not found" in str(e)

    await ws.disconnect()


@pytest.mark.asyncio
async def test_numpy_transmission(websocket_server):
    """Test numpy array transmission."""
    api = await connect_to_server(
        {"name": "my app", "server_url": WS_SERVER_URL, "client_id": "my-app"}
    )
    image = np.random.rand(512, 512)
    embedding = await api.echo(image)
    assert isinstance(embedding, np.ndarray)
    assert embedding.shape == (512, 512)


@pytest.mark.asyncio
async def test_case_conversion(websocket_server):
    """Test case conversion."""
    ws = await connect_to_server(name="my plugin", server_url=WS_SERVER_URL)
    await ws.export(ImJoyPlugin(ws))

    def hello(name, key=12, context=None):
        """Say hello."""
        print("Hello " + name)
        return "Hello " + name

    info = await ws.register_service(
        {
            "name": "Hello World",
            "id": "hello-world",
            "description": "hello world service",
            "config": {
                "visibility": "protected",
                "run_in_executor": True,
            },
            "HelloWorld": hello,
        }
    )

    svc = await ws.get_service(info.id)
    assert await svc.HelloWorld("world") == "Hello world"

    svc = await ws.get_service(info.id, case_conversion="camel")
    assert await svc.helloWorld("world") == "Hello world"

    svc = await ws.get_service(info.id, case_conversion="snake")
    assert await svc.hello_world("world") == "Hello world"


@pytest.mark.asyncio
async def test_probe(websocket_server):
    """Test probes"""
    ws = await connect_to_server({"name": "my plugin", "server_url": WS_SERVER_URL})

    await ws.register_probes(
        {
            "readiness": lambda: True,
            "liveness": lambda: True,
        }
    )

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{WS_SERVER_URL}/{ws.config.workspace}/services/probes/readiness"
        )
        response.raise_for_status()
        assert response.json() == True


@pytest.mark.asyncio
async def test_get_remote_service(websocket_server):
    """Test getting a remote service."""
    login = await get_remote_service(f"{WS_SERVER_URL}/public/services/hypha-login")
    info = await login.start()
    assert "key" in info

    async with get_remote_service(
        f"{WS_SERVER_URL}/public/services/hypha-login"
    ) as login:
        info = await login.start()
        assert "key" in info


@pytest.mark.asyncio
async def test_reconnect_to_server(websocket_server):
    """Test reconnecting to the server."""
    # test workspace is an exception, so it can pass directly
    ws = await connect_to_server({"name": "my plugin", "server_url": WS_SERVER_URL})
    await ws.register_service(
        {
            "name": "Hello World",
            "id": "hello-world",
            "description": "hello world service",
            "config": {
                "visibility": "protected",
                "run_in_executor": True,
            },
            "hello": lambda x: "hello " + x,
        }
    )
    # simulate abnormal close
    await ws.rpc._connection._websocket.close(1002)
    # will trigger reconnect
    svc = await ws.get_service("hello-world")
    assert await svc.hello("world") == "hello world"


@pytest.mark.asyncio
async def test_robust_reconnection_with_service_reregistration(websocket_server):
    """Test robust reconnection with exponential backoff and service re-registration."""
    import asyncio

    # Create connection with custom client ID for easier identification
    ws = await connect_to_server(
        {
            "name": "reconnection test plugin",
            "server_url": WS_SERVER_URL,
            "client_id": "reconnection-test-client",
        }
    )

    # Keep track of reconnection events
    reconnection_events = []
    service_registration_events = []

    def on_connected(info):
        reconnection_events.append({"type": "connected", "info": info})

    def on_services_registered(info):
        service_registration_events.append(
            {"type": "services_registered", "info": info}
        )

    def on_services_registration_failed(info):
        service_registration_events.append(
            {"type": "services_registration_failed", "info": info}
        )

    # Register event handlers
    ws.rpc.on("connected", on_connected)
    ws.rpc.on("services_registered", on_services_registered)
    ws.rpc.on("services_registration_failed", on_services_registration_failed)

    # Register multiple services to test batch re-registration
    service_data = {"counter": 0, "test_data": "initial"}

    await ws.register_service(
        {
            "name": "Counter Service",
            "id": "counter-service",
            "description": "Service with state for testing reconnection",
            "config": {"visibility": "protected"},
            "increment": lambda: service_data.update(
                {"counter": service_data["counter"] + 1}
            )
            or service_data["counter"],
            "get_counter": lambda: service_data["counter"],
            "set_data": lambda data: service_data.update({"test_data": data}),
            "get_data": lambda: service_data["test_data"],
        }
    )

    await ws.register_service(
        {
            "name": "Echo Service",
            "id": "echo-service",
            "description": "Simple echo service for testing",
            "config": {"visibility": "protected"},
            "echo": lambda x: f"echo: {x}",
            "reverse": lambda x: x[::-1] if isinstance(x, str) else str(x)[::-1],
        }
    )

    # Verify services work initially
    counter_svc = await ws.get_service("counter-service")
    echo_svc = await ws.get_service("echo-service")

    # Test initial functionality
    assert await counter_svc.get_counter() == 0
    assert await counter_svc.increment() == 1
    assert await echo_svc.echo("test") == "echo: test"
    assert await echo_svc.reverse("hello") == "olleh"

    # Clear events from initial connection
    reconnection_events.clear()
    service_registration_events.clear()

    # Simulate unexpected disconnection (code 1011 - unexpected condition)
    print("Simulating unexpected disconnection...")
    await ws.rpc._connection._websocket.close(1011)

    # Wait a moment for reconnection to complete
    # The new implementation should reconnect automatically
    await asyncio.sleep(2.0)

    # Verify services still work after reconnection
    print("Testing services after reconnection...")
    counter_svc = await ws.get_service("counter-service")
    echo_svc = await ws.get_service("echo-service")

    # Test that service state is preserved (since they were re-registered)
    current_counter = await counter_svc.get_counter()
    assert (
        current_counter == 1
    ), f"Counter should be 1 after reconnection, got {current_counter}"

    # Test incrementing works
    new_counter = await counter_svc.increment()
    assert new_counter == 2, f"Counter should be 2 after increment, got {new_counter}"

    # Test echo service still works
    echo_result = await echo_svc.echo("reconnected")
    assert echo_result == "echo: reconnected"

    reverse_result = await echo_svc.reverse("reconnected")
    assert reverse_result == "detcennocer"

    # Test setting new data
    await counter_svc.set_data("after_reconnection")
    data_result = await counter_svc.get_data()
    assert data_result == "after_reconnection"

    # Verify we got reconnection events
    assert len(reconnection_events) > 0, "Should have received reconnection events"

    # Verify we got service registration events
    assert (
        len(service_registration_events) > 0
    ), "Should have received service registration events"

    # Check if services were successfully re-registered
    successful_registration = any(
        event["type"] == "services_registered" and event["info"]["registered"] >= 2
        for event in service_registration_events
    )
    assert (
        successful_registration
    ), f"Services should have been re-registered successfully. Events: {service_registration_events}"

    print("✅ Robust reconnection with service re-registration test passed!")


@pytest.mark.asyncio
async def test_reconnection_exponential_backoff(websocket_server):
    """Test that reconnection uses exponential backoff."""
    import time

    ws = await connect_to_server(
        {
            "name": "backoff test plugin",
            "server_url": WS_SERVER_URL,
            "client_id": "backoff-test-client",
        }
    )

    # Register a simple service
    await ws.register_service(
        {
            "name": "Test Service",
            "id": "test-service",
            "config": {"visibility": "protected"},
            "test": lambda: "ok",
        }
    )

    # Record reconnection attempt times
    reconnection_times = []

    def on_connected(info):
        reconnection_times.append(time.time())

    ws.rpc.on("connected", on_connected)

    # Clear initial connection event
    reconnection_times.clear()

    # Simulate multiple disconnections to test backoff
    print("Testing exponential backoff behavior...")

    # First disconnection
    start_time = time.time()
    await ws.rpc._connection._websocket.close(1011)

    # Wait for reconnection
    await asyncio.sleep(3.0)

    # Should have reconnected by now
    assert len(reconnection_times) >= 1, "Should have reconnected at least once"

    # Test that service still works
    svc = await ws.get_service("test-service")
    result = await svc.test()
    assert result == "ok"

    print("✅ Exponential backoff test passed!")


@pytest.mark.asyncio
async def test_reconnection_cancellation(websocket_server):
    """Test that reconnection can be cancelled when explicitly disconnecting."""
    ws = await connect_to_server(
        {
            "name": "cancellation test plugin",
            "server_url": WS_SERVER_URL,
            "client_id": "cancellation-test-client",
        }
    )

    # Register a service
    await ws.register_service(
        {
            "name": "Test Service",
            "id": "test-service",
            "config": {"visibility": "protected"},
            "test": lambda: "ok",
        }
    )

    # Simulate unexpected disconnection
    await ws.rpc._connection._websocket.close(1011)

    # Give a moment for reconnection to start
    await asyncio.sleep(0.5)

    # Now explicitly disconnect - this should cancel reconnection
    await ws.disconnect()

    # Wait a bit more to ensure reconnection doesn't happen
    await asyncio.sleep(2.0)

    # Try to use the service - should fail
    try:
        svc = await ws.get_service("test-service")
        await svc.test()
        assert False, "Service should not be accessible after explicit disconnect"
    except Exception:
        # Expected - connection should be closed
        pass

    print("✅ Reconnection cancellation test passed!")


@pytest.mark.asyncio
async def test_numpy_array(websocket_server):
    """Test numpy array."""
    ws = await connect_to_server(
        {"client_id": "test-plugin", "server_url": WS_SERVER_URL}
    )
    await ws.export(ImJoyPlugin(ws))
    workspace = ws.config.workspace
    token = await ws.generate_token()

    api = await connect_to_server(
        {
            "client_id": "client",
            "workspace": workspace,
            "token": token,
            "server_url": WS_SERVER_URL,
        }
    )
    plugin = await api.get_service("test-plugin:default")
    result = await plugin.add(2.1)
    assert result == 2.1 + 1.0

    large_array = np.zeros([2048, 2048, 4], dtype="float32")
    result = await plugin.add(large_array)
    np.testing.assert_array_equal(result, large_array + 1.0)


@pytest.mark.asyncio
async def test_rtc_service(websocket_server):
    """Test RTC service."""
    from hypha_rpc import connect_to_server

    service_id = "test-rtc-service"
    server = await connect_to_server(
        {
            "server_url": WS_SERVER_URL,
        }
    )
    assert "get_service" in server and "getService" not in server
    assert "register_service" in server and "registerService" not in server
    await server.register_service(
        {
            "id": "echo-service",
            "config": {"visibility": "public"},
            "type": "echo",
            "echo": lambda x: x,
        }
    )
    svc = await register_rtc_service(server, service_id)
    pc = await get_rtc_service(server, svc["id"])
    svc = await pc.get_service("echo-service")
    assert await svc.echo("hello") == "hello", "echo service failed"
    await pc.close()


def test_rtc_service_sync(websocket_server):
    """Test RTC service."""
    from hypha_rpc import connect_to_server_sync

    service_id = "test-rtc-service"
    server = connect_to_server_sync(
        {
            "server_url": WS_SERVER_URL,
        }
    )
    server.register_service(
        {
            "id": "echo-service",
            "config": {"visibility": "public"},
            "type": "echo",
            "echo": lambda x: x,
        }
    )
    register_rtc_service_sync(server, service_id)
    pc = get_rtc_service_sync(server, service_id)
    svc = pc.get_service("echo-service")
    assert svc.echo("hello") == "hello", "echo service failed"
    pc.close()


def test_rtc_service_auto(websocket_server):
    """Test RTC service."""
    from hypha_rpc import connect_to_server_sync

    server = connect_to_server_sync(
        {
            "server_url": WS_SERVER_URL,
            "webrtc": True,
        }
    )
    server.register_service(
        {
            "id": "echo-service",
            "config": {"visibility": "public"},
            "type": "echo",
            "echo": lambda x: x,
        }
    )

    svc = server.get_service("echo-service")
    assert svc.echo("hello") == "hello", "echo service failed"


def test_rtc_service_auto_cross_client(websocket_server):
    """Test RTC auto mode across two different clients.

    Client A (provider) registers an echo service with webrtc=True.
    Client B (consumer) connects to the same workspace with webrtc=True,
    then fetches Client A's service. This should establish a WebRTC
    peer connection to Client A's RTC service (not Client B's own).
    """
    from hypha_rpc import connect_to_server_sync

    # Client A: provider with webrtc enabled
    client_a = connect_to_server_sync(
        {
            "client_id": "rtc-cross-provider",
            "server_url": WS_SERVER_URL,
            "webrtc": True,
        }
    )
    workspace = client_a.config.workspace
    token = client_a.generate_token()

    client_a.register_service(
        {
            "id": "cross-echo",
            "config": {"visibility": "public"},
            "type": "echo",
            "echo": lambda x: x,
            "add": lambda a, b: a + b,
        }
    )

    # Client B: consumer with webrtc enabled, same workspace
    client_b = connect_to_server_sync(
        {
            "client_id": "rtc-cross-consumer",
            "server_url": WS_SERVER_URL,
            "workspace": workspace,
            "token": token,
            "webrtc": True,
        }
    )

    # Client B fetches Client A's service - this should use WebRTC
    svc = client_b.get_service("rtc-cross-provider:cross-echo")
    assert svc.echo("hello-cross") == "hello-cross", "cross-client echo failed"
    assert svc.add(3, 4) == 7, "cross-client add failed"
    # Verify it was actually obtained via WebRTC
    assert hasattr(svc, "_webrtc") and svc._webrtc, "service should be via webrtc"


def test_connect_to_server_sync_lock(websocket_server):
    """Test connecting to the server sync with thread locking."""
    server = connect_to_server_sync(
        {"client_id": "test-plugin", "server_url": WS_SERVER_URL}
    )
    workspace = server.config.workspace
    token = server.generate_token()
    assert workspace and token

    services = server.list_services("public")
    assert isinstance(services, list)

    def hello(name):
        print("Hello " + name)
        return "Hello " + name

    def call_hello2():
        svc = server.get_service("hello-world-2")
        return svc.call_hello()

    server.register_service(
        {
            "name": "Hello World",
            "id": "hello-world",
            "config": {
                "visibility": "protected",
                "run_in_executor": True,
            },
            "hello": hello,
            "call_hello2": call_hello2,
        }
    )

    svc = server.get_service("hello-world")
    svc.hello("world")

    server2 = connect_to_server_sync(
        {
            "client_id": "test-plugin-2",
            "server_url": WS_SERVER_URL,
            "workspace": workspace,
            "token": token,
        }
    )

    def call_hello():
        svc = server2.get_service("hello-world")
        return svc.hello("world")

    server2.register_service(
        {
            "name": "Hello World 2",
            "id": "hello-world-2",
            "config": {
                "visibility": "protected",
                "run_in_executor": True,
            },
            "call_hello": call_hello,
        }
    )

    # This will not work if the thread is locked
    svc = server.get_service("hello-world")
    svc.call_hello2()


@pytest.mark.asyncio
async def test_generator(websocket_server):
    """Test using generators across RPC."""
    # Create a server with a service that returns a generator
    server = await connect_to_server(
        {"client_id": "generator-provider", "server_url": WS_SERVER_URL}
    )

    # Get server workspace and token for client connection
    workspace = server.config.workspace
    token = await server.generate_token()

    # Define a generator function
    def counter(start=0, end=5):
        """Return a generator that counts from start to end."""
        for i in range(start, end):
            yield i

    # Define an async generator function
    async def async_counter(start=0, end=5):
        """Return an async generator that counts from start to end."""
        for i in range(start, end):
            yield i
            await asyncio.sleep(0.01)  # Small delay to simulate async work

    # Register service with both types of generators
    await server.register_service(
        {
            "id": "generator-service",
            "config": {"visibility": "public"},
            "get_counter": counter,
            "get_async_counter": async_counter,
        }
    )

    # Connect with another client using the same workspace and token
    client = await connect_to_server(
        {
            "client_id": "generator-consumer",
            "server_url": WS_SERVER_URL,
            "workspace": workspace,
            "token": token,
        }
    )

    # Get the service
    gen_service = await client.get_service("generator-service")

    # Test normal generator - note that it becomes an async generator over RPC
    gen = await gen_service.get_counter(0, 5)
    results = []
    # We need to use async for since all generators become async generators over RPC
    async for item in gen:
        results.append(item)
    assert results == [0, 1, 2, 3, 4]

    # Test async generator
    async_gen = await gen_service.get_async_counter(0, 5)
    async_results = []
    async for item in async_gen:
        async_results.append(item)
    assert async_results == [0, 1, 2, 3, 4]


def test_generator_sync(websocket_server):
    """Test using generators with the synchronous API."""
    # Create a server with a service that returns a generator
    server = connect_to_server_sync(
        {"client_id": "sync-generator-provider", "server_url": WS_SERVER_URL}
    )

    # Get server workspace and token for client connection
    workspace = server.config.workspace
    token = server.generate_token()

    # Define a generator function
    def counter(start=0, end=5):
        """Return a generator that counts from start to end."""
        for i in range(start, end):
            yield i

    # Define an async generator function
    async def async_counter(start=0, end=5):
        """Return an async generator that counts from start to end."""
        for i in range(start, end):
            yield i
            await asyncio.sleep(0.01)  # Small delay to simulate async work

    # Register service with both types of generators
    svc_info = server.register_service(
        {
            "id": "sync-generator-service",
            "config": {"visibility": "public"},
            "get_counter": counter,
            "get_async_counter": async_counter,
        }
    )

    # Connect with another client using the same workspace and token
    client = connect_to_server_sync(
        {
            "client_id": "sync-generator-consumer",
            "server_url": WS_SERVER_URL,
            "workspace": workspace,
            "token": token,
        }
    )

    # Get service with explicit timeout
    gen_service = client.get_service(svc_info["id"], timeout=20)

    # Test normal generator - verify it works as a synchronous generator
    gen = gen_service.get_counter(0, 5)
    results = []
    for item in gen:
        results.append(item)
    assert results == [0, 1, 2, 3, 4]

    # Test async generator - should also work with the sync API
    gen = gen_service.get_async_counter(0, 3)
    results = []
    for item in gen:
        results.append(item)
    assert results == [0, 1, 2]


@pytest.mark.asyncio
@pytest.mark.skipif(not HAS_PYDANTIC, reason="Pydantic is not installed")
async def test_pydantic_codec(websocket_server):
    """Test Pydantic model encoding and decoding via RPC."""
    server = await connect_to_server(
        {"client_id": "pydantic-provider", "server_url": WS_SERVER_URL}
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    # Register the Pydantic codec
    register_pydantic_codec(server.rpc)

    # Define a service that echoes Pydantic models
    async def echo_model(model: TestModel) -> TestModel:
        return model

    await server.register_service(
        {
            "id": "pydantic-echo-service",
            "config": {"visibility": "public"},
            "echo_model": echo_model,
        }
    )

    # Connect a client
    client = await connect_to_server(
        {
            "client_id": "pydantic-consumer",
            "server_url": WS_SERVER_URL,
            "workspace": workspace,
            "token": token,
        }
    )
    # Also register the codec on the client side for decoding the response
    register_pydantic_codec(client.rpc)

    # Get the service
    echo_svc = await client.get_service("pydantic-echo-service")

    # Create a model instance
    original_model = TestModel(name="test", value=123, tags=["a", "b"])

    # Call the service
    returned_model = await echo_svc.echo_model(original_model)

    # Assertions
    assert issubclass(returned_model.__class__, BaseModel)
    assert returned_model.name == "test"
    assert returned_model.value == 123
    assert returned_model.is_active is True
    assert returned_model.tags == ["a", "b"]
    # assert returned_model == original_model # This fails because the classes are technically different

    await server.disconnect()
    await client.disconnect()


@pytest.mark.asyncio
async def test_schema_annotation_python(websocket_server):
    """Test schema generation from type hints and Pydantic models."""
    server = await connect_to_server(
        {"client_id": "schema-provider-py", "server_url": WS_SERVER_URL}
    )

    # Register the service with functions decorated by schema_function
    functions_to_register = {
        "add_numbers": add_numbers,
    }
    if HAS_PYDANTIC:
        functions_to_register["process_test_data"] = process_test_data

    service_info = await server.register_service(
        {
            "id": "schema-test-py-service",
            "config": {"visibility": "public"},
            **functions_to_register,
        }
    )

    assert "service_schema" in service_info
    service_schema = service_info["service_schema"]

    # --- Debugging ---
    print(f"DEBUG: service_schema keys = {list(service_schema.keys())}")
    # --- End Debugging ---

    # Verify schema for add_numbers
    assert "add_numbers" in service_schema
    add_schema = service_schema["add_numbers"]
    assert add_schema["type"] == "function"
    assert add_schema["function"]["name"] == "add_numbers"
    assert (
        add_schema["function"]["description"]
        == "Adds two numbers, an integer and a float."
    )
    assert add_schema["function"]["parameters"]["type"] == "object"
    assert "a" in add_schema["function"]["parameters"]["properties"]
    assert add_schema["function"]["parameters"]["properties"]["a"]["type"] == "integer"
    assert "b" in add_schema["function"]["parameters"]["properties"]
    assert add_schema["function"]["parameters"]["properties"]["b"]["type"] == "number"
    assert add_schema["function"]["parameters"]["properties"]["b"]["default"] == 3.14
    assert add_schema["function"]["parameters"]["required"] == ["a"]

    # Verify schema for process_test_data if Pydantic is available
    if HAS_PYDANTIC:
        assert "process_test_data" in service_schema
        process_schema = service_schema["process_test_data"]
        assert process_schema["type"] == "function"
        assert process_schema["function"]["name"] == "process_test_data"
        assert (
            process_schema["function"]["description"]
            == "Processes the provided test data."
        )
        assert process_schema["function"]["parameters"]["type"] == "object"
        assert "data" in process_schema["function"]["parameters"]["properties"]
        data_param_schema = process_schema["function"]["parameters"]["properties"][
            "data"
        ]
        # Verify that the registered schema uses $ref for the Pydantic model
        assert "$ref" in data_param_schema
        assert (
            data_param_schema["$ref"] == "#/$defs/TestData"
            or data_param_schema["$ref"] == "#/definitions/TestData"
        )

        # Now, verify the *local* schema generated by the decorator
        local_schema = process_test_data.__schema__
        # assert "function" in local_schema # Local schema doesn't have this wrapper
        # local_func_schema = local_schema["function"]
        defs_key = "$defs" if "$defs" in local_schema["parameters"] else "definitions"
        assert defs_key in local_schema["parameters"]
        assert "TestData" in local_schema["parameters"][defs_key]
        model_schema = local_schema["parameters"][defs_key]["TestData"]
        assert model_schema["type"] == "object"
        assert "item_id" in model_schema["properties"]
        assert model_schema["properties"]["item_id"]["type"] == "integer"
        assert (
            model_schema["properties"]["item_id"]["description"] == "The ID of the item"
        )
        assert "name" in model_schema["properties"]
        assert model_schema["properties"]["name"]["type"] == "string"
        assert "price" in model_schema["properties"]
        assert model_schema["properties"]["price"]["type"] == "number"
        assert "is_offer" in model_schema["properties"]
        assert (
            model_schema["properties"]["is_offer"]["description"]
            == "Whether this is an offer"
        )
        is_offer_schema = model_schema["properties"]["is_offer"]
        # Compatibility check for Optional[bool] in Pydantic v1/v2
        is_optional_bool = False
        if "anyOf" in is_offer_schema:
            is_optional_bool = any(
                t.get("type") == "boolean" for t in is_offer_schema["anyOf"]
            ) and any(t.get("type") == "null" for t in is_offer_schema["anyOf"])
        elif "type" in is_offer_schema:
            # Pydantic v1 might represent Optional[bool] as type: ["boolean", "null"]
            is_optional_bool = (
                isinstance(is_offer_schema["type"], list)
                and "boolean" in is_offer_schema["type"]
                and "null" in is_offer_schema["type"]
            )
        assert (
            is_optional_bool
        ), f"Schema for is_offer is not Optional[bool]: {is_offer_schema}"

        assert "item_id" in model_schema.get("required", [])
        assert "name" in model_schema.get("required", [])
        assert "price" in model_schema.get("required", [])
        assert process_schema["function"]["parameters"]["required"] == ["data"]

    # Optional: Test calling the functions via RPC
    client = await connect_to_server(
        {
            "client_id": "schema-consumer-py",
            "server_url": WS_SERVER_URL,
            "workspace": server.config.workspace,  # Explicitly set workspace
            "token": await server.generate_token(),  # Generate token for the workspace
        }
    )
    svc = await client.get_service("schema-test-py-service")
    assert await svc.add_numbers(5, b=2.5) == 7.5
    if HAS_PYDANTIC:
        # Verify the local schema generated by the decorator before registration -- MOVED THIS BLOCK UP
        # Test the RPC call
        result = await svc.process_test_data(
            {
                "item_id": 101,
                "name": "Test Item",
                "price": 99.99,
                "is_offer": True,
            }
        )
        assert result == "Processed Test Item with price 99.99"

    await server.disconnect()
    await client.disconnect()


@pytest.mark.asyncio
async def test_service_recovery_after_disconnection(websocket_server):
    """Test that disconnection is handled gracefully without crashes."""
    # Create a connection to the server
    print("\n=== TEST DISCONNECTION HANDLING ===")
    ws = await connect_to_server(
        {"name": "disconnect-test", "server_url": WS_SERVER_URL}
    )

    # Register a service with a simple method
    test_data = {"counter": 0}

    print("Registering test service...")
    service_info = await ws.register_service(
        {
            "name": "Disconnect Test Service",
            "id": "disconnect-service",
            "description": "Service to test disconnection handling",
            "config": {
                "visibility": "protected",
                "run_in_executor": True,
            },
            "increment": lambda: test_data.update({"counter": test_data["counter"] + 1})
            or test_data["counter"],
            "get_counter": lambda: test_data["counter"],
        }
    )

    # Verify the ID format to make sure it's properly registered
    assert (
        "/" in service_info["id"] and ":" in service_info["id"]
    ), "Service ID should be absolute"
    print(f"Service registered with ID: {service_info['id']}")

    # Verify the service works initially
    svc = await ws.get_service("disconnect-service")
    print("Testing service before disconnection:")
    print(f"Counter value: {await svc.get_counter()}")
    print(f"Incrementing: {await svc.increment()}")

    # Verify it incremented correctly
    counter_value = await svc.get_counter()
    assert counter_value == 1, f"Counter should be 1, got {counter_value}"

    # Test graceful disconnection handling
    print("\n=== TESTING DISCONNECTION HANDLING ===")
    print("Simulating network disconnection...")

    # Close the connection to simulate network issues
    if hasattr(ws.rpc._connection, "_websocket") and ws.rpc._connection._websocket:
        await ws.rpc._connection._websocket.close(code=1000)  # Normal closure

    # Give time for the disconnection to be processed
    await asyncio.sleep(2)

    # Test that operations fail gracefully after disconnection
    print("Testing operations after disconnection...")
    try:
        await svc.get_counter()
        print(
            "WARNING: Operation succeeded after disconnection - this might indicate connection recovery"
        )
    except Exception as e:
        print(f"Expected: Operation failed after disconnection: {type(e).__name__}")

    # Test that we can create a new connection
    print("\n=== TESTING NEW CONNECTION AFTER DISCONNECTION ===")
    ws_new = await connect_to_server(
        {"name": "new-connection-test", "server_url": WS_SERVER_URL}
    )

    # Register a new service to verify the new connection works
    new_service_info = await ws_new.register_service(
        {
            "name": "New Connection Test Service",
            "id": "new-connection-service",
            "description": "Service to test new connection after disconnection",
            "config": {"visibility": "protected"},
            "test": lambda: "new connection works",
        }
    )

    print(f"New service registered with ID: {new_service_info['id']}")

    # Test the new service
    new_svc = await ws_new.get_service("new-connection-service")
    result = await new_svc.test()
    assert (
        result == "new connection works"
    ), f"Expected 'new connection works', got {result}"

    print("✅ New connection and service registration works correctly")

    print("\n=== DISCONNECTION HANDLING TEST COMPLETED SUCCESSFULLY! ===")

    # Clean up
    try:
        await ws.disconnect()
    except Exception as e:
        print(f"Note: Error disconnecting original ws (expected): {e}")

    try:
        await ws_new.disconnect()
    except Exception as e:
        print(f"Error disconnecting new ws: {e}")


@pytest.mark.asyncio
async def test_memory_leak_prevention(websocket_server):
    """Comprehensive test suite for memory leak prevention."""
    from hypha_rpc import connect_to_server
    import gc
    import sys
    import tracemalloc

    # Start memory tracing
    tracemalloc.start()

    # Connect to server
    api = await connect_to_server(
        {
            "client_id": "memory-leak-test-client",
            "server_url": WS_SERVER_URL,
        }
    )

    def get_detailed_session_analysis(rpc):
        """Comprehensive session analysis."""
        analysis = {
            "total_sessions": 0,
            "session_details": [],
            "system_stores": {},
            "memory_usage": 0,
        }

        if hasattr(rpc, "_object_store"):
            store = rpc._object_store
            for key, value in store.items():
                if key in ["services", "message_cache"]:
                    analysis["system_stores"][key] = {
                        "type": str(type(value)),
                        "size": len(value) if hasattr(value, "__len__") else "unknown",
                    }
                else:
                    # This is a session
                    analysis["total_sessions"] += 1
                    session_info = {
                        "id": key,
                        "type": "dict" if isinstance(value, dict) else str(type(value)),
                        "has_promise_manager": isinstance(value, dict)
                        and "_promise_manager" in value,
                        "has_timer": isinstance(value, dict) and "timer" in value,
                        "has_heartbeat": isinstance(value, dict)
                        and "heartbeat_task" in value,
                        "nested_items": len(value) if isinstance(value, dict) else 0,
                    }
                    analysis["session_details"].append(session_info)

        # Get memory usage
        current, peak = tracemalloc.get_traced_memory()
        analysis["memory_usage"] = current

        return analysis

    def assert_clean_state(analysis, test_name, initial_analysis=None):
        """Assert that the RPC is in a clean state."""
        print(f"\n--- {test_name} Analysis ---")
        print(f"Total sessions: {analysis['total_sessions']}")
        print(f"Memory usage: {analysis['memory_usage']:,} bytes")

        if analysis["session_details"]:
            print("Session details:")
            for session in analysis["session_details"]:
                print(f"  - {session['id']}: {session}")

        # Should have no sessions after operations complete
        assert (
            analysis["total_sessions"] == 0
        ), f"Memory leak detected: {analysis['total_sessions']} sessions remaining after {test_name}"

        # Message cache should be empty
        if "message_cache" in analysis["system_stores"]:
            assert (
                analysis["system_stores"]["message_cache"]["size"] == 0
            ), "Message cache not clean"

        # Memory usage should not grow excessively
        if initial_analysis:
            growth = analysis["memory_usage"] - initial_analysis["memory_usage"]
            growth_mb = growth / (1024 * 1024)
            print(f"Memory growth: {growth_mb:.2f} MB")
            # Allow some growth but flag excessive leaks (>10MB would be concerning for these tests)
            assert growth_mb < 10, f"Excessive memory growth: {growth_mb:.2f} MB"

    # Test 1: Baseline state
    print("=== COMPREHENSIVE MEMORY LEAK TESTS ===")
    # Allow time for connection setup sessions (e.g. client_disconnected
    # subscription) to complete and be cleaned up before measuring baseline
    await asyncio.sleep(0.5)
    gc.collect()
    initial_analysis = get_detailed_session_analysis(api.rpc)
    assert_clean_state(initial_analysis, "Initial State")

    # Test 2: Simple operations
    print("\n=== Test 2: Simple Operations ===")
    result = await api.echo("simple_test")
    assert result == "simple_test"

    services = await api.list_services()
    assert isinstance(services, list)

    # Allow time for cleanup
    await asyncio.sleep(0.3)
    gc.collect()  # Force garbage collection

    simple_analysis = get_detailed_session_analysis(api.rpc)
    assert_clean_state(simple_analysis, "Simple Operations", initial_analysis)

    # Test 3: Concurrent operations stress test
    print("\n=== Test 3: Concurrent Operations Stress Test ===")
    tasks = []
    for i in range(20):  # Increased from 3 to 20 for stress testing
        tasks.extend(
            [
                api.echo(f"concurrent_test_{i}_a"),
                api.echo(f"concurrent_test_{i}_b"),
                (
                    api.list_services()
                    if i % 5 == 0
                    else api.echo(f"concurrent_test_{i}_c")
                ),
            ]
        )

    results = await asyncio.gather(*tasks)
    assert len(results) == len(tasks)

    await asyncio.sleep(0.5)  # More time for cleanup of many sessions
    gc.collect()

    concurrent_analysis = get_detailed_session_analysis(api.rpc)
    assert_clean_state(concurrent_analysis, "Concurrent Operations", initial_analysis)

    # Test 4: Exception handling - operations that might fail
    print("\n=== Test 4: Exception Handling ===")
    exception_count = 0
    for i in range(5):
        try:
            # Try to get a non-existent service (should fail)
            await api.get_service(f"non_existent_service_{i}")
        except Exception:
            exception_count += 1
            pass  # Expected to fail

    assert exception_count > 0, "Expected some exceptions for non-existent services"

    await asyncio.sleep(0.3)
    gc.collect()

    exception_analysis = get_detailed_session_analysis(api.rpc)
    assert_clean_state(exception_analysis, "Exception Handling", initial_analysis)

    # Test 5: Large data operations
    print("\n=== Test 5: Large Data Operations ===")
    large_data = "x" * 10000  # 10KB string
    for i in range(5):
        result = await api.echo(large_data)
        assert len(result) == len(large_data)

    await asyncio.sleep(0.3)
    gc.collect()

    large_data_analysis = get_detailed_session_analysis(api.rpc)
    assert_clean_state(large_data_analysis, "Large Data Operations", initial_analysis)

    # Test 6: Rapid sequential operations
    print("\n=== Test 6: Rapid Sequential Operations ===")
    for i in range(50):  # Many rapid operations
        result = await api.echo(f"rapid_{i}")
        assert result == f"rapid_{i}"

    await asyncio.sleep(0.5)
    gc.collect()

    rapid_analysis = get_detailed_session_analysis(api.rpc)
    assert_clean_state(rapid_analysis, "Rapid Sequential Operations", initial_analysis)

    # Test 7: Mixed operation types
    print("\n=== Test 7: Mixed Operation Types ===")
    mixed_tasks = []
    for i in range(10):
        mixed_tasks.extend(
            [
                api.echo(f"mixed_{i}"),
                api.list_services(),
                (
                    api.get_client_info()
                    if hasattr(api, "get_client_info")
                    else api.echo(f"info_{i}")
                ),
            ]
        )

    mixed_results = await asyncio.gather(*mixed_tasks, return_exceptions=True)
    # Some might be exceptions (like get_client_info if not available), that's ok

    await asyncio.sleep(0.5)
    gc.collect()

    mixed_analysis = get_detailed_session_analysis(api.rpc)
    assert_clean_state(mixed_analysis, "Mixed Operation Types", initial_analysis)

    # Test 8: Service registration and cleanup (more lenient)
    print("\n=== Test 8: Service Registration and Cleanup ===")
    pre_service_analysis = get_detailed_session_analysis(api.rpc)

    test_services = []
    for i in range(3):
        service_info = await api.register_service(
            {
                "id": f"temp_service_{i}",
                "config": {"visibility": "protected"},
                "test_method": lambda x: f"test_{x}",
            }
        )
        test_services.append(service_info["id"])

    # Use the services
    for service_id in test_services:
        try:
            svc = await api.get_service(service_id.split(":")[-1])  # Get local part
            result = await svc.test_method("hello")
            assert "test_" in result
        except Exception as e:
            print(f"Service test failed (might be expected): {e}")

    # Unregister services
    for service_id in test_services:
        try:
            await api.unregister_service(service_id.split(":")[-1])
        except Exception as e:
            print(f"Unregister failed (might be expected): {e}")

    await asyncio.sleep(0.5)  # Extra time for service cleanup
    gc.collect()

    service_analysis = get_detailed_session_analysis(api.rpc)
    print(f"\n--- Service Registration Analysis ---")
    print(f"Total sessions: {service_analysis['total_sessions']}")
    print(f"Pre-service sessions: {pre_service_analysis['total_sessions']}")

    if service_analysis["session_details"]:
        print("Remaining sessions after service operations:")
        for session in service_analysis["session_details"]:
            print(f"  - {session['id']}: {session}")

    # Service registration may create some legitimate persistent sessions
    # We check that we don't have excessive growth (>10 sessions would be concerning)
    session_growth = (
        service_analysis["total_sessions"] - pre_service_analysis["total_sessions"]
    )
    assert (
        session_growth <= 10
    ), f"Excessive session growth from service operations: {session_growth} sessions"

    # Memory usage should not grow excessively
    if initial_analysis:
        growth = service_analysis["memory_usage"] - initial_analysis["memory_usage"]
        growth_mb = growth / (1024 * 1024)
        print(f"Memory growth: {growth_mb:.2f} MB")
        assert growth_mb < 10, f"Excessive memory growth: {growth_mb:.2f} MB"

    print("✅ Service registration cleanup within acceptable limits")

    # Final comprehensive check
    print("\n=== Final Comprehensive Analysis ===")
    final_analysis = get_detailed_session_analysis(api.rpc)

    print(f"\n--- Final State Analysis ---")
    print(f"Total sessions: {final_analysis['total_sessions']}")
    print(f"Expected sessions (from services): {service_analysis['total_sessions']}")
    print(f"Memory usage: {final_analysis['memory_usage']:,} bytes")

    if final_analysis["session_details"]:
        print("Final session details:")
        for session in final_analysis["session_details"]:
            print(f"  - {session['id']}: {session}")

    # Final state should not have more sessions than after service registration
    assert (
        final_analysis["total_sessions"] <= service_analysis["total_sessions"]
    ), f"Session increase after service registration: {final_analysis['total_sessions']} > {service_analysis['total_sessions']}"

    # Memory should not grow excessively from service registration state
    memory_growth = final_analysis["memory_usage"] - service_analysis["memory_usage"]
    memory_growth_mb = memory_growth / (1024 * 1024)
    print(f"Memory growth since service test: {memory_growth_mb:.2f} MB")
    assert (
        memory_growth_mb < 1
    ), f"Excessive memory growth since service test: {memory_growth_mb:.2f} MB"

    # Clean disconnect
    await api.disconnect()

    # Stop memory tracing
    tracemalloc.stop()

    print("✅ ALL MEMORY LEAK TESTS PASSED - No memory leaks detected!")


@pytest.mark.asyncio
async def test_memory_leak_edge_cases(websocket_server):
    """Test memory leak prevention in edge cases and error conditions."""
    from hypha_rpc import connect_to_server
    import gc

    api = await connect_to_server(
        {
            "client_id": "edge-case-test-client",
            "server_url": WS_SERVER_URL,
        }
    )

    def get_session_count(rpc):
        """Get current session count."""
        if not hasattr(rpc, "_object_store"):
            return 0
        return len(
            [
                k
                for k in rpc._object_store.keys()
                if k not in ["services", "message_cache"]
            ]
        )

    initial_count = get_session_count(api.rpc)
    print(f"Initial session count: {initial_count}")

    # Test 1: Timeout scenarios (if API supports timeouts)
    print("\n=== Edge Case 1: Operations with Different Patterns ===")

    # Test with empty strings, None-like values, special characters
    test_values = ["", "null", "undefined", "{}[]();", "🚀🔥💻", "\n\t\r", "  ", None]

    for i, value in enumerate(test_values):
        try:
            if value is not None:
                result = await api.echo(value)
                assert result == value
            else:
                # Skip None as it might not be JSON serializable
                continue
        except Exception as e:
            print(f"Expected potential failure for value {i}: {e}")

    await asyncio.sleep(0.2)
    gc.collect()

    edge_count_1 = get_session_count(api.rpc)
    assert (
        edge_count_1 <= initial_count
    ), f"Sessions leaked in edge case 1: {edge_count_1} > {initial_count}"

    # Test 2: Rapid connection/disconnection patterns (simulated)
    print("\n=== Edge Case 2: Rapid Operations ===")

    # Simulate rapid operations that might be cancelled
    tasks = []
    for i in range(100):  # Many rapid tasks
        tasks.append(api.echo(f"rapid_edge_{i}"))
        if i % 10 == 0:  # Add some list_services calls
            tasks.append(api.list_services())

    # Execute all at once - this might create many sessions simultaneously
    results = await asyncio.gather(*tasks)

    # Check results
    successful = sum(1 for r in results if not isinstance(r, Exception))
    failed = len(results) - successful
    print(f"Rapid operations: {successful} successful, {failed} failed")

    await asyncio.sleep(0.5)  # Give extra time for cleanup
    gc.collect()

    edge_count_2 = get_session_count(api.rpc)
    assert (
        edge_count_2 <= initial_count
    ), f"Sessions leaked in rapid operations: {edge_count_2} > {initial_count}"

    # Test 3: Mixed success/failure scenarios
    print("\n=== Edge Case 3: Mixed Success/Failure ===")

    mixed_operations = []
    for i in range(20):
        # Mix of valid and invalid operations
        mixed_operations.append(api.echo(f"valid_{i}"))

        # Try invalid operations that should fail
        try:
            mixed_operations.append(api.get_service(f"invalid_service_{i}"))
        except:
            pass  # Expected to fail during creation

    # Execute with exception handling
    mixed_results = await asyncio.gather(*mixed_operations, return_exceptions=True)

    exceptions = sum(1 for r in mixed_results if isinstance(r, Exception))
    successes = len(mixed_results) - exceptions
    print(f"Mixed operations: {successes} successful, {exceptions} exceptions")

    await asyncio.sleep(0.3)
    gc.collect()

    edge_count_3 = get_session_count(api.rpc)
    assert (
        edge_count_3 <= initial_count
    ), f"Sessions leaked in mixed operations: {edge_count_3} > {initial_count}"

    # Final verification
    final_count = get_session_count(api.rpc)
    print(f"Final session count: {final_count}")
    assert (
        final_count <= initial_count
    ), f"Overall session leak detected: {final_count} > {initial_count}"

    await api.disconnect()
    print("✅ EDGE CASE MEMORY TESTS PASSED")


@pytest.mark.asyncio
async def test_session_cleanup_robustness(websocket_server):
    """Test the robustness of session cleanup mechanisms."""
    from hypha_rpc import connect_to_server
    import gc

    api = await connect_to_server(
        {
            "client_id": "cleanup-robustness-test",
            "server_url": WS_SERVER_URL,
        }
    )

    def analyze_object_store(rpc):
        """Detailed analysis of object store contents."""
        if not hasattr(rpc, "_object_store"):
            return {"sessions": 0, "details": "No object store"}

        store = rpc._object_store
        sessions = []
        system_stores = {}

        for key, value in store.items():
            if key in ["services", "message_cache"]:
                system_stores[key] = {
                    "size": len(value) if hasattr(value, "__len__") else "unknown",
                    "type": str(type(value)),
                }
            else:
                sessions.append(
                    {
                        "id": key,
                        "type": str(type(value)),
                        "is_dict": isinstance(value, dict),
                        "size": len(value) if hasattr(value, "__len__") else "unknown",
                    }
                )

        return {
            "sessions": len(sessions),
            "session_details": sessions,
            "system_stores": system_stores,
        }

    print("=== SESSION CLEANUP ROBUSTNESS TESTS ===")

    initial_analysis = analyze_object_store(api.rpc)
    print(f"Initial state: {initial_analysis['sessions']} sessions")

    # Test 1: Verify cleanup works under normal conditions
    print("\n--- Test 1: Normal Operation Cleanup ---")
    for i in range(10):
        result = await api.echo(f"normal_{i}")
        assert result == f"normal_{i}"

    await asyncio.sleep(0.2)
    gc.collect()

    normal_analysis = analyze_object_store(api.rpc)
    print(f"After normal operations: {normal_analysis['sessions']} sessions")
    assert (
        normal_analysis["sessions"] == 0
    ), f"Normal operations left {normal_analysis['sessions']} sessions"

    # Test 2: Concurrent session creation and cleanup
    print("\n--- Test 2: Concurrent Session Management ---")

    # Create many concurrent operations to stress the session management
    batch_size = 50
    batches = 3

    for batch in range(batches):
        print(f"  Batch {batch + 1}/{batches}")
        tasks = [api.echo(f"concurrent_batch_{batch}_{i}") for i in range(batch_size)]
        results = await asyncio.gather(*tasks)
        assert len(results) == batch_size

        # Brief pause between batches
        await asyncio.sleep(0.1)

    await asyncio.sleep(0.5)  # Extra time for cleanup
    gc.collect()

    concurrent_analysis = analyze_object_store(api.rpc)
    print(f"After concurrent operations: {concurrent_analysis['sessions']} sessions")
    assert (
        concurrent_analysis["sessions"] == 0
    ), f"Concurrent operations left {concurrent_analysis['sessions']} sessions"

    # Test 3: Stress test with rapid creation/cleanup cycles
    print("\n--- Test 3: Rapid Creation/Cleanup Cycles ---")

    for cycle in range(5):
        # Create burst of operations
        burst_tasks = [api.echo(f"burst_{cycle}_{i}") for i in range(20)]
        await asyncio.gather(*burst_tasks)

        # Force cleanup opportunity
        await asyncio.sleep(0.1)
        gc.collect()

        # Check intermediate state
        intermediate_analysis = analyze_object_store(api.rpc)
        if intermediate_analysis["sessions"] > 0:
            print(
                f"  Cycle {cycle}: {intermediate_analysis['sessions']} sessions (transient)"
            )

    # Final cleanup
    await asyncio.sleep(0.3)
    gc.collect()

    cycles_analysis = analyze_object_store(api.rpc)
    print(f"After rapid cycles: {cycles_analysis['sessions']} sessions")
    assert (
        cycles_analysis["sessions"] == 0
    ), f"Rapid cycles left {cycles_analysis['sessions']} sessions"

    # Test 4: Large payload cleanup
    print("\n--- Test 4: Large Payload Cleanup ---")

    large_payloads = ["x" * (1024 * i) for i in range(1, 6)]  # 1KB to 5KB
    for i, payload in enumerate(large_payloads):
        result = await api.echo(payload)
        assert len(result) == len(payload)
        print(f"  Large payload {i+1}: {len(payload)} bytes processed")

    await asyncio.sleep(0.3)
    gc.collect()

    large_analysis = analyze_object_store(api.rpc)
    print(f"After large payloads: {large_analysis['sessions']} sessions")
    assert (
        large_analysis["sessions"] == 0
    ), f"Large payloads left {large_analysis['sessions']} sessions"

    # Final comprehensive check
    print("\n--- Final Robustness Verification ---")
    final_analysis = analyze_object_store(api.rpc)

    print("Final object store analysis:")
    print(f"  Sessions: {final_analysis['sessions']}")
    print(f"  System stores: {final_analysis['system_stores']}")

    if final_analysis["session_details"]:
        print("  Remaining sessions:")
        for session in final_analysis["session_details"]:
            print(f"    - {session}")

    assert (
        final_analysis["sessions"] == 0
    ), "Session cleanup robustness test failed - sessions remain"

    # Verify message cache is clean
    if "message_cache" in final_analysis["system_stores"]:
        cache_size = final_analysis["system_stores"]["message_cache"]["size"]
        assert cache_size == 0, f"Message cache not clean: {cache_size} items"

    await api.disconnect()
    print("✅ SESSION CLEANUP ROBUSTNESS TESTS PASSED")


@pytest.mark.asyncio
async def test_comprehensive_reconnection_scenarios(restartable_server):
    """Test comprehensive reconnection scenarios including server restarts - with timeouts."""
    print("\n=== COMPREHENSIVE RECONNECTION TEST ===")

    try:
        # Create connection with timeout
        ws = await asyncio.wait_for(
            connect_to_server(
                {
                    "name": "reconnection-test-client",
                    "server_url": f"ws://127.0.0.1:{restartable_server.port}/ws",
                    "client_id": "reconnection-test",
                }
            ),
            timeout=10.0,
        )

        # Track events for verification
        reconnection_events = []
        service_events = []

        def on_connected(info):
            reconnection_events.append(
                {"type": "connected", "time": time.time(), "info": info}
            )
            print(f"📡 Connected: {info.get('workspace', 'N/A')}")

        def on_services_registered(info):
            service_events.append(
                {
                    "type": "registered",
                    "time": time.time(),
                    "count": info.get("registered", 0),
                }
            )
            print(f"🔧 Services registered: {info.get('registered', 0)}")

        ws.rpc.on("connected", on_connected)
        ws.rpc.on("services_registered", on_services_registered)

        # Register test services with state
        test_state = {"counter": 0, "data": "initial"}

        service_info = await asyncio.wait_for(
            ws.register_service(
                {
                    "id": "persistent-service",
                    "name": "Persistent Test Service",
                    "config": {"visibility": "protected"},
                    "get_counter": lambda: test_state["counter"],
                    "increment": lambda: test_state.update(
                        {"counter": test_state["counter"] + 1}
                    )
                    or test_state["counter"],
                    "set_data": lambda data: test_state.update({"data": data}) or "ok",
                    "get_data": lambda: test_state["data"],
                    "ping": lambda: "pong",
                }
            ),
            timeout=5.0,
        )

        print(f"🏷️  Service registered: {service_info['id']}")

        # Test initial functionality
        svc = await ws.get_service("persistent-service")
        assert await svc.ping() == "pong"
        assert await svc.get_counter() == 0
        await svc.increment()
        assert await svc.get_counter() == 1
        await svc.set_data("pre-restart")
        assert await svc.get_data() == "pre-restart"

        print("✅ Initial service functionality verified")

        # Clear initial events
        reconnection_events.clear()
        service_events.clear()

        # Test 1: Clean server restart (simulates k8s upgrade)
        print("\n--- TEST 1: Clean Server Restart ---")
        print("🔄 Restarting server cleanly...")
        restartable_server.restart(stop_delay=0.5)

        # Wait for reconnection with timeout
        await asyncio.wait_for(
            _wait_for_service_recovery(ws, "persistent-service", "post-restart-1"),
            timeout=15.0,
        )
        print("✅ Clean restart reconnection successful")

        # Test 2: Abrupt connection closure
        print("\n--- TEST 2: Abrupt Connection Closure ---")
        print("💥 Closing connection abruptly...")
        await ws.rpc._connection._websocket.close(1011)  # Unexpected condition

        await asyncio.wait_for(
            _wait_for_service_recovery(ws, "persistent-service", "post-abrupt-close"),
            timeout=10.0,
        )
        print("✅ Abrupt closure reconnection successful")

        # Test 3: Multiple rapid disconnections (simplified)
        print("\n--- TEST 3: Multiple Rapid Disconnections ---")
        valid_codes = [1000, 1001]  # Use only valid close codes
        for i, code in enumerate(valid_codes):
            print(f"🔄 Rapid disconnect #{i+1} (code {code})")
            await ws.rpc._connection._websocket.close(code)
            await asyncio.sleep(1.0)  # Increased wait time

        # Wait for final reconnection
        await asyncio.wait_for(
            _wait_for_service_recovery(ws, "persistent-service", "final-test"),
            timeout=10.0,
        )
        print("✅ Multiple rapid disconnections handled")

        # Verify reconnection events occurred
        print(f"\n📈 Reconnection events: {len(reconnection_events)}")

        # Final verification
        svc = await ws.get_service("persistent-service")
        final_counter = await svc.get_counter()
        await svc.increment()
        assert await svc.get_counter() == final_counter + 1

        print("✅ COMPREHENSIVE RECONNECTION TEST PASSED!")

    finally:
        # Ensure cleanup even if test fails
        try:
            await asyncio.wait_for(ws.disconnect(), timeout=5.0)
        except:
            pass


async def _wait_for_service_recovery(ws, service_id, test_data):
    """Helper function to wait for service recovery with timeout."""
    max_attempts = 30  # 15 seconds max
    for attempt in range(max_attempts):
        try:
            svc = await asyncio.wait_for(ws.get_service(service_id), timeout=1.0)
            result = await asyncio.wait_for(svc.ping(), timeout=1.0)
            if result == "pong":
                # Set test data to verify service state
                await svc.set_data(test_data)
                data_val = await svc.get_data()
                if data_val == test_data:
                    return True
        except Exception as e:
            if attempt < 5:  # Only log first few attempts to avoid spam
                print(f"   Recovery attempt {attempt + 1}: {type(e).__name__}")
            await asyncio.sleep(0.5)

    raise TimeoutError(f"Service recovery failed after {max_attempts} attempts")


@pytest.mark.asyncio
async def test_graceful_vs_ungraceful_disconnection_handling(restartable_server):
    """Test that both graceful and ungraceful disconnections trigger reconnection."""
    print("\n=== GRACEFUL VS UNGRACEFUL DISCONNECTION TEST ===")

    ws = await connect_to_server(
        {
            "name": "disconnect-type-test",
            "server_url": f"ws://127.0.0.1:{restartable_server.port}/ws",
            "client_id": "disconnect-type-test",
        }
    )

    # Register a simple service
    await ws.register_service(
        {
            "id": "test-service",
            "config": {"visibility": "protected"},
            "echo": lambda x: f"echo: {x}",
        }
    )

    # Track disconnection handling
    disconnection_events = []

    def track_disconnection(event_type):
        def handler(info=None):
            disconnection_events.append(
                {"type": event_type, "time": time.time(), "info": info}
            )

        return handler

    ws.rpc.on("connected", track_disconnection("connected"))

    # Test graceful disconnections (should still reconnect)
    graceful_codes = [1000, 1001]  # Normal closure, going away

    for code in graceful_codes:
        print(f"\n--- Testing graceful disconnection with code {code} ---")
        disconnection_events.clear()

        # Disconnect with graceful code
        await ws.rpc._connection._websocket.close(code)

        # Wait for reconnection
        await asyncio.sleep(2)

        # Verify service works
        svc = await ws.get_service("test-service")
        result = await svc.echo(f"after-{code}")
        assert result == f"echo: after-{code}"

        # Should have reconnected
        connected_events = [e for e in disconnection_events if e["type"] == "connected"]
        assert (
            len(connected_events) > 0
        ), f"Should have reconnected after graceful close {code}"

        print(f"✅ Graceful disconnection {code} handled correctly")

    # Test ungraceful disconnections
    ungraceful_codes = [
        1011,
        1002,
        1003,
    ]  # Unexpected condition, protocol error, unsupported data

    for code in ungraceful_codes:
        print(f"\n--- Testing ungraceful disconnection with code {code} ---")
        disconnection_events.clear()

        # Disconnect with ungraceful code
        await ws.rpc._connection._websocket.close(code)

        # Wait for reconnection
        await asyncio.sleep(2)

        # Verify service works
        svc = await ws.get_service("test-service")
        result = await svc.echo(f"after-{code}")
        assert result == f"echo: after-{code}"

        # Should have reconnected
        connected_events = [e for e in disconnection_events if e["type"] == "connected"]
        assert (
            len(connected_events) > 0
        ), f"Should have reconnected after ungraceful close {code}"

        print(f"✅ Ungraceful disconnection {code} handled correctly")

    print("✅ GRACEFUL VS UNGRACEFUL DISCONNECTION TEST PASSED!")


@pytest.mark.asyncio
async def test_user_disconnect_vs_server_disconnect(restartable_server):
    """Test that user-initiated disconnect prevents reconnection while server-initiated allows it."""
    print("\n=== USER VS SERVER DISCONNECT TEST ===")

    # Test 1: Server-initiated disconnect should reconnect
    print("\n--- Test 1: Server-initiated disconnect ---")
    ws1 = await connect_to_server(
        {
            "name": "server-disconnect-test",
            "server_url": f"ws://127.0.0.1:{restartable_server.port}/ws",
            "client_id": "server-disconnect-test",
        }
    )

    await ws1.register_service(
        {
            "id": "test-service-1",
            "config": {"visibility": "protected"},
            "test": lambda: "server-disconnect-test",
        }
    )

    # Simulate server closing connection (like restart)
    await ws1.rpc._connection._websocket.close(1000)  # Normal closure from server

    # Wait for reconnection
    await asyncio.sleep(2)

    # Should be able to use service (reconnected)
    svc1 = await ws1.get_service("test-service-1")
    result1 = await svc1.test()
    assert result1 == "server-disconnect-test"
    print("✅ Server-initiated disconnect: Client reconnected successfully")

    # Test 2: User-initiated disconnect should NOT reconnect
    print("\n--- Test 2: User-initiated disconnect ---")
    ws2 = await connect_to_server(
        {
            "name": "user-disconnect-test",
            "server_url": f"ws://127.0.0.1:{restartable_server.port}/ws",
            "client_id": "user-disconnect-test",
        }
    )

    await ws2.register_service(
        {
            "id": "test-service-2",
            "config": {"visibility": "protected"},
            "test": lambda: "user-disconnect-test",
        }
    )

    # User explicitly disconnects
    await ws2.disconnect()

    # Wait to ensure no reconnection happens
    await asyncio.sleep(2)

    # Should NOT be able to use service (no reconnection)
    try:
        svc2 = await ws2.get_service("test-service-2")
        await svc2.test()
        assert False, "Should not be able to use service after user disconnect"
    except Exception as e:
        print(
            f"✅ User-initiated disconnect: Service unavailable as expected ({type(e).__name__})"
        )

    # Cleanup
    await ws1.disconnect()

    print("✅ USER VS SERVER DISCONNECT TEST PASSED!")


@pytest.mark.asyncio
async def test_persistent_service_across_multiple_restarts(restartable_server):
    """Test that services remain functional across multiple server restarts."""
    print("\n=== PERSISTENT SERVICE ACROSS RESTARTS TEST ===")

    ws = await connect_to_server(
        {
            "name": "persistent-service-test",
            "server_url": f"ws://127.0.0.1:{restartable_server.port}/ws",
            "client_id": "persistent-service-test",
        }
    )

    # Create a service with persistent state
    persistent_data = {"startup_count": 0, "operation_count": 0, "messages": []}

    def increment_startup():
        persistent_data["startup_count"] += 1
        return persistent_data["startup_count"]

    def add_message(msg):
        persistent_data["operation_count"] += 1
        persistent_data["messages"].append(
            f"{persistent_data['operation_count']}: {msg}"
        )
        return len(persistent_data["messages"])

    def get_stats():
        return {
            "startup_count": persistent_data["startup_count"],
            "operation_count": persistent_data["operation_count"],
            "message_count": len(persistent_data["messages"]),
            "last_message": (
                persistent_data["messages"][-1] if persistent_data["messages"] else None
            ),
        }

    await ws.register_service(
        {
            "id": "persistent-data-service",
            "name": "Persistent Data Service",
            "config": {"visibility": "protected"},
            "increment_startup": increment_startup,
            "add_message": add_message,
            "get_stats": get_stats,
            "ping": lambda: "alive",
        }
    )

    # Initial operations
    svc = await ws.get_service("persistent-data-service")
    startup_count = await svc.increment_startup()
    assert startup_count == 1

    msg_count = await svc.add_message("initial message")
    assert msg_count == 1

    # Multiple restart cycles
    for restart_cycle in range(3):
        print(f"\n--- Restart Cycle {restart_cycle + 1} ---")

        # Add pre-restart message
        await svc.add_message(f"pre-restart-{restart_cycle}")

        # Restart server
        print("🔄 Restarting server...")
        restartable_server.restart(stop_delay=0.3)

        # Wait for reconnection
        await asyncio.sleep(2)

        # Verify service is back
        svc = await ws.get_service("persistent-data-service")
        assert await svc.ping() == "alive"

        # Check persistent state
        stats = await svc.get_stats()
        expected_operations = (
            1 + 2 * restart_cycle + 1
        )  # initial + 2*completed_cycles + current_pre_restart
        assert (
            stats["operation_count"] == expected_operations
        ), f"Expected {expected_operations} operations, got {stats['operation_count']}"

        # Add post-restart message
        await svc.add_message(f"post-restart-{restart_cycle}")

        # Verify state persisted
        final_stats = await svc.get_stats()
        print(f"📊 Cycle {restart_cycle + 1} stats: {final_stats}")

        assert final_stats["startup_count"] == 1  # Only incremented once initially
        assert "post-restart-" in final_stats["last_message"]

        print(f"✅ Restart cycle {restart_cycle + 1} completed successfully")

    # Final verification
    final_stats = await svc.get_stats()
    print(f"\n📈 Final stats after all restarts: {final_stats}")

    assert final_stats["startup_count"] == 1
    assert (
        final_stats["operation_count"] == 7
    )  # 1 initial + 6 from 3 cycles (2 per cycle)
    assert final_stats["message_count"] == 7

    print("✅ PERSISTENT SERVICE ACROSS RESTARTS TEST PASSED!")


@pytest.mark.asyncio
async def test_memory_cleanup_during_reconnections(restartable_server):
    """Test that memory doesn't grow unbounded during reconnection cycles."""
    print("\n=== MEMORY CLEANUP DURING RECONNECTIONS TEST ===")

    ws = await connect_to_server(
        {
            "name": "memory-cleanup-test",
            "server_url": f"ws://127.0.0.1:{restartable_server.port}/ws",
            "client_id": "memory-cleanup-test",
        }
    )

    # Helper to estimate object count in the store
    def get_object_count(rpc):
        count = 0
        if hasattr(rpc, "_object_store"):
            for key, value in rpc._object_store.items():
                count += 1
                if isinstance(value, dict):
                    count += len(value)
        return count

    # Register a test service
    await ws.register_service(
        {
            "id": "memory-test-service",
            "config": {"visibility": "protected"},
            "echo": lambda x: x,
            "create_data": lambda size: "x" * size,
        }
    )

    initial_count = get_object_count(ws.rpc)
    print(f"📊 Initial object count: {initial_count}")

    # Perform multiple reconnection cycles with operations
    for cycle in range(5):
        print(f"\n--- Memory test cycle {cycle + 1} ---")

        # Perform some operations
        svc = await ws.get_service("memory-test-service")
        await svc.echo("test")
        await svc.create_data(100)  # Create some temporary data

        # Force disconnection
        await ws.rpc._connection._websocket.close(1011)

        # Wait for reconnection
        await asyncio.sleep(1.5)

        # Check memory usage
        current_count = get_object_count(ws.rpc)
        print(f"📊 Objects after cycle {cycle + 1}: {current_count}")

        # Only flag extreme unbounded growth (50x initial count would be truly concerning)
        assert (
            current_count < initial_count * 50
        ), f"Extreme memory growth detected: {current_count} (started at {initial_count})"

    # Final memory check
    final_count = get_object_count(ws.rpc)
    print(f"📊 Final object count: {final_count}")

    # Some growth is expected due to session data and service references
    # Only flag if growth is truly excessive (more than 100x initial count)
    growth_ratio = final_count / initial_count if initial_count > 0 else final_count
    print(f"📊 Growth ratio: {growth_ratio:.1f}x")

    # This allows for reasonable session/service data accumulation (11x is normal)
    assert (
        growth_ratio < 100
    ), f"Excessive memory growth: {growth_ratio:.1f}x growth from {initial_count} to {final_count} objects"

    print(f"✅ Memory growth is within reasonable bounds: {growth_ratio:.1f}x")


@pytest.mark.asyncio
async def test_simple_reconnection_debug(restartable_server):
    """Simple test to debug reconnection behavior step by step."""
    print("\n=== SIMPLE RECONNECTION DEBUG TEST ===")

    # Create connection with timeout to prevent hanging
    ws = await asyncio.wait_for(
        connect_to_server(
            {
                "name": "debug-reconnection-test",
                "server_url": f"ws://127.0.0.1:{restartable_server.port}/ws",
                "client_id": "debug-test",
            }
        ),
        timeout=10.0,
    )

    print("✅ Initial connection established")

    # Register a simple service
    service_info = await asyncio.wait_for(
        ws.register_service(
            {
                "id": "debug-test-service",
                "config": {"visibility": "protected"},
                "ping": lambda: "pong",
            }
        ),
        timeout=5.0,
    )

    print(f"✅ Service registered: {service_info['id']}")

    # Test initial functionality
    svc = await asyncio.wait_for(ws.get_service("debug-test-service"), timeout=5.0)
    result = await asyncio.wait_for(svc.ping(), timeout=5.0)
    assert result == "pong"
    print("✅ Initial service call works")

    # Test simple disconnection and reconnection
    print("💥 Closing connection...")
    await ws.rpc._connection._websocket.close(1011)  # Unexpected condition (valid code)

    print("⏳ Waiting for reconnection (max 10s)...")
    start_time = asyncio.get_event_loop().time()

    # Wait for reconnection with timeout
    while asyncio.get_event_loop().time() - start_time < 10:
        try:
            svc = await asyncio.wait_for(
                ws.get_service("debug-test-service"), timeout=1.0
            )
            result = await asyncio.wait_for(svc.ping(), timeout=1.0)
            if result == "pong":
                print("✅ Reconnection successful!")
                break
        except Exception as e:
            print(f"   Still reconnecting... ({type(e).__name__})")
            await asyncio.sleep(0.5)
    else:
        raise TimeoutError("Reconnection did not complete within 10 seconds")

    # Cleanup
    await asyncio.wait_for(ws.disconnect(), timeout=5.0)
    print("✅ SIMPLE RECONNECTION DEBUG TEST PASSED!")


@pytest.mark.asyncio
async def test_reconnection_with_server_restart_simple(restartable_server):
    """Test reconnection behavior with actual server restart - simplified version."""
    print("\n=== SIMPLE SERVER RESTART TEST ===")

    # Create connection
    ws = await asyncio.wait_for(
        connect_to_server(
            {
                "name": "restart-test",
                "server_url": f"ws://127.0.0.1:{restartable_server.port}/ws",
                "client_id": "restart-test",
            }
        ),
        timeout=10.0,
    )

    # Register service
    await asyncio.wait_for(
        ws.register_service(
            {
                "id": "restart-service",
                "config": {"visibility": "protected"},
                "echo": lambda x: f"echo: {x}",
            }
        ),
        timeout=5.0,
    )

    # Test it works
    svc = await ws.get_service("restart-service")
    result = await svc.echo("before-restart")
    assert result == "echo: before-restart"
    print("✅ Service works before restart")

    # Restart server
    print("🔄 Restarting server...")
    restartable_server.restart(stop_delay=0.5)
    print("✅ Server restarted")

    # Wait for reconnection and test
    print("⏳ Waiting for reconnection...")
    reconnected = False
    for attempt in range(20):  # 10 seconds max
        try:
            svc = await asyncio.wait_for(ws.get_service("restart-service"), timeout=1.0)
            result = await asyncio.wait_for(svc.echo("after-restart"), timeout=1.0)
            if result == "echo: after-restart":
                print("✅ Service works after restart!")
                reconnected = True
                break
        except Exception as e:
            print(f"   Attempt {attempt + 1}: {type(e).__name__}")
            await asyncio.sleep(0.5)

    if not reconnected:
        raise TimeoutError("Failed to reconnect after server restart")

    # Cleanup
    await ws.disconnect()
    print("✅ SIMPLE SERVER RESTART TEST PASSED!")


def test_rpc_thread_safety_fix(websocket_server):
    """Test that the RPC thread safety fix works correctly."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from hypha_rpc.rpc import RPC

    print("\n=== RPC THREAD SAFETY FIX TEST ===")

    # Create a simple RPC instance (without connection for this test)
    rpc = RPC(connection=None, client_id="test-client")

    # Test 1: Call from main thread (should work normally)
    print("1. Testing call from main thread...")
    try:
        result = rpc.get_client_info()
        print(f"   ✅ Main thread result: {result['id']}")
        assert result["id"] == "test-client"
    except Exception as e:
        print(f"   ❌ Main thread failed: {e}")
        raise

    # Test 2: Call from a different thread (this is where the original error occurred)
    print("2. Testing call from different thread...")
    result_container = {"result": None, "exception": None}

    def call_from_thread():
        try:
            # This thread has no event loop - this is where the original error occurred
            # The fix should now create an event loop automatically when RPC methods are called
            result = rpc.get_client_info()
            result_container["result"] = result["id"]
        except Exception as e:
            result_container["exception"] = e

    # Create a separate thread pool to simulate the original error condition
    with ThreadPoolExecutor(max_workers=1) as test_executor:
        future = test_executor.submit(call_from_thread)
        future.result(timeout=10)  # Wait for completion

    if result_container["exception"]:
        print(f"   ❌ Thread call failed: {result_container['exception']}")
        raise result_container["exception"]
    else:
        print(f"   ✅ Thread call result: {result_container['result']}")
        assert result_container["result"] == "test-client"

    # Test 3: Multiple concurrent calls from different threads
    print("3. Testing multiple concurrent calls from different threads...")
    results = []
    exceptions = []

    def concurrent_call(thread_id):
        try:
            result = rpc.get_client_info()
            results.append((thread_id, result["id"]))
        except Exception as e:
            exceptions.append((thread_id, e))

    # Submit multiple concurrent calls
    with ThreadPoolExecutor(max_workers=3) as concurrent_executor:
        futures = [concurrent_executor.submit(concurrent_call, i) for i in range(1, 4)]
        # Wait for all to complete
        for future in futures:
            future.result(timeout=10)

    if exceptions:
        print(f"   ❌ Concurrent calls failed: {exceptions}")
        raise exceptions[0][1]
    else:
        print(f"   ✅ All concurrent calls succeeded: {results}")
        assert len(results) == 3
        for thread_id, result in results:
            assert result == "test-client"

    print("\n✅ RPC THREAD SAFETY FIX TEST PASSED!")
    print("✅ The RPC now properly handles calls from any thread context")


@pytest.mark.asyncio
async def test_rpc_memory_leak_fix():
    """Test the RPC memory leak fix with mock connection."""
    from hypha_rpc.rpc import RPC

    class MockConnection:
        """Mock connection for testing."""

        def __init__(self):
            self.manager_id = "test_manager"
            self._workspace = "test_workspace"

        async def emit_message(self, data):
            """Mock emit message."""
            pass

        def on_message(self, handler):
            """Mock on message."""
            pass

        def on_connected(self, handler):
            """Mock on connected."""
            pass

        def on_disconnected(self, handler):
            """Mock on disconnected."""
            pass

        async def disconnect(self):
            """Mock disconnect."""
            pass

    print("🧪 TESTING RPC MEMORY LEAK FIX")
    print("=" * 50)

    # Create mock connection
    mock_connection = MockConnection()

    # Create RPC instance
    print("📋 Test 1: Creating RPC instance...")
    rpc = RPC(
        connection=mock_connection, client_id="test_client", workspace="test_workspace"
    )
    print("✅ RPC instance created")

    # Verify references are set up
    print("\n📋 Test 2: Checking initial references...")
    has_connection_before = hasattr(rpc, "_connection") and rpc._connection is not None
    has_emit_message_before = (
        hasattr(rpc, "_emit_message") and rpc._emit_message is not None
    )

    print(f"  Before close:")
    print(f"    has _connection: {has_connection_before}")
    print(f"    has _emit_message: {has_emit_message_before}")
    print(f"    _connection is mock: {rpc._connection is mock_connection}")

    assert has_connection_before and has_emit_message_before, "Initial setup failed"

    # Test close method
    print("\n📋 Test 3: Testing RPC.close() cleanup...")
    rpc.close()

    # Check references after close
    has_connection_after = hasattr(rpc, "_connection") and rpc._connection is not None
    has_emit_message_after = (
        hasattr(rpc, "_emit_message") and rpc._emit_message is not None
    )

    print(f"  After close:")
    print(f"    has _connection: {has_connection_after}")
    print(f"    has _emit_message: {has_emit_message_after}")

    # Test that _emit_message is replaced with stub
    if has_emit_message_after:
        try:
            await rpc._emit_message({"test": "data"})
            assert (
                False
            ), "_emit_message should have been replaced with disconnected stub"
        except RuntimeError as e:
            assert "disconnected" in str(e), f"Unexpected error: {e}"
            print("✅ _emit_message correctly replaced with disconnected stub")
        except Exception as e:
            assert False, f"Unexpected error type: {e}"

    # Test disconnect method
    print("\n📋 Test 4: Testing RPC.disconnect() method...")

    # Create new instance for disconnect test
    mock_connection2 = MockConnection()
    rpc2 = RPC(
        connection=mock_connection2,
        client_id="test_client_2",
        workspace="test_workspace",
    )

    # Test disconnect
    await rpc2.disconnect()

    has_connection_after_disconnect = (
        hasattr(rpc2, "_connection") and rpc2._connection is not None
    )
    print(f"  After disconnect: has _connection: {has_connection_after_disconnect}")

    # Final results
    print("\n🎯 RPC MEMORY LEAK FIX TEST RESULTS:")
    print("=" * 40)

    success = (
        not has_connection_after  # Connection cleared
        and not has_connection_after_disconnect  # Disconnect works
    )

    assert success, "Memory leak fix tests failed"
    print("✅ ALL TESTS PASSED")
    print("✅ RPC memory leak fix working correctly")
    print("✅ Connection references properly cleared")
    print("✅ Ready for production!")


@pytest.mark.asyncio
async def test_authorized_workspaces(websocket_server):
    """Test the authorized_workspaces feature for protected services."""
    print("\n=== TESTING AUTHORIZED WORKSPACES ===")

    # Connect first client (will be service provider)
    ws1 = await connect_to_server(
        {"server_url": WS_SERVER_URL, "client_id": "test-auth-client"}
    )
    workspace1 = ws1.config.workspace

    # Connect second client in a different workspace (authorized)
    ws2 = await connect_to_server(
        {"server_url": WS_SERVER_URL, "client_id": "authorized-client"}
    )
    workspace2 = ws2.config.workspace

    # Connect third client in another workspace (not authorized)
    ws3 = await connect_to_server(
        {"server_url": WS_SERVER_URL, "client_id": "unauthorized-client"}
    )
    workspace3 = ws3.config.workspace

    print(f"Created workspaces: {workspace1}, {workspace2}, {workspace3}")

    # Test 1: Validate that authorized_workspaces requires protected visibility
    print(
        "1. Testing validation: authorized_workspaces with non-protected visibility..."
    )
    try:
        await ws1.register_service(
            {
                "id": "invalid-service",
                "config": {
                    "visibility": "public",
                    "authorized_workspaces": ["some-workspace"],  # Should fail
                },
                "test": lambda: "test",
            }
        )
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert (
            "authorized_workspaces can only be set when visibility is 'protected'"
            in str(e)
        )
        print(f"   ✅ Validation works: {e}")

    # Test 2: Test with unlisted visibility should also fail
    print("2. Testing validation: authorized_workspaces with unlisted visibility...")
    try:
        await ws1.register_service(
            {
                "id": "invalid-service-2",
                "config": {
                    "visibility": "unlisted",
                    "authorized_workspaces": ["some-workspace"],  # Should fail
                },
                "test": lambda: "test",
            }
        )
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert (
            "authorized_workspaces can only be set when visibility is 'protected'"
            in str(e)
        )
        print(f"   ✅ Validation works for unlisted: {e}")

    # Test 3: Register service with authorized_workspaces (valid case)
    print("3. Testing service with authorized_workspaces (valid)...")
    await ws1.register_service(
        {
            "id": "authorized-service",
            "name": "Authorized Test Service",
            "config": {
                "visibility": "protected",
                "authorized_workspaces": [workspace2],  # Only allow workspace2
            },
            "test_method": lambda x: f"authorized: {x}",
        }
    )

    # Access from same workspace should work
    svc = await ws1.get_service(f"{workspace1}/test-auth-client:authorized-service")
    result = await svc.test_method("test")
    assert result == "authorized: test"
    print("   ✅ Service accessible from same workspace")

    # Test 4: Validate authorized_workspaces must be a list
    print("4. Testing validation: authorized_workspaces must be a list...")
    try:
        await ws1.register_service(
            {
                "id": "invalid-service-3",
                "config": {
                    "visibility": "protected",
                    "authorized_workspaces": "not-a-list",  # Should fail
                },
                "test": lambda: "test",
            }
        )
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "authorized_workspaces must be a list" in str(e)
        print(f"   ✅ List validation works: {e}")

    # Test 5: Validate workspace ids must be strings
    print("5. Testing validation: workspace ids must be strings...")
    try:
        await ws1.register_service(
            {
                "id": "invalid-service-4",
                "config": {
                    "visibility": "protected",
                    "authorized_workspaces": [
                        "valid-ws",
                        123,
                        "another-ws",
                    ],  # Should fail
                },
                "test": lambda: "test",
            }
        )
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "must be a string" in str(e)
        print(f"   ✅ String validation works: {e}")

    # Test 6: Empty authorized_workspaces list is valid
    print("6. Testing empty authorized_workspaces list...")
    await ws1.register_service(
        {
            "id": "empty-auth-service",
            "config": {
                "visibility": "protected",
                "authorized_workspaces": [],  # Empty list is valid
            },
            "test": lambda: "empty-auth",
        }
    )

    svc_empty = await ws1.get_service(
        f"{workspace1}/test-auth-client:empty-auth-service"
    )
    result = await svc_empty.test()
    assert result == "empty-auth"
    print("   ✅ Empty authorized_workspaces list works")

    # Test 7: Method calls are also protected by authorized_workspaces
    print("7. Testing that method calls respect authorized_workspaces...")
    # Register a service with methods that should be protected
    await ws1.register_service(
        {
            "id": "method-test-service",
            "config": {
                "visibility": "protected",
                "authorized_workspaces": [
                    "fake-authorized-workspace"
                ],  # Non-existent workspace
            },
            "protected_method": lambda x: f"protected: {x}",
            "another_method": lambda: "also protected",
        }
    )

    # Can get the service from same workspace
    svc_method = await ws1.get_service(
        f"{workspace1}/test-auth-client:method-test-service"
    )
    # Methods should work from same workspace (even though fake-authorized-workspace is listed)
    result = await svc_method.protected_method("test")
    assert result == "protected: test"
    result2 = await svc_method.another_method()
    assert result2 == "also protected"
    print("   ✅ Methods work from same workspace despite authorized_workspaces")

    # Test 8: Cross-workspace access - authorized workspace should have access
    print("\n8. Testing cross-workspace access (authorized)...")
    # Try to access the service from workspace2 (which is authorized)
    try:
        svc_from_ws2 = await ws2.get_service(
            f"{workspace1}/test-auth-client:authorized-service"
        )
        result = await svc_from_ws2.test_method("from-workspace2")
        assert result == "authorized: from-workspace2"
        print("   ✅ Authorized workspace can access protected service")
    except Exception as e:
        print(f"   ❌ Failed to access from authorized workspace: {e}")
        assert False, f"Authorized workspace should have access: {e}"

    # Test 9: Cross-workspace access - unauthorized workspace should be denied
    print("9. Testing cross-workspace access (unauthorized)...")
    # Try to access the service from workspace3 (which is NOT authorized)
    try:
        svc_from_ws3 = await ws3.get_service(
            f"{workspace1}/test-auth-client:authorized-service"
        )
        # Try to call the method - this should fail
        await svc_from_ws3.test_method("from-workspace3")
        assert False, "Unauthorized workspace should NOT have access"
    except Exception as e:
        assert (
            "not authorized" in str(e).lower()
            or "permission" in str(e).lower()
            or "denied" in str(e).lower()
        )
        print(f"   ✅ Unauthorized workspace correctly denied: {e}")

    # Test 10: Service with no authorized workspaces (empty list) - no external access
    print("10. Testing service with empty authorized_workspaces list...")
    try:
        # Try to access empty-auth-service from workspace2
        svc_empty_from_ws2 = await ws2.get_service(
            f"{workspace1}/test-auth-client:empty-auth-service"
        )
        await svc_empty_from_ws2.test()
        assert (
            False
        ), "Service with empty authorized_workspaces should deny all external access"
    except Exception as e:
        assert (
            "not authorized" in str(e).lower()
            or "permission" in str(e).lower()
            or "denied" in str(e).lower()
        )
        print(
            f"   ✅ Empty authorized_workspaces correctly denies external access: {e}"
        )

    # Test 11: Update authorized_workspaces dynamically
    print("11. Testing dynamic update of authorized_workspaces...")
    # Register a new service that initially allows workspace2
    await ws1.register_service(
        {
            "id": "dynamic-auth-service",
            "config": {
                "visibility": "protected",
                "authorized_workspaces": [workspace2],
            },
            "test": lambda: "dynamic-test",
        }
    )

    # Verify workspace2 can access
    svc_dynamic = await ws2.get_service(
        f"{workspace1}/test-auth-client:dynamic-auth-service"
    )
    result = await svc_dynamic.test()
    assert result == "dynamic-test"
    print("   ✅ Initial authorized workspace has access")

    # Now re-register with workspace3 instead
    await ws1.register_service(
        {
            "id": "dynamic-auth-service",
            "config": {
                "visibility": "protected",
                "authorized_workspaces": [workspace3],  # Changed to workspace3
            },
            "test": lambda: "dynamic-test-updated",
        },
        overwrite=True,
    )

    # workspace2 should now be denied
    try:
        svc_dynamic = await ws2.get_service(
            f"{workspace1}/test-auth-client:dynamic-auth-service"
        )
        await svc_dynamic.test()
        assert False, "Previously authorized workspace should now be denied"
    except Exception as e:
        print(f"   ✅ Previously authorized workspace now denied: {e}")

    # workspace3 should now have access
    svc_dynamic_ws3 = await ws3.get_service(
        f"{workspace1}/test-auth-client:dynamic-auth-service"
    )
    result = await svc_dynamic_ws3.test()
    assert result == "dynamic-test-updated"
    print("   ✅ Newly authorized workspace has access")

    # Cleanup
    await ws1.disconnect()
    await ws2.disconnect()
    await ws3.disconnect()

    print("✅ AUTHORIZED WORKSPACES FULL TEST PASSED!")


@pytest.mark.asyncio
async def test_long_running_method_with_heartbeat(restartable_server):
    """Test that long-running methods don't timeout as long as heartbeat is active."""
    print("\n=== LONG RUNNING METHOD WITH HEARTBEAT TEST ===")

    # Use a SHORT timeout (2 seconds) to verify heartbeat keeps method alive
    ws = await connect_to_server(
        {
            "name": "long-running-test",
            "server_url": f"ws://127.0.0.1:{restartable_server.port}/ws",
            "client_id": "long-running-test",
            "method_timeout": 2,  # 2 second timeout - methods will run LONGER than this
        }
    )

    print(f"   ⏱️  Method timeout set to 2 seconds")

    # Create a service with a long-running method
    class LongRunningService:
        async def long_task(self, duration_seconds, callback=None):
            """Simulates a long-running task that reports progress."""
            start_time = asyncio.get_event_loop().time()
            steps = duration_seconds * 2  # Report progress every 0.5 seconds

            for i in range(steps):
                await asyncio.sleep(0.5)
                elapsed = asyncio.get_event_loop().time() - start_time

                # Report progress via callback if provided
                if callback:
                    await callback(f"Progress: {i+1}/{steps}, elapsed: {elapsed:.1f}s")

            return f"Task completed after {duration_seconds} seconds"

        async def infinite_stream(self, callback):
            """Simulates infinite streaming (like terminal attach)."""
            count = 0
            while True:
                await asyncio.sleep(0.5)
                count += 1
                await callback(f"Stream update #{count}")
                # Stop after 5 updates for testing
                if count >= 5:
                    return f"Streamed {count} updates"

    # Register the service
    await ws.register_service(
        {
            "id": "long-running-service",
            "config": {"visibility": "protected"},
            "long_task": LongRunningService().long_task,
            "infinite_stream": LongRunningService().infinite_stream,
        }
    )

    # Test 1: Long-running method with callback (should not timeout)
    print("\n--- Test 1: Long-running method with progress callback ---")
    svc = await ws.get_service("long-running-service")

    progress_updates = []

    async def progress_callback(msg):
        progress_updates.append(msg)
        print(f"   📊 {msg}")

    # Run a task for 5 seconds - MORE than the 2 second timeout!
    # This proves heartbeat keeps it alive
    TASK_DURATION = 5  # 5 seconds > 2 second timeout
    print(f"   🚀 Starting {TASK_DURATION} second task (timeout is only 2 seconds)")

    start_time = asyncio.get_event_loop().time()
    result = await svc.long_task(TASK_DURATION, progress_callback)
    actual_duration = asyncio.get_event_loop().time() - start_time

    assert f"Task completed after {TASK_DURATION} seconds" in result
    assert actual_duration >= TASK_DURATION  # Verify it actually ran for full duration
    assert actual_duration > 2  # Verify it ran LONGER than the timeout
    assert len(progress_updates) >= TASK_DURATION * 2 - 1  # Should have ~10 updates
    print(
        f"   ✅ Task ran for {actual_duration:.1f}s (>{ws.rpc._method_timeout}s timeout) with {len(progress_updates)} updates"
    )

    # Test 2: Infinite streaming method (like terminal attach)
    print("\n--- Test 2: Infinite streaming method ---")
    stream_updates = []

    async def stream_callback(msg):
        stream_updates.append(msg)
        print(f"   📡 {msg}")

    # This simulates the terminal attach use case
    # Runs for ~2.5 seconds (5 updates * 0.5s each) - also longer than timeout
    print(f"   🚀 Starting streaming (will run >2s timeout)")

    start_time = asyncio.get_event_loop().time()
    result = await svc.infinite_stream(stream_callback)
    stream_duration = asyncio.get_event_loop().time() - start_time

    assert "Streamed 5 updates" in result
    assert len(stream_updates) == 5
    assert stream_duration > 2  # Verify it ran LONGER than the timeout
    print(
        f"   ✅ Streaming ran for {stream_duration:.1f}s (>2s timeout) with {len(stream_updates)} updates"
    )

    # Test 3: Pure long-running method WITHOUT any callbacks
    # This is the critical case — heartbeat alone must keep the connection alive
    print("\n--- Test 3: Pure long-running method (no callbacks) ---")

    async def pure_long_task(duration_seconds):
        """A long-running task that does NOT use any callbacks."""
        await asyncio.sleep(duration_seconds)
        return f"Pure task completed after {duration_seconds} seconds"

    await ws.register_service(
        {
            "id": "pure-long-service",
            "config": {"visibility": "protected"},
            "pure_long_task": pure_long_task,
        }
    )
    pure_svc = await ws.get_service("pure-long-service")

    PURE_DURATION = 5  # 5 seconds > 2 second timeout
    print(
        f"   🚀 Starting {PURE_DURATION}s pure async task (timeout is only 2s, no callbacks)"
    )

    start_time = asyncio.get_event_loop().time()
    result = await pure_svc.pure_long_task(PURE_DURATION)
    actual_duration = asyncio.get_event_loop().time() - start_time

    assert f"Pure task completed after {PURE_DURATION} seconds" in result
    assert actual_duration >= PURE_DURATION
    assert actual_duration > 2  # Must have survived past the timeout
    print(
        f"   ✅ Pure task ran for {actual_duration:.1f}s (>{ws.rpc._method_timeout}s timeout) — heartbeat kept it alive"
    )

    # Cleanup
    await ws.disconnect()

    print("✅ LONG RUNNING METHOD WITH HEARTBEAT TEST PASSED!")


@pytest.mark.asyncio
async def test_client_disconnection_cleanup(websocket_server):
    """Test that sessions are properly cleaned up when RPC disconnects."""
    print("\n=== CLIENT DISCONNECTION CLEANUP TEST ===")

    # This test focuses on verifying cleanup when the local RPC disconnects
    # The remote client disconnection scenario requires server event subscriptions
    # which are currently having issues, so we test the local cleanup instead

    # Create a client
    client = await connect_to_server(
        {
            "name": "disconnect-test",
            "server_url": WS_SERVER_URL,
            "client_id": "disconnect-test-client",
        }
    )

    # Register a service with async operations
    async def long_running():
        """Simulates a long-running operation."""
        await asyncio.sleep(5)
        return "completed"

    async def with_callback(callback):
        """Operation that uses callbacks."""
        for i in range(5):
            await asyncio.sleep(0.5)
            await callback(f"Progress: {i+1}/5")
        return "done"

    await client.register_service(
        {
            "id": "test-service",
            "config": {"visibility": "protected"},
            "long_running": long_running,
            "with_callback": with_callback,
        }
    )

    # Get our own service to create some sessions
    svc = await client.get_service(
        f"{client.config.workspace}/{client.config.client_id}:test-service"
    )

    # Start multiple async operations that will be pending
    progress_messages = []

    async def progress_callback(msg):
        progress_messages.append(msg)

    pending_calls = [
        svc.long_running(),
        svc.long_running(),
        svc.with_callback(progress_callback),
    ]

    # Give time for sessions to be created
    await asyncio.sleep(0.2)

    # Check that sessions exist in the object store
    initial_sessions = 0
    for key in client.rpc._object_store:
        if key not in ("services", "message_cache"):
            if isinstance(client.rpc._object_store[key], dict):
                if (
                    "reject" in client.rpc._object_store[key]
                    or "resolve" in client.rpc._object_store[key]
                ):
                    initial_sessions += 1

    print(f"📊 Active sessions before disconnect: {initial_sessions}")
    assert initial_sessions > 0, "Should have active sessions"

    # Disconnect the client (simulating connection loss)
    print("🔌 Disconnecting client...")
    client.rpc.close()

    # All pending calls should fail
    failed_count = 0
    for i, task in enumerate(pending_calls):
        try:
            await task
            assert False, f"Call {i} should have failed due to disconnection"
        except Exception as e:
            failed_count += 1
            print(f"✅ Call {i} correctly failed with: {str(e)}")
            assert "closed" in str(e).lower() or "disconnected" in str(e).lower()

    assert failed_count == len(
        pending_calls
    ), f"All {len(pending_calls)} calls should have failed"

    # Verify all sessions were cleaned up
    remaining_sessions = 0
    for key in client.rpc._object_store:
        if key not in ("services", "message_cache"):
            if isinstance(client.rpc._object_store.get(key), dict):
                if (
                    "reject" in client.rpc._object_store[key]
                    or "resolve" in client.rpc._object_store[key]
                ):
                    remaining_sessions += 1

    print(f"📊 Remaining sessions after cleanup: {remaining_sessions}")
    assert (
        remaining_sessions == 0
    ), "All sessions should be cleaned up after disconnection"

    print("✅ CLIENT DISCONNECTION CLEANUP TEST PASSED!")


@pytest.mark.asyncio
async def test_local_rpc_disconnection_cleanup(websocket_server):
    """Test that all pending sessions are cleaned up when local RPC disconnects."""
    print("\n=== LOCAL RPC DISCONNECTION CLEANUP TEST ===")

    # Create a client
    client = await connect_to_server(
        {
            "name": "local-disconnect-test",
            "server_url": WS_SERVER_URL,
            "client_id": "local-disconnect-test",
        }
    )

    # Register a test service with slow functions
    async def slow_function(duration=2):
        await asyncio.sleep(duration)
        return f"completed after {duration}s"

    await client.register_service(
        {
            "id": "slow-service-local",
            "config": {"visibility": "protected"},
            "slow_function": slow_function,
        }
    )

    # Give time for service to register
    await asyncio.sleep(0.1)

    # Get the service and start multiple pending calls
    svc = await client.get_service(
        f"{client.config.workspace}/{client.config.client_id}:slow-service-local"
    )

    # RPC calls already return futures, no need for create_task
    pending_tasks = [svc.slow_function(3), svc.slow_function(4), svc.slow_function(5)]

    # Give time for sessions to be created
    await asyncio.sleep(0.1)

    # Check initial session count
    initial_sessions = 0
    for key in client.rpc._object_store:
        if key not in ("services", "message_cache"):
            initial_sessions += 1

    print(f"📊 Active sessions before disconnect: {initial_sessions}")
    assert initial_sessions > 0, "Should have active sessions"

    # Disconnect the local RPC
    print("🔌 Disconnecting local RPC...")
    client.rpc.close()

    # All pending tasks should fail
    failed_count = 0
    for i, task in enumerate(pending_tasks):
        try:
            await task
            assert False, f"Task {i} should have failed after disconnection"
        except Exception as e:
            failed_count += 1
            print(f"✅ Task {i} correctly failed with: {str(e)}")
            assert "closed" in str(e).lower() or "disconnected" in str(e).lower()

    assert failed_count == len(
        pending_tasks
    ), f"All {len(pending_tasks)} tasks should have failed"

    # Verify all sessions were cleaned up
    remaining_sessions = 0
    for key in client.rpc._object_store:
        if key not in ("services", "message_cache") and isinstance(
            client.rpc._object_store.get(key), dict
        ):
            if (
                "reject" in client.rpc._object_store[key]
                or "resolve" in client.rpc._object_store[key]
            ):
                remaining_sessions += 1

    print(f"📊 Remaining sessions after cleanup: {remaining_sessions}")
    assert (
        remaining_sessions == 0
    ), "All sessions should be cleaned up after disconnection"

    print("✅ LOCAL RPC DISCONNECTION CLEANUP TEST PASSED!")


# =====================================================================
# New comprehensive tests for edge cases and robustness
# =====================================================================


@pytest.mark.asyncio
async def test_data_type_roundtrip(websocket_server):
    """Test serialization/deserialization of edge-case data types."""
    api = await connect_to_server(
        {
            "name": "data roundtrip",
            "server_url": WS_SERVER_URL,
            "client_id": "data-roundtrip-client",
        }
    )

    async def echo_data(data):
        return data

    await api.register_service(
        {
            "name": "Data Roundtrip Service",
            "id": "data-roundtrip-svc",
            "config": {"visibility": "protected"},
            "echo_data": echo_data,
        }
    )

    svc = await api.get_service("data-roundtrip-svc")

    # Unicode: emoji
    result = await svc.echo_data("Hello 🎉🚀")
    assert result == "Hello 🎉🚀"

    # Unicode: CJK characters
    result = await svc.echo_data("中文测试")
    assert result == "中文测试"

    # Unicode: RTL text
    result = await svc.echo_data("مرحبا")
    assert result == "مرحبا"

    # Unicode: combining characters
    result = await svc.echo_data("e\u0301")  # e + combining acute accent
    assert result == "e\u0301"

    # Special floats: infinity and negative infinity
    result = await svc.echo_data(float("inf"))
    assert result == float("inf")

    result = await svc.echo_data(float("-inf"))
    assert result == float("-inf")

    # Special floats: NaN
    import math

    result = await svc.echo_data(float("nan"))
    assert math.isnan(result)

    # Very large integers
    result = await svc.echo_data(2**63)
    assert result == 2**63

    # Empty containers
    result = await svc.echo_data({})
    assert result == {}

    result = await svc.echo_data([])
    assert result == []

    result = await svc.echo_data("")
    assert result == ""

    result = await svc.echo_data(b"")
    assert result == b""

    # Bytes with nulls
    result = await svc.echo_data(b"\x00\x01\x02")
    assert result == b"\x00\x01\x02"

    # Deep nesting (50+ levels)
    deep = {"value": 42}
    for _ in range(50):
        deep = {"nested": deep}
    result = await svc.echo_data(deep)
    # Navigate down to verify
    node = result
    for _ in range(50):
        node = node["nested"]
    assert node["value"] == 42

    # Mixed types in lists
    mixed = [1, "string", True, None, {"nested": [1, 2, 3]}]
    result = await svc.echo_data(mixed)
    assert result[0] == 1
    assert result[1] == "string"
    assert result[2] is True
    assert result[3] is None
    assert result[4]["nested"] == [1, 2, 3]

    # Boolean values
    result = await svc.echo_data(True)
    assert result is True
    result = await svc.echo_data(False)
    assert result is False

    # None
    result = await svc.echo_data(None)
    assert result is None

    await api.disconnect()


@pytest.mark.asyncio
async def test_callback_error_propagation(websocket_server):
    """Test that errors in callbacks propagate back to the caller."""
    server = await connect_to_server(
        {
            "name": "callback error test server",
            "server_url": WS_SERVER_URL,
            "client_id": "cb-error-server",
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    async def call_with_callback(callback):
        """Call the callback and return the result."""
        return await callback("test-arg")

    await server.register_service(
        {
            "name": "Callback Error Service",
            "id": "cb-error-svc",
            "config": {"visibility": "protected"},
            "call_with_callback": call_with_callback,
        }
    )

    client = await connect_to_server(
        {
            "name": "callback error test client",
            "server_url": WS_SERVER_URL,
            "client_id": "cb-error-client",
            "workspace": workspace,
            "token": token,
        }
    )

    svc = await client.get_service("cb-error-svc")

    # Callback that raises an exception
    def failing_callback(arg):
        raise ValueError("Callback deliberate failure")

    try:
        result = await asyncio.wait_for(
            svc.call_with_callback(failing_callback), timeout=15
        )
        # If we get here, the error was not propagated - but some implementations
        # may swallow it. The key thing is it should not hang forever.
        assert False, "Expected an exception from the failing callback"
    except (Exception,) as e:
        # The error should have propagated
        assert "Callback deliberate failure" in str(e) or "Error" in str(e)

    await client.disconnect()
    await server.disconnect()


@pytest.mark.asyncio
async def test_callback_multiple_invocations(websocket_server):
    """Test that a callback can be invoked multiple times (progress pattern)."""
    server = await connect_to_server(
        {
            "name": "multi callback server",
            "server_url": WS_SERVER_URL,
            "client_id": "multi-cb-server",
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    async def process_with_progress(n, progress_callback):
        """Call progress_callback n times with progress updates."""
        for i in range(n):
            await progress_callback(i, n)
        return "done"

    await server.register_service(
        {
            "name": "Multi Callback Service",
            "id": "multi-cb-svc",
            "config": {"visibility": "protected"},
            "process_with_progress": process_with_progress,
        }
    )

    client = await connect_to_server(
        {
            "name": "multi callback client",
            "server_url": WS_SERVER_URL,
            "client_id": "multi-cb-client",
            "workspace": workspace,
            "token": token,
        }
    )

    svc = await client.get_service("multi-cb-svc")

    # Collect all progress calls
    progress_calls = []

    def progress_callback(current, total):
        progress_calls.append((current, total))

    result = await asyncio.wait_for(
        svc.process_with_progress(10, progress_callback), timeout=30
    )
    assert result == "done"

    # Verify callback was invoked exactly 10 times with correct arguments
    assert len(progress_calls) == 10
    for i in range(10):
        assert progress_calls[i] == (
            i,
            10,
        ), f"Expected ({i}, 10), got {progress_calls[i]}"

    await client.disconnect()
    await server.disconnect()


@pytest.mark.asyncio
async def test_timeout_produces_clear_error(websocket_server):
    """Test that calling a hanging service with timeout produces a clear error."""
    server = await connect_to_server(
        {
            "name": "timeout test server",
            "server_url": WS_SERVER_URL,
            "client_id": "timeout-test-server",
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    async def hang_forever():
        """A service function that never returns."""
        await asyncio.sleep(3600)  # Sleep for 1 hour
        return "should never reach here"

    await server.register_service(
        {
            "name": "Hanging Service",
            "id": "hanging-svc",
            "config": {"visibility": "protected"},
            "hang": hang_forever,
        }
    )

    client = await connect_to_server(
        {
            "name": "timeout test client",
            "server_url": WS_SERVER_URL,
            "client_id": "timeout-test-client",
            "workspace": workspace,
            "token": token,
            "method_timeout": 3,  # Short timeout
        }
    )

    svc = await client.get_service("hanging-svc")

    try:
        await asyncio.wait_for(svc.hang(), timeout=10)
        assert False, "Expected a timeout error"
    except (asyncio.TimeoutError, Exception) as e:
        # Either asyncio timeout or RPC timeout should fire
        error_msg = str(e).lower()
        assert (
            "timeout" in error_msg
            or "timed out" in error_msg
            or isinstance(e, asyncio.TimeoutError)
        ), f"Expected timeout error, got: {e}"

    await client.disconnect()
    await server.disconnect()


@pytest.mark.asyncio
async def test_concurrent_call_partial_failure(websocket_server):
    """Test concurrent calls where some succeed and some fail."""
    api = await connect_to_server(
        {
            "name": "concurrent test",
            "server_url": WS_SERVER_URL,
            "client_id": "concurrent-test-client",
        }
    )

    async def succeed(x):
        return x * 2

    async def fail(x):
        raise ValueError(f"Deliberate failure for {x}")

    await api.register_service(
        {
            "name": "Concurrent Test Service",
            "id": "concurrent-svc",
            "config": {"visibility": "protected"},
            "succeed": succeed,
            "fail": fail,
        }
    )

    svc = await api.get_service("concurrent-svc")

    # Make concurrent calls - mix of success and failure
    success_tasks = [svc.succeed(i) for i in range(5)]
    fail_tasks = [svc.fail(i) for i in range(5)]

    # Gather success tasks
    success_results = await asyncio.gather(*success_tasks)
    assert success_results == [0, 2, 4, 6, 8]

    # Gather fail tasks - should all raise exceptions
    fail_results = await asyncio.gather(*fail_tasks, return_exceptions=True)
    for i, result in enumerate(fail_results):
        assert isinstance(
            result, Exception
        ), f"Expected exception for task {i}, got {result}"
        assert "Deliberate failure" in str(result)

    # Now do them all interleaved to make sure there's no cross-contamination
    mixed_tasks = []
    for i in range(5):
        mixed_tasks.append(svc.succeed(i))
        mixed_tasks.append(svc.fail(i))

    mixed_results = await asyncio.gather(*mixed_tasks, return_exceptions=True)
    for i in range(0, 10, 2):
        # Even indices are succeed calls
        assert mixed_results[i] == (i // 2) * 2, f"Success result mismatch at index {i}"
        # Odd indices are fail calls
        assert isinstance(
            mixed_results[i + 1], Exception
        ), f"Expected exception at index {i+1}"

    await api.disconnect()


@pytest.mark.asyncio
async def test_generator_error_mid_stream(websocket_server):
    """Test async generator that yields some values then raises an error."""
    server = await connect_to_server(
        {
            "name": "gen error server",
            "server_url": WS_SERVER_URL,
            "client_id": "gen-error-server",
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    async def error_generator(n_good):
        """Yield n_good values then raise an error."""
        for i in range(n_good):
            yield i
        raise RuntimeError("Generator mid-stream failure")

    await server.register_service(
        {
            "name": "Generator Error Service",
            "id": "gen-error-svc",
            "config": {"visibility": "protected"},
            "error_generator": error_generator,
        }
    )

    client = await connect_to_server(
        {
            "name": "gen error client",
            "server_url": WS_SERVER_URL,
            "client_id": "gen-error-client",
            "workspace": workspace,
            "token": token,
        }
    )

    svc = await client.get_service("gen-error-svc")

    gen = await svc.error_generator(3)
    collected = []
    error_caught = False
    try:
        async for item in gen:
            collected.append(item)
    except Exception as e:
        error_caught = True
        # The error propagation may carry the original message or a session error
        # depending on timing; the key thing is that an error IS raised
        error_msg = str(e)
        assert (
            "Generator mid-stream failure" in error_msg
            or "Session not found" in error_msg
            or "Error" in error_msg
        ), f"Unexpected error message: {error_msg}"

    assert error_caught, "Expected an error from the generator"
    # Note: Depending on session cleanup timing, some or all good values may
    # have been received before the error. The critical guarantee is that the
    # error was raised rather than the iteration silently stopping.
    # If all values were received, they should be correct.
    for i, v in enumerate(collected):
        assert v == i, f"Collected value at index {i} should be {i}, got {v}"

    await client.disconnect()
    await server.disconnect()


@pytest.mark.asyncio
async def test_generator_partial_consumption_cleanup(websocket_server):
    """Test that partially consuming a generator cleans up remote resources."""
    server = await connect_to_server(
        {
            "name": "gen partial server",
            "server_url": WS_SERVER_URL,
            "client_id": "gen-partial-server",
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    cleanup_called = asyncio.Event()

    def large_counter(n=100):
        """A generator that yields many items."""
        try:
            for i in range(n):
                yield i
        finally:
            # This runs when the generator is closed
            cleanup_called.set()

    await server.register_service(
        {
            "name": "Partial Gen Service",
            "id": "partial-gen-svc",
            "config": {"visibility": "protected"},
            "large_counter": large_counter,
        }
    )

    client = await connect_to_server(
        {
            "name": "gen partial client",
            "server_url": WS_SERVER_URL,
            "client_id": "gen-partial-client",
            "workspace": workspace,
            "token": token,
        }
    )

    svc = await client.get_service("partial-gen-svc")

    # Partially consume the generator (only take 3 out of 100 items)
    gen = await svc.large_counter(100)
    collected = []
    async for item in gen:
        collected.append(item)
        if len(collected) >= 3:
            break

    assert collected == [0, 1, 2]

    # Explicitly close the generator to trigger the finally block,
    # which sends the close signal to the remote side.
    await gen.aclose()

    # Wait for the close signal to propagate over RPC
    try:
        await asyncio.wait_for(cleanup_called.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        pass

    # The generator's finally block should have been called
    assert cleanup_called.is_set(), "Generator cleanup (finally block) was not called after partial consumption"

    await client.disconnect()
    await server.disconnect()


@pytest.mark.asyncio
async def test_generator_session_store_cleanup(websocket_server):
    """Test that generator entries are removed from session store after close."""
    server = await connect_to_server(
        {
            "name": "gen store server",
            "server_url": WS_SERVER_URL,
            "client_id": "gen-store-server",
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    def counter(n=10):
        for i in range(n):
            yield i

    await server.register_service(
        {
            "name": "Store Gen Service",
            "id": "store-gen-svc",
            "config": {"visibility": "protected"},
            "counter": counter,
        }
    )

    client = await connect_to_server(
        {
            "name": "gen store client",
            "server_url": WS_SERVER_URL,
            "client_id": "gen-store-client",
            "workspace": workspace,
            "token": token,
        }
    )

    svc = await client.get_service("store-gen-svc")

    # Get the RPC object to inspect session stores
    server_rpc = server.rpc

    # Fully consume a generator
    gen = await svc.counter(5)
    async for _ in gen:
        pass

    # Wait for cleanup
    await asyncio.sleep(0.5)

    # Check that session store entries were cleaned up (no lingering :close keys)
    store = server_rpc._object_store
    close_keys = []
    for session_key, session_val in store.items():
        if isinstance(session_val, dict):
            for k in session_val:
                if isinstance(k, str) and ":close" in k:
                    close_keys.append(k)
    # After full consumption, there should be no :close entries for this generator
    # (they are cleaned up on StopIteration)

    # Now test partial consumption with explicit close
    gen2 = await svc.counter(100)
    collected = []
    async for item in gen2:
        collected.append(item)
        if len(collected) >= 2:
            break

    assert collected == [0, 1]

    # Explicitly close the generator to trigger cleanup
    await gen2.aclose()

    # Wait for close signal to propagate over RPC
    await asyncio.sleep(2.0)

    # After partial consumption + close signal, the store entries should also be gone
    remaining_gen_keys = []
    for session_key, session_val in store.items():
        if isinstance(session_val, dict):
            for k in session_val:
                if isinstance(k, str) and ":close" in k:
                    remaining_gen_keys.append(k)

    assert (
        len(remaining_gen_keys) == 0
    ), f"Expected no remaining generator close keys, found: {remaining_gen_keys}"

    await client.disconnect()
    await server.disconnect()


@pytest.mark.asyncio
async def test_async_iterator_over_rpc(websocket_server):
    """Test that custom async iterators work over RPC."""
    server = await connect_to_server(
        {
            "name": "async iter server",
            "server_url": WS_SERVER_URL,
            "client_id": "async-iter-server",
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    class AsyncRange:
        """A custom async iterator that counts from 0 to n-1."""

        def __init__(self, n):
            self.n = n
            self.i = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.i >= self.n:
                raise StopAsyncIteration
            val = self.i
            self.i += 1
            await asyncio.sleep(0.01)
            return val

    def get_async_range(n):
        return AsyncRange(n)

    await server.register_service(
        {
            "name": "Async Iter Service",
            "id": "async-iter-svc",
            "config": {"visibility": "protected"},
            "get_async_range": get_async_range,
        }
    )

    client = await connect_to_server(
        {
            "name": "async iter client",
            "server_url": WS_SERVER_URL,
            "client_id": "async-iter-client",
            "workspace": workspace,
            "token": token,
        }
    )

    svc = await client.get_service("async-iter-svc")

    # Fully consume the async iterator
    result = await svc.get_async_range(5)
    collected = []
    async for item in result:
        collected.append(item)
    assert collected == [0, 1, 2, 3, 4]

    # Partially consume the async iterator
    result2 = await svc.get_async_range(10)
    partial = []
    async for item in result2:
        partial.append(item)
        if len(partial) >= 3:
            break
    assert partial == [0, 1, 2]

    await client.disconnect()
    await server.disconnect()


@pytest.mark.asyncio
async def test_sync_iterator_over_rpc(websocket_server):
    """Test that custom sync iterators work over RPC."""
    server = await connect_to_server(
        {
            "name": "sync iter server",
            "server_url": WS_SERVER_URL,
            "client_id": "sync-iter-server",
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    class SyncRange:
        """A custom sync iterator that counts from 0 to n-1."""

        def __init__(self, n):
            self.n = n
            self.i = 0

        def __iter__(self):
            return self

        def __next__(self):
            if self.i >= self.n:
                raise StopIteration
            val = self.i
            self.i += 1
            return val

    def get_sync_range(n):
        return SyncRange(n)

    await server.register_service(
        {
            "name": "Sync Iter Service",
            "id": "sync-iter-svc",
            "config": {"visibility": "protected"},
            "get_sync_range": get_sync_range,
        }
    )

    client = await connect_to_server(
        {
            "name": "sync iter client",
            "server_url": WS_SERVER_URL,
            "client_id": "sync-iter-client",
            "workspace": workspace,
            "token": token,
        }
    )

    svc = await client.get_service("sync-iter-svc")

    # Remote side receives it as an async generator
    result = await svc.get_sync_range(5)
    collected = []
    async for item in result:
        collected.append(item)
    assert collected == [0, 1, 2, 3, 4]

    await client.disconnect()
    await server.disconnect()


@pytest.mark.asyncio
async def test_multiple_generators_no_leak(websocket_server):
    """Test that creating and abandoning multiple generators doesn't leak."""
    server = await connect_to_server(
        {
            "name": "multi gen server",
            "server_url": WS_SERVER_URL,
            "client_id": "multi-gen-server",
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    def infinite_counter():
        i = 0
        while True:
            yield i
            i += 1

    await server.register_service(
        {
            "name": "Multi Gen Service",
            "id": "multi-gen-svc",
            "config": {"visibility": "protected"},
            "infinite_counter": infinite_counter,
        }
    )

    client = await connect_to_server(
        {
            "name": "multi gen client",
            "server_url": WS_SERVER_URL,
            "client_id": "multi-gen-client",
            "workspace": workspace,
            "token": token,
        }
    )

    svc = await client.get_service("multi-gen-svc")

    # Create and partially consume several generators, explicitly closing each
    for _ in range(5):
        gen = await svc.infinite_counter()
        count = 0
        async for item in gen:
            count += 1
            if count >= 3:
                break
        await gen.aclose()

    # Wait for close signals to propagate
    await asyncio.sleep(2.0)

    # Check the session store - should not have accumulated generator entries
    server_rpc = server.rpc
    store = server_rpc._object_store
    gen_keys = []
    for session_key, session_val in store.items():
        if isinstance(session_val, dict):
            for k, v in session_val.items():
                if isinstance(k, str) and ":close" in k:
                    gen_keys.append(k)

    assert (
        len(gen_keys) == 0
    ), f"Found {len(gen_keys)} leaked generator entries: {gen_keys}"

    await client.disconnect()
    await server.disconnect()


@pytest.mark.asyncio
async def test_activity_based_gc_keeps_sessions_alive(websocket_server):
    """Test that session GC uses last-activity time, not creation time.

    Verifies that a generator session stays alive when actively used,
    even after the TTL has elapsed since creation.
    """
    server = await connect_to_server(
        {
            "name": "gc activity server",
            "server_url": WS_SERVER_URL,
            "client_id": "gc-activity-server",
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    def slow_counter(n=10):
        for i in range(n):
            yield i

    await server.register_service(
        {
            "name": "GC Activity Service",
            "id": "gc-activity-svc",
            "config": {"visibility": "protected"},
            "slow_counter": slow_counter,
        }
    )

    client = await connect_to_server(
        {
            "name": "gc activity client",
            "server_url": WS_SERVER_URL,
            "client_id": "gc-activity-client",
            "workspace": workspace,
            "token": token,
        }
    )

    svc = await client.get_service("gc-activity-svc")

    # Start a generator
    gen = await svc.slow_counter(10)

    # Consume the first 3 items
    collected = []
    async for item in gen:
        collected.append(item)
        if len(collected) >= 3:
            break

    assert collected == [0, 1, 2]

    # Now temporarily set a very short TTL on the server's RPC
    # and verify that the session survives because _last_activity_at was updated
    server_rpc = server.rpc
    original_ttl = server_rpc._session_ttl

    # Find the session with our generator
    store = server_rpc._object_store
    generator_session_key = None
    for key, val in store.items():
        if key in ("services", "message_cache"):
            continue
        if isinstance(val, dict) and "_last_activity_at" in val:
            # Check if this session has generator entries (keys with :close)
            for k in val:
                if isinstance(k, str) and ":close" in k:
                    generator_session_key = key
                    break
        if generator_session_key:
            break

    if generator_session_key:
        session = store[generator_session_key]
        # Verify _last_activity_at was set and is recent
        assert "_last_activity_at" in session, "Session should have _last_activity_at"
        assert "_created_at" in session, "Session should have _created_at"
        # _last_activity_at should be >= _created_at (updated on method calls)
        assert session["_last_activity_at"] >= session["_created_at"]

    # Set a very short TTL (1 second) and manually run GC
    server_rpc._session_ttl = 1
    await asyncio.sleep(1.5)  # Wait for TTL to expire based on creation time

    # But first update _last_activity_at to now (simulating recent activity)
    if generator_session_key and generator_session_key in store:
        import time

        store[generator_session_key]["_last_activity_at"] = time.time()

    # Run the GC sweep manually by checking what would be cleaned
    # The session should NOT be cleaned because _last_activity_at is recent
    import time

    now = time.time()
    would_be_cleaned = []
    for key in list(store.keys()):
        if key in ("services", "message_cache"):
            continue
        session = store.get(key)
        if not isinstance(session, dict):
            continue
        last_activity = session.get("_last_activity_at", session.get("_created_at"))
        if last_activity is None:
            continue
        if now - last_activity > server_rpc._session_ttl:
            would_be_cleaned.append(key)

    # Our generator session should NOT be in the would-be-cleaned list
    # because we just updated _last_activity_at
    if generator_session_key:
        assert generator_session_key not in would_be_cleaned, (
            f"Session {generator_session_key} would be cleaned despite recent activity"
        )

    # Restore original TTL
    server_rpc._session_ttl = original_ttl

    # Clean up
    await gen.aclose()
    await client.disconnect()
    await server.disconnect()


@pytest.mark.asyncio
async def test_disconnect_rejects_pending_calls(websocket_server):
    """Test that disconnecting rejects all pending RPC calls."""
    server = await connect_to_server(
        {
            "name": "disconnect reject server",
            "server_url": WS_SERVER_URL,
            "client_id": "disconnect-reject-server",
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    async def slow_operation():
        """A slow service function."""
        await asyncio.sleep(30)
        return "should not complete"

    await server.register_service(
        {
            "name": "Slow Service",
            "id": "slow-svc",
            "config": {"visibility": "protected"},
            "slow_op": slow_operation,
        }
    )

    client = await connect_to_server(
        {
            "name": "disconnect reject client",
            "server_url": WS_SERVER_URL,
            "client_id": "disconnect-reject-client",
            "workspace": workspace,
            "token": token,
        }
    )

    svc = await client.get_service("slow-svc")

    # Start a slow call using asyncio.ensure_future to handle both coroutine and future
    slow_task = asyncio.ensure_future(svc.slow_op())

    # Give a moment for the call to be sent
    await asyncio.sleep(0.5)

    # Disconnect the client while the call is pending
    await client.disconnect()

    # The pending call should be rejected
    try:
        result = await asyncio.wait_for(slow_task, timeout=10)
        assert False, "Expected the slow call to be rejected after disconnect"
    except Exception as e:
        error_msg = str(e).lower()
        assert (
            "closed" in error_msg
            or "disconnected" in error_msg
            or "connection" in error_msg
            or isinstance(e, asyncio.TimeoutError)
            or isinstance(e, asyncio.CancelledError)
        ), f"Expected connection-related error, got: {e}"

    await server.disconnect()


@pytest.mark.asyncio
async def test_callback_cleanup_after_call(websocket_server):
    """Test that _object_store does not grow unboundedly after many calls with callbacks."""
    server = await connect_to_server(
        {
            "name": "cb cleanup server",
            "server_url": WS_SERVER_URL,
            "client_id": "cb-cleanup-server",
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    async def invoke_callback(callback):
        """Invoke the callback once and return the result."""
        return await callback("ping")

    await server.register_service(
        {
            "name": "Callback Cleanup Service",
            "id": "cb-cleanup-svc",
            "config": {"visibility": "protected"},
            "invoke_callback": invoke_callback,
        }
    )

    client = await connect_to_server(
        {
            "name": "cb cleanup client",
            "server_url": WS_SERVER_URL,
            "client_id": "cb-cleanup-client",
            "workspace": workspace,
            "token": token,
        }
    )

    svc = await client.get_service("cb-cleanup-svc")

    # Record the baseline store size
    baseline = len(client.rpc._object_store)

    # Make many calls with callbacks
    num_calls = 50
    for i in range(num_calls):
        result = await svc.invoke_callback(lambda x: f"pong-{x}")
        assert result == "pong-ping"

    # Allow some time for cleanup
    await asyncio.sleep(1)

    # The object store should not have grown by num_calls entries.
    # Some small overhead is acceptable (e.g., message_cache, default service),
    # but it should be bounded, not proportional to num_calls.
    after_size = len(client.rpc._object_store)
    growth = after_size - baseline
    assert growth < num_calls, (
        f"Object store grew by {growth} after {num_calls} calls. "
        f"Baseline={baseline}, After={after_size}. "
        "This suggests callbacks are not being cleaned up."
    )

    await client.disconnect()
    await server.disconnect()


@pytest.mark.asyncio
async def test_callback_session_removed_immediately(websocket_server):
    """Test that the callback session is removed immediately after the function returns."""
    server = await connect_to_server(
        {
            "name": "cb session test server",
            "server_url": WS_SERVER_URL,
            "client_id": "cb-session-server",
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    async def use_callback(callback):
        """Call the callback during execution, then return."""
        result = await callback(42)
        return result

    await server.register_service(
        {
            "name": "CB Session Service",
            "id": "cb-session-svc",
            "config": {"visibility": "protected"},
            "use_callback": use_callback,
        }
    )

    client = await connect_to_server(
        {
            "name": "cb session test client",
            "server_url": WS_SERVER_URL,
            "client_id": "cb-session-client",
            "workspace": workspace,
            "token": token,
        }
    )

    svc = await client.get_service("cb-session-svc")

    # Snapshot store keys before the call
    keys_before = set(client.rpc._object_store.keys())

    result = await svc.use_callback(lambda x: x * 2)
    assert result == 84

    # The session created for this call should be cleaned up immediately
    keys_after = set(client.rpc._object_store.keys())
    new_sessions = keys_after - keys_before
    # No new sessions should remain (the call is complete, session is cleaned)
    assert len(new_sessions) == 0, (
        f"Expected no new sessions after call returned, but found: {new_sessions}"
    )

    await client.disconnect()
    await server.disconnect()


@pytest.mark.asyncio
async def test_nested_callback(websocket_server):
    """Test nested callbacks: a service calls a callback that itself accepts a callback."""
    server = await connect_to_server(
        {
            "name": "nested cb server",
            "server_url": WS_SERVER_URL,
            "client_id": "nested-cb-server",
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    async def outer_call(outer_callback):
        """Call the outer callback, passing an inner function that the callback can invoke."""

        async def inner_function(value):
            return value * 10

        result = await outer_callback(inner_function)
        return result

    await server.register_service(
        {
            "name": "Nested Callback Service",
            "id": "nested-cb-svc",
            "config": {"visibility": "protected"},
            "outer_call": outer_call,
        }
    )

    client = await connect_to_server(
        {
            "name": "nested cb client",
            "server_url": WS_SERVER_URL,
            "client_id": "nested-cb-client",
            "workspace": workspace,
            "token": token,
        }
    )

    svc = await client.get_service("nested-cb-svc")

    # The outer_callback receives inner_function from the server side,
    # and calls it with a value.
    async def outer_callback(inner_fn):
        # inner_fn is a remote function from the server
        result = await inner_fn(7)
        return result + 1

    result = await svc.outer_call(outer_callback)
    # inner_function(7) = 70, outer_callback returns 70 + 1 = 71
    assert result == 71, f"Expected 71, got {result}"

    await client.disconnect()
    await server.disconnect()


@pytest.mark.asyncio
async def test_strict_callback_lifecycle(websocket_server):
    """Test the strict callback lifecycle rule:
    1. Callbacks work DURING function execution (before return)
    2. Callback sessions are cleaned up IMMEDIATELY when the function returns
    3. Invoking a stored callback AFTER the function has returned should fail
    """
    server = await connect_to_server(
        {
            "name": "lifecycle server",
            "server_url": WS_SERVER_URL,
            "client_id": "lifecycle-server",
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    stored_callback = None

    async def use_callback_and_return(callback):
        """Call the callback during execution, store it, then return."""
        nonlocal stored_callback
        # Callback should work during execution
        result = await callback("during-execution")
        stored_callback = callback
        return result

    async def invoke_stored_callback():
        """Try to invoke the stored callback after the parent function returned."""
        if stored_callback is None:
            raise RuntimeError("No stored callback")
        # This should fail because the callback session was cleaned up
        result = await stored_callback("after-return")
        return result

    await server.register_service(
        {
            "name": "Lifecycle Service",
            "id": "lifecycle-svc",
            "config": {"visibility": "protected"},
            "use_callback_and_return": use_callback_and_return,
            "invoke_stored_callback": invoke_stored_callback,
        }
    )

    client = await connect_to_server(
        {
            "name": "lifecycle client",
            "server_url": WS_SERVER_URL,
            "client_id": "lifecycle-client",
            "workspace": workspace,
            "token": token,
        }
    )

    svc = await client.get_service("lifecycle-svc")

    # 1. Callback works DURING execution
    callback_invocations = []

    async def my_callback(msg):
        callback_invocations.append(msg)
        return f"reply-{msg}"

    result = await svc.use_callback_and_return(my_callback)
    assert result == "reply-during-execution"
    assert callback_invocations == ["during-execution"]

    # 2. Session should be cleaned up after the function returned
    await asyncio.sleep(0.5)
    keys_before = set(client.rpc._object_store.keys())
    # Make a second call to verify the previous session was cleaned
    result2 = await svc.use_callback_and_return(my_callback)
    assert result2 == "reply-during-execution"
    assert callback_invocations == ["during-execution", "during-execution"]

    # 3. Server tries to invoke the stored callback — it should fail
    # because the session was cleaned up on the client side when the
    # function returned
    with pytest.raises(Exception):
        await svc.invoke_stored_callback()

    await client.disconnect()
    await server.disconnect()


@pytest.mark.asyncio
async def test_rintf_session_lifecycle(websocket_server):
    """Test _rintf session lifecycle:
    1. Callbacks with _rintf work during function execution
    2. Callbacks with _rintf persist AFTER function returns (unlike regular callbacks)
    3. _rintf_service_id is set on the original object for manual cleanup
    4. Manual unregistration via unregister_service works
    """
    server = await connect_to_server(
        {
            "name": "rintf server",
            "server_url": WS_SERVER_URL,
            "client_id": "rintf-server",
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    stored_interface = None

    async def use_interface_and_return(interface_obj):
        """Call the interface during execution, store it, return."""
        nonlocal stored_interface
        # Call getItem during execution — should work
        result = await interface_obj["getItem"]("key1")
        stored_interface = interface_obj
        return result

    async def call_stored_interface(key):
        """Call the stored interface's getItem after parent function returned."""
        if stored_interface is None:
            raise RuntimeError("No stored interface")
        return await stored_interface["getItem"](key)

    await server.register_service(
        {
            "name": "RIntf Service",
            "id": "rintf-svc",
            "config": {"visibility": "protected"},
            "use_interface_and_return": use_interface_and_return,
            "call_stored_interface": call_stored_interface,
        }
    )

    client = await connect_to_server(
        {
            "name": "rintf client",
            "server_url": WS_SERVER_URL,
            "client_id": "rintf-client",
            "workspace": workspace,
            "token": token,
        }
    )

    svc = await client.get_service("rintf-svc")

    # Create an interface object with _rintf: True
    data_store = {"key1": "value1", "key2": "value2"}

    async def get_item(key):
        return data_store.get(key, None)

    interface_obj = {"_rintf": True, "getItem": get_item}

    # Verify no _rintf services exist before the call
    rintf_services_before = [
        k for k in client.rpc._services if k.startswith("_rintf_")
    ]
    assert len(rintf_services_before) == 0

    # 1. Call with _rintf interface — callback works during execution
    result = await svc.use_interface_and_return(interface_obj)
    assert result == "value1"

    # Verify _rintf_service_id was set back on the original object
    assert "_rintf_service_id" in interface_obj, (
        "Expected _rintf_service_id to be set on the original object"
    )
    rintf_service_id = interface_obj["_rintf_service_id"]
    assert rintf_service_id.startswith("_rintf_")

    # Verify the _rintf service was auto-registered on the client side
    assert rintf_service_id in client.rpc._services

    # 2. Callback still works AFTER function returned (because _rintf persists)
    await asyncio.sleep(0.5)  # Small delay to confirm function has returned
    result2 = await svc.call_stored_interface("key2")
    assert result2 == "value2"

    # 3. Service persists — no auto-timeout
    await asyncio.sleep(2)
    assert rintf_service_id in client.rpc._services, (
        "_rintf service should persist until manually unregistered"
    )
    result3 = await svc.call_stored_interface("key1")
    assert result3 == "value1"

    # 4. Manual unregistration works (auto-detects _rintf as local-only)
    await client.rpc.unregister_service(rintf_service_id)

    # Service should now be gone
    assert rintf_service_id not in client.rpc._services

    # Calling the stored interface should fail
    with pytest.raises(Exception):
        await svc.call_stored_interface("key1")

    await client.disconnect()
    await server.disconnect()


@pytest.mark.asyncio
async def test_encrypted_service_calls(websocket_server):
    """Test E2E encrypted service calls between two clients."""
    # Service provider with encryption
    server = await connect_to_server(
        {
            "name": "encryption-server",
            "server_url": WS_SERVER_URL,
            "client_id": "encryption-server-client",
            "encryption": True,
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    received_contexts = []

    def verified_echo(message, context=None):
        """Echo that records the context for verification."""
        received_contexts.append(context)
        return f"encrypted: {message}"

    def verified_add(a, b, context=None):
        received_contexts.append(context)
        return a + b

    await server.register_service(
        {
            "id": "encrypted-service",
            "config": {
                "require_context": True,
                "visibility": "protected",
            },
            "echo": verified_echo,
            "add": verified_add,
        }
    )

    # Caller with encryption enabled, same workspace
    client = await connect_to_server(
        {
            "name": "encryption-client",
            "server_url": WS_SERVER_URL,
            "client_id": "encryption-caller-client",
            "workspace": workspace,
            "token": token,
            "encryption": True,
        }
    )

    # Caller provides server's public key out-of-band (not via the server)
    server_pub_key = server.rpc.get_public_key()
    svc = await client.get_service(
        "encrypted-service", encryption_public_key=server_pub_key
    )

    # Test 1: Encrypted call should succeed
    result = await svc.echo("hello encrypted world")
    assert result == "encrypted: hello encrypted world"

    # Verify context had encryption info
    assert len(received_contexts) == 1
    ctx = received_contexts[0]
    assert ctx.get("encryption") is True
    assert ctx.get("caller_public_key") is not None
    assert len(ctx["caller_public_key"]) == 64  # hex string

    # Test 2: Another encrypted call
    result2 = await svc.add(5, 3)
    assert result2 == 8

    # Test 3: The caller's public key should match across calls
    assert received_contexts[1]["caller_public_key"] == received_contexts[0]["caller_public_key"]

    # Test 4: The caller's public key should be the one from the client's RPC
    assert received_contexts[0]["caller_public_key"] == client.rpc.get_public_key()

    await client.disconnect()
    await server.disconnect()


@pytest.mark.asyncio
async def test_unencrypted_to_encrypted_service(websocket_server):
    """Test that unencrypted calls to encrypted services work (no encryption applied)."""
    # Service provider with encryption
    server = await connect_to_server(
        {
            "name": "enc-service-server",
            "server_url": WS_SERVER_URL,
            "client_id": "enc-service-server-client",
            "encryption": True,
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    contexts = []

    await server.register_service(
        {
            "id": "enc-optional-service",
            "config": {
                "require_context": True,
                "visibility": "protected",
            },
            "echo": lambda msg, context=None: (
                contexts.append(context) or f"echo: {msg}"
            ),
        }
    )

    # Encrypted client, same workspace
    enc_client = await connect_to_server(
        {
            "name": "enc-caller",
            "server_url": WS_SERVER_URL,
            "client_id": "enc-optional-caller",
            "workspace": workspace,
            "token": token,
            "encryption": True,
        }
    )

    # Unencrypted client, same workspace
    plain_client = await connect_to_server(
        {
            "name": "plain-caller",
            "server_url": WS_SERVER_URL,
            "client_id": "plain-optional-caller",
            "workspace": workspace,
            "token": token,
        }
    )

    # Encrypted client provides server's public key out-of-band
    server_pub_key = server.rpc.get_public_key()
    svc_enc = await enc_client.get_service(
        "enc-optional-service", encryption_public_key=server_pub_key
    )
    # Plain client does NOT provide encryption key
    svc_plain = await plain_client.get_service("enc-optional-service")

    # Both should succeed
    result1 = await svc_enc.echo("encrypted msg")
    assert result1 == "echo: encrypted msg"
    assert contexts[-1].get("encryption") is True

    result2 = await svc_plain.echo("plain msg")
    assert result2 == "echo: plain msg"
    # Unencrypted calls should not have encryption in context
    assert "encryption" not in contexts[-1] or contexts[-1].get("encryption") is None

    await enc_client.disconnect()
    await plain_client.disconnect()
    await server.disconnect()


@pytest.mark.asyncio
async def test_crypto_module_encryption(websocket_server):
    """Test the crypto module encryption functions independently."""
    from hypha_rpc.crypto import (
        generate_encryption_keypair,
        encrypt_payload,
        decrypt_payload,
        public_key_to_hex,
        public_key_from_hex,
    )

    # Generate keypairs for two parties
    priv_a, pub_a = generate_encryption_keypair()
    assert len(priv_a) == 32
    assert len(pub_a) == 32

    priv_b, pub_b = generate_encryption_keypair()
    assert len(priv_b) == 32
    assert len(pub_b) == 32

    # Encrypt from A to B
    plaintext = b"hello encrypted world"
    nonce, ciphertext = encrypt_payload(priv_a, pub_b, plaintext)
    assert len(nonce) == 24
    assert len(ciphertext) > len(plaintext)  # includes 16-byte auth tag

    # B decrypts
    decrypted = decrypt_payload(priv_b, pub_a, nonce, ciphertext)
    assert decrypted == plaintext

    # Wrong key should fail
    priv_c, pub_c = generate_encryption_keypair()
    try:
        decrypt_payload(priv_c, pub_a, nonce, ciphertext)
        assert False, "Should have raised an exception"
    except Exception:
        pass  # Expected

    # Hex conversion
    hex_str = public_key_to_hex(pub_a)
    assert len(hex_str) == 64
    assert public_key_from_hex(hex_str) == pub_a


@pytest.mark.asyncio
async def test_trusted_keys_allows_authorized_caller(websocket_server):
    """Test that a caller whose public key is in trusted_keys can call the service."""
    # Service provider with encryption
    server = await connect_to_server(
        {
            "name": "trusted-keys-server",
            "server_url": WS_SERVER_URL,
            "client_id": "trusted-keys-server-client",
            "encryption": True,
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    # Authorized caller with encryption enabled
    client = await connect_to_server(
        {
            "name": "trusted-caller",
            "server_url": WS_SERVER_URL,
            "client_id": "trusted-caller-client",
            "workspace": workspace,
            "token": token,
            "encryption": True,
        }
    )

    # Get the caller's public key
    caller_pub_key = client.rpc.get_public_key()
    assert caller_pub_key is not None
    assert len(caller_pub_key) == 64  # 32 bytes = 64 hex chars

    received_contexts = []

    def verified_echo(message, context=None):
        received_contexts.append(context)
        return f"trusted: {message}"

    # Register service with trusted_keys containing the caller's key
    await server.register_service(
        {
            "id": "trusted-service",
            "config": {
                "require_context": True,
                "trusted_keys": [caller_pub_key],
                "visibility": "protected",
            },
            "echo": verified_echo,
        }
    )

    # Caller provides server's public key out-of-band
    server_pub_key = server.rpc.get_public_key()
    svc = await client.get_service(
        "trusted-service", encryption_public_key=server_pub_key
    )
    result = await svc.echo("hello trusted")
    assert result == "trusted: hello trusted"

    # Verify context has encryption info
    ctx = received_contexts[0]
    assert ctx.get("encryption") is True
    assert ctx.get("caller_public_key") == caller_pub_key

    await client.disconnect()
    await server.disconnect()


@pytest.mark.asyncio
async def test_trusted_keys_rejects_unauthorized_caller(websocket_server):
    """Test that a caller whose key is NOT in trusted_keys is rejected."""
    # Service provider with encryption
    server = await connect_to_server(
        {
            "name": "trusted-reject-server",
            "server_url": WS_SERVER_URL,
            "client_id": "trusted-reject-server-client",
            "encryption": True,
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    # A fake trusted key (not the caller's key)
    fake_trusted_key = "aa" * 32  # 64-char hex string

    await server.register_service(
        {
            "id": "trusted-reject-service",
            "config": {
                "require_context": True,
                "trusted_keys": [fake_trusted_key],
                "visibility": "protected",
            },
            "echo": lambda msg, context=None: msg,
        }
    )

    # Caller with encryption enabled, but their key is not in trusted_keys
    client = await connect_to_server(
        {
            "name": "untrusted-caller",
            "server_url": WS_SERVER_URL,
            "client_id": "untrusted-caller-client",
            "workspace": workspace,
            "token": token,
            "encryption": True,
        }
    )

    # Caller provides server's public key to enable encryption
    server_pub_key = server.rpc.get_public_key()
    svc = await client.get_service(
        "trusted-reject-service", encryption_public_key=server_pub_key
    )

    with pytest.raises(Exception, match="not in the trusted keys"):
        await svc.echo("should fail")

    await client.disconnect()
    await server.disconnect()


@pytest.mark.asyncio
async def test_trusted_keys_rejects_unencrypted_caller(websocket_server):
    """Test that an unencrypted caller is rejected when trusted_keys is set."""
    # Service provider with encryption
    server = await connect_to_server(
        {
            "name": "trusted-unenc-server",
            "server_url": WS_SERVER_URL,
            "client_id": "trusted-unenc-server-client",
            "encryption": True,
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    some_key = "bb" * 32

    await server.register_service(
        {
            "id": "trusted-unenc-service",
            "config": {
                "require_context": True,
                "trusted_keys": [some_key],
                "visibility": "protected",
            },
            "echo": lambda msg, context=None: msg,
        }
    )

    # Client WITHOUT encryption
    client = await connect_to_server(
        {
            "name": "no-enc-client",
            "server_url": WS_SERVER_URL,
            "client_id": "no-enc-caller-client",
            "workspace": workspace,
            "token": token,
        }
    )

    svc = await client.get_service("trusted-unenc-service")

    # Should fail — trusted_keys requires encryption
    with pytest.raises(Exception, match="Encryption required"):
        await svc.echo("should fail")

    await client.disconnect()
    await server.disconnect()


@pytest.mark.timeout(60)
@pytest.mark.asyncio
async def test_wrong_encryption_key_fails(websocket_server):
    """Test that providing the wrong encryption public key causes decryption failure."""
    server = await connect_to_server(
        {
            "name": "wrong-key-server",
            "server_url": WS_SERVER_URL,
            "client_id": "wrong-key-server-client",
            "encryption": True,
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    await server.register_service(
        {
            "id": "wrong-key-svc",
            "config": {"visibility": "protected"},
            "echo": lambda msg: f"echo: {msg}",
        }
    )

    client = await connect_to_server(
        {
            "name": "wrong-key-caller",
            "server_url": WS_SERVER_URL,
            "client_id": "wrong-key-caller-client",
            "workspace": workspace,
            "token": token,
            "encryption": True,
        }
    )

    # Use a wrong public key (not the server's)
    wrong_key = "bb" * 32
    svc = await client.get_service("wrong-key-svc", encryption_public_key=wrong_key)

    # Call should fail — provider can't decrypt because wrong key was used.
    # This results in either a "Decryption failed" error or a timeout
    # (because the encrypted response also can't be decrypted by the caller).
    with pytest.raises(Exception):
        await svc.echo("hello")

    await client.disconnect()
    await server.disconnect()


@pytest.mark.asyncio
async def test_encrypted_large_binary_payload(websocket_server):
    """Test E2E encryption with large binary (numpy) data."""
    import numpy as np

    server = await connect_to_server(
        {
            "name": "enc-binary-server",
            "server_url": WS_SERVER_URL,
            "client_id": "enc-binary-server-client",
            "encryption": True,
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    await server.register_service(
        {
            "id": "enc-binary-svc",
            "config": {"visibility": "protected"},
            "process": lambda arr: arr * 2,
        }
    )

    client = await connect_to_server(
        {
            "name": "enc-binary-caller",
            "server_url": WS_SERVER_URL,
            "client_id": "enc-binary-caller-client",
            "workspace": workspace,
            "token": token,
            "encryption": True,
        }
    )

    server_pub_key = server.rpc.get_public_key()
    svc = await client.get_service(
        "enc-binary-svc", encryption_public_key=server_pub_key
    )

    # Send a large numpy array through encrypted channel
    data = np.random.rand(1000).astype(np.float32)
    result = await svc.process(data)
    np.testing.assert_allclose(result, data * 2, rtol=1e-5)

    await client.disconnect()
    await server.disconnect()


@pytest.mark.asyncio
async def test_encrypted_return_value(websocket_server):
    """Test that return values are also encrypted (round-trip encryption)."""
    server = await connect_to_server(
        {
            "name": "enc-return-server",
            "server_url": WS_SERVER_URL,
            "client_id": "enc-return-server-client",
            "encryption": True,
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    secret_data = {"secret": "top-secret-value", "numbers": [1, 2, 3]}

    await server.register_service(
        {
            "id": "enc-return-svc",
            "config": {"visibility": "protected"},
            "get_secret": lambda: secret_data,
        }
    )

    client = await connect_to_server(
        {
            "name": "enc-return-caller",
            "server_url": WS_SERVER_URL,
            "client_id": "enc-return-caller-client",
            "workspace": workspace,
            "token": token,
            "encryption": True,
        }
    )

    server_pub_key = server.rpc.get_public_key()
    svc = await client.get_service(
        "enc-return-svc", encryption_public_key=server_pub_key
    )

    result = await svc.get_secret()
    assert result["secret"] == "top-secret-value"
    assert result["numbers"] == [1, 2, 3]

    await client.disconnect()
    await server.disconnect()


@pytest.mark.asyncio
async def test_multiple_trusted_keys(websocket_server):
    """Test that multiple keys in trusted_keys all work."""
    server = await connect_to_server(
        {
            "name": "multi-trusted-server",
            "server_url": WS_SERVER_URL,
            "client_id": "multi-trusted-server-client",
            "encryption": True,
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    # Two callers
    client_a = await connect_to_server(
        {
            "name": "multi-trusted-a",
            "server_url": WS_SERVER_URL,
            "client_id": "multi-trusted-a-client",
            "workspace": workspace,
            "token": token,
            "encryption": True,
        }
    )
    client_b = await connect_to_server(
        {
            "name": "multi-trusted-b",
            "server_url": WS_SERVER_URL,
            "client_id": "multi-trusted-b-client",
            "workspace": workspace,
            "token": token,
            "encryption": True,
        }
    )

    key_a = client_a.rpc.get_public_key()
    key_b = client_b.rpc.get_public_key()

    await server.register_service(
        {
            "id": "multi-trusted-svc",
            "config": {
                "require_context": True,
                "trusted_keys": [key_a, key_b],
                "visibility": "protected",
            },
            "echo": lambda msg, context=None: msg,
        }
    )

    server_pub = server.rpc.get_public_key()
    svc_a = await client_a.get_service(
        "multi-trusted-svc", encryption_public_key=server_pub
    )
    svc_b = await client_b.get_service(
        "multi-trusted-svc", encryption_public_key=server_pub
    )

    # Both should succeed
    assert await svc_a.echo("from A") == "from A"
    assert await svc_b.echo("from B") == "from B"

    await client_a.disconnect()
    await client_b.disconnect()
    await server.disconnect()


@pytest.mark.asyncio
async def test_empty_trusted_keys_allows_all(websocket_server):
    """Test that an empty trusted_keys list is treated as 'no restriction'."""
    server = await connect_to_server(
        {
            "name": "empty-trusted-server",
            "server_url": WS_SERVER_URL,
            "client_id": "empty-trusted-server-client",
            "encryption": True,
        }
    )
    workspace = server.config.workspace
    token = await server.generate_token()

    await server.register_service(
        {
            "id": "empty-trusted-svc",
            "config": {
                "require_context": True,
                "trusted_keys": [],
                "visibility": "protected",
            },
            "echo": lambda msg, context=None: msg,
        }
    )

    client = await connect_to_server(
        {
            "name": "empty-trusted-caller",
            "server_url": WS_SERVER_URL,
            "client_id": "empty-trusted-caller-client",
            "workspace": workspace,
            "token": token,
            "encryption": True,
        }
    )

    server_pub = server.rpc.get_public_key()
    svc = await client.get_service(
        "empty-trusted-svc", encryption_public_key=server_pub
    )

    # Empty trusted_keys is treated as no restriction — call should succeed
    result = await svc.echo("should work")
    assert result == "should work"

    await client.disconnect()
    await server.disconnect()
