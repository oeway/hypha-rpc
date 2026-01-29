/**
 * HTTP Streaming RPC Client for Hypha.
 *
 * This module provides HTTP-based RPC transport as an alternative to WebSocket.
 * It uses:
 * - HTTP GET with streaming (NDJSON/msgpack) for server-to-client messages
 * - HTTP POST for client-to-server messages
 *
 * This is more resilient to network issues than WebSocket because:
 * 1. Each POST request is independent (stateless)
 * 2. GET stream can be easily reconnected
 * 3. Works through more proxies and firewalls
 */

import { RPC } from "./rpc.js";
import { assert, randId, waitFor, parseServiceUrl } from "./utils/index.js";
import { schemaFunction } from "./utils/schema.js";
import { encode as msgpackEncode, decode as msgpackDecode } from "@msgpack/msgpack";

const MAX_RETRY = 1000000;

/**
 * HTTP Streaming RPC Connection.
 *
 * Uses HTTP GET with streaming for receiving messages and HTTP POST for sending messages.
 * Supports two formats:
 * - NDJSON (default): JSON lines for text-based messages
 * - msgpack: Binary format with length-prefixed frames for binary data support
 */
export class HTTPStreamingRPCConnection {
  /**
   * Initialize HTTP streaming connection.
   *
   * @param {string} server_url - The server URL (http:// or https://)
   * @param {string} client_id - Unique client identifier
   * @param {string} workspace - Target workspace (optional)
   * @param {string} token - Authentication token (optional)
   * @param {string} reconnection_token - Token for reconnection (optional)
   * @param {number} timeout - Request timeout in seconds (default: 60)
   * @param {number} token_refresh_interval - Interval for token refresh (default: 2 hours)
   * @param {string} format - Stream format - "json" (NDJSON) or "msgpack" (default: "json")
   */
  constructor(
    server_url,
    client_id,
    workspace = null,
    token = null,
    reconnection_token = null,
    timeout = 60,
    token_refresh_interval = 2 * 60 * 60,
    format = "json",
  ) {
    assert(server_url && client_id, "server_url and client_id are required");
    this._server_url = server_url.replace(/\/$/, "");
    this._client_id = client_id;
    this._workspace = workspace;
    this._token = token;
    this._reconnection_token = reconnection_token;
    this._timeout = timeout;
    this._token_refresh_interval = token_refresh_interval;
    this._format = format;

    this._handle_message = null;
    this._handle_disconnected = null;
    this._handle_connected = null;

    this._closed = false;
    this._enable_reconnect = false;
    this.connection_info = null;
    this.manager_id = null;

    this._stream_reader = null;
    this._stream_controller = null;
  }

  /**
   * Register message handler.
   */
  on_message(handler) {
    assert(handler, "handler is required");
    this._handle_message = handler;
  }

  /**
   * Register disconnection handler.
   */
  on_disconnected(handler) {
    this._handle_disconnected = handler;
  }

  /**
   * Register connection handler.
   */
  on_connected(handler) {
    this._handle_connected = handler;
  }

  /**
   * Get HTTP headers with authentication.
   *
   * @param {boolean} for_stream - If true, set Accept header based on format preference
   * @returns {Object} Headers object
   */
  _get_headers(for_stream = false) {
    const headers = {
      "Content-Type": "application/msgpack",
    };
    if (for_stream) {
      if (this._format === "msgpack") {
        headers["Accept"] = "application/x-msgpack-stream";
      } else {
        headers["Accept"] = "application/x-ndjson";
      }
    }
    if (this._token) {
      headers["Authorization"] = `Bearer ${this._token}`;
    }
    return headers;
  }

  /**
   * Open the streaming connection.
   */
  async open() {
    console.info(
      `Opening HTTP streaming connection to ${this._server_url} (format=${this._format})`,
    );

    // Build stream URL
    const workspace = this._workspace || "public";
    let stream_url = `${this._server_url}/${workspace}/rpc?client_id=${this._client_id}`;
    if (this._format === "msgpack") {
      stream_url += "&format=msgpack";
    }

    // Start streaming in background
    this._startStreamLoop(stream_url);

    // Wait for connection info (first message)
    const start = Date.now();
    while (this.connection_info === null) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      if (Date.now() - start > this._timeout * 1000) {
        throw new Error("Timeout waiting for connection info");
      }
      if (this._closed) {
        throw new Error("Connection closed during setup");
      }
    }

    this.manager_id = this.connection_info.manager_id;
    if (this._workspace) {
      const actual_ws = this.connection_info.workspace;
      if (actual_ws !== this._workspace) {
        throw new Error(
          `Connected to wrong workspace: ${actual_ws}, expected: ${this._workspace}`,
        );
      }
    }
    this._workspace = this.connection_info.workspace;

    if (this._handle_connected) {
      await this._handle_connected();
    }

    return this.connection_info;
  }

  /**
   * Start the streaming loop.
   */
  async _startStreamLoop(url) {
    let retry = 0;

    while (!this._closed && retry < MAX_RETRY) {
      try {
        const response = await fetch(url, {
          method: "GET",
          headers: this._get_headers(true),
        });

        if (!response.ok) {
          const error_text = await response.text();
          throw new Error(
            `Stream failed with status ${response.status}: ${error_text}`,
          );
        }

        retry = 0; // Reset retry counter on successful connection

        if (this._format === "msgpack") {
          // Binary msgpack stream with 4-byte length prefix
          await this._processMsgpackStream(response);
        } else {
          // NDJSON stream (line-based)
          await this._processNdjsonStream(response);
        }
      } catch (error) {
        if (this._closed) break;
        console.error(`Connection error: ${error.message}`);

        if (!this._enable_reconnect) {
          break;
        }
      }

      // Reconnection logic
      if (!this._closed && this._enable_reconnect) {
        retry += 1;
        // Exponential backoff with max 30 seconds
        const delay = Math.min(Math.pow(2, retry) * 0.1, 30);
        console.info(`Reconnecting in ${delay.toFixed(1)}s (attempt ${retry})`);
        await new Promise((resolve) => setTimeout(resolve, delay * 1000));
      } else {
        break;
      }
    }

    if (!this._closed && this._handle_disconnected) {
      this._handle_disconnected("Stream ended");
    }
  }

  /**
   * Process NDJSON (line-based JSON) stream.
   */
  async _processNdjsonStream(response) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (!this._closed) {
      const { done, value } = await reader.read();

      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (!line.trim()) continue;

        try {
          const message = JSON.parse(line);
          await this._handleStreamMessage(message);
        } catch (error) {
          console.warn(`Failed to parse JSON message: ${error.message}`);
        }
      }
    }
  }

  /**
   * Process msgpack stream with 4-byte length prefix.
   */
  async _processMsgpackStream(response) {
    const reader = response.body.getReader();
    let buffer = new Uint8Array(0);

    while (!this._closed) {
      const { done, value } = await reader.read();

      if (done) break;

      // Append new chunk to buffer
      const newBuffer = new Uint8Array(buffer.length + value.length);
      newBuffer.set(buffer);
      newBuffer.set(value, buffer.length);
      buffer = newBuffer;

      // Process complete frames from buffer
      while (buffer.length >= 4) {
        // Read 4-byte length prefix (big-endian)
        const length =
          (buffer[0] << 24) | (buffer[1] << 16) | (buffer[2] << 8) | buffer[3];

        if (buffer.length < 4 + length) {
          // Incomplete frame, wait for more data
          break;
        }

        // Extract the frame
        const frame_data = buffer.slice(4, 4 + length);
        buffer = buffer.slice(4 + length);

        try {
          const message = msgpackDecode(frame_data);

          // Check for control messages
          if (typeof message === "object" && message !== null) {
            const msg_type = message.type;
            if (msg_type === "connection_info") {
              this.connection_info = message;
              continue;
            } else if (msg_type === "ping") {
              continue;
            } else if (msg_type === "reconnection_token") {
              this._reconnection_token = message.reconnection_token;
              continue;
            } else if (msg_type === "error") {
              console.error(`Server error: ${message.message}`);
              continue;
            }
          }

          // For RPC messages, pass the raw frame data to the handler
          if (this._handle_message) {
            await this._handle_message(frame_data);
          }
        } catch (error) {
          console.error(`Error handling msgpack message: ${error.message}`);
        }
      }
    }
  }

  /**
   * Handle a decoded stream message.
   */
  async _handleStreamMessage(message) {
    // Handle connection info
    if (message.type === "connection_info") {
      this.connection_info = message;
      return;
    }

    // Handle ping (keep-alive)
    if (message.type === "ping") {
      return;
    }

    // Handle reconnection token refresh
    if (message.type === "reconnection_token") {
      this._reconnection_token = message.reconnection_token;
      return;
    }

    // Handle errors
    if (message.type === "error") {
      console.error(`Server error: ${message.message}`);
      return;
    }

    // Pass to message handler (convert to msgpack for RPC)
    if (this._handle_message) {
      const data = msgpackEncode(message);
      await this._handle_message(data);
    }
  }

  /**
   * Send a message to the server via HTTP POST.
   */
  async emit_message(data) {
    if (this._closed) {
      throw new Error("Connection is closed");
    }

    // Build POST URL - use the connected workspace
    const workspace = this._workspace || "public";
    const post_url = `${this._server_url}/${workspace}/rpc?client_id=${this._client_id}`;

    // Ensure data is Uint8Array
    const body = data instanceof Uint8Array ? data : new Uint8Array(data);

    const response = await fetch(post_url, {
      method: "POST",
      headers: this._get_headers(false),
      body: body,
    });

    if (!response.ok) {
      const error_text = await response.text();
      throw new Error(`POST failed with status ${response.status}: ${error_text}`);
    }

    return true;
  }

  /**
   * Set reconnection flag.
   */
  set_reconnection(value) {
    this._enable_reconnect = value;
  }

  /**
   * Close the connection.
   */
  async disconnect(reason = "client disconnect") {
    if (this._closed) return;

    this._closed = true;

    if (this._handle_disconnected) {
      this._handle_disconnected(reason);
    }
  }
}

/**
 * Normalize server URL for HTTP transport.
 */
export function normalizeServerUrl(server_url) {
  if (!server_url) {
    throw new Error("server_url is required");
  }

  // Convert ws:// to http://
  if (server_url.startsWith("ws://")) {
    server_url = server_url.replace("ws://", "http://");
  } else if (server_url.startsWith("wss://")) {
    server_url = server_url.replace("wss://", "https://");
  }

  // Remove /ws suffix if present (WebSocket endpoint)
  if (server_url.endsWith("/ws")) {
    server_url = server_url.slice(0, -3);
  }

  return server_url.replace(/\/$/, "");
}

/**
 * Internal function to establish HTTP streaming connection.
 */
export async function _connectToServerHTTP(config) {
  let clientId = config.clientId || config.client_id;
  if (!clientId) {
    clientId = randId();
  }

  const server_url = normalizeServerUrl(config.serverUrl || config.server_url);

  const connection = new HTTPStreamingRPCConnection(
    server_url,
    clientId,
    config.workspace,
    config.token,
    config.reconnection_token,
    config.method_timeout || 30,
    config.token_refresh_interval || 2 * 60 * 60,
    // Default to msgpack for full binary support and proper RPC message handling
    config.format || "msgpack",
  );

  const connection_info = await connection.open();
  assert(connection_info, "Failed to connect to server");

  await new Promise((resolve) => setTimeout(resolve, 100));

  const workspace = connection_info.workspace;

  const rpc = new RPC(connection, {
    client_id: clientId,
    workspace,
    default_context: { connection_type: "http_streaming" },
    name: config.name,
    method_timeout: config.method_timeout,
    app_id: config.app_id,
    server_base_url: connection_info.public_base_url,
  });

  await waitFor(
    () => rpc._services_registered,
    null,
    config.method_timeout || 120,
    "Timeout waiting for services to register",
  );

  const wm = await rpc.get_manager_service({
    timeout: config.method_timeout || 30,
  });
  wm.rpc = rpc;

  // Add standard methods
  wm.disconnect = schemaFunction(rpc.disconnect.bind(rpc), {
    name: "disconnect",
    description: "Disconnect from server",
    parameters: { properties: {}, type: "object" },
  });

  wm.registerService = schemaFunction(rpc.register_service.bind(rpc), {
    name: "registerService",
    description: "Register a service",
    parameters: {
      properties: {
        service: { description: "Service to register", type: "object" },
      },
      required: ["service"],
      type: "object",
    },
  });

  const _getService = wm.getService;
  wm.getService = async (query, config = {}) => {
    return await _getService(query, config);
  };
  if (_getService.__schema__) {
    wm.getService.__schema__ = _getService.__schema__;
  }

  async function serve() {
    await new Promise(() => {}); // Wait forever
  }

  wm.serve = schemaFunction(serve, {
    name: "serve",
    description: "Run event loop forever",
    parameters: {},
  });

  if (connection_info) {
    wm.config = Object.assign(wm.config || {}, connection_info);
  }

  // Handle force-exit from manager
  if (connection.manager_id) {
    rpc.on("force-exit", async (message) => {
      if (message.from === "*/" + connection.manager_id) {
        console.info(`Disconnecting from server: ${message.reason}`);
        await rpc.disconnect();
      }
    });
  }

  return wm;
}

/**
 * Connect to server using HTTP streaming transport.
 *
 * This is an alternative to WebSocket connection that's more resilient
 * to network issues.
 *
 * @param {Object} config - Configuration object
 * @returns {Promise<Object>} Connected workspace manager
 */
export async function connectToServerHTTP(config = {}) {
  return await _connectToServerHTTP(config);
}

/**
 * Get a remote service using HTTP transport.
 */
export async function getRemoteServiceHTTP(serviceUri, config = {}) {
  const { serverUrl, workspace, clientId, serviceId, appId } =
    parseServiceUrl(serviceUri);
  const fullServiceId = `${workspace}/${clientId}:${serviceId}@${appId}`;

  if (config.serverUrl) {
    if (config.serverUrl !== serverUrl) {
      throw new Error("server_url mismatch");
    }
  }
  config.serverUrl = serverUrl;

  const server = await connectToServerHTTP(config);
  return await server.getService(fullServiceId);
}
