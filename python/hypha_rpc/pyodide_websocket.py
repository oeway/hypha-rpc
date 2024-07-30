"""Provide a pyodide websocket."""
import asyncio
import inspect
import sys
from js import WebSocket
import json
import logging

try:
    from pyodide.ffi import to_js
except ImportError:
    from pyodide import to_js

MAX_RETRY = 1000000

logging.basicConfig(stream=sys.stdout)
logger = logging.getLogger("pyodide-websocket")
logger.setLevel(logging.WARNING)

local_websocket_patch = """
class LocalWebSocket {
  constructor(url, client_id, workspace) {
    this.url = url;
    this.onopen = () => {};
    this.onmessage = () => {};
    this.onclose = () => {};
    this.onerror = () => {};
    this.client_id = client_id;
    this.workspace = workspace;
    const context = typeof window !== "undefined" ? window : self;
    const isWindow = typeof window !== "undefined";
    this.postMessage = message => {
      if (isWindow) {
        window.parent.postMessage(message, "*");
      } else {
        self.postMessage(message);
      }
    };

    this.readyState = WebSocket.CONNECTING;
    context.addEventListener(
      "message",
      event => {
        const { type, data, to } = event.data;
        if (to !== this.client_id) {
          console.debug("message not for me", to, this.client_id);
          return;
        }
        switch (type) {
          case "message":
            if (this.readyState === WebSocket.OPEN && this.onmessage) {
              this.onmessage({ data: data });
            }
            break;
          case "connected":
            this.readyState = WebSocket.OPEN;
            this.onopen(event);
            break;
          case "closed":
            this.readyState = WebSocket.CLOSED;
            this.onclose(event);
            break;
          default:
            break;
        }
      },
      false
    );

    if (!this.client_id) throw new Error("client_id is required");
    if (!this.workspace) throw new Error("workspace is required");
    this.postMessage({
      type: "connect",
      url: this.url,
      from: this.client_id,
      workspace: this.workspace
    });
  }

  send(data) {
    if (this.readyState === WebSocket.OPEN) {
      this.postMessage({
        type: "message",
        data: data,
        from: this.client_id,
        workspace: this.workspace
      });
    }
  }

  close() {
    this.readyState = WebSocket.CLOSING;
    this.postMessage({
      type: "close",
      from: this.client_id,
      workspace: this.workspace
    });
    this.onclose();
  }

  addEventListener(type, listener) {
    if (type === "message") {
      this.onmessage = listener;
    }
    if (type === "open") {
      this.onopen = listener;
    }
    if (type === "close") {
      this.onclose = listener;
    }
    if (type === "error") {
      this.onerror = listener;
    }
  }
}
"""

class PyodideWebsocketRPCConnection:
    """Represent a Pyodide websocket RPC connection, with local and remote server connection capabilities."""

    def __init__(self, server_url, client_id, workspace=None, token=None, reconnection_token=None, timeout=5):
        assert server_url and client_id, "server_url and client_id are required"
        self._server_url = server_url
        self._client_id = client_id
        self._workspace = workspace
        self._token = token
        self._reconnection_token = reconnection_token
        self._handle_disconnected = None
        self._timeout = timeout # seconds
        self._websocket = None
        self._handle_message = None
        self._handle_connected = None
        self._is_async = False
        self._legacy_auth = None
        self._closed = False
        self.connection_info = None
        self._enable_reconnect = False
        self.manager_id = None

    def on_message(self, handler):
        """Register a message handler."""
        self._handle_message = handler
        self._is_async = inspect.iscoroutinefunction(handler)

    async def _attempt_connection(self, server_url):
        """Attempt to establish a WebSocket connection."""
        fut = asyncio.Future()
        self._legacy_auth = False
        websocket = WebSocket.new(server_url)
        websocket.binaryType = 'arraybuffer'
        websocket.onopen = lambda evt: fut.set_result(websocket)
        async def on_error(evt):
            fut.set_exception(ConnectionError('WebSocket error occurred: ' + str(evt)))
        websocket.onerror = on_error
        websocket.onclose = lambda evt: fut.set_exception(ConnectionError('WebSocket closed unexpectedly'))
        try:
            return await fut
        except ConnectionError:
            logger.error(f"Failed to connect, attempting connection with query parameters.")
            self._legacy_auth = True
            return await self._attempt_connection_with_query_params(server_url)
            

    async def open(self):
        """Open connection, attempting fallback on specific errors."""
        logger.info("Creating a new websocket connection to %s", self._server_url.split("?")[0])
        try:
            self._websocket = await self._attempt_connection(self._server_url)
            fut = asyncio.Future()
            if self._legacy_auth:
                raise NotImplementedError("Legacy authentication is not supported")
            # Send authentication info as the first message
            auth_info = json.dumps({
                'client_id': self._client_id,
                'workspace': self._workspace,
                'token': self._token,
                'reconnection_token': self._reconnection_token
            })
            self._websocket.send(to_js(auth_info))
            def onmessage(evt):
                # Handle the first message as connection info
                first_message = json.loads(evt.data)
                if first_message.get("type") == "connection_info":
                    self.connection_info = first_message
                    if self._workspace:
                        assert self.connection_info.get("workspace") == self._workspace, f"Connected to the wrong workspace: {self.connection_info['workspace']}, expected: {self._workspace}"
                    if "reconnection_token" in self.connection_info:
                        self._reconnection_token = self.connection_info["reconnection_token"]
                    self.manager_id = self.connection_info.get("manager_id", None)
                    logger.info(f"Successfully connected to the server, workspace: {self.connection_info.get('workspace')}, manager_id: {self.manager_id}")
                    if "announcement" in self.connection_info:
                        print(self.connection_info["announcement"])
                    fut.set_result(self.connection_info)
                elif first_message.get("type") == "error":
                    error = first_message["message"]
                    logger.error("Failed to connect: %s", error)
                    fut.set_exception(ConnectionAbortedError(error))
                else:
                    logger.error("Unexpected message received from the server: %s", first_message)
                    fut.set_exception(ConnectionAbortedError("Unexpected message received from the server"))
            self._websocket.onmessage = onmessage
            self._websocket.onerror = lambda evt: fut.set_exception(ConnectionError('WebSocket error occurred'))
            self._websocket.onclose = lambda evt: fut.set_exception(ConnectionError('WebSocket closed'))
            # Wait for the connection info
            await asyncio.wait_for(fut, self._timeout)
            
            self._enable_reconnect = True
            self._closed = False
            self._websocket.onmessage = lambda evt: self._handle_message(evt.data.to_py().tobytes())
            self._websocket.onerror = lambda evt: logger.error(f"WebSocket error: {evt}")
            self._websocket.onclose = self._handle_close
            if self._handle_connected:
                await self._handle_connected(self.connection_info)
            return self.connection_info
        except Exception as exp:
            logger.error(f"Failed to open connection: {exp}")
            raise exp

    def on_disconnected(self, handler):
        """Register a disconnect handler."""
        self._handle_disconnected = handler

    def on_connected(self, handler):
        """Register a connect handler."""
        self._handle_connected = handler
        assert inspect.iscoroutinefunction(handler), "On connect handler must be a coroutine function"

    async def _attempt_connection_with_query_params(self, server_url):
        """Create URL with query parameters."""
        query_params = []
        if self._client_id:
            query_params.append(f"client_id={self._client_id}")
        if self._workspace:
            query_params.append(f"workspace={self._workspace}")
        if self._token:
            query_params.append(f"token={self._token}")
        if self._reconnection_token:
            query_params.append(f"reconnection_token={self._reconnection_token}")
        query_string = "&".join(query_params)
        server_url = f"{server_url}?{query_string}" if query_string else server_url
        fut = asyncio.Future()
        websocket = WebSocket.new(server_url)
        websocket.binaryType = 'arraybuffer'
        websocket.onopen = lambda evt: fut.set_result(websocket)
        async def on_error(evt):
            fut.set_exception(ConnectionError('WebSocket error occurred'))
        websocket.onerror = on_error
        websocket.onclose = lambda evt: fut.set_exception(ConnectionError('WebSocket closed'))
        return await fut

    def _handle_close(self, evt):
        """Handle the close event."""
        if not self._closed and self._websocket and self._websocket.readyState == WebSocket.CLOSED:
            if evt.code in [1000, 1001]:
                logger.info(f"Websocket connection closed (code: {evt.code}): {evt.reason}")
                if self._handle_disconnected:
                    self._handle_disconnected(evt.reason)
                self._closed = True
            elif self._enable_reconnect:
                logger.warning(f"Websocket connection closed unexpectedly (code: {evt.code}): {evt.reason}")
                retry = 0
                async def reconnect():
                    nonlocal retry
                    try:
                        logger.warning(f"Reconnecting to {self._server_url.split('?')[0]} (attempt #{retry})")
                        await self.open()
                        logger.warning(f"Successfully reconnected to the server {self._server_url.split('?')[0]}")
                    except ConnectionAbortedError as e:
                        logger.warning("Failed to reconnect, connection aborted: %s", e)
                        return
                    except NotImplementedError as e:
                        logger.warning("Failed to reconnect, connection aborted: %s", e)
                        return
                    except Exception as e:
                        logger.warning("Failed to reconnect: %s", e)
                        await asyncio.sleep(1)
                        if self._websocket and self._websocket.readyState == WebSocket.OPEN:
                            return
                        retry += 1
                        if retry < MAX_RETRY:
                            await reconnect()
                        else:
                            logger.error("Failed to reconnect after 5 attempts")
                asyncio.ensure_future(reconnect())
        else:
            if self._handle_disconnected:
                self._handle_disconnected(evt.reason)

    async def emit_message(self, data):
        """Emit a message."""
        if self._closed:
            raise Exception("Connection is closed")
        assert self._handle_message, "No handler for message"
        if (
            not self._websocket
            or self._websocket.readyState == WebSocket.CLOSED
        ):
            await self.open()

        try:
            data = to_js(data)
            self._websocket.send(data)
        except Exception as exp:
            logger.error("Failed to send data, error: %s", exp)
            raise exp

    async def disconnect(self, reason=None):
        """Disconnect the WebSocket."""
        self._closed = True
        if self._websocket and self._websocket.readyState == WebSocket.OPEN:
            self._websocket.close(1000, reason)
        logger.info(f"WebSocket connection disconnected ({reason})")
