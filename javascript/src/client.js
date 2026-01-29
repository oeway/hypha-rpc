/**
 * Hypha RPC client - unified connection interface.
 *
 * This module provides the main entry point for connecting to Hypha servers.
 * It supports multiple transport types:
 * - "websocket" (default): Traditional WebSocket connection
 * - "http": HTTP streaming connection (more resilient to network issues)
 */

import { parseServiceUrl } from "./utils/index.js";

/**
 * Connect to a Hypha server.
 *
 * @param {Object} config - Configuration object with connection options
 * @param {string} config.serverUrl - The server URL (required, alias: server_url)
 * @param {string} config.workspace - Target workspace (optional)
 * @param {string} config.token - Authentication token (optional)
 * @param {string} config.clientId - Unique client identifier (optional, alias: client_id)
 * @param {string} config.transport - Transport type - "websocket" (default) or "http"
 * @param {number} config.method_timeout - Timeout for RPC method calls
 * @returns {Promise<Object>} Connected workspace manager
 *
 * @example
 * const server = await connectToServer({
 *   serverUrl: "https://hypha.aicell.io",
 *   transport: "http"  // or "websocket" (default)
 * });
 * await server.registerService({ id: "my-service", ... });
 */
export async function connectToServer(config = {}) {
  const transport = config.transport || "websocket";

  if (transport === "http") {
    const { _connectToServerHTTP } = await import("./http-client.js");
    return await _connectToServerHTTP(config);
  } else {
    const { _connectToServer } = await import("./websocket-client.js");
    return await _connectToServer(config);
  }
}

/**
 * Get a remote service by URI.
 *
 * @param {string} serviceUri - Service URI in format "server_url/workspace/client_id:service_id"
 * @param {Object} config - Additional configuration options
 * @returns {Promise<Object>} The remote service
 *
 * @example
 * const service = await getRemoteService(
 *   "https://hypha.aicell.io/public/client:service",
 *   { transport: "http" }
 * );
 * const result = await service.someMethod();
 */
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
