"""Hypha RPC client - unified connection interface.

This module provides the main entry point for connecting to Hypha servers.
It supports multiple transport types:
- "websocket" (default): Traditional WebSocket connection
- "http": HTTP streaming connection (more resilient to network issues)
"""

import os
from .utils import parse_service_url


class ServerContextManager:
    """Server context manager.

    Supports multiple transport types:
    - "websocket" (default): Traditional WebSocket connection
    - "http": HTTP streaming connection (more resilient to network issues)
    """

    def __init__(self, config=None, service_id=None, **kwargs):
        self.config = config or {}
        self.config.update(kwargs)

        if not self.config:
            # try to load from env
            if not os.environ.get("HYPHA_SERVER_URL"):
                try:
                    from dotenv import load_dotenv, find_dotenv
                    load_dotenv(dotenv_path=find_dotenv(usecwd=True))
                    # use info from .env file
                    print("✅ Loaded connection configuration from .env file.")
                except ImportError:
                    pass
            self.config = {
                "server_url": os.getenv("HYPHA_SERVER_URL"),
                "token": os.getenv("HYPHA_TOKEN"),
                "client_id": os.getenv("HYPHA_CLIENT_ID"),
                "workspace": os.getenv("HYPHA_WORKSPACE"),
            }
            if not self.config["server_url"]:
                raise ValueError(
                    "Please set the HYPHA_SERVER_URL, HYPHA_TOKEN, "
                    "HYPHA_CLIENT_ID, and HYPHA_WORKSPACE environment variables"
                )
        self._service_id = service_id
        self._transport = self.config.pop("transport", "websocket")
        self.wm = None

    async def __aenter__(self):
        if self._transport == "http":
            from .http_client import _connect_to_server_http
            self.wm = await _connect_to_server_http(self.config)
        else:
            from .websocket_client import _connect_to_server
            self.wm = await _connect_to_server(self.config)
        if self._service_id:
            return await self.wm.get_service(
                self._service_id,
                {"case_conversion": self.config.get("case_conversion")},
            )
        return self.wm

    async def __aexit__(self, exc_type, exc, tb):
        await self.wm.disconnect()

    def __await__(self):
        return self.__aenter__().__await__()


def connect_to_server(config=None, **kwargs):
    """Connect to a Hypha server.

    Args:
        config: Configuration dict with connection options
        **kwargs: Additional configuration options

    Configuration options:
        server_url: The server URL (required)
        workspace: Target workspace (optional)
        token: Authentication token (optional)
        client_id: Unique client identifier (optional, auto-generated if not provided)
        transport: Transport type - "websocket" (default) or "http"
        method_timeout: Timeout for RPC method calls
        ssl: SSL configuration (True/False/SSLContext)

    Returns:
        ServerContextManager that can be used as async context manager

    Example:
        async with connect_to_server({"server_url": "https://hypha.aicell.io"}) as server:
            await server.register_service({"id": "my-service", ...})
    """
    return ServerContextManager(config=config, **kwargs)


def get_remote_service(service_uri, config=None, **kwargs):
    """Get a remote service by URI.

    Args:
        service_uri: Service URI in format "server_url/workspace/client_id:service_id"
        config: Additional configuration options
        **kwargs: Additional configuration options

    Returns:
        ServerContextManager that resolves to the service when awaited

    Example:
        async with get_remote_service("https://hypha.aicell.io/public/client:service") as svc:
            result = await svc.some_method()
    """
    server_url, workspace, client_id, service_id, app_id = parse_service_url(
        service_uri
    )
    full_service_id = f"{workspace}/{client_id}:{service_id}@{app_id}"
    config = config or {}
    config.update(kwargs)
    if "server_url" in config:
        assert (
            config["server_url"] == server_url
        ), "server_url in config does not match the server_url in the url"
    config["server_url"] = server_url
    return ServerContextManager(config, service_id=full_service_id)
