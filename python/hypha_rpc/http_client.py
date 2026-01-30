"""HTTP Streaming RPC Client.

This module provides HTTP-based RPC transport as an alternative to WebSocket.
It uses:
- HTTP GET with streaming (NDJSON) for server-to-client messages
- HTTP POST for client-to-server messages

Benefits:
- More resilient to network issues (each POST is independent)
- Automatic reconnection for the stream
- Works through more proxies and firewalls
- Supports callbacks through the streaming channel
"""

import asyncio
import inspect
import io
import json
import logging
import os
import sys
from typing import Callable, Optional

import httpx
import msgpack
import shortuuid

from .rpc import RPC
from .utils import ObjectProxy, parse_service_url
from .utils.schema import schema_function

LOGLEVEL = os.environ.get("HYPHA_LOGLEVEL", "WARNING").upper()
logging.basicConfig(level=LOGLEVEL, stream=sys.stdout)
logger = logging.getLogger("http-client")
logger.setLevel(LOGLEVEL)

MAX_RETRY = 1000000


class HTTPStreamingRPCConnection:
    """HTTP Streaming RPC Connection.

    Uses HTTP GET with streaming for receiving messages and HTTP POST for sending messages.
    Supports two formats:
    - NDJSON (default): JSON lines for text-based messages
    - msgpack: Binary format with length-prefixed frames for binary data support
    """

    def __init__(
        self,
        server_url: str,
        client_id: str,
        workspace: Optional[str] = None,
        token: Optional[str] = None,
        reconnection_token: Optional[str] = None,
        timeout: float = 60,
        ssl=None,
        token_refresh_interval: float = 2 * 60 * 60,
        format: str = "json",  # "json" or "msgpack"
    ):
        """Initialize HTTP streaming connection.

        Args:
            server_url: The server URL (http:// or https://)
            client_id: Unique client identifier
            workspace: Target workspace
            token: Authentication token
            reconnection_token: Token for reconnection
            timeout: Request timeout in seconds
            ssl: SSL configuration (True/False/SSLContext)
            token_refresh_interval: Interval for token refresh
            format: Stream format - "json" (NDJSON) or "msgpack" (binary with length prefix)
        """
        self._server_url = server_url.rstrip("/")
        self._client_id = client_id
        self._workspace = workspace
        self._token = token
        self._reconnection_token = reconnection_token
        self._timeout = timeout
        self._ssl = ssl
        self._token_refresh_interval = token_refresh_interval
        self._format = format

        self._handle_message: Optional[Callable] = None
        self._handle_disconnected: Optional[Callable] = None
        self._handle_connected: Optional[Callable] = None
        self._is_async = False

        self._closed = False
        self._enable_reconnect = False
        self._stream_task: Optional[asyncio.Task] = None
        self._refresh_token_task: Optional[asyncio.Task] = None
        self._http_client: Optional[httpx.AsyncClient] = None

        self.connection_info = None
        self.manager_id = None

    def on_message(self, handler: Callable):
        """Register message handler."""
        self._handle_message = handler
        self._is_async = inspect.iscoroutinefunction(handler)

    def on_disconnected(self, handler: Callable):
        """Register disconnection handler."""
        self._handle_disconnected = handler

    def on_connected(self, handler: Callable):
        """Register connection handler."""
        self._handle_connected = handler
        assert inspect.iscoroutinefunction(handler), "Handler must be async"

    async def _send_refresh_token(self, token_refresh_interval: float):
        """Send refresh token request at regular intervals.

        Similar to WebSocket, this periodically requests a new reconnection token
        to keep the session alive and allow reconnection with a fresh token.
        """
        try:
            await asyncio.sleep(2)  # Initial delay
            while not self._closed and self._http_client:
                try:
                    # Send refresh token request via POST
                    workspace = self._workspace or "public"
                    url = f"{self._server_url}/{workspace}/rpc"
                    params = {"client_id": self._client_id}

                    refresh_message = msgpack.packb({"type": "refresh_token"})
                    response = await self._http_client.post(
                        url,
                        content=refresh_message,
                        params=params,
                        headers=self._get_headers(),
                    )
                    if response.status_code == 200:
                        logger.debug("Token refresh requested successfully")
                    else:
                        logger.warning(f"Token refresh request failed: {response.status_code}")
                except Exception as e:
                    logger.warning(f"Failed to send refresh token request: {e}")

                await asyncio.sleep(token_refresh_interval)
        except asyncio.CancelledError:
            logger.debug("Token refresh task was cancelled")
        except Exception as e:
            logger.error(f"Error in token refresh task: {e}")

    def _get_headers(self, for_stream: bool = False) -> dict:
        """Get HTTP headers with authentication.

        Args:
            for_stream: If True, set Accept header based on format preference
        """
        headers = {
            "Content-Type": "application/msgpack",
        }
        if for_stream:
            if self._format == "msgpack":
                headers["Accept"] = "application/x-msgpack-stream"
            else:
                headers["Accept"] = "application/x-ndjson"
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _create_http_client(self) -> httpx.AsyncClient:
        """Create configured HTTP client with connection pooling.

        Connection pooling improves performance by reusing TCP connections
        for multiple requests, reducing connection overhead.
        """
        verify = True
        if self._ssl is False:
            verify = False
        elif self._ssl is not None:
            verify = self._ssl

        # Try to enable HTTP/2 if h2 is available
        try:
            import h2  # noqa
            http2_enabled = True
            logger.info("HTTP/2 enabled for improved performance")
        except ImportError:
            http2_enabled = False
            logger.debug("HTTP/2 not available (install httpx[http2] for better performance)")

        return httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout, connect=30.0),
            verify=verify,
            # Optimized connection pooling for high-performance RPC
            limits=httpx.Limits(
                max_connections=200,  # Max total connections (increased for parallel requests)
                max_keepalive_connections=50,  # More reusable connections (up from 20)
                keepalive_expiry=300.0,  # Keep connections alive longer (5 minutes)
            ),
            # Enable HTTP/2 for better multiplexing if available
            http2=http2_enabled,
        )

    async def open(self):
        """Open the streaming connection."""
        logger.info(f"Opening HTTP streaming connection to {self._server_url} (format={self._format})")

        if self._http_client is None:
            self._http_client = await self._create_http_client()

        # Build stream URL
        workspace = self._workspace or "public"
        stream_url = f"{self._server_url}/{workspace}/rpc"
        params = {"client_id": self._client_id}
        if self._format == "msgpack":
            params["format"] = "msgpack"

        # Add reconnection token if available (for reconnection)
        if self._reconnection_token:
            params["reconnection_token"] = self._reconnection_token
            logger.info(f"Using reconnection token for HTTP streaming connection")

        try:
            # Start streaming in background task
            self._stream_task = asyncio.create_task(
                self._stream_loop(stream_url, params)
            )

            # Wait for connection info (first message)
            wait_start = asyncio.get_event_loop().time()
            while self.connection_info is None:
                await asyncio.sleep(0.1)
                if asyncio.get_event_loop().time() - wait_start > self._timeout:
                    raise TimeoutError("Timeout waiting for connection info")
                if self._closed:
                    raise ConnectionError("Connection closed during setup")

            self.manager_id = self.connection_info.get("manager_id")
            if self._workspace:
                actual_ws = self.connection_info.get("workspace")
                if actual_ws != self._workspace:
                    raise ConnectionError(
                        f"Connected to wrong workspace: {actual_ws}, expected: {self._workspace}"
                    )
            self._workspace = self.connection_info.get("workspace")

            if "reconnection_token" in self.connection_info:
                self._reconnection_token = self.connection_info["reconnection_token"]

            # Adjust token refresh interval based on server's token lifetime
            if "reconnection_token_life_time" in self.connection_info:
                token_life_time = self.connection_info["reconnection_token_life_time"]
                if self._token_refresh_interval > token_life_time / 1.5:
                    logger.warning(
                        f"Token refresh interval ({self._token_refresh_interval}s) is too long, "
                        f"adjusting to {token_life_time / 1.5:.0f}s based on token lifetime"
                    )
                    self._token_refresh_interval = token_life_time / 1.5

            logger.info(
                f"HTTP streaming connected to workspace: {self._workspace}, "
                f"manager_id: {self.manager_id}"
            )

            # Start token refresh task
            if self._token_refresh_interval > 0:
                self._refresh_token_task = asyncio.create_task(
                    self._send_refresh_token(self._token_refresh_interval)
                )

            if self._handle_connected:
                await self._handle_connected(self.connection_info)

            return self.connection_info

        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            await self._cleanup()
            raise

    async def _stream_loop(self, url: str, params: dict):
        """Main loop for receiving streaming messages."""
        self._enable_reconnect = True
        self._closed = False
        retry = 0

        while not self._closed and retry < MAX_RETRY:
            try:
                async with self._http_client.stream(
                    "GET",
                    url,
                    params=params,
                    headers=self._get_headers(for_stream=True),
                ) as response:
                    if response.status_code != 200:
                        error_text = await response.aread()
                        raise ConnectionError(
                            f"Stream failed with status {response.status_code}: {error_text}"
                        )

                    retry = 0  # Reset retry counter on successful connection

                    if self._format == "msgpack":
                        # Binary msgpack stream with 4-byte length prefix
                        await self._process_msgpack_stream(response)
                    else:
                        # NDJSON stream (line-based)
                        await self._process_ndjson_stream(response)

            except httpx.ReadTimeout:
                logger.warning("Stream read timeout, reconnecting...")
            except httpx.ConnectError as e:
                logger.error(f"Connection error: {e}")
            except ConnectionError as e:
                logger.error(f"Connection error: {e}")
                if not self._enable_reconnect:
                    break
            except asyncio.CancelledError:
                logger.info("Stream task cancelled")
                break
            except Exception as e:
                logger.error(f"Stream error: {e}")

            # Reconnection logic
            if not self._closed and self._enable_reconnect:
                retry += 1
                delay = min(1.0 * (2 ** min(retry, 6)), 60.0)  # Max 60s
                logger.info(f"Reconnecting in {delay:.1f}s (attempt {retry})")
                await asyncio.sleep(delay)
            else:
                break

        if not self._closed and self._handle_disconnected:
            self._handle_disconnected("Stream ended")

    async def _process_ndjson_stream(self, response):
        """Process NDJSON (line-based JSON) stream."""
        async for line in response.aiter_lines():
            if self._closed:
                break

            if not line.strip():
                continue

            try:
                message = json.loads(line)
                await self._handle_stream_message(message)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse JSON message: {e}")
            except Exception as e:
                logger.error(f"Error handling message: {e}")

    async def _process_msgpack_stream(self, response):
        """Process msgpack stream with 4-byte length prefix."""
        buffer = b""
        async for chunk in response.aiter_bytes():
            if self._closed:
                break

            buffer += chunk

            # Process complete frames from buffer
            while len(buffer) >= 4:
                # Read 4-byte length prefix (big-endian)
                length = int.from_bytes(buffer[:4], 'big')

                if len(buffer) < 4 + length:
                    # Incomplete frame, wait for more data
                    break

                # Extract the frame
                frame_data = buffer[4:4 + length]
                buffer = buffer[4 + length:]

                try:
                    # For msgpack, first check if it's a control message
                    # Control messages have a "type" field we need to check
                    unpacker = msgpack.Unpacker(io.BytesIO(frame_data))
                    message = unpacker.unpack()

                    # Check for control messages
                    if isinstance(message, dict):
                        msg_type = message.get("type")
                        if msg_type == "connection_info":
                            self.connection_info = message
                            continue
                        elif msg_type == "ping":
                            continue
                        elif msg_type == "reconnection_token":
                            self._reconnection_token = message.get("reconnection_token")
                            continue
                        elif msg_type == "error":
                            logger.error(f"Server error: {message.get('message')}")
                            continue

                    # For RPC messages, pass the raw frame data to the handler
                    # The RPC layer expects raw msgpack bytes
                    if self._handle_message:
                        if self._is_async:
                            await self._handle_message(frame_data)
                        else:
                            self._handle_message(frame_data)
                except Exception as e:
                    logger.error(f"Error handling msgpack message: {e}")

    async def _handle_stream_message(self, message: dict):
        """Handle a decoded stream message."""
        # Handle connection info
        if message.get("type") == "connection_info":
            self.connection_info = message
            return

        # Handle ping (keep-alive)
        if message.get("type") == "ping":
            return

        # Handle reconnection token refresh
        if message.get("type") == "reconnection_token":
            self._reconnection_token = message.get("reconnection_token")
            return

        # Handle errors
        if message.get("type") == "error":
            logger.error(f"Server error: {message.get('message')}")
            return

        # Pass to message handler (convert to msgpack for RPC)
        if self._handle_message:
            # Convert to msgpack bytes for RPC layer
            data = msgpack.packb(message)
            if self._is_async:
                await self._handle_message(data)
            else:
                self._handle_message(data)

    async def emit_message(self, data: bytes):
        """Send a message to the server via HTTP POST.

        Uses optimized connection pooling with keep-alive for better performance.
        HTTP client automatically handles efficient transfer for all payload sizes.
        """
        if self._closed:
            raise ConnectionError("Connection is closed")

        if self._http_client is None:
            self._http_client = await self._create_http_client()

        workspace = self._workspace or "public"
        url = f"{self._server_url}/{workspace}/rpc"
        params = {"client_id": self._client_id}

        try:
            # httpx handles large payloads efficiently with connection pooling
            response = await self._http_client.post(
                url,
                content=data,
                params=params,
                headers=self._get_headers(),
            )

            if response.status_code != 200:
                error = response.json() if response.content else {"detail": "Unknown error"}
                raise ConnectionError(f"POST failed: {error.get('detail', error)}")

        except httpx.TimeoutException:
            logger.error("Request timeout")
            raise TimeoutError("Request timeout")
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            raise

    async def disconnect(self, reason: Optional[str] = None):
        """Disconnect and cleanup."""
        self._closed = True
        self._enable_reconnect = False
        await self._cleanup()
        logger.info(f"HTTP streaming connection disconnected ({reason})")

    async def _cleanup(self):
        """Cleanup resources."""
        # Cancel token refresh task
        if self._refresh_token_task and not self._refresh_token_task.done():
            self._refresh_token_task.cancel()
            try:
                await asyncio.wait_for(self._refresh_token_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self._refresh_token_task = None

        # Cancel stream task
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            try:
                await asyncio.wait_for(self._stream_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self._stream_task = None

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


def normalize_server_url(server_url: str) -> str:
    """Normalize server URL for HTTP transport."""
    if not server_url:
        raise ValueError("server_url is required")

    # Convert ws:// to http://
    if server_url.startswith("ws://"):
        server_url = server_url.replace("ws://", "http://")
    elif server_url.startswith("wss://"):
        server_url = server_url.replace("wss://", "https://")

    # Remove /ws suffix if present (WebSocket endpoint)
    if server_url.endswith("/ws"):
        server_url = server_url[:-3]

    return server_url.rstrip("/")


def connect_to_server_http(config=None, **kwargs):
    """Connect to server using HTTP streaming transport.

    This is a convenience function that sets transport="http" automatically.
    For a unified interface, use connect_to_server(transport="http") instead.

    Args:
        config: Configuration dict with server_url, token, workspace, etc.
        **kwargs: Additional configuration options

    Returns:
        ServerContextManager that can be used as async context manager
    """
    from .websocket_client import connect_to_server
    config = config or {}
    config.update(kwargs)
    config["transport"] = "http"
    return connect_to_server(config)


async def _connect_to_server_http(config: dict):
    """Internal function to establish HTTP streaming connection."""
    client_id = config.get("client_id")
    if client_id is None:
        client_id = shortuuid.uuid()

    server_url = normalize_server_url(config["server_url"])

    connection = HTTPStreamingRPCConnection(
        server_url,
        client_id,
        workspace=config.get("workspace"),
        token=config.get("token"),
        reconnection_token=config.get("reconnection_token"),
        timeout=config.get("method_timeout", 30),
        ssl=config.get("ssl"),
        token_refresh_interval=config.get("token_refresh_interval", 2 * 60 * 60),
        # Default to msgpack for full binary support and proper RPC message handling
        format=config.get("format", "msgpack"),
    )

    connection_info = await connection.open()
    assert connection_info, "Failed to connect to server"

    await asyncio.sleep(0.1)

    workspace = connection_info["workspace"]

    rpc = RPC(
        connection,
        client_id=client_id,
        workspace=workspace,
        default_context={"connection_type": "http_streaming"},
        name=config.get("name"),
        method_timeout=config.get("method_timeout"),
        loop=config.get("loop"),
        app_id=config.get("app_id"),
        server_base_url=connection_info.get("public_base_url"),
    )

    await rpc.wait_for("services_registered", timeout=config.get("method_timeout", 120))

    wm = await rpc.get_manager_service(
        {"timeout": config.get("method_timeout", 30), "case_conversion": "snake"}
    )
    wm.rpc = rpc

    # Add standard methods
    wm.disconnect = schema_function(
        rpc.disconnect,
        name="disconnect",
        description="Disconnect from server",
        parameters={"properties": {}, "type": "object"},
    )

    wm.register_service = schema_function(
        rpc.register_service,
        name="register_service",
        description="Register a service",
        parameters={
            "properties": {
                "service": {"description": "Service to register", "type": "object"},
            },
            "required": ["service"],
            "type": "object",
        },
    )

    _get_service = wm.get_service

    async def get_service(query, config=None, **kwargs):
        config = config or {}
        config.update(kwargs)
        return await _get_service(query, config=config)

    if hasattr(wm.get_service, "__schema__"):
        get_service.__schema__ = wm.get_service.__schema__
    wm.get_service = get_service

    async def serve():
        await asyncio.Event().wait()

    wm.serve = schema_function(
        serve, name="serve", description="Run event loop forever", parameters={}
    )

    if connection_info:
        wm.config.update(connection_info)

    # Handle force-exit from manager
    if connection.manager_id:
        async def handle_disconnect(message):
            if message.get("from") == "*/" + connection.manager_id:
                logger.info(f"Disconnecting from server: {message.get('reason')}")
                await rpc.disconnect()

        rpc.on("force-exit", handle_disconnect)

    return wm


def get_remote_service_http(service_uri: str, config=None, **kwargs):
    """Get a remote service using HTTP transport.

    This is a convenience function that sets transport="http" automatically.
    For a unified interface, use get_remote_service with transport="http" instead.
    """
    from .websocket_client import get_remote_service
    config = config or {}
    config.update(kwargs)
    config["transport"] = "http"
    return get_remote_service(service_uri, config)
