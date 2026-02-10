interface MessageEmitter {
  emit: (event: any) => void;
  on(event: string, handler: Function): void;
  once(event: string, handler: Function): void;
  off(event: string, handler?: Function): void;
}

interface RPC extends MessageEmitter {
  init(): void;
  setConfig(config: any): void;
  getRemoteCallStack(): any;
  getRemote(): any;
  setInterface(_interface: any, config?: any): Promise<void>;
  sendInterface(): void;
  disposeObject(obj: any): Promise<void>;
  requestRemote(): void;
  reset(): void;
  disconnect(): void;
}

interface hRPC extends MessageEmitter {
  register_codec(config: any): void;
  ping(client_id: any, timeout: any): Promise<any>;
  reset(): void;
  disconnect(): Promise<void>;
  get_manager_service(config?: any): Promise<void>;
  get_all_local_services(): any;
  get_local_service(service_id: any, context: any): any;
  get_remote_service(service_uri: any, config?: any): Promise<any>;
  add_service(api: any, overwrite?: any): any;
  register_service(
    api: any,
    config?: any,
    context?: any
  ): Promise<any>;
  unregister_service(service: any, notify?: any): Promise<void>;
  get_client_info(): any;
  encode(aObject: any, session_id: any): any;
  decode(aObject: any): Promise<any>;
}

interface HyphaServer {
  url: string;
  WebSocketClass: any;
}

/**
 * Transport type for Hypha RPC connections.
 * - "websocket": Traditional WebSocket connection (default)
 * - "http": HTTP streaming connection (more resilient to network issues)
 */
type TransportType = "websocket" | "http";

/**
 * Configuration for connecting to a Hypha server.
 */
interface ServerConfig {
  server?: HyphaServer;
  /** Server URL (e.g., "https://hypha.aicell.io") */
  server_url?: string;
  /** Alias for server_url */
  serverUrl?: string;
  /** Unique client identifier */
  client_id?: string;
  /** Alias for client_id */
  clientId?: string;
  /** Target workspace */
  workspace?: string;
  /** Authentication token */
  token?: string;
  /** Timeout for RPC method calls in seconds */
  method_timeout?: number;
  /** Client name for identification */
  name?: string;
  /** Custom WebSocket class (for WebSocket transport) */
  WebSocketClass?: any;
  /**
   * Transport type for the connection.
   * - "websocket" (default): Traditional WebSocket connection
   * - "http": HTTP streaming connection
   * If undefined or null, defaults to "websocket"
   */
  transport?: TransportType | null | undefined;
  /** Token for reconnection (internal use) */
  reconnection_token?: string;
  /** Interval for token refresh in seconds */
  token_refresh_interval?: number;
  /** Application ID */
  app_id?: string;
  /** Enable WebRTC support */
  webrtc?: boolean | "auto";
  /** WebRTC configuration */
  webrtc_config?: any;
}

interface LoginConfig {
  server_url: string;
  login_service_id?: string;
  login_timeout?: number;
  login_callback?: Function;
  workspace?: string;
  expires_in?: number;
  profile?: any;
  additional_headers?: any;
}

interface LogoutConfig {
  server_url: string;
  login_service_id?: string;
  logout_callback?: Function;
  additional_headers?: any;
}

interface API {
  id: string;
  name: string;
}

interface FunctionAnnotation {
  schema_type?: string;
  name: string;
  description: string;
  parameters: any;
}

/**
 * HTTP Streaming RPC Connection class.
 * Uses HTTP GET with streaming for receiving messages and HTTP POST for sending.
 */
interface HTTPStreamingRPCConnection {
  new (
    server_url: string,
    client_id: string,
    workspace?: string | null,
    token?: string | null,
    reconnection_token?: string | null,
    timeout?: number,
    token_refresh_interval?: number
  ): HTTPStreamingRPCConnection;

  on_message(handler: Function): void;
  on_disconnected(handler: Function): void;
  on_connected(handler: Function): void;
  open(): Promise<any>;
  emit_message(data: any): Promise<boolean>;
  set_reconnection(value: boolean): void;
  disconnect(reason?: string): Promise<void>;

  connection_info: any;
  manager_id: string | null;
}

/**
 * Workspace Manager returned from connectToServer.
 */
interface WorkspaceManager {
  rpc: hRPC;
  config: any;

  // Service management
  registerService(service: any, config?: any): Promise<any>;
  unregisterService(service: string, notify?: boolean): Promise<void>;
  getService(query: string | any, config?: any): Promise<any>;
  listServices(query?: any): Promise<any[]>;

  // App management
  export(api: API): Promise<void>;
  getApp(clientId?: string): Promise<any>;
  listApps(workspace?: string): Promise<any[]>;

  // Connection management
  disconnect(): Promise<void>;

  // Events
  emit(data: any): void;
  on(event: string, handler: Function): void;
  off(event: string, handler?: Function): void;
  once(event: string, handler: Function): void;

  // Utilities
  registerCodec(codec: any): void;
  getServiceSchema(service: any): any;
  registerProbes(probes: any): Promise<any>;

  // WebRTC (if enabled)
  getRTCService?(config: any): Promise<any>;
}

/**
 * The hyphaWebsocketClient namespace containing all Hypha RPC exports.
 */
interface HyphaWebsocketClient {
  RPC: hRPC;
  API_VERSION: string;

  /**
   * Annotate a function with JSON Schema for RPC.
   */
  schemaFunction: (func: Function, annotation: FunctionAnnotation) => Function;

  /**
   * Load JavaScript requirements dynamically.
   */
  loadRequirements: (config: any) => Promise<any>;

  /**
   * Login to Hypha server via OAuth.
   */
  login: (config: LoginConfig) => Promise<any>;

  /**
   * Logout from Hypha server.
   */
  logout: (config: LogoutConfig) => Promise<any>;

  /**
   * Connect to a Hypha server with unified transport support.
   * Supports both WebSocket (default) and HTTP streaming transports.
   *
   * @example
   * // WebSocket transport (default)
   * const server = await connectToServer({ server_url: "https://hypha.aicell.io" });
   *
   * // HTTP streaming transport
   * const server = await connectToServer({
   *   server_url: "https://hypha.aicell.io",
   *   transport: "http"
   * });
   */
  connectToServer: (config: ServerConfig) => Promise<WorkspaceManager>;

  /**
   * Connect using HTTP streaming transport explicitly.
   */
  connectToServerHTTP: (config: ServerConfig) => Promise<WorkspaceManager>;

  /**
   * Get a remote service by URI.
   */
  getRemoteService: (serviceUri: string, config?: ServerConfig) => Promise<any>;

  /**
   * Get a remote service using HTTP streaming transport explicitly.
   */
  getRemoteServiceHTTP: (serviceUri: string, config?: ServerConfig) => Promise<any>;

  /**
   * Register a WebRTC service for peer-to-peer communication.
   */
  registerRTCService: (server: WorkspaceManager, service_id: string, config?: any) => Promise<any>;

  /**
   * Get a WebRTC service for peer-to-peer communication.
   */
  getRTCService: (server: WorkspaceManager, service_id: string, config?: any) => Promise<any>;

  /**
   * Setup local client for iframe/web worker communication.
   */
  setupLocalClient: (config: {
    enable_execution?: boolean;
    on_ready?: Function;
  }) => Promise<WorkspaceManager>;

  /**
   * LocalWebSocket class for iframe/web worker communication.
   */
  LocalWebSocket: any;

  /**
   * HTTP Streaming RPC Connection class.
   */
  HTTPStreamingRPCConnection: HTTPStreamingRPCConnection;

  /**
   * Normalize server URL for HTTP transport.
   */
  normalizeServerUrlHTTP: (server_url: string) => string;
}

declare module "hypha-rpc" {
  export const hyphaWebsocketClient: HyphaWebsocketClient;
}
