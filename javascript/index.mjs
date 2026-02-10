import * as hyphaWebsocketClient from "./dist/hypha-rpc-websocket.mjs";

// Primary export: the hyphaWebsocketClient namespace (most common pattern)
export { hyphaWebsocketClient };

// Re-export all individual members for backward compatibility with the old
// "module" entry point (dist/hypha-rpc-websocket.mjs) which exported them
// as top-level named exports, and for direct import convenience.
export {
  RPC,
  API_VERSION,
  schemaFunction,
  loadRequirements,
  getRTCService,
  registerRTCService,
  HTTPStreamingRPCConnection,
  connectToServerHTTP,
  getRemoteServiceHTTP,
  normalizeServerUrlHTTP,
  connectToServer,
  getRemoteService,
  login,
  logout,
  setupLocalClient,
  LocalWebSocket,
} from "./dist/hypha-rpc-websocket.mjs";
