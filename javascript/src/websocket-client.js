import { RPC, API_VERSION } from "./rpc.js";
import {
  assert,
  randId,
  waitFor,
  loadRequirements,
  parseServiceUrl,
} from "./utils/index.js";
import { schemaFunction } from "./utils/schema.js";
import { getRTCService, registerRTCService } from "./webrtc-client.js";

// Import HTTP client for internal use and re-export
import {
  HTTPStreamingRPCConnection,
  connectToServerHTTP,
  getRemoteServiceHTTP,
  normalizeServerUrl as normalizeServerUrlHTTP,
} from "./http-client.js";

// Re-export HTTP client classes and functions
export {
  HTTPStreamingRPCConnection,
  connectToServerHTTP,
  getRemoteServiceHTTP,
  normalizeServerUrlHTTP,
};

export { RPC, API_VERSION, schemaFunction };
export { loadRequirements };
export { getRTCService, registerRTCService };
export {
  generateSigningKeypair,
  signMessage,
  verifySignature,
  createSignableData,
  isTimestampValid,
} from "./crypto.js";

const MAX_RETRY = 1000000;

class WebsocketRPCConnection {
  constructor(
    server_url,
    client_id,
    workspace,
    token,
    reconnection_token = null,
    timeout = 60,
    WebSocketClass = null,
    token_refresh_interval = 2 * 60 * 60,
    additional_headers = null,
  ) {
    assert(server_url && client_id, "server_url and client_id are required");
    this._server_url = server_url;
    this._client_id = client_id;
    this._workspace = workspace;
    this._token = token;
    this._reconnection_token = reconnection_token;
    this._websocket = null;
    this._handle_message = null;
    this._handle_connected = null; // Connection open event handler
    this._handle_disconnected = null; // Disconnection event handler
    this._timeout = timeout;
    this._WebSocketClass = WebSocketClass || WebSocket; // Allow overriding the WebSocket class
    this._closed = false;
    this._legacy_auth = null;
    this.connection_info = null;
    this._enable_reconnect = false;
    this._token_refresh_interval = token_refresh_interval;
    this.manager_id = null;
    this._refresh_token_task = null;
    this._reconnect_timeouts = new Set(); // Track reconnection timeouts
    this._additional_headers = additional_headers;
    this._reconnecting = false; // Mutex to prevent overlapping reconnection attempts
    this._disconnectedNotified = false;
  }

  /**
   * Centralized cleanup method to clear all timers and prevent resource leaks
   */
  _cleanup() {
    // Clear token refresh delay timeout
    if (this._refresh_token_delay) {
      clearTimeout(this._refresh_token_delay);
      this._refresh_token_delay = null;
    }

    // Clear token refresh interval
    if (this._refresh_token_task) {
      clearInterval(this._refresh_token_task);
      this._refresh_token_task = null;
    }

    // Clear all reconnection timeouts
    for (const timeoutId of this._reconnect_timeouts) {
      clearTimeout(timeoutId);
    }
    this._reconnect_timeouts.clear();
  }

  on_message(handler) {
    assert(handler, "handler is required");
    this._handle_message = handler;
  }

  on_connected(handler) {
    this._handle_connected = handler;
  }

  on_disconnected(handler) {
    this._handle_disconnected = handler;
  }

  async _attempt_connection(server_url, attempt_fallback = true) {
    return new Promise((resolve, reject) => {
      this._legacy_auth = false;
      const websocket = new this._WebSocketClass(server_url);
      websocket.binaryType = "arraybuffer";

      websocket.onopen = () => {
        console.info("WebSocket connection established");
        resolve(websocket);
      };

      websocket.onerror = (event) => {
        console.error("WebSocket connection error:", event);
        reject(new Error(`WebSocket connection error: ${event}`));
      };

      websocket.onclose = (event) => {
        if (event.code === 1003 && attempt_fallback) {
          console.info(
            "Received 1003 error, attempting connection with query parameters.",
          );
          this._legacy_auth = true;
          this._attempt_connection_with_query_params(server_url)
            .then(resolve)
            .catch(reject);
        } else {
          this._notifyDisconnected(event.reason);
        }
      };
    });
  }

  async _attempt_connection_with_query_params(server_url) {
    // Initialize an array to hold parts of the query string
    const queryParamsParts = [];

    // Conditionally add each parameter if it has a non-empty value
    if (this._client_id)
      queryParamsParts.push(`client_id=${encodeURIComponent(this._client_id)}`);
    if (this._workspace)
      queryParamsParts.push(`workspace=${encodeURIComponent(this._workspace)}`);
    if (this._token)
      queryParamsParts.push(`token=${encodeURIComponent(this._token)}`);
    if (this._reconnection_token)
      queryParamsParts.push(
        `reconnection_token=${encodeURIComponent(this._reconnection_token)}`,
      );

    // Join the parts with '&' to form the final query string, prepend '?' if there are any parameters
    const queryString =
      queryParamsParts.length > 0 ? `?${queryParamsParts.join("&")}` : "";

    // Construct the full URL by appending the query string if it exists
    const full_url = server_url + queryString;

    return await this._attempt_connection(full_url, false);
  }

  _establish_connection() {
    return new Promise((resolve, reject) => {
      this._websocket.onmessage = (event) => {
        const data = event.data;
        const first_message = JSON.parse(data);
        if (first_message.type == "connection_info") {
          this.connection_info = first_message;
          if (this._workspace) {
            assert(
              this.connection_info.workspace === this._workspace,
              `Connected to the wrong workspace: ${this.connection_info.workspace}, expected: ${this._workspace}`,
            );
          }
          if (this.connection_info.reconnection_token) {
            this._reconnection_token = this.connection_info.reconnection_token;
          }
          if (this.connection_info.reconnection_token_life_time) {
            // make sure the token refresh interval is less than the token life time
            if (
              this._token_refresh_interval >
              this.connection_info.reconnection_token_life_time / 1.5
            ) {
              console.warn(
                `Token refresh interval is too long (${this._token_refresh_interval}), setting it to 1.5 times of the token life time(${this.connection_info.reconnection_token_life_time}).`,
              );
              this._token_refresh_interval =
                this.connection_info.reconnection_token_life_time / 1.5;
            }
          }
          this.manager_id = this.connection_info.manager_id || null;
          console.log(
            `Successfully connected to the server, workspace: ${this.connection_info.workspace}, manager_id: ${this.manager_id}`,
          );
          if (this.connection_info.announcement) {
            console.log(`${this.connection_info.announcement}`);
          }
          resolve(this.connection_info);
        } else if (first_message.type == "error") {
          const error = "ConnectionAbortedError: " + first_message.message;
          console.error("Failed to connect, " + error);
          reject(new Error(error));
          return;
        } else {
          console.error(
            "ConnectionAbortedError: Unexpected message received from the server:",
            data,
          );
          reject(
            new Error(
              "ConnectionAbortedError: Unexpected message received from the server",
            ),
          );
          return;
        }
      };
    });
  }

  async open() {
    console.log(
      "Creating a new websocket connection to",
      this._server_url.split("?")[0],
    );
    try {
      this._websocket = await this._attempt_connection(this._server_url);
      if (this._legacy_auth) {
        throw new Error(
          "NotImplementedError: Legacy authentication is not supported",
        );
      }
      // Send authentication info as the first message if connected without query params
      const authInfo = JSON.stringify({
        client_id: this._client_id,
        workspace: this._workspace,
        token: this._token,
        reconnection_token: this._reconnection_token,
      });
      this._websocket.send(authInfo);
      // Wait for the first message from the server
      await waitFor(
        this._establish_connection(),
        this._timeout,
        "Failed to receive the first message from the server",
      );
      if (this._token_refresh_interval > 0) {
        this._refresh_token_delay = setTimeout(() => {
          this._refresh_token_delay = null;
          if (this._closed) return;
          this._send_refresh_token();
          this._refresh_token_task = setInterval(() => {
            this._send_refresh_token();
          }, this._token_refresh_interval * 1000);
        }, 2000);
      }
      // Listen to messages from the server
      this._enable_reconnect = true;
      this._closed = false;
      this._disconnectedNotified = false;
      this._websocket.onmessage = (event) => {
        if (typeof event.data === "string") {
          const parsedData = JSON.parse(event.data);
          // Check if the message is a reconnection token
          if (parsedData.type === "reconnection_token") {
            this._reconnection_token = parsedData.reconnection_token;
            // console.log("Reconnection token received");
          } else {
            console.log("Received message from the server:", parsedData);
          }
        } else {
          this._handle_message(event.data);
        }
      };

      this._websocket.onerror = (event) => {
        console.error("WebSocket connection error:", event);
        // Clean up timers on error
        this._cleanup();
      };

      this._websocket.onclose = this._handle_close.bind(this);

      if (this._handle_connected) {
        this._handle_connected(this.connection_info);
      }
      return this.connection_info;
    } catch (error) {
      // Clean up any timers that might have been set up before the error
      this._cleanup();
      console.error(
        "Failed to connect to",
        this._server_url.split("?")[0],
        error,
      );
      throw error;
    }
  }

  _send_refresh_token() {
    if (this._websocket && this._websocket.readyState === WebSocket.OPEN) {
      const refreshMessage = JSON.stringify({ type: "refresh_token" });
      this._websocket.send(refreshMessage);
      // console.log("Requested refresh token");
    }
  }

  _notifyDisconnected(reason) {
    if (this._disconnectedNotified) return;
    this._disconnectedNotified = true;
    if (this._handle_disconnected) {
      this._handle_disconnected(reason);
    }
  }

  _handle_close(event) {
    if (
      !this._closed &&
      this._websocket &&
      this._websocket.readyState === WebSocket.CLOSED
    ) {
      // Clean up timers when connection closes
      this._cleanup();
      // Reset the guard so reconnection can re-notify on next disconnect
      this._disconnectedNotified = false;

      // Even if it's a graceful closure (codes 1000, 1001), if it wasn't user-initiated,
      // we should attempt to reconnect (e.g., server restart, k8s upgrade)
      if (this._enable_reconnect) {
        if ([1000, 1001].includes(event.code)) {
          console.warn(
            `Websocket connection closed gracefully by server (code: ${event.code}): ${event.reason} - attempting reconnect`,
          );
        } else {
          console.warn(
            "Websocket connection closed unexpectedly (code: %s): %s",
            event.code,
            event.reason,
          );
        }

        // Notify the RPC layer immediately so it can reject pending calls
        this._notifyDisconnected(event.reason);

        // Prevent overlapping reconnection attempts
        if (this._reconnecting) {
          console.debug("Reconnection already in progress, skipping");
          return;
        }
        this._reconnecting = true;

        let retry = 0;
        const baseDelay = 1000; // Start with 1 second
        const maxDelay = 60000; // Maximum delay of 60 seconds
        const maxJitter = 0.1; // Maximum jitter factor

        const reconnect = async () => {
          // Check if we were explicitly closed
          if (this._closed) {
            console.info("Connection was closed, stopping reconnection");
            this._reconnecting = false;
            return;
          }

          try {
            console.warn(
              `Reconnecting to ${this._server_url.split("?")[0]} (attempt #${retry})`,
            );
            // Open the connection, this will trigger the on_connected callback
            await this.open();

            // Wait a short time for services to be registered
            // This gives time for the on_connected callback to complete
            // which includes re-registering all services to the server
            await new Promise((resolve) => setTimeout(resolve, 500));

            console.warn(
              `Successfully reconnected to server ${this._server_url} (services re-registered)`,
            );
            this._reconnecting = false;
          } catch (e) {
            if (`${e}`.includes("ConnectionAbortedError:")) {
              console.warn("Server refused to reconnect:", e);
              this._closed = true;
              this._reconnecting = false;
              this._notifyDisconnected(`Server refused reconnection: ${e}`);
              return;
            } else if (`${e}`.includes("NotImplementedError:")) {
              console.error(
                `${e}\nIt appears that you are trying to connect to a hypha server that is older than 0.20.0, please upgrade the hypha server or use the websocket client in imjoy-rpc(https://www.npmjs.com/package/imjoy-rpc) instead`,
              );
              this._closed = true;
              this._reconnecting = false;
              this._notifyDisconnected(`Server too old: ${e}`);
              return;
            }

            // Log specific error types for better debugging
            if (e.name === "NetworkError" || e.message.includes("network")) {
              console.error(`Network error during reconnection: ${e.message}`);
            } else if (
              e.name === "TimeoutError" ||
              e.message.includes("timeout")
            ) {
              console.error(
                `Connection timeout during reconnection: ${e.message}`,
              );
            } else {
              console.error(
                `Unexpected error during reconnection: ${e.message}`,
              );
            }

            // Calculate exponential backoff with jitter
            const delay = Math.min(baseDelay * Math.pow(2, retry), maxDelay);
            // Add jitter to prevent thundering herd
            const jitter = (Math.random() * 2 - 1) * maxJitter * delay;
            const finalDelay = Math.max(100, delay + jitter);

            console.debug(
              `Waiting ${(finalDelay / 1000).toFixed(2)}s before next reconnection attempt`,
            );

            // Track the reconnection timeout to prevent leaks
            const timeoutId = setTimeout(async () => {
              this._reconnect_timeouts.delete(timeoutId);

              // Check if connection was restored externally
              if (
                this._websocket &&
                this._websocket.readyState === WebSocket.OPEN
              ) {
                console.info("Connection restored externally");
                this._reconnecting = false;
                return;
              }

              // Check if we were explicitly closed
              if (this._closed) {
                console.info("Connection was closed, stopping reconnection");
                this._reconnecting = false;
                return;
              }

              retry += 1;
              if (retry < MAX_RETRY) {
                await reconnect();
              } else {
                console.error(
                  `Failed to reconnect after ${MAX_RETRY} attempts, giving up.`,
                );
                this._closed = true;
                this._reconnecting = false;
                this._notifyDisconnected("Max reconnection attempts exceeded");
              }
            }, finalDelay);
            this._reconnect_timeouts.add(timeoutId);
          }
        };
        reconnect();
      }
    } else {
      // Clean up timers in all cases
      this._cleanup();
      this._notifyDisconnected(event.reason);
    }
  }

  async emit_message(data) {
    if (this._closed) {
      throw new Error("Connection is closed");
    }
    if (!this._websocket || this._websocket.readyState !== WebSocket.OPEN) {
      await this.open();
    }
    try {
      this._websocket.send(data);
    } catch (exp) {
      console.error(`Failed to send data, error: ${exp}`);
      throw exp;
    }
  }

  disconnect(reason) {
    this._closed = true;
    this._reconnecting = false;
    // Ensure websocket is closed if it exists and is not already closed or closing
    if (
      this._websocket &&
      this._websocket.readyState !== WebSocket.CLOSED &&
      this._websocket.readyState !== WebSocket.CLOSING
    ) {
      this._websocket.close(1000, reason);
    }
    // Use centralized cleanup to clear all timers
    this._cleanup();
    console.info(`WebSocket connection disconnected (${reason})`);
  }
}

function normalizeServerUrl(server_url) {
  if (!server_url) throw new Error("server_url is required");
  if (server_url.startsWith("http://")) {
    server_url =
      server_url.replace("http://", "ws://").replace(/\/$/, "") + "/ws";
  } else if (server_url.startsWith("https://")) {
    server_url =
      server_url.replace("https://", "wss://").replace(/\/$/, "") + "/ws";
  }
  return server_url;
}

/**
 * Login to the hypha server.
 *
 * Configuration options:
 *   server_url: The server URL (required)
 *   workspace: Target workspace (optional)
 *   login_service_id: Login service ID (default: "public/hypha-login")
 *   expires_in: Token expiration time (optional)
 *   login_timeout: Timeout for login process (default: 60)
 *   login_callback: Callback function for login URL (optional)
 *   profile: Whether to return user profile (optional)
 *   additional_headers: Additional HTTP headers (optional)
 *   transport: Transport type - "websocket" (default) or "http"
 */
export async function login(config) {
  const service_id = config.login_service_id || "public/hypha-login";
  const workspace = config.workspace;
  const expires_in = config.expires_in;
  const timeout = config.login_timeout || 60;
  const callback = config.login_callback;
  const profile = config.profile;
  const additional_headers = config.additional_headers;
  const transport = config.transport || "websocket";

  const server = await connectToServer({
    name: "initial login client",
    server_url: config.server_url,
    additional_headers: additional_headers,
    transport: transport,
  });
  try {
    const svc = await server.getService(service_id);
    assert(svc, `Failed to get the login service: ${service_id}`);
    let context;
    if (workspace) {
      context = await svc.start({ workspace, expires_in, _rkwargs: true });
    } else {
      context = await svc.start();
    }
    if (callback) {
      await callback(context);
    } else {
      console.log(`Please open your browser and login at ${context.login_url}`);
    }
    return await svc.check(context.key, { timeout, profile, _rkwargs: true });
  } catch (error) {
    throw error;
  } finally {
    await server.disconnect();
  }
}

/**
 * Logout from the hypha server.
 *
 * Configuration options:
 *   server_url: The server URL (required)
 *   login_service_id: Login service ID (default: "public/hypha-login")
 *   logout_callback: Callback function for logout URL (optional)
 *   additional_headers: Additional HTTP headers (optional)
 *   transport: Transport type - "websocket" (default) or "http"
 */
export async function logout(config) {
  const service_id = config.login_service_id || "public/hypha-login";
  const callback = config.logout_callback;
  const additional_headers = config.additional_headers;
  const transport = config.transport || "websocket";

  const server = await connectToServer({
    name: "initial logout client",
    server_url: config.server_url,
    additional_headers: additional_headers,
    transport: transport,
  });
  try {
    const svc = await server.getService(service_id);
    assert(svc, `Failed to get the login service: ${service_id}`);

    // Check if logout function exists for backward compatibility
    if (!svc.logout) {
      throw new Error(
        "Logout is not supported by this server. " +
          "Please upgrade the Hypha server to a version that supports logout.",
      );
    }

    const context = await svc.logout({});
    if (callback) {
      await callback(context);
    } else {
      console.log(
        `Please open your browser to logout at ${context.logout_url}`,
      );
    }
    return context;
  } catch (error) {
    throw error;
  } finally {
    await server.disconnect();
  }
}

async function webrtcGetService(wm, query, config) {
  config = config || {};
  // Default to "auto" since this wrapper is only used when connection was
  // established with webrtc: true
  const webrtc = config.webrtc !== undefined ? config.webrtc : "auto";
  const webrtc_config = config.webrtc_config;
  if (config.webrtc !== undefined) delete config.webrtc;
  if (config.webrtc_config !== undefined) delete config.webrtc_config;
  assert(
    [undefined, true, false, "auto"].includes(webrtc),
    "webrtc must be true, false or 'auto'",
  );

  const svc = await wm.getService(query, config);
  if (webrtc === true || webrtc === "auto") {
    if (svc.id.includes(":") && svc.id.includes("/")) {
      try {
        // Extract remote client_id from service id
        // svc.id format: "workspace/client_id:service_id"
        const wsAndClient = svc.id.split(":")[0]; // "workspace/client_id"
        const parts = wsAndClient.split("/");
        const remoteClientId = parts[parts.length - 1]; // "client_id"
        const remoteWorkspace = parts.slice(0, -1).join("/"); // "workspace"
        const remoteRtcServiceId = `${remoteWorkspace}/${remoteClientId}-rtc`;
        const peer = await getRTCService(wm, remoteRtcServiceId, webrtc_config);
        const rtcSvc = await peer.getService(svc.id.split(":")[1], config);
        rtcSvc._webrtc = true;
        rtcSvc._peer = peer;
        rtcSvc._service = svc;
        return rtcSvc;
      } catch (e) {
        console.warn(
          "Failed to get webrtc service, using websocket connection",
          e,
        );
      }
    }
    if (webrtc === true) {
      throw new Error("Failed to get the service via webrtc");
    }
  }
  return svc;
}

export async function connectToServer(config) {
  // Support HTTP transport via transport option
  const transport = config.transport || "websocket";
  if (transport === "http") {
    return await connectToServerHTTP(config);
  }

  if (config.server) {
    config.server_url = config.server_url || config.server.url;
    config.WebSocketClass =
      config.WebSocketClass || config.server.WebSocketClass;
  }
  let clientId = config.client_id;
  if (!clientId) {
    clientId = randId();
    config.client_id = clientId;
  }
  if (Object.keys(config).length === 0) {
    if (typeof process !== "undefined" && process.env) {
      // Node.js
      config.server_url = process.env.HYPHA_SERVER_URL;
      config.token = process.env.HYPHA_TOKEN;
      config.client_id = process.env.HYPHA_CLIENT_ID;
      config.workspace = process.env.HYPHA_WORKSPACE;
    } else if (typeof self !== "undefined" && self.env) {
      // WebWorker (only if you inject self.env manually)
      config.server_url = self.env.HYPHA_SERVER_URL;
      config.token = self.env.HYPHA_TOKEN;
      config.client_id = self.env.HYPHA_CLIENT_ID;
      config.workspace = self.env.HYPHA_WORKSPACE;
    } else if (typeof globalThis !== "undefined" && globalThis.env) {
      // Browser (only if you define globalThis.env beforehand)
      config.server_url = globalThis.env.HYPHA_SERVER_URL;
      config.token = globalThis.env.HYPHA_TOKEN;
      config.client_id = globalThis.env.HYPHA_CLIENT_ID;
      config.workspace = globalThis.env.HYPHA_WORKSPACE;
    }
  }

  let server_url = normalizeServerUrl(config.server_url);

  let connection = new WebsocketRPCConnection(
    server_url,
    clientId,
    config.workspace,
    config.token,
    config.reconnection_token,
    config.method_timeout || 60,
    config.WebSocketClass,
    config.token_refresh_interval,
    config.additional_headers,
  );
  const connection_info = await connection.open();
  assert(
    connection_info,
    "Failed to connect to the server, no connection info obtained. This issue is most likely due to an outdated Hypha server version. Please use `imjoy-rpc` for compatibility, or upgrade the Hypha server to the latest version.",
  );
  // wait for 0.5 seconds
  await new Promise((resolve) => setTimeout(resolve, 100));
  // Ensure manager_id is set before proceeding
  if (!connection.manager_id) {
    console.warn("Manager ID not set immediately, waiting...");

    // Wait for manager_id to be set with timeout
    const maxWaitTime = 5000; // 5 seconds
    const checkInterval = 100; // 100ms
    const startTime = Date.now();

    while (!connection.manager_id && Date.now() - startTime < maxWaitTime) {
      await new Promise((resolve) => setTimeout(resolve, checkInterval));
    }

    if (!connection.manager_id) {
      console.error("Manager ID still not set after waiting");
      throw new Error("Failed to get manager ID from server");
    } else {
      console.info(`Manager ID set after waiting: ${connection.manager_id}`);
    }
  }
  if (config.workspace && connection_info.workspace !== config.workspace) {
    throw new Error(
      `Connected to the wrong workspace: ${connection_info.workspace}, expected: ${config.workspace}`,
    );
  }

  const workspace = connection_info.workspace;
  const rpc = new RPC(connection, {
    client_id: clientId,
    workspace,
    default_context: { connection_type: "websocket" },
    name: config.name,
    method_timeout: config.method_timeout,
    app_id: config.app_id,
    server_base_url: connection_info.public_base_url,
    long_message_chunk_size: config.long_message_chunk_size,
    signing: config.signing || false,
    signing_private_key: config.signing_private_key || null,
    signing_public_key: config.signing_public_key || null,
  });
  await rpc.waitFor("services_registered", config.method_timeout || 120);
  const wm = await rpc.get_manager_service({
    timeout: config.method_timeout,
    case_conversion: "camel",
    kwargs_expansion: config.kwargs_expansion || false,
  });
  wm.rpc = rpc;

  async function _export(api) {
    api.id = "default";
    api.name = api.name || config.name || api.id;
    api.description = api.description || config.description;
    await rpc.register_service(api, { overwrite: true });
  }

  async function getApp(clientId) {
    clientId = clientId || "*";
    assert(!clientId.includes(":"), "clientId should not contain ':'");
    if (!clientId.includes("/")) {
      clientId = connection_info.workspace + "/" + clientId;
    }
    assert(
      clientId.split("/").length === 2,
      "clientId should match pattern workspace/clientId",
    );
    return await wm.getService(`${clientId}:default`);
  }

  async function listApps(ws) {
    ws = ws || workspace;
    assert(!ws.includes(":"), "workspace should not contain ':'");
    assert(!ws.includes("/"), "workspace should not contain '/'");
    const query = { workspace: ws, service_id: "default" };
    return await wm.listServices(query);
  }

  if (connection_info) {
    wm.config = Object.assign(wm.config, connection_info);
  }
  wm.export = schemaFunction(_export, {
    name: "export",
    description: "Export the api.",
    parameters: {
      properties: { api: { description: "The api to export", type: "object" } },
      required: ["api"],
      type: "object",
    },
  });
  wm.getApp = schemaFunction(getApp, {
    name: "getApp",
    description: "Get the app.",
    parameters: {
      properties: {
        clientId: { default: "*", description: "The clientId", type: "string" },
      },
      type: "object",
    },
  });
  wm.listApps = schemaFunction(listApps, {
    name: "listApps",
    description: "List the apps.",
    parameters: {
      properties: {
        workspace: {
          default: workspace,
          description: "The workspace",
          type: "string",
        },
      },
      type: "object",
    },
  });
  wm.disconnect = schemaFunction(rpc.disconnect.bind(rpc), {
    name: "disconnect",
    description: "Disconnect from the server.",
    parameters: { type: "object", properties: {}, required: [] },
  });
  wm.registerCodec = schemaFunction(rpc.register_codec.bind(rpc), {
    name: "registerCodec",
    description: "Register a codec for the webrtc connection",
    parameters: {
      type: "object",
      properties: {
        codec: {
          type: "object",
          description: "Codec to register",
          properties: {
            name: { type: "string" },
            type: {},
            encoder: { type: "function" },
            decoder: { type: "function" },
          },
        },
      },
    },
  });

  wm.emit = schemaFunction(rpc.emit.bind(rpc), {
    name: "emit",
    description: "Emit a message.",
    parameters: {
      properties: { data: { description: "The data to emit", type: "object" } },
      required: ["data"],
      type: "object",
    },
  });

  wm.on = schemaFunction(rpc.on.bind(rpc), {
    name: "on",
    description: "Register a message handler.",
    parameters: {
      properties: {
        event: { description: "The event to listen to", type: "string" },
        handler: { description: "The handler function", type: "function" },
      },
      required: ["event", "handler"],
      type: "object",
    },
  });

  wm.off = schemaFunction(rpc.off.bind(rpc), {
    name: "off",
    description: "Remove a message handler.",
    parameters: {
      properties: {
        event: { description: "The event to remove", type: "string" },
        handler: { description: "The handler function", type: "function" },
      },
      required: ["event", "handler"],
      type: "object",
    },
  });

  wm.once = schemaFunction(rpc.once.bind(rpc), {
    name: "once",
    description: "Register a one-time message handler.",
    parameters: {
      properties: {
        event: { description: "The event to listen to", type: "string" },
        handler: { description: "The handler function", type: "function" },
      },
      required: ["event", "handler"],
      type: "object",
    },
  });

  wm.getServiceSchema = schemaFunction(rpc.get_service_schema, {
    name: "getServiceSchema",
    description: "Get the service schema.",
    parameters: {
      properties: {
        service: {
          description: "The service to extract schema",
          type: "object",
        },
      },
      required: ["service"],
      type: "object",
    },
  });

  wm.registerService = schemaFunction(rpc.register_service.bind(rpc), {
    name: "registerService",
    description: "Register a service.",
    parameters: {
      properties: {
        service: { description: "The service to register", type: "object" },
        force: {
          default: false,
          description: "Force to register the service",
          type: "boolean",
        },
      },
      required: ["service"],
      type: "object",
    },
  });
  wm.unregisterService = schemaFunction(rpc.unregister_service.bind(rpc), {
    name: "unregisterService",
    description: "Unregister a service.",
    parameters: {
      properties: {
        service: {
          description: "The service id to unregister",
          type: "string",
        },
        notify: {
          default: true,
          description: "Notify the workspace manager",
          type: "boolean",
        },
      },
      required: ["service"],
      type: "object",
    },
  });
  if (connection.manager_id) {
    rpc.on("force-exit", async (message) => {
      if (message.from === "*/" + connection.manager_id) {
        console.log("Disconnecting from server, reason:", message.reason);
        await rpc.disconnect();
      }
    });
  }
  if (config.webrtc) {
    await registerRTCService(wm, `${clientId}-rtc`, config.webrtc_config);
    // make a copy of wm, so webrtc can use the original wm.getService
    const _wm = Object.assign({}, wm);
    const description = _wm.getService.__schema__.description;
    // TODO: Fix the schema for adding options for webrtc
    const parameters = _wm.getService.__schema__.parameters;
    wm.getService = schemaFunction(webrtcGetService.bind(null, _wm), {
      name: "getService",
      description,
      parameters,
    });

    wm.getRTCService = schemaFunction(getRTCService.bind(null, wm), {
      name: "getRTCService",
      description: "Get the webrtc connection, returns a peer connection.",
      parameters: {
        properties: {
          config: {
            description: "The config for the webrtc service",
            type: "object",
          },
        },
        required: ["config"],
        type: "object",
      },
    });
  } else {
    const _getService = wm.getService;
    wm.getService = (query, config) => {
      config = config || {};
      return _getService(query, config);
    };
    wm.getService.__schema__ = _getService.__schema__;
  }

  async function registerProbes(probes) {
    probes.id = "probes";
    probes.name = "Probes";
    probes.config = { visibility: "public" };
    probes.type = "probes";
    probes.description = `Probes Service, visit ${server_url}/${workspace}services/probes for the available probes.`;
    return await wm.registerService(probes, { overwrite: true });
  }

  wm.registerProbes = schemaFunction(registerProbes, {
    name: "registerProbes",
    description: "Register probes service",
    parameters: {
      properties: {
        probes: {
          description:
            "The probes to register, e.g. {'liveness': {'type': 'function', 'description': 'Check the liveness of the service'}}",
          type: "object",
        },
      },
      required: ["probes"],
      type: "object",
    },
  });
  return wm;
}

export async function getRemoteService(serviceUri, config = {}) {
  const { serverUrl, workspace, clientId, serviceId, appId } =
    parseServiceUrl(serviceUri);
  const fullServiceId = `${workspace}/${clientId}:${serviceId}@${appId}`;

  if (config.serverUrl) {
    if (config.serverUrl !== serverUrl) {
      throw new Error(
        "server_url in config does not match the server_url in the url",
      );
    }
  }
  config.serverUrl = serverUrl;
  const server = await connectToServer(config);
  return await server.getService(fullServiceId);
}

export class LocalWebSocket {
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
    this.postMessage = (message) => {
      if (isWindow) {
        window.parent.postMessage(message, "*");
      } else {
        self.postMessage(message);
      }
    };

    this.readyState = WebSocket.CONNECTING;
    this._context = context;
    this._messageListener = (event) => {
      const { type, data, to } = event.data;
      if (to !== this.client_id) {
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
    };
    context.addEventListener("message", this._messageListener, false);

    if (!this.client_id) throw new Error("client_id is required");
    if (!this.workspace) throw new Error("workspace is required");
    this.postMessage({
      type: "connect",
      url: this.url,
      from: this.client_id,
      workspace: this.workspace,
    });
  }

  send(data) {
    if (this.readyState === WebSocket.OPEN) {
      this.postMessage({
        type: "message",
        data: data,
        from: this.client_id,
        workspace: this.workspace,
      });
    }
  }

  close() {
    this.readyState = WebSocket.CLOSING;
    this.postMessage({
      type: "close",
      from: this.client_id,
      workspace: this.workspace,
    });
    if (this._context && this._messageListener) {
      this._context.removeEventListener(
        "message",
        this._messageListener,
        false,
      );
      this._messageListener = null;
    }
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

export function setupLocalClient({
  enable_execution = false,
  on_ready = null,
}) {
  return new Promise((resolve, reject) => {
    const context = typeof window !== "undefined" ? window : self;
    const isWindow = typeof window !== "undefined";
    context.addEventListener(
      "message",
      (event) => {
        const {
          type,
          server_url,
          workspace,
          client_id,
          token,
          method_timeout,
          name,
          config,
        } = event.data;

        if (type === "initializeHyphaClient") {
          if (!server_url || !workspace || !client_id) {
            console.error("server_url, workspace, and client_id are required.");
            return;
          }

          if (!server_url.startsWith("https://local-hypha-server:")) {
            console.error(
              "server_url should start with https://local-hypha-server:",
            );
            return;
          }

          class FixedLocalWebSocket extends LocalWebSocket {
            constructor(url) {
              // Call the parent class's constructor with fixed values
              super(url, client_id, workspace);
            }
          }
          connectToServer({
            server_url,
            workspace,
            client_id,
            token,
            method_timeout,
            name,
            WebSocketClass: FixedLocalWebSocket,
          }).then(async (server) => {
            globalThis.api = server;
            try {
              // for iframe
              if (isWindow && enable_execution) {
                function loadScript(script) {
                  return new Promise((resolve, reject) => {
                    const scriptElement = document.createElement("script");
                    scriptElement.innerHTML = script.content;
                    scriptElement.lang = script.lang;

                    scriptElement.onload = () => resolve();
                    scriptElement.onerror = (e) => reject(e);

                    document.head.appendChild(scriptElement);
                  });
                }
                if (config.styles && config.styles.length > 0) {
                  for (const style of config.styles) {
                    const styleElement = document.createElement("style");
                    styleElement.innerHTML = style.content;
                    styleElement.lang = style.lang;
                    document.head.appendChild(styleElement);
                  }
                }
                if (config.links && config.links.length > 0) {
                  for (const link of config.links) {
                    const linkElement = document.createElement("a");
                    linkElement.href = link.url;
                    linkElement.innerText = link.text;
                    document.body.appendChild(linkElement);
                  }
                }
                if (config.windows && config.windows.length > 0) {
                  for (const w of config.windows) {
                    document.body.innerHTML = w.content;
                    break;
                  }
                }
                if (config.scripts && config.scripts.length > 0) {
                  for (const script of config.scripts) {
                    if (script.lang !== "javascript")
                      throw new Error("Only javascript scripts are supported");
                    await loadScript(script); // Await the loading of each script
                  }
                }
              }
              // for web worker
              else if (
                !isWindow &&
                enable_execution &&
                config.scripts &&
                config.scripts.length > 0
              ) {
                for (const script of config.scripts) {
                  if (script.lang !== "javascript")
                    throw new Error("Only javascript scripts are supported");
                  eval(script.content);
                }
              }

              if (on_ready) {
                await on_ready(server, config);
              }
              resolve(server);
            } catch (e) {
              reject(e);
            }
          });
        }
      },
      false,
    );
    if (isWindow) {
      window.parent.postMessage({ type: "hyphaClientReady" }, "*");
    } else {
      self.postMessage({ type: "hyphaClientReady" });
    }
  });
}
