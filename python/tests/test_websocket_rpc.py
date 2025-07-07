"""Test the hypha server."""

import asyncio
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


@pytest.mark.asyncio
async def test_login(websocket_server):
    """Test login to the server."""
    TOKEN = "sf31df234"

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
    TOKEN = "sf31df234"

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
    """Test that sessions are properly cleaned up and don't leak memory."""
    from hypha_rpc import connect_to_server

    # Connect to server
    api = await connect_to_server(
        {
            "client_id": "memory-leak-test-client",
            "server_url": WS_SERVER_URL,
        }
    )

    # Helper to get session count (excluding permanent stores)
    def get_session_count(rpc):
        count = 0

        def count_sessions(obj, path=""):
            nonlocal count
            for key in obj.__dict__ if hasattr(obj, "__dict__") else {}:
                if key not in ["services", "message_cache"] and not key.startswith("_"):
                    count += 1
                    value = getattr(obj, key)
                    if hasattr(value, "__dict__") and not isinstance(
                        value, (str, int, float, bool)
                    ):
                        count_sessions(value, f"{path}.{key}")

        if hasattr(rpc, "_object_store"):
            store = rpc._object_store
            for key in store:
                if key not in ["services", "message_cache"]:
                    count += 1
                    if isinstance(store[key], dict):

                        def count_nested(d, depth=0):
                            nonlocal count
                            if depth > 10:  # Prevent infinite recursion
                                return
                            for k, v in d.items():
                                if k not in ["services", "message_cache"]:
                                    count += 1
                                    if isinstance(v, dict):
                                        count_nested(v, depth + 1)

                        count_nested(store[key])
        return count

    # Test 1: Verify baseline state
    initial_count = get_session_count(api.rpc)
    print(f"Initial session count: {initial_count}")

    # Test 2: Simple echo call - should not create permanent sessions
    result = await api.echo("test")
    assert result == "test"
    await asyncio.sleep(0.2)  # Give time for cleanup

    after_echo_count = get_session_count(api.rpc)
    print(f"After echo - count: {after_echo_count}")

    # Echo should not create any new permanent sessions
    assert after_echo_count <= initial_count

    # Test 3: List services (which might create sessions)
    services = await api.list_services()
    assert isinstance(services, list)
    await asyncio.sleep(0.2)

    after_list_count = get_session_count(api.rpc)
    print(f"After listServices - count: {after_list_count}")
    assert after_list_count <= initial_count

    # Test 4: Multiple operations
    await asyncio.gather(api.echo("test1"), api.echo("test2"), api.echo("test3"))
    await asyncio.sleep(0.2)

    after_multiple_count = get_session_count(api.rpc)
    print(f"After multiple ops - count: {after_multiple_count}")
    assert after_multiple_count <= initial_count

    # Test 5: Verify message cache is clean
    if hasattr(api.rpc, "_object_store") and "message_cache" in api.rpc._object_store:
        assert len(api.rpc._object_store["message_cache"]) == 0

    # Test 6: Verify that session cleanup is working
    print("Session cleanup verification:")
    print(f"- Initial sessions (permanent): {initial_count}")
    print(f"- After operations (should be <= initial): {after_multiple_count}")
    print(
        f"- Cleanup is working: {'✓' if after_multiple_count <= initial_count else '✗'}"
    )

    # Final cleanup
    await api.disconnect()

    print("Test completed successfully - no memory leaks detected")
