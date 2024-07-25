"""Provide a pyodide websocket."""
import asyncio
import inspect
from js import WebSocket
import json

try:
    from pyodide.ffi import to_js
except ImportError:
    from pyodide import to_js

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

    def __init__(self, server_url, client_id, workspace=None, token=None, reconnection_token=None, logger=None, timeout=5):
        assert server_url and client_id, "server_url and client_id are required"
        self.server_url = server_url
        self.client_id = client_id
        self.workspace = workspace
        self.token = token
        self.reconnection_token = reconnection_token
        self._disconnect_handler = None
        self.timeout = timeout
        self._websocket = None
        self._handle_message = None
        self._handle_connect = None
        self._is_async = False
        self._legacy_auth = False
        self._logger = logger
        self.connection_info = None

    def on_message(self, handler):
        """Register a message handler."""
        self._handle_message = handler
        self._is_async = inspect.iscoroutinefunction(handler)

    async def _attempt_connection(self, server_url):
        """Attempt to establish a WebSocket connection."""
        fut = asyncio.Future()
        websocket = WebSocket.new(server_url)
        websocket.binaryType = 'arraybuffer'
        websocket.onopen = lambda evt: fut.set_result(websocket)
        websocket.onmessage = lambda evt: fut.set_result(websocket)
        websocket.onerror = lambda evt: fut.set_exception(ConnectionError('WebSocket error occurred'))
        websocket.onclose = lambda evt: fut.set_exception(ConnectionError('WebSocket closed unexpectedly'))
        return await fut

    async def open(self):
        """Open connection, attempting fallback on specific errors."""
        try:
            self._websocket = await self._attempt_connection(self.server_url)
            fut = asyncio.Future()
            # Send authentication info as the first message
            auth_info = json.dumps({
                'client_id': self.client_id,
                'workspace': self.workspace,
                'token': self.token,
                'reconnection_token': self.reconnection_token
            })
            websocket.send(to_js(auth_info))
            def onmessage(evt):
                # Handle the first message as connection info
                first_message = json.loads(evt.data)
                if first_message.get("type") == "connection_info":
                    if self._logger:
                        self._logger.info("Successfully connected: %s", first_message)
                    fut.set_result(first_message)
                elif first_message.get("type") == "error":
                    error = first_message.get("message", "Unknown error")
                    if self._logger:
                        self._logger.error("Failed to connect: %s", error)
                    fut.set_exception(ConnectionAbortedError(error))
                else:
                    if self._logger:
                        self._logger.error("Unexpected message received from the server: %s", first_message)
                    fut.set_exception(ConnectionAbortedError("Unexpected message received from the server"))
            websocket.onmessage = onmessage
            websocket.onerror = lambda evt: fut.set_exception(ConnectionError('WebSocket error occurred'))
            websocket.onclose = lambda evt: fut.set_exception(ConnectionError('WebSocket closed unexpectedly'))
            self.connection_info = await fut
            WebSocket.onerror = None
            WebSocket.onclose = None
        except ConnectionError as e:
            if self._logger:
                self._logger.error(f"Failed to open connection: {e}")
            server_url_with_params = self._create_url_with_params()
            self._websocket = await self._attempt_connection(server_url_with_params)
        
        self._websocket.onmessage = lambda evt: self._handle_message(evt.data.to_py().tobytes())
        if self._logger:
            self._websocket.onerror = lambda evt: self._logger.error(f"WebSocket error: {evt}")
            self._websocket.onclose = lambda evt: self._logger.info(f"WebSocket closed: {evt}")
        if self._disconnect_handler:
            self._websocket.onclose = lambda evt: self._disconnect_handler(evt.reason)
        if self._handle_connect:
            await self._handle_connect(self)
        return self.connection_info

    def on_disconnected(self, handler):
        """Register a disconnect handler."""
        self._disconnect_handler = handler

    def on_connect(self, handler):
        """Register a connect handler."""
        self._handle_connect = handler
        assert inspect.iscoroutinefunction(handler), "On connect handler must be a coroutine function"

    def _create_url_with_params(self):
        """Create URL with query parameters."""
        query_params = []
        if self.client_id:
            query_params.append(f"client_id={self.client_id}")
        if self.workspace:
            query_params.append(f"workspace={self.workspace}")
        if self.token:
            query_params.append(f"token={self.token}")
        if self.reconnection_token:
            query_params.append(f"reconnection_token={self.reconnection_token}")
        query_string = "&".join(query_params)
        return f"{self.server_url}?{query_string}"

    async def emit_message(self, data):
        """Emit a message."""
        assert self._handle_message, "No handler for message"
        if not self._websocket:
            await self.open()
        try:
            data = to_js(data)
            self._websocket.send(data)
        except Exception as exp:
            if self._logger:
                self._logger.error("Failed to send data, error: %s", exp)
            print("Failed to send data, error: %s", exp)
            raise

    async def disconnect(self, reason=None):
        """Disconnect the WebSocket."""
        if self._websocket:
            self._websocket.close(1000, reason)
        self._websocket = None
        if self._logger:
            self._logger.info(f"WebSocket connection disconnected ({reason})")
