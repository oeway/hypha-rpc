"""This module provides an interface to interact with the Hypha server synchronously."""

import asyncio
import inspect
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import wraps

from hypha_rpc.utils import ObjectProxy, parse_service_url
from hypha_rpc.webrtc_client import get_rtc_service as get_rtc_service_async
from hypha_rpc.webrtc_client import (
    register_rtc_service as register_rtc_service_async,
)
from hypha_rpc.websocket_client import (
    connect_to_server as connect_to_server_async,
    get_remote_service as get_remote_service_async,
)
from hypha_rpc.websocket_client import normalize_server_url


def get_async_methods(instance):
    """Get all the asynchronous methods from the given instance."""
    methods = []
    for attr_name in dir(instance):
        if not attr_name.startswith("_"):
            attr_value = getattr(instance, attr_name)
            if inspect.iscoroutinefunction(attr_value):
                methods.append(attr_name)
    return methods


def convert_sync_to_async(sync_func, loop, executor):
    """Convert a synchronous function to an asynchronous function."""
    if asyncio.iscoroutinefunction(sync_func):
        return sync_func

    @wraps(sync_func)
    async def wrapped_async(*args, **kwargs):
        result_future = loop.create_future()
        args = _encode_callables(args, convert_async_to_sync, loop, executor)
        kwargs = _encode_callables(kwargs, convert_async_to_sync, loop, executor)

        def run_and_set_result():
            try:
                result = sync_func(*args, **kwargs)
                loop.call_soon_threadsafe(result_future.set_result, result)
            except Exception as e:
                loop.call_soon_threadsafe(result_future.set_exception, e)

        executor.submit(run_and_set_result)
        result = await result_future
        obj = _encode_callables(result, convert_async_to_sync, loop, executor)
        return obj

    wrapped_async._sync = sync_func
    return wrapped_async


def convert_async_to_sync(async_func, loop, executor):
    """Convert an asynchronous function to a synchronous function."""

    @wraps(async_func)
    def wrapped_sync(*args, **kwargs):
        args = _encode_callables(args, convert_sync_to_async, loop, executor)
        kwargs = _encode_callables(kwargs, convert_sync_to_async, loop, executor)

        async def async_wrapper():
            obj = await async_func(*args, **kwargs)
            return _encode_callables(obj, convert_async_to_sync, loop, executor)

        result = asyncio.run_coroutine_threadsafe(async_wrapper(), loop).result()
        return result

    wrapped_sync._async = async_func
    return wrapped_sync


def _encode_callables(obj, wrap, loop, executor):
    """Encode callables in the given object to sync or async."""
    if isinstance(obj, dict):
        return ObjectProxy.fromDict(
            {k: _encode_callables(v, wrap, loop, executor) for k, v in obj.items()}
        )
    elif isinstance(obj, (list, tuple)):
        return [_encode_callables(item, wrap, loop, executor) for item in obj]
    elif callable(obj):
        return wrap(obj, loop, executor)
    else:
        return obj


class SyncHyphaServer(ObjectProxy):
    """A class to interact with the Hypha server synchronously."""

    def __init__(self, config, service_id=None):
        """Initialize the SyncHyphaServer."""
        self._loop = None
        self._thread = None
        self._server = None
        self._service_id = service_id
        sync_max_workers = config.get("sync_max_workers", 2)
        # Note: we need at least 2 workers to avoid deadlock
        self._executor = ThreadPoolExecutor(max_workers=sync_max_workers)

        if not self._loop:
            self._thread = threading.Thread(target=self._start_loop, daemon=True)
            self._thread.start()

        while not self._loop or not self._loop.is_running():
            pass

        future = asyncio.run_coroutine_threadsafe(self._connect(config), self._loop)
        future.result()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self._server:
            self._server.disconnect()

    async def _connect(self, config):
        config["loop"] = self._loop
        # get the a config to see if we need to convert
        mix_async = config.get("mix_async", False)
        self._server = await connect_to_server_async(config)
        if self._service_id:
            service = await self._server.get_service(self._service_id)
        else:
            service = self._server

        for k, v in service.items():
            if callable(v):
                if hasattr(v, "__rpc_object__"):
                    rpc_obj = v.__rpc_object__
                    if mix_async and "_rasync" in rpc_obj and rpc_obj["_rasync"]:
                        setattr(self, k, v)
                    else:
                        setattr(
                            self,
                            k,
                            _encode_callables(
                                v, convert_async_to_sync, self._loop, self._executor
                            ),
                        )
                else:
                    if k in ["on", "off", "once", "register_codec"]:
                        setattr(self, k, v)
                    else:
                        setattr(
                            self,
                            k,
                            _encode_callables(
                                v, convert_async_to_sync, self._loop, self._executor
                            ),
                        )

            else:
                setattr(self, k, v)

    def _start_loop(self):
        asyncio.set_event_loop(asyncio.new_event_loop())
        self._loop = asyncio.get_event_loop()
        self._loop.run_forever()


def connect_to_server(config):
    """Connect to the Hypha server synchronously."""
    server = SyncHyphaServer(config)
    return server


def get_remote_service(service_uri, config=None):
    """Get a remote service from the Hypha server."""
    server_url, workspace, client_id, service_id, app_id = parse_service_url(service_uri)
    full_service_id = f"{workspace}/{client_id}:{service_id}@{app_id}"
    config = config or {}
    if "server_url" in config:
        assert (
            config["server_url"] == server_url
        ), "server_url in config does not match the server_url in the url"
    config["server_url"] = server_url
    service = SyncHyphaServer(config, service_id=full_service_id)
    return service


def login(config):
    """Login to the Hypha server."""
    server_url = normalize_server_url(config.get("server_url"))
    service_id = config.get("login_service_id", "public/hypha-login")
    timeout = config.get("login_timeout", 60)
    callback = config.get("login_callback")

    server = connect_to_server(
        {"name": "initial login client", "server_url": server_url}
    )
    try:
        svc = server.get_service(service_id)
        assert svc, f"Service {service_id} not found on the server."
        context = svc.start()
        if callback:
            callback(context)
        else:
            print(f"Please open your browser and login at {context['login_url']}")

        return svc.check(context["key"], timeout)
    except Exception as error:
        raise error
    finally:
        server.disconnect()


def register_rtc_service(server, service_id, config=None):
    """Register an RTC service on the Hypha server."""
    assert isinstance(
        server, SyncHyphaServer
    ), "server must be an instance of SyncHyphaServer, please use hypha.sync.connect_to_server to create a server instance."
    future = asyncio.run_coroutine_threadsafe(
        register_rtc_service_async(
            server._server,
            service_id,
            _encode_callables(
                config, convert_sync_to_async, server._loop, server._executor
            ),
        ),
        server._loop,
    )
    future.result()


def get_rtc_service(server, service_id, config=None):
    """Get an RTC service from the Hypha server."""
    assert isinstance(
        server, SyncHyphaServer
    ), "server must be an instance of SyncHyphaServer, please use hypha.sync.connect_to_server to create a server instance."

    future = asyncio.run_coroutine_threadsafe(
        get_rtc_service_async(
            server._server,
            service_id,
            _encode_callables(
                config, convert_sync_to_async, server._loop, server._executor
            ),
        ),
        server._loop,
    )
    pc = future.result()
    for func in get_async_methods(pc):
        setattr(
            pc,
            func,
            convert_async_to_sync(getattr(pc, func), server._loop, server._executor),
        )
    return pc


if __name__ == "__main__":
    server_url = "https://hypha.aicell.io"
    server = connect_to_server({"server_url": server_url})

    services = server.list_services("public")
    print("Public services: #", len(services))

    def hello(name):
        """Say hello."""
        print("Hello " + name)
        print("Current thread id: ", threading.get_ident(), threading.current_thread())
        time.sleep(2)
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

    workspace = server.config.workspace
    token = server.generate_token()

    server2 = connect_to_server(
        {"server_url": server_url, "workspace": workspace, "token": token}
    )

    hello_svc = server2.get_service("hello-world")

    print("Calling hello world service...")
    print(hello_svc.hello("World"))

    server.disconnect()
    server2.disconnect()
