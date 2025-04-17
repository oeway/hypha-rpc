"""Test the hypha server."""

import asyncio

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
        {"name": "my app", "server_url": WS_SERVER_URL, "client_id": "my-app"}
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
