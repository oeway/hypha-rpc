// Shared types and interfaces to prevent circular dependencies
// These are extracted from index.ts to break the circular dependency

// Events enum
export enum KernelEvents {
  // IOPub Channel Messages
  STREAM = "stream",
  DISPLAY_DATA = "display_data",
  UPDATE_DISPLAY_DATA = "update_display_data",
  EXECUTE_RESULT = "execute_result",
  EXECUTE_ERROR = "execute_error",
  EXECUTE_REQUEST = "execute_request",
  
  // Input request
  INPUT_REQUEST = "input_request",
  
  // Output control
  CLEAR_OUTPUT = "clear_output",
  
  // Comm messages
  COMM_OPEN = "comm_open",
  COMM_MSG = "comm_msg",
  COMM_CLOSE = "comm_close",
  
  // Internal Events
  KERNEL_READY = "kernel_ready",
  KERNEL_BUSY = "kernel_busy",
  KERNEL_IDLE = "kernel_idle",
  
  // Special catchall for internal use
  ALL = "*", // Wildcard event type
  
  // Execution monitoring events
  EXECUTION_STALLED = "execution_stalled",
  
  // Enhanced stuck kernel handling events
  KERNEL_UNRECOVERABLE = "kernel_unrecoverable",
  EXECUTION_INTERRUPTED = "execution_interrupted",
  KERNEL_RESTARTED = "kernel_restarted",
  KERNEL_TERMINATED = "kernel_terminated"
}

// EventEmitter interface for typing
export interface IEventEmitter {
  on(eventName: string, listener: Function): void;
  off(eventName: string, listener: Function): void;
  emit(eventName: string, ...args: any[]): void;
  setMaxListeners(n: number): void;
}

// Filesystem mount options
export interface IFilesystemMountOptions {
  enabled?: boolean;
  root?: string;
  mountPoint?: string;
}

// Kernel options interface
export interface IKernelOptions {
  filesystem?: IFilesystemMountOptions;
  env?: Record<string, string>; // Environment variables to set in the kernel
  lockFileURL?: string; // URL to pyodide-lock.json file for faster loading
}

// Kernel interface
export interface IKernel extends IEventEmitter {
  initialize(options?: IKernelOptions): Promise<void>;
  execute(code: string, parent?: any): Promise<{ success: boolean, result?: any, error?: Error }>;
  executeStream?(code: string, parent?: any): AsyncGenerator<any, { success: boolean, result?: any, error?: Error }, void>;
  isInitialized(): boolean;
  inputReply(content: { value: string }): Promise<void>;
  getStatus(): Promise<"active" | "busy" | "unknown">;
  
  // Interrupt functionality
  interrupt?(): Promise<boolean>;
  setInterruptBuffer?(buffer: Uint8Array): void;
  
  // Optional methods
  complete?(code: string, cursor_pos: number, parent?: any): Promise<any>;
  inspect?(code: string, cursor_pos: number, detail_level: 0 | 1, parent?: any): Promise<any>;
  isComplete?(code: string, parent?: any): Promise<any>;
  commInfo?(target_name: string | null, parent?: any): Promise<any>;
  commOpen?(content: any, parent?: any): Promise<void>;
  commMsg?(content: any, parent?: any): Promise<void>;
  commClose?(content: any, parent?: any): Promise<void>;
}

// Execute options interface
export interface IKernelExecuteOptions {
  code: string;
  silent?: boolean;
  storeHistory?: boolean;
}

// Message interface
export interface IMessage {
  type: string;
  bundle?: any;
  content?: any;
  metadata?: any;
  parentHeader?: any;
  buffers?: any;
  ident?: any;
}

// Event data interface
export interface IEventData {
  type: string;
  data: any;
} 