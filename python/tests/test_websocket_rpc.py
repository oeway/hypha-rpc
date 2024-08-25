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

from . import WS_SERVER_URL


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
    with pytest.raises(
        Exception, match=r".*does not have permission to access workspace.*"
    ):
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
async def test_case_conversion(websocket_server):
    """Test case conversion."""
    ws = await connect_to_server({"name": "my plugin", "server_url": WS_SERVER_URL})
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

    svc = await ws.get_service(info.id, {"case_conversion": "camel"})
    assert await svc.helloWorld("world") == "Hello world"

    svc = await ws.get_service(info.id, {"case_conversion": "snake"})
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
