// Web Worker file for running the Kernel in a separate thread
// Import necessary modules
import * as Comlink from "comlink";
import { KernelEvents, IKernelOptions } from "./types";
import { Kernel } from "./index";

// Use a browser-compatible EventEmitter
class EventEmitter {
  public events: { [key: string]: Function[] } = {};

  on(eventName: string, listener: Function) {
    if (!this.events[eventName]) {
      this.events[eventName] = [];
    }
    this.events[eventName].push(listener);
  }

  off(eventName: string, listener: Function) {
    if (!this.events[eventName]) return;
    const index = this.events[eventName].indexOf(listener);
    if (index > -1) {
      this.events[eventName].splice(index, 1);
    }
  }

  emit(eventName: string, ...args: any[]) {
    if (!this.events[eventName]) return;
    this.events[eventName].forEach(listener => listener(...args));
  }
}


// Create a new kernel instance
const kernel = new Kernel();

// Variable to store the event port
let eventPort: MessagePort | null = null;

// Store kernel initialization options
let kernelOptions: IKernelOptions = {};

// Track current event listeners for cleanup
let currentEventListeners: Map<string, (data: any) => void> = new Map();

// Interrupt handling for worker
let interruptBuffer: Uint8Array | null = null;

// Helper function to check if an error is a KeyboardInterrupt
function isKeyboardInterrupt(error: any): boolean {
  return error && 
         typeof error === 'object' && 
         (error.type === "KeyboardInterrupt" || 
          (error.message && error.message.includes("KeyboardInterrupt")));
}

// Helper function to create KeyboardInterrupt error result
function createKeyboardInterruptResult() {
  return {
    success: false,
    error: new Error("KeyboardInterrupt: Execution interrupted by user"),
    result: {
      payload: [],
      status: "error",
      ename: "KeyboardInterrupt",
      evalue: "Execution interrupted by user",
      traceback: ["KeyboardInterrupt: Execution interrupted by user"]
    }
  };
}

// Global error handlers to prevent worker crashes
self.addEventListener("error", (event) => {
  console.error("[WORKER] Global error caught:", event.error);
  event.preventDefault();
});

self.addEventListener("unhandledrejection", (event) => {
  if (isKeyboardInterrupt(event.reason)) {
    console.log("[WORKER] KeyboardInterrupt caught in unhandled rejection handler - this is expected during interrupts");
    
    // Send interrupt acknowledgment if we have an event port
    if (eventPort) {
      eventPort.postMessage({
        type: KernelEvents.EXECUTE_ERROR,
        data: {
          ename: "KeyboardInterrupt",
          evalue: "Execution interrupted by user",
          traceback: ["KeyboardInterrupt: Execution interrupted by user"]
        }
      });
    }
  } else {
    console.error("[WORKER] Unhandled promise rejection:", event.reason);
  }
  
  event.preventDefault();
});

// Listen for messages to set up the event port and initialize kernel
self.addEventListener("message", (event) => {
  if (event.data?.type === "SET_EVENT_PORT" && event.data?.port) {
    // Clean up old event listeners and port before setting up new ones
    cleanupEventForwarding();
    
    // Set the new port
    eventPort = event.data.port;
    
    // If the kernel is already initialized, set up event forwarding immediately
    if (kernel.isInitialized()) {
      setupEventForwarding();
    }
  } else if (event.data?.type === "INITIALIZE_KERNEL") {
    // Save the options for kernel initialization
    kernelOptions = event.data.options || {};
    
    // Initialize the kernel with the provided options
    initializeKernel(kernelOptions).catch(error => {
      console.error("[WORKER] Error initializing kernel in worker:", error);
      if (eventPort) {
        eventPort.postMessage({
          type: KernelEvents.EXECUTE_ERROR,
          data: {
            ename: "WorkerInitError",
            evalue: `Failed to initialize kernel: ${error.message}`,
            traceback: [error.stack || ""]
          }
        });
      }
    });
  } else if (event.data?.type === "SET_INTERRUPT_BUFFER") {
    // Handle interrupt buffer setup
    interruptBuffer = event.data.buffer;
    
    // Set the interrupt buffer in the kernel if it's initialized
    if (kernel.isInitialized() && interruptBuffer && typeof kernel.setInterruptBuffer === 'function') {
      kernel.setInterruptBuffer(interruptBuffer);
      console.log("[WORKER] Interrupt buffer set in pyodide kernel");
    } else if (interruptBuffer) {
      console.log("[WORKER] Interrupt buffer stored, will be set when kernel initializes");
    }
    
    const responseMessage = {
      type: "INTERRUPT_BUFFER_SET",
      data: { success: true }
    };
    
    // Send response on both channels to ensure it's received
    if (eventPort) {
      eventPort.postMessage(responseMessage);
    }
    
    // Also send on main worker channel in case eventPort isn't set up yet
    self.postMessage(responseMessage);
    
  } else if (event.data?.type === "INTERRUPT_KERNEL") {
    // Handle interrupt request
    
    if (interruptBuffer) {
      // Set interrupt signal (2 = SIGINT)
      interruptBuffer[0] = 2;
      
      const responseMessage = {
        type: "INTERRUPT_TRIGGERED",
        data: { success: true, method: "buffer" }
      };
      
      // Send response on both channels
      if (eventPort) {
        eventPort.postMessage(responseMessage);
      }
      self.postMessage(responseMessage);
      
    } else {
      console.log("[WORKER] No interrupt buffer available, trying kernel.interrupt()");
      
      // Fallback to kernel interrupt method
      if (typeof kernel.interrupt === 'function') {
        kernel.interrupt().then(success => {
          const responseMessage = {
            type: "INTERRUPT_TRIGGERED",
            data: { success, method: "kernel" }
          };
          
          if (eventPort) {
            eventPort.postMessage(responseMessage);
          }
          self.postMessage(responseMessage);
        }).catch(error => {
          console.error("[WORKER] Error during kernel interrupt:", error);
          const responseMessage = {
            type: "INTERRUPT_TRIGGERED",
            data: { success: false, error: error.message, method: "kernel" }
          };
          
          if (eventPort) {
            eventPort.postMessage(responseMessage);
          }
          self.postMessage(responseMessage);
        });
      } else {
        console.warn("[WORKER] No interrupt method available");
        const responseMessage = {
          type: "INTERRUPT_TRIGGERED",
          data: { success: false, error: "No interrupt method available", method: "none" }
        };
        
        if (eventPort) {
          eventPort.postMessage(responseMessage);
        }
        self.postMessage(responseMessage);
      }
    }
  }
});

// Initialize the kernel with provided options
async function initializeKernel(options: IKernelOptions): Promise<void> {
  try {
    await kernel.initialize(options);
    
    // Set up the interrupt buffer if it's available and the kernel supports it
    if (interruptBuffer && typeof kernel.setInterruptBuffer === 'function') {
      kernel.setInterruptBuffer(interruptBuffer);
    }
    
    // Set up event forwarding AFTER kernel is initialized
    setupEventForwarding();
    
    if (eventPort) {
      eventPort.postMessage({
        type: "KERNEL_INITIALIZED",
        data: { success: true }
      });
    }
  } catch (error) {
    console.error("Kernel initialization failed:", error);
    throw error;
  }
}

// Clean up old event listeners and port
function cleanupEventForwarding() {
  if (currentEventListeners.size > 0) {
    // Remove all current event listeners
    for (const [eventType, listener] of currentEventListeners.entries()) {
      (kernel as unknown as EventEmitter).off(eventType, listener);
    }
    
    // Clear the listeners map
    currentEventListeners.clear();
  }
  
  // Close the old port if it exists
  if (eventPort) {
    eventPort.close();
    eventPort = null;
  }
}

// Set up event forwarding from kernel to main thread
function setupEventForwarding() {
  if (!eventPort) {
    console.error("[WORKER] Cannot set up event forwarding: no event port available");
    return;
  }

  // Forward all kernel events to the main thread
  Object.values(KernelEvents).forEach((eventType) => {
    // Create a listener function for this event type
    const listener = (data: any) => {
      if (eventPort) {
        // Send just the event type and raw data
        // This matches the structure used in main thread mode
        eventPort.postMessage({
          type: eventType,
          data: data
        });
      }
    };
    
    // Store the listener for later cleanup
    currentEventListeners.set(eventType, listener);
    
    // Add the listener to the kernel
    (kernel as unknown as EventEmitter).on(eventType, listener);
  });
}

// Handle cleanup when worker is terminated
self.addEventListener("beforeunload", async () => {
  // Close any resources or connections
  try {
    // Send a final message before termination if needed
    if (eventPort) {
      eventPort.postMessage({
        type: "WORKER_TERMINATING",
        data: { message: "Worker is shutting down" }
      });
    }
  } catch (error) {
    console.error("Error during worker cleanup:", error);
  }
});

// Log available methods for debugging

// Create a simplified proxy that only exposes the methods we need
// We're not trying to implement the full EventEmitter interface
const simpleProxy = {
  // Required methods from IKernel interface
  initialize: async (options?: IKernelOptions) => {
    try {
      await kernel.initialize(options);
      return undefined;
    } catch (error) {
      console.error("[WORKER] Initialize error:", error);
      throw error;
    }
  },
  
  execute: async (code: string, parent?: any) => {
    try {
      const result = await kernel.execute(code, parent);
      return result;
    } catch (error) {
      console.error("[WORKER] Execute error:", error);
      
      // Check if this is a KeyboardInterrupt and handle it specially
      if (isKeyboardInterrupt(error)) {
        console.log("[WORKER] KeyboardInterrupt caught in execute method");
        return createKeyboardInterruptResult();
      }
      
      // Handle other errors normally
      return {
        success: false,
        error: error instanceof Error ? error : new Error(String(error)),
        result: {
          payload: [],
          status: "error",
          ename: error instanceof Error ? error.constructor.name : "Error",
          evalue: error instanceof Error ? error.message : String(error),
          traceback: error instanceof Error && error.stack ? error.stack.split('\n') : [String(error)]
        }
      };
    }
  },
  
  isInitialized: () => {
    try {
      const result = kernel.isInitialized();
      return result;
    } catch (error) {
      console.error("[WORKER] IsInitialized error:", error);
      return false;
    }
  },
  
  inputReply: async (content: { value: string }) => {
    try {
      await kernel.inputReply(content);
    } catch (error) {
      console.error("[WORKER] InputReply error:", error);
      throw error;
    }
  },
  
  // Async method for status
  getStatus: async () => {
    try {
      const status = await kernel.getStatus();
      return status;
    } catch (error) {
      console.error("[WORKER] getStatus error:", error);
      return "unknown";
    }
  },
  
  // Interrupt functionality
  interrupt: async () => {
    try {
      if (typeof kernel.interrupt === 'function') {
        const result = await kernel.interrupt();
        return result;
      } else {
        console.warn("[WORKER] Kernel does not support interrupt method");
        return false;
      }
    } catch (error) {
      console.error("[WORKER] Interrupt error:", error);
      // Don't let interrupt errors crash the worker
      return false;
    }
  },
  
  setInterruptBuffer: (buffer: Uint8Array) => {
    try {
      if (typeof kernel.setInterruptBuffer === 'function') {
        kernel.setInterruptBuffer(buffer);
        return true;
      } else {
        console.warn("[WORKER] Kernel does not support setInterruptBuffer method");
        return false;
      }
    } catch (error) {
      console.error("[WORKER] setInterruptBuffer error:", error);
      return false;
    }
  },
  
  // Completion methods
  complete: async (code: string, cursor_pos: number, parent?: any) => {
    try {
      if (typeof kernel.complete === 'function') {
        const result = await kernel.complete(code, cursor_pos, parent);
        return result;
      } else {
        console.warn("[WORKER] Kernel does not support complete method");
        return { status: 'error', error: 'Completion not supported' };
      }
    } catch (error) {
      console.error("[WORKER] Complete error:", error);
      return { status: 'error', error: String(error) };
    }
  },
  
  inspect: async (code: string, cursor_pos: number, detail_level: 0 | 1, parent?: any) => {
    try {
      if (typeof kernel.inspect === 'function') {
        const result = await kernel.inspect(code, cursor_pos, detail_level, parent);
        return result;
      } else {
        console.warn("[WORKER] Kernel does not support inspect method");
        return { status: 'error', error: 'Inspection not supported' };
      }
    } catch (error) {
      console.error("[WORKER] Inspect error:", error);
      return { status: 'error', error: String(error) };
    }
  },
  
  isComplete: async (code: string, parent?: any) => {
    try {
      if (typeof kernel.isComplete === 'function') {
        const result = await kernel.isComplete(code, parent);
        return result;
      } else {
        console.warn("[WORKER] Kernel does not support isComplete method");
        return { status: 'unknown' };
      }
    } catch (error) {
      console.error("[WORKER] IsComplete error:", error);
      return { status: 'error', error: String(error) };
    }
  },
  
  // Comm methods
  commInfo: async (target_name: string | null, parent?: any) => {
    try {
      if (typeof kernel.commInfo === 'function') {
        const result = await kernel.commInfo(target_name, parent);
        return result;
      } else {
        console.warn("[WORKER] Kernel does not support commInfo method");
        return { comms: {}, status: 'ok' };
      }
    } catch (error) {
      console.error("[WORKER] CommInfo error:", error);
      return { comms: {}, status: 'error', error: String(error) };
    }
  },
  
  commOpen: async (content: any, parent?: any) => {
    try {
      if (typeof kernel.commOpen === 'function') {
        const result = await kernel.commOpen(content, parent);
        return result;
      } else {
        console.warn("[WORKER] Kernel does not support commOpen method");
        return undefined;
      }
    } catch (error) {
      console.error("[WORKER] CommOpen error:", error);
      return undefined;
    }
  },
  
  commMsg: async (content: any, parent?: any) => {
    try {
      if (typeof kernel.commMsg === 'function') {
        const result = await kernel.commMsg(content, parent);
        return result;
      } else {
        console.warn("[WORKER] Kernel does not support commMsg method");
        return undefined;
      }
    } catch (error) {
      console.error("[WORKER] CommMsg error:", error);
      return undefined;
    }
  },
  
  commClose: async (content: any, parent?: any) => {
    try {
      if (typeof kernel.commClose === 'function') {
        const result = await kernel.commClose(content, parent);
        return result;
      } else {
        console.warn("[WORKER] Kernel does not support commClose method");
        return undefined;
      }
    } catch (error) {
      console.error("[WORKER] CommClose error:", error);
      return undefined;
    }
  }
};

// Expose the proxy through Comlink
try {
  Comlink.expose(simpleProxy);
} catch (error) {
  console.error("Error exposing proxy:", error);
} 