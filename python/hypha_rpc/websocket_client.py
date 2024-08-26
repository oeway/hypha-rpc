"""Provide a websocket client."""

import asyncio
import inspect
import logging
import sys
import re

import shortuuid
import json
from functools import partial

from .rpc import RPC
from .utils.schema import schema_function
from .utils import ObjectProxy, parse_service_url

try:
    import js  # noqa: F401
    import pyodide  # noqa: F401
    from pyodide.ffi import create_proxy  # noqa: F401

    from .pyodide_websocket import PyodideWebsocketRPCConnection

    def custom_exception_handler(loop, context):
        """Handle exceptions."""
        pass

    # Patch the exception handler to avoid the default one
    asyncio.get_event_loop().set_exception_handler(custom_exception_handler)

    IS_PYODIDE = True
except ImportError:
    import websockets

    IS_PYODIDE = False

logging.basicConfig(stream=sys.stdout)
logger = logging.getLogger("websocket-client")
logger.setLevel(logging.WARNING)

MAX_RETRY = 1000000


class WebsocketRPCConnection:
    """Represent a websocket connection."""

    def __init__(
        self,
        server_url,
        client_id,
        workspace=None,
        token=None,
        reconnection_token=None,
        timeout=60,
        ssl=None,
    ):
        """Set up instance."""
        self._websocket = None
        self._handle_message = None
        self._handle_disconnected = None  # Disconnection handler
        self._handle_connected = None  # Connection open handler
        assert server_url and client_id
        self._server_url = server_url
        self._client_id = client_id
        self._workspace = workspace
        self._token = token
        self._reconnection_token = reconnection_token
        self._timeout = timeout
        self._closed = False
        self._legacy_auth = None
        self.connection_info = None
        self._enable_reconnect = False
        if ssl == False:
            import ssl as ssl_module

            ssl = ssl_module.create_default_context()
            ssl.check_hostname = False
            ssl.verify_mode = ssl_module.CERT_NONE
            logger.warning(
                "SSL is disabled, this is not recommended for production use."
            )
        self._ssl = ssl
        self.manager_id = None

    def on_message(self, handler):
        """Handle message."""
        self._handle_message = handler
        self._is_async = inspect.iscoroutinefunction(handler)

    def on_disconnected(self, handler):
        """Register a disconnection event handler."""
        self._handle_disconnected = handler

    def on_connected(self, handler):
        """Register a connection open event handler."""
        self._handle_connected = handler
        assert inspect.iscoroutinefunction(
            handler
        ), "reconnect handler must be a coroutine"

    async def _attempt_connection(self, server_url, attempt_fallback=True):
        """Attempt to establish a WebSocket connection."""
        try:
            self._legacy_auth = False
            # Only pass ssl if it's not None
            if self._ssl is None:
                websocket = await asyncio.wait_for(
                    websockets.connect(server_url), self._timeout
                )
            else:
                websocket = await asyncio.wait_for(
                    websockets.connect(server_url, ssl=self._ssl), self._timeout
                )
            return websocket
        except websockets.exceptions.InvalidStatusCode as e:
            # websocket code should be 1003, but it's not available in the library
            if e.status_code == 403 and attempt_fallback:
                logger.info(
                    "Received 403 error, attempting connection with query parameters."
                )
                self._legacy_auth = True
                return await self._attempt_connection_with_query_params(server_url)
            else:
                raise e

    async def _attempt_connection_with_query_params(self, server_url):
        """Attempt to establish a WebSocket connection including authentication details in the query string."""
        # Initialize an empty list to hold query parameters
        query_params_list = []

        # Add each parameter only if it has a non-empty value
        if self._client_id:
            query_params_list.append(f"client_id={self._client_id}")
        if self._workspace:
            query_params_list.append(f"workspace={self._workspace}")
        if self._token:
            query_params_list.append(f"token={self._token}")
        if self._reconnection_token:
            query_params_list.append(f"reconnection_token={self._reconnection_token}")

        # Join the parameters with '&' to form the final query string
        query_string = "&".join(query_params_list)

        # Construct the full URL by appending the query string if it's not empty
        full_url = f"{server_url}?{query_string}" if query_string else server_url

        # Attempt to establish the WebSocket connection with the constructed URL
        # Only pass ssl if it's not None
        if self._ssl is None:
            return await asyncio.wait_for(websockets.connect(full_url), self._timeout)
        else:
            return await asyncio.wait_for(
                websockets.connect(full_url, ssl=self._ssl), self._timeout
            )

    async def open(self):
        """Open the connection with fallback logic for backward compatibility."""
        logger.info(
            "Creating a new websocket connection to %s", self._server_url.split("?")[0]
        )
        try:
            self._websocket = await self._attempt_connection(self._server_url)
            # Send authentication info as the first message if connected without query params
            if self._legacy_auth:
                raise NotImplementedError("Legacy authentication is not supported")
            auth_info = json.dumps(
                {
                    "client_id": self._client_id,
                    "workspace": self._workspace,
                    "token": self._token,
                    "reconnection_token": self._reconnection_token,
                }
            )
            await self._websocket.send(auth_info)
            first_message = await self._websocket.recv()
            first_message = json.loads(first_message)
            if first_message.get("type") == "connection_info":
                self.connection_info = first_message
                if self._workspace:
                    assert (
                        self.connection_info.get("workspace") == self._workspace
                    ), f"Connected to the wrong workspace: {self.connection_info['workspace']}, expected: {self._workspace}"
                if "reconnection_token" in self.connection_info:
                    self._reconnection_token = self.connection_info[
                        "reconnection_token"
                    ]
                self.manager_id = self.connection_info.get("manager_id", None)
                logger.info(
                    f"Successfully connected to the server, workspace: {self.connection_info.get('workspace')}, manager_id: {self.manager_id}"
                )
                if "announcement" in self.connection_info:
                    print(self.connection_info["announcement"])
            elif first_message.get("type") == "error":
                error = first_message["message"]
                logger.error("Failed to connect: %s", error)
                raise ConnectionAbortedError(error)
            else:
                logger.error(
                    "ConnectionAbortedError: Unexpected message received from the server: %s",
                    first_message,
                )
                raise ConnectionAbortedError(
                    "Unexpected message received from the server"
                )

            self._listen_task = asyncio.ensure_future(self._listen())
            if self._handle_connected:
                await self._handle_connected(self.connection_info)
            return self.connection_info
        except Exception as exp:
            logger.error("Failed to connect to %s", self._server_url.split("?")[0])
            raise exp

    async def emit_message(self, data):
        """Emit a message."""
        if self._closed:
            raise Exception("Connection is closed")
        if not self._websocket or self._websocket.closed:
            await self.open()

        try:
            await self._websocket.send(data)
        except Exception as exp:
            logger.error(f"Failed to send message: {exp}")
            raise exp

    async def _listen(self):
        """Listen to the connection and handle disconnection."""
        self._enable_reconnect = True
        self._closed = False
        try:
            while not self._closed and not self._websocket.closed:
                data = await self._websocket.recv()
                try:
                    if self._is_async:
                        await self._handle_message(data)
                    else:
                        self._handle_message(data)
                except Exception as exp:
                    logger.exception(
                        "Failed to handle message: %s, error: %s", data, exp
                    )
        except websockets.exceptions.ConnectionClosedError as e:
            logger.info("Websocket connection closed: %s", e)
        finally:
            # Handle unexpected disconnection or disconnection caused by the server
            if not self._closed and self._websocket.closed:
                # normal closure, means no need to recover
                if self._websocket.close_code in [1000, 1001]:
                    logger.info(
                        "Websocket connection closed (code: %s): %s",
                        self._websocket.close_code,
                        self._websocket.close_reason,
                    )
                    if self._handle_disconnected:
                        self._handle_disconnected(str(e))
                    # make it as closed
                    self._closed = True
                elif self._enable_reconnect:
                    logger.warning(
                        "Websocket connection closed unexpectedly (code: %s): %s",
                        self._websocket.close_code,
                        self._websocket.close_reason,
                    )
                    retry = 0
                    while retry < MAX_RETRY:
                        try:
                            logger.warning(
                                "Reconnecting to %s (attempt #%s)",
                                self._server_url.split("?")[0],
                                retry,
                            )
                            await self.open()
                            logger.warning(
                                "Successfully reconnected to %s",
                                self._server_url.split("?")[0],
                            )
                            break
                        except NotImplementedError as e:
                            logger.error(
                                f"{e}"
                                "It appears that you are trying to connect "
                                "to a hypha server that is older than 0.20.0, "
                                "please upgrade the hypha server or "
                                "use imjoy-rpc(https://pypi.org/project/imjoy-rpc/) "
                                "with `from imjoy_rpc.hypha import connect_to_sever` instead"
                            )
                            break
                        except ConnectionAbortedError as e:
                            logger.warning("Server refuse to reconnect: %s", e)
                            break
                        except Exception as e:
                            await asyncio.sleep(1)
                            if self._websocket and self._websocket.open:
                                break
                        retry += 1
            else:
                if self._handle_disconnected:
                    self._handle_disconnected(str(e))

    async def disconnect(self, reason=None):
        """Disconnect."""
        self._closed = True
        if self._websocket and not self._websocket.closed:
            await self._websocket.close(code=1000)
        if self._listen_task:
            self._listen_task.cancel()
        logger.info("Websocket connection disconnected (%s)", reason)


def normalize_server_url(server_url):
    """Normalize the server url."""
    if not server_url:
        raise ValueError("server_url is required")

    if server_url.startswith("http://"):
        server_url = server_url.replace("http://", "ws://").rstrip("/") + "/ws"
    elif server_url.startswith("https://"):
        server_url = server_url.replace("https://", "wss://").rstrip("/") + "/ws"

    return server_url


async def login(config):
    """Login to the hypha server."""
    service_id = config.get("login_service_id", "public/hypha-login")
    timeout = config.get("login_timeout", 60)
    callback = config.get("login_callback")
    profile = config.get("profile", False)
    ssl = config.get("ssl")

    server = await connect_to_server(
        {
            "name": "initial login client",
            "server_url": config.get("server_url"),
            "method_timeout": timeout,
            "ssl": ssl,
        }
    )
    try:
        svc = await server.get_service(service_id)
        assert svc, f"Failed to get the login service: {service_id}"
        context = await svc.start()
        if callback:
            await callback(context)
        else:
            print(f"Please open your browser and login at {context['login_url']}")

        return await svc.check(context["key"], timeout, profile=profile)
    except Exception as error:
        raise error
    finally:
        await server.disconnect()


class ServerContextManager:
    """Server context manager."""

    def __init__(self, config, service_id=None):
        self.config = config
        self._service_id = service_id
        self.wm = None

    async def __aenter__(self):
        self.wm = await _connect_to_server(self.config)
        if self._service_id:
            return await self.wm.get_service(self._service_id)
        return self.wm

    async def __aexit__(self, exc_type, exc, tb):
        await self.wm.disconnect()

    def __await__(self):
        return self.__aenter__().__await__()


def connect_to_server(config):
    """Connect to the server."""
    return ServerContextManager(config)


def get_remote_service(service_uri, config=None):
    """Get a remote service."""
    server_url, workspace, client_id, service_id, app_id = parse_service_url(
        service_uri
    )
    full_service_id = f"{workspace}/{client_id}:{service_id}@{app_id}"
    config = config or {}
    if "server_url" in config:
        assert (
            config["server_url"] == server_url
        ), "server_url in config does not match the server_url in the url"
    config["server_url"] = server_url
    return ServerContextManager(config, service_id=full_service_id)


async def webrtc_get_service(wm, rtc_service_id, query, config=None):
    config = config or {}
    webrtc = config.get("webrtc")
    webrtc_config = config.get("webrtc_config")
    if "webrtc" in config:
        del config["webrtc"]
    if "webrtc_config" in config:
        del config["webrtc_config"]
    assert webrtc in [
        None,
        True,
        False,
        "auto",
    ], "webrtc must be true, false or 'auto'"
    # pass other kwargs to the original get_service function
    svc = await wm.get_service(query, config)

    from .webrtc_client import AIORTC_AVAILABLE, get_rtc_service

    if ":" in svc.id and "/" in svc.id and AIORTC_AVAILABLE:
        try:
            # Assuming that the client registered
            # a webrtc service with the client_id + "-rtc"
            peer = await get_rtc_service(
                wm,
                rtc_service_id,
                webrtc_config,
            )
            rtc_svc = await peer.get_service(svc.id.split(":")[1], config)
            rtc_svc._webrtc = True
            rtc_svc._peer = peer
            rtc_svc._service = svc
            return rtc_svc
        except Exception:
            logger.warning("Failed to get webrtc service, using websocket connection")
    if webrtc is True:
        if not AIORTC_AVAILABLE:
            raise Exception("aiortc is not available, please install it first.")
        raise Exception("Failed to get the service via webrtc")
    return svc


async def _connect_to_server(config):
    """Connect to RPC via a hypha server."""
    client_id = config.get("client_id")
    if client_id is None:
        client_id = shortuuid.uuid()

    server_url = normalize_server_url(config["server_url"])

    if IS_PYODIDE:
        Connection = PyodideWebsocketRPCConnection
    else:
        Connection = WebsocketRPCConnection

    connection = Connection(
        server_url,
        client_id,
        workspace=config.get("workspace"),
        token=config.get("token"),
        reconnection_token=config.get("reconnection_token"),
        timeout=config.get("method_timeout", 30),
        ssl=config.get("ssl"),
    )
    connection_info = await connection.open()
    assert connection_info, (
        "Failed to connect to the server, no connection info obtained."
        " This issue is most likely due to an outdated Hypha server version. "
        "Please use `imjoy-rpc` for compatibility, or upgrade the Hypha server to the latest version."
    )
    if config.get("workspace") and connection_info["workspace"] != config["workspace"]:
        raise Exception(
            f"Connected to the wrong workspace: {connection_info['workspace']}, expected: {config['workspace']}"
        )
    workspace = connection_info["workspace"]
    rpc = RPC(
        connection,
        client_id=client_id,
        workspace=workspace,
        default_context={"connection_type": "websocket"},
        name=config.get("name"),
        method_timeout=config.get("method_timeout"),
        loop=config.get("loop"),
        app_id=config.get("app_id"),
        server_base_url=connection_info.get("public_base_url"),
    )
    wm = await rpc.get_manager_service(
        {"timeout": config.get("method_timeout", 30), "case_conversion": "snake"}
    )
    wm.rpc = rpc

    def export(api: dict):
        """Export the api."""
        # Convert class instance to a dict
        if not isinstance(api, dict) and inspect.isclass(type(api)):
            api = {a: getattr(api, a) for a in dir(api)}
        api["id"] = "default"
        api["description"] = api.get("description") or config.get("description")
        return asyncio.ensure_future(rpc.register_service(api, {"overwrite": True}))

    async def get_app(client_id: str):
        """Get the app."""
        assert ":" not in client_id, "clientId should not contain ':'"
        if "/" not in client_id:
            client_id = connection_info["workspace"] + "/" + client_id
        assert (
            len(client_id.split("/")) == 2
        ), "clientId should be in the format of 'workspace/client_id'"
        return await wm.get_service(f"{client_id}:default")

    async def list_apps(workspace: str = None):
        """List the apps."""
        workspace = workspace or connection_info["workspace"]
        assert ":" not in workspace, "workspace should not contain ':'"
        assert "/" not in workspace, "workspace should not contain '/'"
        query = {"workspace": workspace, "service_id": "default"}
        return await wm.list_services(query)

    if connection_info:
        wm.config.update(connection_info)

    wm.export = schema_function(
        export,
        name="export",
        description="Export the api.",
        parameters={
            "properties": {
                "api": {"description": "The api to export", "type": "object"}
            },
            "required": ["api"],
            "type": "object",
        },
    )
    wm.get_app = schema_function(
        get_app,
        name="get_app",
        description="Get the app.",
        parameters={
            "properties": {
                "clientId": {
                    "default": "*",
                    "description": "The clientId",
                    "type": "string",
                }
            },
            "type": "object",
        },
    )
    wm.list_apps = schema_function(
        list_apps,
        name="list_apps",
        description="List the apps.",
        parameters={
            "properties": {
                "workspace": {
                    "default": workspace,
                    "description": "The workspace",
                    "type": "string",
                }
            },
            "type": "object",
        },
    )
    wm.disconnect = schema_function(
        rpc.disconnect,
        name="disconnect",
        description="Disconnect.",
        parameters={"properties": {}, "type": "object"},
    )
    wm.register_codec = schema_function(
        rpc.register_codec,
        name="register_codec",
        description="Register a codec",
        parameters={
            "type": "object",
            "properties": {
                "codec": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "type": {},
                        "encoder": {"type": "function"},
                        "decoder": {"type": "function"},
                    },
                    "description": "codec",
                }
            },
            "required": ["codec"],
        },
    )
    wm.emit = schema_function(
        rpc.emit,
        name="emit",
        description="Emit a message.",
        parameters={
            "properties": {
                "data": {"description": "The data to emit", "type": "object"}
            },
            "required": ["data"],
            "type": "object",
        },
    )
    wm.on = schema_function(
        rpc.on,
        name="on",
        description="Register a message handler.",
        parameters={
            "properties": {
                "event": {"description": "The event to listen to", "type": "string"},
                "handler": {"description": "The handler function", "type": "function"},
            },
            "required": ["event", "handler"],
            "type": "object",
        },
    )

    wm.off = schema_function(
        rpc.off,
        name="off",
        description="Remove a message handler.",
        parameters={
            "properties": {
                "event": {"description": "The event to remove", "type": "string"},
                "handler": {"description": "The handler function", "type": "function"},
            },
            "required": ["event", "handler"],
            "type": "object",
        },
    )

    wm.once = schema_function(
        rpc.once,
        name="once",
        description="Register a one-time message handler.",
        parameters={
            "properties": {
                "event": {"description": "The event to listen to", "type": "string"},
                "handler": {"description": "The handler function", "type": "function"},
            },
            "required": ["event", "handler"],
            "type": "object",
        },
    )

    wm.get_service_schema = schema_function(
        rpc.get_service_schema,
        name="get_service_schema",
        description="Get the service schema.",
        parameters={
            "properties": {
                "service": {
                    "description": "The service to extract schema",
                    "type": "object",
                },
            },
            "required": ["service"],
            "type": "object",
        },
    )

    wm.register_service = schema_function(
        rpc.register_service,
        name="register_service",
        description="Register a service.",
        parameters={
            "properties": {
                "service": {"description": "The service to register", "type": "object"},
                "force": {
                    "default": False,
                    "description": "Force to register the service",
                    "type": "boolean",
                },
            },
            "required": ["service"],
            "type": "object",
        },
    )
    wm.unregister_service = schema_function(
        rpc.unregister_service,
        name="unregister_service",
        description="Unregister a service.",
        parameters={
            "properties": {
                "service": {
                    "description": "The service id to unregister",
                    "type": "string",
                },
                "notify": {
                    "default": True,
                    "description": "Notify the workspace manager",
                },
            },
            "required": ["service"],
            "type": "object",
        },
    )
    if connection.manager_id:

        async def handle_disconnect(message):
            if message["from"] == "*/" + connection.manager_id:
                logger.info(
                    "Disconnecting from server, reason: %s", message.get("reason")
                )
                await rpc.disconnect()

        rpc.on("force-exit", handle_disconnect)

    if config.get("webrtc", False):
        from .webrtc_client import (
            AIORTC_AVAILABLE,
            register_rtc_service,
            get_rtc_service,
        )

        if not AIORTC_AVAILABLE:
            raise Exception("aiortc is not available, please install it first.")
        await register_rtc_service(wm, f"{client_id}-rtc", config.get("webrtc_config"))
        # Make a copy of wm so, webrtc can use the original wm.get_service
        _wm = ObjectProxy.fromDict(dict(wm))
        description = _wm.get_service.__schema__.get("description")
        # TODO: add webrtc options to the get_service schema
        parameters = _wm.get_service.__schema__.get("parameters")
        wm.get_service = schema_function(
            partial(webrtc_get_service, _wm, f"{workspace}/{client_id}-rtc"),
            name="get_service",
            description=description,
            parameters=parameters,
        )

        wm.get_rtc_service = schema_function(
            partial(get_rtc_service, wm, client_id + "-rtc"),
            name="get_rtc_service",
            description="Get the webrtc connection, returns a peer connection",
            parameters={
                "properties": {
                    "config": {
                        "description": "The config for the webrtc service",
                        "type": "object",
                    },
                },
                "required": ["config"],
                "type": "object",
            },
        )
    else:
        _get_service = wm.get_service

        async def get_service(query, config=None):
            config = config or {}
            return await _get_service(query, config)

        get_service.__schema__ = wm.get_service.__schema__
        wm.get_service = get_service

    async def serve():
        await asyncio.Event().wait()

    wm.serve = schema_function(
        serve, name="serve", description="Run the event loop forever", parameters={}
    )

    async def register_probes(probes):
        probes["id"] = "probes"
        probes["name"] = "Probes"
        probes["config"] = {"visibility": "public"}
        probes["type"] = "probes"
        probes["description"] = (
            f"Probes Service, visit {server_url}/{workspace}services/probes for the available probes."
        )
        return await wm.register_service(probes, {"overwrite": True})

    wm.register_probes = schema_function(
        register_probes,
        name="register_probes",
        description="Register probes service",
        parameters={
            "properties": {
                "probes": {
                    "description": "The probes to register, e.g. {'liveness': {'type': 'function', 'description': 'Check the liveness of the service'}}",
                    "type": "object",
                }
            },
            "required": ["probes"],
            "type": "object",
        },
    )
    return wm


def setup_local_client(enable_execution=False, on_ready=None):
    """Set up a local client."""
    fut = asyncio.Future()

    async def message_handler(event):
        data = event.data.to_py()
        type = data.get("type")
        server_url = data.get("server_url")
        workspace = data.get("workspace")
        client_id = data.get("client_id")
        token = data.get("token")
        method_timeout = data.get("method_timeout")
        name = data.get("name")
        config = data.get("config")
        ssl = data.get("ssl")

        if type == "initializeHyphaClient":
            if not server_url or not workspace or not client_id:
                print("server_url, workspace, and client_id are required.")
                return

            if not server_url.startswith("https://local-hypha-server:"):
                print("server_url should start with https://local-hypha-server:")
                return

            server = await connect_to_server(
                {
                    "server_url": server_url,
                    "workspace": workspace,
                    "client_id": client_id,
                    "token": token,
                    "method_timeout": method_timeout,
                    "name": name,
                    "ssl": ssl,
                }
            )

            js.globalThis.api = server
            try:
                if enable_execution:
                    raise NotImplementedError("execution is not implemented")
                if on_ready:
                    await on_ready(server, config)
            except Exception as e:
                fut.set_exception(e)
                return
            fut.set_result(server)

    js.globalThis.addEventListener("message", create_proxy(message_handler), False)
    return fut
