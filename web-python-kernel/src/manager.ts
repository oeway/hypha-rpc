// Kernel Manager for Deno App Engine
// This file manages kernel instances in either main thread or worker mode

import * as Comlink from "comlink";
// @ts-ignore Importing from npm
// Use a browser-compatible EventEmitter
class EventEmitter {
  private events: { [key: string]: Function[] } = {};

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

  removeListener(eventName: string, listener: Function) {
    this.off(eventName, listener);
  }

  emit(eventName: string, ...args: any[]) {
    if (!this.events[eventName]) return;
    this.events[eventName].forEach(listener => listener(...args));
  }

  setMaxListeners(n: number) {
    // No-op for browser compatibility
  }
}
import { KernelEvents, IKernel, IKernelOptions, IFilesystemMountOptions } from "./types";
import { Kernel } from "./index";

// Re-export KernelEvents for test usage
export { KernelEvents };

// Execution mode enum
export enum KernelMode {
  MAIN_THREAD = "main_thread",
  WORKER = "worker"
}

// Kernel language enum
export enum KernelLanguage {
  PYTHON = "python"
}

// Extended WorkerOptions interface to include Deno permissions
interface WorkerOptions {
  type?: "classic" | "module";
  name?: string;
  deno?: {
    permissions?: IDenoPermissions;
  };
}

// Interface for kernel pool configuration
export interface IKernelPoolConfig {
  enabled: boolean;
  poolSize: number; // Number of kernels to keep ready per configuration
  autoRefill: boolean; // Whether to automatically refill the pool when kernels are taken
  preloadConfigs: Array<{
    mode: KernelMode;
    language: KernelLanguage;
  }>; // Configurations to preload in the pool
}

// Interface for kernel manager options
export interface IKernelManagerOptions {
  pool?: IKernelPoolConfig;
  allowedKernelTypes?: Array<{
    mode: KernelMode;
    language: KernelLanguage;
  }>; // Restrict which kernel types can be created
  interruptionMode?: 'shared-array-buffer' | 'kernel-interrupt' | 'auto'; // Default: 'auto'
}

// Interface for kernel instance
export interface IKernelInstance {
  id: string;
  kernel: IKernel;
  mode: KernelMode;
  language: KernelLanguage;
  worker?: Worker;
  created: Date;
  options: IManagerKernelOptions;
  isFromPool?: boolean; // Track if this kernel came from the pool
  destroy(): Promise<void>;
}

// Interface for Deno worker permissions
export interface IDenoPermissions {
  read?: (string | URL)[];
  write?: (string | URL)[];
  net?: string[];
  env?: string[];
  run?: string[];
  ffi?: string[];
  hrtime?: boolean;
}

// Interface for kernel creation options
export interface IManagerKernelOptions {
  id?: string;
  mode?: KernelMode;
  lang?: KernelLanguage;
  namespace?: string;
  deno?: {
    permissions?: IDenoPermissions;
  };
  filesystem?: IFilesystemMountOptions;
  env?: Record<string, string>; // Environment variables to set in the kernel
  lockFileURL?: string; // URL to pyodide-lock.json file for faster loading
  inactivityTimeout?: number; // Time in milliseconds after which an inactive kernel will be shut down
  maxExecutionTime?: number; // Maximum time in milliseconds a single execution can run before considered stuck/dead
}

// Helper type for listener management
type ListenerWrapper = {
  original: (data: any) => void;
  wrapped: (event: { kernelId: string, data: any }) => void;
};

/**
 * KernelManager class manages multiple kernel instances 
 * in either main thread or worker mode
 */
export class KernelManager extends EventEmitter {
  private kernels: Map<string, IKernelInstance> = new Map();
  // Track listeners for each kernel to enable individual removal
  private listenerWrappers: Map<string, Map<string, Map<Function, ListenerWrapper>>> = new Map();
  // Track last activity time for each kernel
  private lastActivityTime: Map<string, number> = new Map();
  // Store inactivity timers for each kernel
  private inactivityTimers: Map<string, any> = new Map();
  // Track ongoing executions for each kernel
  private ongoingExecutions: Map<string, Set<string>> = new Map();
  // Track execution timeouts for detecting stuck/dead kernels
  private executionTimeouts: Map<string, Map<string, any>> = new Map();
  // Track execution start times for accurate duration calculation
  private executionStartTimes: Map<string, Map<string, number>> = new Map();
  // Track execution metadata for better monitoring
  private executionMetadata: Map<string, Map<string, { startTime: number; code?: string; timeoutId?: any }>> = new Map();
  
  // Track AbortControllers for each kernel's ongoing operations
  private abortControllers: Map<string, Map<string, AbortController>> = new Map();
  
  // Pool management - now using promises for immediate response
  private pool: Map<string, Promise<IKernelInstance>[]> = new Map();
  private poolConfig: IKernelPoolConfig;
  private isPreloading: boolean = false;
  // Track which pool keys are currently being prefilled to prevent duplicates
  private prefillingInProgress: Map<string, boolean> = new Map();
  
  // Allowed kernel types configuration
  private allowedKernelTypes: Array<{
    mode: KernelMode;
    language: KernelLanguage;
  }>;
  
  // Interrupt buffers for worker kernels (using SharedArrayBuffer)
  private interruptBuffers: Map<string, Uint8Array> = new Map();
  
  // Interruption mode configuration
  private interruptionMode: 'shared-array-buffer' | 'kernel-interrupt' | 'auto';
  
  /**
   * Helper function to check if an error is a KeyboardInterrupt
   * @private
   */
  private isKeyboardInterrupt(error: any): boolean {
    return error && 
           typeof error === 'object' && 
           (('type' in error && error.type === "KeyboardInterrupt") ||
            ('message' in error && typeof error.message === 'string' && error.message.includes("KeyboardInterrupt")));
  }
  
  /**
   * Helper function to create a standardized KeyboardInterrupt error result
   * @private
   */
  private createKeyboardInterruptResult(): { success: boolean; error: Error; result: any } {
    return {
      success: false,
      error: new Error("KeyboardInterrupt: Execution interrupted by user"),
      result: {
        status: "error",
        ename: "KeyboardInterrupt",
        evalue: "Execution interrupted by user",
        traceback: ["KeyboardInterrupt: Execution interrupted by user"]
      }
    };
  }
  
  /**
   * Store an AbortController for a specific kernel execution
   * @private
   */
  private storeAbortController(kernelId: string, executionId: string, controller: AbortController): void {
    if (!this.abortControllers.has(kernelId)) {
      this.abortControllers.set(kernelId, new Map());
    }
    this.abortControllers.get(kernelId)!.set(executionId, controller);
  }

  /**
   * Remove and return an AbortController for a specific kernel execution
   * @private
   */
  private removeAbortController(kernelId: string, executionId: string): AbortController | undefined {
    const kernelControllers = this.abortControllers.get(kernelId);
    if (!kernelControllers) return undefined;
    
    const controller = kernelControllers.get(executionId);
    if (controller) {
      kernelControllers.delete(executionId);
      if (kernelControllers.size === 0) {
        this.abortControllers.delete(kernelId);
      }
    }
    return controller;
  }

  /**
   * Abort all ongoing operations for a specific kernel
   * @private
   */
  private abortAllKernelOperations(kernelId: string): void {
    const kernelControllers = this.abortControllers.get(kernelId);
    if (!kernelControllers) return;

    for (const [executionId, controller] of kernelControllers) {
      try {
        controller.abort();
        console.log(`🚫 Aborted execution ${executionId} for kernel ${kernelId}`);
      } catch (error) {
        console.warn(`⚠️ Error aborting execution ${executionId}:`, error);
      }
    }
    
    // Clear all controllers for this kernel
    this.abortControllers.delete(kernelId);
  }
  
  constructor(options: IKernelManagerOptions = {}) {
    super();
    super.setMaxListeners(100); // Allow many listeners for kernel events
    
    // Set interruption mode (default to 'auto')
    this.interruptionMode = options.interruptionMode || 'auto';
    
    // Set default allowed kernel types (worker mode only for security)
    this.allowedKernelTypes = options.allowedKernelTypes || [
      { mode: KernelMode.WORKER, language: KernelLanguage.PYTHON }
    ];
    
    // Initialize pool configuration with defaults based on allowed types
    const defaultPreloadConfigs = this.allowedKernelTypes.filter(type => 
      type.language === KernelLanguage.PYTHON // Only preload Python kernels by default
    );
    
    this.poolConfig = {
      enabled: false,
      poolSize: 2,
      autoRefill: true,
      preloadConfigs: defaultPreloadConfigs,
      ...options.pool
    };
    
    // Validate that pool preload configs are within allowed types
    if (this.poolConfig.preloadConfigs) {
      this.poolConfig.preloadConfigs = this.poolConfig.preloadConfigs.filter(config => {
        const isAllowed = this.isKernelTypeAllowed(config.mode, config.language);
        if (!isAllowed) {
          console.warn(`Pool preload config ${config.mode}-${config.language} is not in allowedKernelTypes, skipping`);
        }
        return isAllowed;
      });
    }
    
    // Start preloading if pool is enabled
    if (this.poolConfig.enabled) {
      this.preloadPool().catch(error => {
        console.error("Error preloading kernel pool:", error);
      });
    }
  }
  
  
  /**
   * Generate a pool key for a given mode and language combination
   * @param mode Kernel mode
   * @param language Kernel language
   * @returns Pool key string
   * @private
   */
  private getPoolKey(mode: KernelMode, language: KernelLanguage): string {
    return `${mode}-${language}`;
  }
  
  /**
   * Get a kernel promise from the pool if available
   * @param mode Kernel mode
   * @param language Kernel language
   * @returns Kernel promise or null if none available
   * @private
   */
  private getFromPool(mode: KernelMode, language: KernelLanguage): Promise<IKernelInstance> | null {
    if (!this.poolConfig.enabled) {
      return null;
    }
    
    const poolKey = this.getPoolKey(mode, language);
    const poolPromises = this.pool.get(poolKey);
    
    if (!poolPromises || poolPromises.length === 0) {
      return null;
    }
    
    // Remove and return the first promise from the pool (FIFO)
    const kernelPromise = poolPromises.shift()!;
    
    // Immediately trigger background refill to add one promise back
    if (this.poolConfig.autoRefill) {
      setTimeout(() => {
        this.refillPoolSingle(mode, language).catch(error => {
          console.error(`Error refilling single kernel for ${poolKey}:`, error);
        });
      }, 0);
    }
    
    return kernelPromise;
  }
  
  /**
   * Add a kernel promise to the pool
   * @param mode Kernel mode
   * @param language Kernel language
   * @param kernelPromise Kernel promise
   * @private
   */
  private addToPool(mode: KernelMode, language: KernelLanguage, kernelPromise: Promise<IKernelInstance>): void {
    if (!this.poolConfig.enabled) {
      return;
    }
    
    const poolKey = this.getPoolKey(mode, language);
    
    if (!this.pool.has(poolKey)) {
      this.pool.set(poolKey, []);
    }
    
    const poolPromises = this.pool.get(poolKey)!;
    
    // Only add if we haven't reached the pool size limit
    if (poolPromises.length < this.poolConfig.poolSize) {
      poolPromises.push(kernelPromise);
      
      // Handle promise rejection to prevent unhandled rejections
      kernelPromise.catch(error => {
        console.error(`Pool kernel promise rejected for ${poolKey}:`, error);
        // Remove the failed promise from the pool
        const index = poolPromises.indexOf(kernelPromise);
        if (index !== -1) {
          poolPromises.splice(index, 1);
        }
      });
    } else {
      // Pool is full, let the excess promise resolve and then destroy the kernel
      kernelPromise.then(kernel => {
        kernel.destroy().catch(error => {
          console.error("Error destroying excess pool kernel:", error);
        });
      }).catch(error => {
        console.error("Excess pool kernel promise rejected:", error);
      });
    }
  }
  
  /**
   * Refill the pool with a single kernel promise
   * @param mode Kernel mode
   * @param language Kernel language
   * @private
   */
  private async refillPoolSingle(mode: KernelMode, language: KernelLanguage): Promise<void> {
    if (!this.poolConfig.enabled) {
      return;
    }
    
    const poolKey = this.getPoolKey(mode, language);
    const poolPromises = this.pool.get(poolKey) || [];
    
    // Only add one if we're below the pool size
    if (poolPromises.length < this.poolConfig.poolSize) {
      const kernelPromise = this.createPoolKernelPromise(mode, language);
      this.addToPool(mode, language, kernelPromise);
    }
  }

  /**
   * Refill the pool for a specific configuration with parallel creation
   * @param mode Kernel mode
   * @param language Kernel language
   * @private
   */
  private async refillPool(mode: KernelMode, language: KernelLanguage): Promise<void> {
    if (!this.poolConfig.enabled) {
      return;
    }
    
    const poolKey = this.getPoolKey(mode, language);
    
    // Check if already prefilling this pool key to prevent duplicates
    if (this.prefillingInProgress.get(poolKey)) {
      return;
    }
    
    // Set prefilling flag
    this.prefillingInProgress.set(poolKey, true);
    
    try {
      const poolPromises = this.pool.get(poolKey) || [];
      const needed = this.poolConfig.poolSize - poolPromises.length;
      
      if (needed <= 0) {
        return;
      }
      
      // Create all needed kernel promises in parallel
      const newPromises = Array.from({ length: needed }, () => 
        this.createPoolKernelPromise(mode, language)
      );
      
      // Add all promises to the pool
      for (const kernelPromise of newPromises) {
        this.addToPool(mode, language, kernelPromise);
      }
      
    } catch (error) {
      console.error(`Error refilling pool for ${poolKey}:`, error);
    } finally {
      // Always clear the prefilling flag
      this.prefillingInProgress.set(poolKey, false);
    }
  }
  
  /**
   * Create a kernel promise for the pool
   * @param mode Kernel mode
   * @param language Kernel language
   * @returns Promise that resolves to a kernel instance
   * @private
   */
  private createPoolKernelPromise(mode: KernelMode, language: KernelLanguage): Promise<IKernelInstance> {
    return new Promise(async (resolve, reject) => {
      try {
        const kernel = await this.createPoolKernel(mode, language);
        // Mark as taken from pool
        kernel.isFromPool = true;
        resolve(kernel);
      } catch (error) {
        console.error(`Error creating pool kernel for ${mode}-${language}:`, error);
        reject(error);
      }
    });
  }

  /**
   * Create a kernel specifically for the pool
   * @param mode Kernel mode
   * @param language Kernel language
   * @returns Kernel instance
   * @private
   */
  private async createPoolKernel(mode: KernelMode, language: KernelLanguage): Promise<IKernelInstance> {
    // Generate a temporary ID for the pool kernel
    const tempId = `pool-${crypto.randomUUID()}`;
    
    // Create kernel with minimal configuration
    const options: IManagerKernelOptions = {
      mode,
      lang: language
    };
    
    // Store options temporarily - but don't store incomplete instance in kernels map
    // Instead, we'll pass the options directly to the creation methods
    let instance: IKernelInstance;
    
    try {
      if (mode === KernelMode.MAIN_THREAD) {
        // For main thread, we need to temporarily store the instance for createMainThreadKernel
        const tempInstance = {
          id: tempId,
          options,
          mode,
          language
        };
        this.kernels.set(tempId, tempInstance as unknown as IKernelInstance);
        
        try {
          instance = await this.createMainThreadKernel(tempId);
        } finally {
          // Always clean up the temporary instance
          this.kernels.delete(tempId);
        }
      } else {
        // For worker mode, we need to temporarily store the instance for createWorkerKernel
        const tempInstance = {
          id: tempId,
          options,
          mode,
          language
        };
        this.kernels.set(tempId, tempInstance as unknown as IKernelInstance);
        
        try {
          instance = await this.createWorkerKernel(tempId);
        } finally {
          // Always clean up the temporary instance
          this.kernels.delete(tempId);
        }
      }
    } catch (error) {
      // Ensure cleanup on any error
      this.kernels.delete(tempId);
      throw error;
    }
    
    return instance;
  }
  
  /**
   * Preload the kernel pool with configured kernel types
   * @private
   */
  private async preloadPool(): Promise<void> {
    if (!this.poolConfig.enabled || this.isPreloading) {
      return;
    }
    
    this.isPreloading = true;
    
    try {
      // Preload kernels for each configured type
      for (const config of this.poolConfig.preloadConfigs) {
        try {
          await this.refillPool(config.mode, config.language);
        } catch (error) {
          console.error(`Error preloading ${config.mode}-${config.language}:`, error);
          // Continue with other configurations
        }
      }
    } catch (error) {
      console.error("Error during kernel pool preloading:", error);
    } finally {
      this.isPreloading = false;
    }
  }
  
  /**
   * Check if a kernel request can use the pool
   * @param options Kernel creation options
   * @returns True if the request can use pool
   * @private
   */
  private canUsePool(options: IManagerKernelOptions): boolean {
    // Don't use pool if it's disabled
    if (!this.poolConfig.enabled) {
      return false;
    }
    
    // Don't use pool if custom filesystem or permissions are specified
    if (options.filesystem || options.deno?.permissions) {
      return false;
    }
    
    // Don't use pool if custom timeouts are specified
    if (options.inactivityTimeout !== undefined || options.maxExecutionTime !== undefined) {
      return false;
    }
    
    return true;
  }
  
  /**
   * Reassign a pool kernel with new ID and options
   * @param poolKernel Kernel from pool
   * @param newId New kernel ID
   * @param options Kernel options
   * @returns Updated kernel instance
   * @private
   */
  private reassignPoolKernel(
    poolKernel: IKernelInstance, 
    newId: string, 
    options: IManagerKernelOptions
  ): IKernelInstance {
    // Create a new instance object explicitly to avoid spread operator issues
    const updatedInstance: IKernelInstance = {
      id: newId,
      kernel: poolKernel.kernel,
      mode: poolKernel.mode,
      language: poolKernel.language,
      worker: poolKernel.worker,
      created: new Date(), // Update creation time
      options: { ...poolKernel.options, ...options },
      isFromPool: true,
      destroy: poolKernel.destroy // Preserve the original destroy function
    };
    
    // Verify the destroy function is properly set
    if (typeof updatedInstance.destroy !== 'function') {
      console.error('Failed to preserve destroy function during pool kernel reassignment');
      console.error('poolKernel.destroy type:', typeof poolKernel.destroy);
      console.error('updatedInstance.destroy type:', typeof updatedInstance.destroy);
      throw new Error(`Failed to preserve destroy function during pool kernel reassignment`);
    }
    
    return updatedInstance;
  }
  
  /**
   * Get pool statistics for debugging/monitoring
   * @returns Pool statistics
   */
  public getPoolStats(): Record<string, { available: number; total: number }> {
    const stats: Record<string, { available: number; total: number }> = {};
    
    for (const [poolKey, promises] of this.pool.entries()) {
      stats[poolKey] = {
        available: promises.length,
        total: this.poolConfig.poolSize
      };
    }
    
    return stats;
  }
  
  /**
   * Get pool configuration information
   * @returns Pool configuration details
   */
  public getPoolConfig(): {
    enabled: boolean;
    poolSize: number;
    autoRefill: boolean;
    preloadConfigs: Array<{
      mode: KernelMode;
      language: KernelLanguage;
    }>;
    isPreloading: boolean;
  } {
    return {
      enabled: this.poolConfig.enabled,
      poolSize: this.poolConfig.poolSize,
      autoRefill: this.poolConfig.autoRefill,
      preloadConfigs: [...this.poolConfig.preloadConfigs], // Return a copy to prevent modification
      isPreloading: this.isPreloading
    };
  }
  
  /**
   * Create a new kernel instance
   * @param options Options for creating the kernel
   * @param options.id Optional custom ID for the kernel
   * @param options.mode Optional kernel mode (main_thread or worker)
   * @param options.lang Optional kernel language (python or typescript)
   * @param options.namespace Optional namespace prefix for the kernel ID
   * @param options.deno.permissions Optional Deno permissions for worker mode
   * @param options.filesystem Optional filesystem mounting options
   * @param options.inactivityTimeout Optional timeout in ms after which an inactive kernel will be shut down
   * @param options.maxExecutionTime Optional maximum time in ms an execution can run before considered stuck
   * @returns Promise resolving to the kernel instance ID
   */
  public async createKernel(options: IManagerKernelOptions = {}): Promise<string> {
    // make sure the options.id does not contain colons because it will be used as a namespace prefix
    if (options.id && options.id.includes(':')) {
      throw new Error('Kernel ID cannot contain colons');
    }
    const baseId = options.id || crypto.randomUUID();
    const mode = options.mode || KernelMode.WORKER;
    const language = options.lang || KernelLanguage.PYTHON;
    
    // Check if the requested kernel type is allowed
    if (!this.isKernelTypeAllowed(mode, language)) {
      throw new Error(`Kernel type ${mode}-${language} is not allowed. Allowed types: ${
        this.allowedKernelTypes.map(t => `${t.mode}-${t.language}`).join(', ')
      }`);
    }
    
    // Apply namespace prefix if provided
    const id = options.namespace ? `${options.namespace}:${baseId}` : baseId;
    
    // Check if kernel with this ID already exists
    if (this.kernels.has(id)) {
      throw new Error(`Kernel with ID ${id} already exists`);
    }
    
    // Try to get from pool if possible
    if (this.canUsePool(options)) {
      const poolKey = this.getPoolKey(mode, language);
      
      // Check if this kernel type is configured for pooling
      const isPooledType = this.poolConfig.preloadConfigs.some(config => 
        config.mode === mode && config.language === language
      );
      
      if (isPooledType) {
        // First try to get from existing pool
        let poolKernelPromise = this.getFromPool(mode, language);
        
        if (poolKernelPromise) {
          return await this.setupPoolKernelFromPromise(poolKernelPromise, id, options);
        }
        
        // Pool is empty, but this type should be pooled
        // Create a new promise immediately and trigger background refill
        try {
          // Create a new kernel promise specifically for this request
          const newKernelPromise = this.createPoolKernelPromise(mode, language);
          
          // Trigger background refill to replenish the pool for future requests
          if (this.poolConfig.autoRefill) {
            setTimeout(() => {
              this.refillPool(mode, language).catch(error => {
                console.error(`Error refilling exhausted pool for ${poolKey}:`, error);
              });
            }, 0);
          }
          
          return await this.setupPoolKernelFromPromise(newKernelPromise, id, options);
        } catch (error) {
          console.error(`Failed to create kernel promise for exhausted pool: ${error}`);
          // Fall through to on-demand creation as last resort
        }
      } else {
        // This kernel type is not configured for pooling, try to get from pool anyway
        // in case there are kernels available from previous configurations
        const poolKernelPromise = this.getFromPool(mode, language);
        if (poolKernelPromise) {
          return await this.setupPoolKernelFromPromise(poolKernelPromise, id, options);
        }
      }
    }
    
    // Fall back to creating a new kernel on-demand
    return this.createOnDemandKernel(id, mode, language, options);
  }
  
  /**
   * Setup a pool kernel from a promise with new ID and options
   * @param poolKernelPromise Kernel promise from pool
   * @param id New kernel ID
   * @param options Kernel options
   * @returns Kernel ID (returned after kernel is ready)
   * @private
   */
  private async setupPoolKernelFromPromise(
    poolKernelPromise: Promise<IKernelInstance>, 
    id: string, 
    options: IManagerKernelOptions
  ): Promise<string> {
    try {
      // Wait for the pool kernel to be ready
      const poolKernel = await poolKernelPromise;
      
      // Reassign the pool kernel with the new ID and options
      const instance = this.reassignPoolKernel(poolKernel, id, options);
      
      // For worker kernels, we need to recreate the event handler with the new ID
      if (instance.mode === KernelMode.WORKER && instance.worker) {
        // Get the worker and create new message channel
        const worker = instance.worker;
        
        // Create a new message channel for the reassigned kernel
        const { port1, port2 } = new MessageChannel();
        
        // Send the new event port to the worker
        worker.postMessage({
          type: "SET_EVENT_PORT",
          port: port2
        }, [port2]);
        
        // Create a new event handler with the correct kernel ID
        const eventHandler = (event: MessageEvent) => {
          if (event.data && event.data.type) {
            // Emit the event from the manager with kernel ID
            // This structure matches the setupEventForwarding method for main thread kernels
            super.emit(event.data.type, {
              kernelId: id,
              data: event.data.data
            });
          }
        };
        
        // Listen for events from the worker with the new handler
        port1.addEventListener('message', eventHandler);
        port1.start();
        
        // Update the destroy function to clean up the new event handler
        const originalDestroy = instance.destroy;
        instance.destroy = async () => {
          port1.removeEventListener('message', eventHandler);
          port1.close();
          return originalDestroy();
        };
      }
      
      // Store the kernel instance
      this.kernels.set(id, instance);
      
      // Forward kernel events to manager (for main thread kernels)
      this.setupEventForwarding(instance);
      
      // Initialize activity tracking
      this.updateKernelActivity(id);
      
      // Set up inactivity timeout if specified and greater than 0
      if (options.inactivityTimeout && options.inactivityTimeout > 0) {
        this.setupInactivityTimeout(id, options.inactivityTimeout);
      }
      
      // Setup handlers for stalled executions if maxExecutionTime is specified
      if (options.maxExecutionTime && options.maxExecutionTime > 0) {
        this.setupStalledExecutionHandler(id);
      }
      
      return id;
    } catch (error) {
      console.error(`Error setting up pool kernel ${id}:`, error);
      // Emit an error event for this kernel
      super.emit(KernelEvents.EXECUTE_ERROR, {
        kernelId: id,
        data: {
          ename: "KernelSetupError",
          evalue: `Failed to setup kernel: ${error instanceof Error ? error.message : String(error)}`,
          traceback: [error instanceof Error ? (error.stack || error.message) : String(error)]
        }
      });
      throw error; // Re-throw to let the caller handle it
    }
  }

  /**
   * Setup a pool kernel with new ID and options (for already resolved kernels)
   * @param poolKernel Kernel from pool
   * @param id New kernel ID
   * @param options Kernel options
   * @returns Kernel ID
   * @private
   */
  private setupPoolKernel(
    poolKernel: IKernelInstance, 
    id: string, 
    options: IManagerKernelOptions
  ): string {
    // Reassign the pool kernel with the new ID and options
    const instance = this.reassignPoolKernel(poolKernel, id, options);
    
    // For worker kernels, we need to recreate the event handler with the new ID
    if (instance.mode === KernelMode.WORKER && instance.worker) {
      // Get the worker and create new message channel
      const worker = instance.worker;
      
      // Create a new message channel for the reassigned kernel
      const { port1, port2 } = new MessageChannel();
      
      // Send the new event port to the worker
      worker.postMessage({
        type: "SET_EVENT_PORT",
        port: port2
      }, [port2]);
      
      // Create a new event handler with the correct kernel ID
      const eventHandler = (event: MessageEvent) => {
        if (event.data && event.data.type) {
          // Emit the event from the manager with kernel ID
          // This structure matches the setupEventForwarding method for main thread kernels
          super.emit(event.data.type, {
            kernelId: id,
            data: event.data.data
          });
        }
      };
      
      // Listen for events from the worker with the new handler
      port1.addEventListener('message', eventHandler);
      port1.start();
      
      // Update the destroy function to clean up the new event handler
      const originalDestroy = instance.destroy;
      instance.destroy = async () => {
        port1.removeEventListener('message', eventHandler);
        port1.close();
        return originalDestroy();
      };
    }
    
    // Store the kernel instance
    this.kernels.set(id, instance);
    
    // Forward kernel events to manager (for main thread kernels)
    this.setupEventForwarding(instance);
    
    // Initialize activity tracking
    this.updateKernelActivity(id);
    
    // Set up inactivity timeout if specified and greater than 0
    if (options.inactivityTimeout && options.inactivityTimeout > 0) {
      this.setupInactivityTimeout(id, options.inactivityTimeout);
    }
    
    // Setup handlers for stalled executions if maxExecutionTime is specified
    if (options.maxExecutionTime && options.maxExecutionTime > 0) {
      this.setupStalledExecutionHandler(id);
    }
    
    return id;
  }
  
  /**
   * Create a kernel on-demand (not from pool)
   * @param id Kernel ID
   * @param mode Kernel mode
   * @param language Kernel language
   * @param options Kernel options
   * @returns Kernel ID
   * @private
   */
  private async createOnDemandKernel(
    id: string, 
    mode: KernelMode, 
    language: KernelLanguage, 
    options: IManagerKernelOptions
  ): Promise<string> {
    // Store options temporarily to be used in createWorkerKernel
    const tempInstance = {
      id,
      options: { ...options, lang: language },
      mode,
      language
    };
    this.kernels.set(id, tempInstance as unknown as IKernelInstance);
    
    // Create the appropriate kernel instance
    let instance: IKernelInstance;
    
    if (mode === KernelMode.MAIN_THREAD) {
      instance = await this.createMainThreadKernel(id);
    } else {
      instance = await this.createWorkerKernel(id);
    }
    
    // Store the kernel instance
    this.kernels.set(id, instance);
    
    // Forward kernel events to manager
    this.setupEventForwarding(instance);
    
    // Initialize activity tracking
    this.updateKernelActivity(id);
    
    // Set up inactivity timeout if specified and greater than 0
    if (options.inactivityTimeout && options.inactivityTimeout > 0) {
      this.setupInactivityTimeout(id, options.inactivityTimeout);
    }
    
    // Setup handlers for stalled executions if maxExecutionTime is specified
    if (options.maxExecutionTime && options.maxExecutionTime > 0) {
      this.setupStalledExecutionHandler(id);
    }
    
    return id;
  }
  
  /**
   * Create a kernel instance running in the main thread
   * @param id Kernel ID
   * @returns Kernel instance
   */
  private async createMainThreadKernel(id: string): Promise<IKernelInstance> {
    // Get options from the temporary instance
    const options = this.kernels.get(id)?.options || {};
    const language = options.lang || KernelLanguage.PYTHON;
    
    // Create the Python kernel
    const kernel = new Kernel();
    
    // Create the kernel instance
    const instance: IKernelInstance = {
      id,
      kernel,
      mode: KernelMode.MAIN_THREAD,
      language,
      created: new Date(),
      options,
      destroy: async () => {
        // Nothing special to do for main thread kernel
        return Promise.resolve();
      }
    };
    
    // Initialize the kernel with filesystem options
    const kernelOptions: IKernelOptions = {};
    
    // Add filesystem options if provided
    if (options.filesystem) {
      kernelOptions.filesystem = options.filesystem;
    }
    
    // Add environment variables if provided
    if (options.env) {
      kernelOptions.env = options.env;
    }
    
    // Add lockFileURL if provided
    if (options.lockFileURL) {
      kernelOptions.lockFileURL = options.lockFileURL;
    }
    
    // Initialize the kernel
    await kernel.initialize(kernelOptions);
    
    return instance;
  }
  
  /**
   * Create a kernel instance running in a worker
   * @param id Kernel ID
   * @returns Kernel instance
   */
  private async createWorkerKernel(id: string): Promise<IKernelInstance> {
    // Get permissions from options when creating the kernel
    const options = this.kernels.get(id)?.options || {};
    const language = options.lang || KernelLanguage.PYTHON;
    
    // Create a new worker with optional permissions
    const workerOptions: WorkerOptions = {
      type: "module",
    };
    
    // If Deno permissions are provided, use them.
    // Otherwise don't specify Deno permissions at all to inherit from host script
    if (options.deno?.permissions) {
      workerOptions.deno = {
        permissions: options.deno.permissions
      };
    }
    
    // Create worker by loading the compiled worker bundle
    // The worker bundle will be available as a separate file after webpack compilation
    // Use relative path from the dist directory where the main bundle is served
    const workerPath = './dist/kernel.worker.js';
    const worker = new Worker(workerPath, { type: 'classic' });
    
    // Create a message channel for events
    const { port1, port2 } = new MessageChannel();
    
    // Create a promise that will resolve when the kernel is initialized
    const initPromise = new Promise<void>((resolve, reject) => {
      const initHandler = (event: MessageEvent) => {
        if (event.data?.type === "KERNEL_INITIALIZED") {
          if (event.data.data.success) {
            port1.removeEventListener('message', initHandler);
            resolve();
          } else {
            port1.removeEventListener('message', initHandler);
            reject(new Error("Kernel initialization failed"));
          }
        }
      };
      port1.addEventListener('message', initHandler);
    });
    
    // Send the port to the worker
    worker.postMessage({ type: "SET_EVENT_PORT", port: port2 }, [port2]);
    
    // Create a proxy to the worker using Comlink
    const kernelProxy = Comlink.wrap<IKernel>(worker);
    
    // Add a local event handler to bridge the worker events
    // This works around the limitation that Comlink doesn't proxy event emitters
    const eventHandler = (event: MessageEvent) => {
      if (event.data && event.data.type) {
        // Emit the event from the manager with kernel ID
        // This structure matches the setupEventForwarding method for main thread kernels
        super.emit(event.data.type, {
          kernelId: id,
          data: event.data.data
        });
      }
    };
    
    // Listen for events from the worker
    port1.addEventListener('message', eventHandler);
    port1.start();
    
    // Initialize the kernel with filesystem options
    // We need to pass these options to the worker
    worker.postMessage({
      type: "INITIALIZE_KERNEL",
      options: {
        filesystem: options.filesystem,
        env: options.env,
        lockFileURL: options.lockFileURL,
        lang: language
      }
    });
    
    // Wait for kernel initialization
    await initPromise;
    
    // Set up interrupt buffer automatically for worker kernels
    await this.setupWorkerInterruptBuffer(id, worker);
    
    // Create the kernel instance
    const instance: IKernelInstance = {
      id,
      kernel: {
        // Map methods from the Comlink proxy to the IKernel interface
        initialize: async (options?: IKernelOptions) => {
          return kernelProxy.initialize(options);
        },
        execute: async (code: string, parent?: any) => {
          const result = await kernelProxy.execute(code, parent);
          
          // Handle Python worker results (no special display reconstruction needed)
          
          return result;
        },
        isInitialized: () => {
          return kernelProxy.isInitialized();
        },
        inputReply: async (content: { value: string }) => {
          return kernelProxy.inputReply(content);
        },
        // Map async getStatus method
        getStatus: async () => {
          try {
            if (typeof kernelProxy.getStatus === 'function') {
              return await kernelProxy.getStatus();
            } else {
              return "unknown";
            }
          } catch (error) {
            return "unknown";
          }
        },
        // Map completion methods
        complete: async (code: string, cursor_pos: number, parent?: any) => {
          try {
            if (typeof kernelProxy.complete === 'function') {
              return await kernelProxy.complete(code, cursor_pos, parent);
            } else {
              return { status: 'error', error: 'Completion not supported' };
            }
          } catch (error) {
            return { status: 'error', error: String(error) };
          }
        },
        inspect: async (code: string, cursor_pos: number, detail_level: 0 | 1, parent?: any) => {
          try {
            if (typeof kernelProxy.inspect === 'function') {
              return await kernelProxy.inspect(code, cursor_pos, detail_level, parent);
            } else {
              return { status: 'error', error: 'Inspection not supported' };
            }
          } catch (error) {
            return { status: 'error', error: String(error) };
          }
        },
        isComplete: async (code: string, parent?: any) => {
          try {
            if (typeof kernelProxy.isComplete === 'function') {
              return await kernelProxy.isComplete(code, parent);
            } else {
              return { status: 'unknown' };
            }
          } catch (error) {
            return { status: 'error', error: String(error) };
          }
        },
        // Map interrupt methods
        interrupt: async () => {
          try {
            if (typeof kernelProxy.interrupt === 'function') {
              return await kernelProxy.interrupt();
            } else {
              return false;
            }
          } catch (error) {
            return false;
          }
        },
        setInterruptBuffer: (buffer: Uint8Array) => {
          try {
            if (typeof kernelProxy.setInterruptBuffer === 'function') {
              kernelProxy.setInterruptBuffer(buffer);
            }
          } catch (error) {
            console.warn('Failed to set interrupt buffer:', error);
          }
        },
        // Map comm methods
        commInfo: async (target_name: string | null, parent?: any) => {
          try {
            if (typeof kernelProxy.commInfo === 'function') {
              return await kernelProxy.commInfo(target_name, parent);
            } else {
              return { comms: {}, status: 'ok' };
            }
          } catch (error) {
            return { comms: {}, status: 'error', error: String(error) };
          }
        },
        commOpen: async (content: any, parent?: any) => {
          try {
            if (typeof kernelProxy.commOpen === 'function') {
              return await kernelProxy.commOpen(content, parent);
            }
          } catch (error) {
            console.warn('Failed to open comm:', error);
          }
        },
        commMsg: async (content: any, parent?: any) => {
          try {
            if (typeof kernelProxy.commMsg === 'function') {
              return await kernelProxy.commMsg(content, parent);
            }
          } catch (error) {
            console.warn('Failed to send comm message:', error);
          }
        },
        commClose: async (content: any, parent?: any) => {
          try {
            if (typeof kernelProxy.commClose === 'function') {
              return await kernelProxy.commClose(content, parent);
            }
          } catch (error) {
            console.warn('Failed to close comm:', error);
          }
        }
      } as unknown as IKernel,
      mode: KernelMode.WORKER,
      language,
      worker,
      created: new Date(),
      options, // Store the options for reference
      destroy: async () => {
        // Clean up the worker and event listeners
        port1.removeEventListener('message', eventHandler);
        port1.close();
        worker.terminate();
        return Promise.resolve();
      }
    };
    
    return instance;
  }
  
  /**
   * Setup event forwarding from kernel to manager
   * @param instance Kernel instance
   */
  private setupEventForwarding(instance: IKernelInstance): void {
    // Only needed for main thread kernels as worker events are handled directly
    if (instance.mode === KernelMode.MAIN_THREAD) {
      // Forward all kernel events to the manager with kernel ID
      Object.values(KernelEvents).forEach((eventType) => {
        // Access the kernel as a Kernel instance which extends EventEmitter
        const kernelEmitter = instance.kernel as unknown as EventEmitter;
        
        // Add event listener to forward events
        kernelEmitter.on(eventType, (data: any) => {
          super.emit(eventType, {
            kernelId: instance.id,
            data
          });
        });
      });
    }
  }
  
  /**
   * Get a kernel instance by ID
   * @param id Kernel ID
   * @returns Kernel instance or undefined if not found
   */
  public getKernel(id: string): IKernelInstance | undefined {
    return this.kernels.get(id);
  }
  
  /**
   * Get a list of all kernel IDs
   * @returns Array of kernel IDs
   */
  public getKernelIds(): string[] {
    return Array.from(this.kernels.keys());
  }
  
  /**
   * Get a list of all kernels with their details
   * @param namespace Optional namespace to filter kernels by
   * @returns Array of kernel information objects
   */
  public async listKernels(namespace?: string): Promise<Array<{
    id: string;
    mode: KernelMode;
    language: KernelLanguage;
    status: "active" | "busy" | "unknown";
    created: Date;
    namespace?: string;
    deno?: {
      permissions?: IDenoPermissions;
    };
  }>> {
          const filteredKernels = Array.from(this.kernels.entries())
        .filter(([id]) => {
          // Filter out pool kernels (temporary kernels with IDs starting with "pool-")
          if (id.startsWith("pool-")) return false;
          
          if (!namespace) return true;
          return id.startsWith(`${namespace}:`);
        });

      // Use Promise.all to get all statuses concurrently
      const kernelInfos = await Promise.all(
        filteredKernels.map(async ([id, instance]) => {
          // Extract namespace from id if present
          const namespaceMatch = id.match(/^([^:]+):/);
          const extractedNamespace = namespaceMatch ? namespaceMatch[1] : undefined;
          
          // Get status using async getStatus method
          let status: "active" | "busy" | "unknown" = "unknown";
          try {
            if (instance && instance.kernel && typeof instance.kernel.getStatus === 'function') {
              status = await instance.kernel.getStatus();
            }
          } catch (error) {
            console.warn(`Error getting status for kernel ${id}:`, error);
            status = "unknown";
          }
          
          return {
            id,
            mode: instance.mode,
            language: instance.language,
            status,
            created: instance.created || new Date(),
            namespace: extractedNamespace,
            deno: instance.options?.deno
          };
        })
      );

      return kernelInfos;
  }
  
  /**
   * Destroy a kernel instance
   * @param id Kernel ID
   * @returns Promise resolving when kernel is destroyed
   */
  public async destroyKernel(id: string): Promise<void> {
    const instance = this.kernels.get(id);
    
    if (!instance) {
      // Handle gracefully - kernel may already be destroyed
      return;
    }
    
    // Verify the destroy function exists
    if (typeof instance.destroy !== 'function') {
      throw new Error(`Kernel ${id} is missing destroy function (type: ${typeof instance.destroy})`);
    }
    
    // Abort all ongoing operations for this kernel first
    this.abortAllKernelOperations(id);
    
    // Clear any inactivity timer
    this.clearInactivityTimeout(id);
    
    // Clean up execution timeouts
    if (this.executionTimeouts.has(id)) {
      const timeouts = this.executionTimeouts.get(id)!;
      for (const timeoutId of timeouts.values()) {
        clearTimeout(timeoutId);
      }
      this.executionTimeouts.delete(id);
    }
    
    // Clean up execution start times
    if (this.executionStartTimes.has(id)) {
      this.executionStartTimes.delete(id);
    }
    
    // Clean up execution metadata
    if (this.executionMetadata.has(id)) {
      this.executionMetadata.delete(id);
    }
    
    // Clean up interrupt buffers
    if (this.interruptBuffers.has(id)) {
      this.interruptBuffers.delete(id);
    }
    
    // Clean up ongoing executions tracking
    this.ongoingExecutions.delete(id);
    
    // Clean up activity tracking
    this.lastActivityTime.delete(id);
    
    // Remove all event listeners for this kernel
    this.removeAllKernelListeners(id);
    
    // Destroy the kernel instance
    await instance.destroy();
    
    // Remove the kernel from the map
    this.kernels.delete(id);
  }
  
  /**
   * Destroy all kernel instances
   * @param namespace Optional namespace to filter kernels to destroy
   * @returns Promise resolving when all kernels are destroyed
   */
  public async destroyAll(namespace?: string): Promise<void> {
    const ids = Array.from(this.kernels.keys())
      .filter(id => {
        if (!namespace) return true;
        return id.startsWith(`${namespace}:`);
      });
    
    // Destroy all kernels, but skip incomplete instances
    const destroyPromises = ids.map(async (id) => {
      const instance = this.kernels.get(id);
      if (!instance || typeof instance.destroy !== 'function') {
        console.warn(`Skipping incomplete kernel instance ${id} during destroyAll`);
        // Just remove it from the map
        this.kernels.delete(id);
        return;
      }
      return this.destroyKernel(id);
    });
    
    await Promise.all(destroyPromises);
    
    // If no namespace specified, also clean up the pool
    if (!namespace) {
      await this.destroyPool();
    }
  }
  
  /**
   * Destroy all kernels in the pool
   * @private
   */
  private async destroyPool(): Promise<void> {
    
    const destroyPromises: Promise<void>[] = [];
    
    for (const [poolKey, promises] of this.pool.entries()) {
      
      for (const kernelPromise of promises) {
        // Handle each promise - if it resolves, destroy the kernel
        const destroyPromise = kernelPromise.then(kernel => {
          return kernel.destroy();
        }).catch(error => {
          console.error(`Error destroying pool kernel from promise:`, error);
          // Don't re-throw to avoid unhandled rejections
        });
        
        destroyPromises.push(destroyPromise);
      }
    }
    
    // Wait for all pool kernels to be destroyed
    await Promise.all(destroyPromises);
    
    // Clear the pool and prefilling flags
    this.pool.clear();
    this.prefillingInProgress.clear();
  }
  
  /**
   * Register an event listener for a specific kernel's events
   * @param kernelId Kernel ID
   * @param eventType Event type
   * @param listener Event listener
   */
  public onKernelEvent(kernelId: string, eventType: KernelEvents, listener: (data: any) => void): void {
    // Check if kernel exists
    if (!this.kernels.has(kernelId)) {
      throw new Error(`Kernel with ID ${kernelId} not found`);
    }
    
    // Create wrapper that filters events for this specific kernel
    const wrapper: ListenerWrapper = {
      original: listener,
      wrapped: (event: { kernelId: string, data: any }) => {
        if (event.kernelId === kernelId) {
          // Pass just the data to the listener
          // The data structure is consistent across main thread and worker modes
          listener(event.data);
        }
      }
    };
    
    // Store the wrapper for later removal
    this.storeListener(kernelId, eventType, listener, wrapper);
    
    // Add the wrapped listener to the manager
    super.on(eventType, wrapper.wrapped);
  }
  
  /**
   * Remove an event listener for a specific kernel
   * @param kernelId Kernel ID
   * @param eventType Event type
   * @param listener Event listener
   */
  public offKernelEvent(kernelId: string, eventType: KernelEvents, listener: (data: any) => void): void {
    const wrapper = this.getListener(kernelId, eventType, listener);
    
    if (wrapper) {
      // Remove the wrapped listener from the manager
      super.removeListener(eventType, wrapper.wrapped);
      
      // Remove the wrapper from our tracking map
      this.removeStoredListener(kernelId, eventType, listener);
    }
  }
  
  /**
   * Store a listener wrapper for later removal
   */
  private storeListener(
    kernelId: string, 
    eventType: string, 
    original: Function, 
    wrapper: ListenerWrapper
  ): void {
    // Get or create kernel map
    if (!this.listenerWrappers.has(kernelId)) {
      this.listenerWrappers.set(kernelId, new Map());
    }
    const kernelMap = this.listenerWrappers.get(kernelId)!;
    
    // Get or create event type map
    if (!kernelMap.has(eventType)) {
      kernelMap.set(eventType, new Map());
    }
    const eventMap = kernelMap.get(eventType)!;
    
    // Store the wrapper
    eventMap.set(original, wrapper);
  }
  
  /**
   * Get a stored listener wrapper
   */
  private getListener(
    kernelId: string, 
    eventType: string, 
    original: Function
  ): ListenerWrapper | undefined {
    const kernelMap = this.listenerWrappers.get(kernelId);
    if (!kernelMap) return undefined;
    
    const eventMap = kernelMap.get(eventType);
    if (!eventMap) return undefined;
    
    return eventMap.get(original);
  }
  
  /**
   * Remove a stored listener wrapper
   */
  private removeStoredListener(
    kernelId: string, 
    eventType: string, 
    original: Function
  ): void {
    const kernelMap = this.listenerWrappers.get(kernelId);
    if (!kernelMap) return;
    
    const eventMap = kernelMap.get(eventType);
    if (!eventMap) return;
    
    // Remove the listener
    eventMap.delete(original);
    
    // Clean up empty maps
    if (eventMap.size === 0) {
      kernelMap.delete(eventType);
    }
    
    if (kernelMap.size === 0) {
      this.listenerWrappers.delete(kernelId);
    }
  }
  
  /**
   * Remove all listeners for a specific kernel
   */
  private removeAllKernelListeners(kernelId: string): void {
    const kernelMap = this.listenerWrappers.get(kernelId);
    if (!kernelMap) return;
    
    // For each event type
    for (const [eventType, eventMap] of kernelMap.entries()) {
      // For each original listener
      for (const wrapper of eventMap.values()) {
        // Remove the wrapped listener from the manager
        super.removeListener(eventType, wrapper.wrapped);
      }
    }
    
    // Clear the kernel's listener map
    this.listenerWrappers.delete(kernelId);
  }
  
  /**
   * Get all listeners for a specific kernel and event type
   * @param kernelId Kernel ID
   * @param eventType Event type
   * @returns Array of listeners
   */
  public getListeners(kernelId: string, eventType: KernelEvents): ((data: any) => void)[] {
    const kernelListeners = this.listenerWrappers.get(kernelId);
    if (!kernelListeners) {
      return [];
    }
    
    const eventListeners = kernelListeners.get(eventType);
    if (!eventListeners) {
      return [];
    }
    
    return Array.from(eventListeners.keys()) as ((data: any) => void)[];
  }

  /**
   * Execute Python code with streaming output
   * This method works in both main thread and worker modes
   * @param kernelId ID of the kernel to use
   * @param code The Python code to execute
   * @param parent Optional parent message header
   * @returns AsyncGenerator yielding intermediate outputs
   */
  public async* executeStream(
    kernelId: string, 
    code: string, 
    parent: any = {}
  ): AsyncGenerator<any, { success: boolean, result?: any, error?: Error }, void> {
    const instance = this.getKernel(kernelId);
    
    if (!instance) {
      throw new Error(`Kernel with ID ${kernelId} not found`);
    }
    
    // Update kernel activity
    this.updateKernelActivity(kernelId);
    
    // Track this execution with the code for better monitoring
    const executionId = this.trackExecution(kernelId, code);
    
    // Create AbortController for this execution to enable cancellation
    const abortController = new AbortController();
    this.storeAbortController(kernelId, executionId, abortController);
    
    try {
      // For main thread kernels, we can use the executeStream method directly
      if (instance.mode === KernelMode.MAIN_THREAD) {
        const kernel = instance.kernel as unknown as { 
          executeStream: (code: string, parent: any) => AsyncGenerator<any, any, void> 
        };
        
        // Forward to the kernel's executeStream method
        if (typeof kernel.executeStream === 'function') {
          try {
            yield* kernel.executeStream(code, parent);
            
            // Update activity after execution completes
            this.updateKernelActivity(kernelId);
            
            // Complete execution tracking
            this.completeExecution(kernelId, executionId);
            
            return { success: true };
          } catch (error) {
            console.error(`Error in main thread executeStream:`, error);
            
            // Update activity even if there's an error
            this.updateKernelActivity(kernelId);
            
            // Complete execution tracking even on error
            this.completeExecution(kernelId, executionId);
            
            return { 
              success: false, 
              error: error instanceof Error ? error : new Error(String(error))
            };
          }
        }
      }
      
      // For worker mode, we need to implement streaming via events with proper isolation
      try {
        // Event-based approach for worker kernels or main thread kernels without executeStream
        const streamQueue: any[] = [];
        let executionComplete = false;
        let executionResult: { success: boolean, result?: any, error?: Error } = { success: true };
        
        // Store handler references for guaranteed cleanup
        const eventHandlers = new Map<string, (event: { kernelId: string, data: any }) => void>();
        
        // Helper function to clean up all event handlers
        const cleanupHandlers = () => {
          for (const [eventType, handler] of eventHandlers.entries()) {
            super.off(eventType as any, handler);
          }
          eventHandlers.clear();
        };
        
        // Create execution-specific event handlers that include executionId check
        const createHandler = (eventType: string) => {
          const handler = (event: { kernelId: string, data: any }) => {
            // Only process events for this specific kernel and while this execution is active
            if (event.kernelId === kernelId && !executionComplete) {
              streamQueue.push({
                type: eventType,
                data: event.data,
                executionId // Include execution ID for debugging
              });
              
              // Events also count as activity
              this.updateKernelActivity(kernelId);
            }
          };
          eventHandlers.set(eventType, handler);
          return handler;
        };
        
        // Create and register all event handlers
        const handleStreamEvent = createHandler('stream');
        const handleDisplayEvent = createHandler('display_data');
        const handleUpdateDisplayEvent = createHandler('update_display_data');
        const handleResultEvent = createHandler('execute_result');
        const handleErrorEvent = createHandler('execute_error');
        
        // Register handlers
        super.on(KernelEvents.STREAM, handleStreamEvent);
        super.on(KernelEvents.DISPLAY_DATA, handleDisplayEvent);
        super.on(KernelEvents.UPDATE_DISPLAY_DATA, handleUpdateDisplayEvent);
        super.on(KernelEvents.EXECUTE_RESULT, handleResultEvent);
        super.on(KernelEvents.EXECUTE_ERROR, handleErrorEvent);
        
        // Create a promise that will resolve when execution is complete
        const executionPromise = new Promise<{ success: boolean, result?: any, error?: Error }>((resolve, reject) => {
          // Set up a handler for execution errors specifically
          const handleExecutionError = (event: { kernelId: string, data: any }) => {
            if (event.kernelId === kernelId && !executionComplete) {
              // Mark execution as complete to stop processing more events
              executionComplete = true;
              
              // Store the error for the final result
              executionResult = {
                success: false,
                error: new Error(`${event.data.ename}: ${event.data.evalue}`),
                result: event.data
              };
              
              // Update activity
              this.updateKernelActivity(kernelId);
              
              resolve(executionResult);
            }
          };
          
          // Add error handler to our cleanup list
          eventHandlers.set('execute_error_completion', handleExecutionError);
          super.on(KernelEvents.EXECUTE_ERROR, handleExecutionError);
          
          // Check if already aborted
          if (abortController.signal.aborted) {
            executionComplete = true;
            resolve({
              success: false,
              error: new Error('Execution was aborted')
            });
            return;
          }
          
          // Set up abort handler
          const abortHandler = () => {
            if (!executionComplete) {
              console.log(`🚫 Execution ${executionId} aborted`);
              executionComplete = true;
              
              resolve({
                success: false,
                error: new Error('Execution was aborted')
              });
            }
          };
          
          abortController.signal.addEventListener('abort', abortHandler);
          
          // Execute the code
          // We know the execute method is available directly on the kernel object
          try {
            const executePromise = instance.kernel.execute(code, parent);
            
            executePromise.then((result) => {
              // Only process if execution hasn't been marked complete already
              if (!executionComplete) {
                // Check if the execution result indicates an error (for Python kernels)
                if (result.success && result.result && result.result.status === "error") {
                  // Handle as error
                  const errorData = {
                    status: result.result.status,
                    ename: result.result.ename,
                    evalue: result.result.evalue,
                    traceback: result.result.traceback
                  };
                  
                  // Push error to stream queue directly 
                  streamQueue.push({
                    type: 'error',
                    data: errorData,
                    executionId
                  });
                  
                  // Update execution result to reflect the error
                  executionResult = {
                    success: false,
                    error: new Error(`${result.result.ename}: ${result.result.evalue}`),
                    result: result.result
                  };
                } else {
                  executionResult = result;
                }
                
                executionComplete = true;
                
                // Update activity when execution completes
                this.updateKernelActivity(kernelId);
                
                resolve(executionResult);
              }
            }).catch((error) => {
              // Only process if execution hasn't been marked complete already
              if (!executionComplete) {
                console.error(`Error in execute for kernel ${kernelId}:`, error);
                
                // Check if this is a KeyboardInterrupt and handle it specially
                let errorResult;
                if (this.isKeyboardInterrupt(error)) {
                  console.log(`KeyboardInterrupt caught in executeStream for kernel ${kernelId}`);
                  errorResult = this.createKeyboardInterruptResult();
                  
                  // Also push to stream queue for immediate feedback
                  streamQueue.push({
                    type: 'error',
                    data: errorResult.result,
                    executionId
                  });
                } else {
                  // Handle other errors normally
                  errorResult = {
                    success: false,
                    error: error instanceof Error ? error : new Error(String(error))
                  };
                }
                
                executionComplete = true;
                executionResult = errorResult;
                
                // Update activity even on error
                this.updateKernelActivity(kernelId);
                
                resolve(errorResult);
              }
            });
          } catch (error) {
            // Only process if execution hasn't been marked complete already
            if (!executionComplete) {
              console.error(`Error calling execute for kernel ${kernelId}:`, error);
              
              // Simple error handling
              const errorResult = {
                success: false,
                error: error instanceof Error ? error : new Error(String(error))
              };
              
              executionComplete = true;
              executionResult = errorResult;
              
              // Update activity even on direct error
              this.updateKernelActivity(kernelId);
              
              resolve(errorResult);
            }
          }
        });
        
        // Use try/finally to guarantee cleanup
        try {
          // Monitor the stream queue and yield results
          // Continue until execution is complete AND all queued events have been yielded
          while ((!executionComplete || streamQueue.length > 0) && !abortController.signal.aborted) {
            // If there are items in the queue, yield them
            if (streamQueue.length > 0) {
              const event = streamQueue.shift();
              yield event;
              continue;
            }
            
            // If no more events but execution is not complete, wait a little
            if (!executionComplete) {
              // Use abort signal to cancel the wait
              try {
                await new Promise((resolve, reject) => {
                  const timeoutId = setTimeout(resolve, 10);
                  abortController.signal.addEventListener('abort', () => {
                    clearTimeout(timeoutId);
                    reject(new Error('Aborted'));
                  });
                });
              } catch (error) {
                // If aborted, break out of loop
                if (abortController.signal.aborted) {
                  break;
                }
              }
            }
          }
          
          // Check if execution was aborted during stream monitoring
          if (abortController.signal.aborted && !executionComplete) {
            throw new Error('Execution was aborted during stream monitoring');
          }
          
          // Wait for the final result
          const result = await executionPromise;
          return result;
        } finally {
          // ALWAYS clean up event handlers regardless of how execution ends
          cleanupHandlers();
          
          // Remove AbortController to prevent memory leaks
          this.removeAbortController(kernelId, executionId);
          
          // Complete execution tracking
          this.completeExecution(kernelId, executionId);
        }
      } catch (error) {
        // Complete execution tracking on any outer error
        this.completeExecution(kernelId, executionId);
        
        console.error(`Unexpected error in executeStream:`, error);
        return {
          success: false, 
          error: error instanceof Error ? error : new Error(String(error))
        };
      }
    } catch (error) {
      // Complete execution tracking on any outer error
      this.completeExecution(kernelId, executionId);
      
      console.error(`Unexpected error in executeStream:`, error);
      return {
        success: false, 
        error: error instanceof Error ? error : new Error(String(error))
      };
    }
  }

  /**
   * Track a new execution task for a kernel
   * @param kernelId Kernel ID
   * @param code Optional code being executed for metadata
   * @returns Unique execution ID
   * @private
   */
  private trackExecution(kernelId: string, code?: string): string {
    // Create a unique execution ID
    const executionId = `exec-${crypto.randomUUID()}`;
    const startTime = Date.now();
    
    // Reset interrupt buffer for worker kernels before each new execution
    // This ensures the kernel can be interrupted multiple times
    const instance = this.kernels.get(kernelId);
    if (instance && instance.mode === KernelMode.WORKER && this.interruptBuffers.has(kernelId)) {
      const interruptBuffer = this.interruptBuffers.get(kernelId)!;
      // Reset buffer to 0 (no interrupt signal) to ensure clean state
      interruptBuffer[0] = 0;
    }
    
    // Get or create the set of ongoing executions for this kernel
    if (!this.ongoingExecutions.has(kernelId)) {
      this.ongoingExecutions.set(kernelId, new Set());
    }
    
    // Add this execution to the set
    this.ongoingExecutions.get(kernelId)!.add(executionId);
    
    // Track execution start time
    if (!this.executionStartTimes.has(kernelId)) {
      this.executionStartTimes.set(kernelId, new Map());
    }
    this.executionStartTimes.get(kernelId)!.set(executionId, startTime);
    
    // Track execution metadata
    if (!this.executionMetadata.has(kernelId)) {
      this.executionMetadata.set(kernelId, new Map());
    }
    
    // Update activity timestamp
    this.updateKernelActivity(kernelId);
    
    // If maxExecutionTime is set, create a timeout to detect stuck/dead kernels
    if (instance && instance.options.maxExecutionTime && instance.options.maxExecutionTime > 0) {
      // Get or create the map of execution timeouts for this kernel
      if (!this.executionTimeouts.has(kernelId)) {
        this.executionTimeouts.set(kernelId, new Map());
      }
      
      // Set a timeout for this execution with enhanced handling
      const timeoutId = setTimeout(() => {
        console.warn(`Execution ${executionId} on kernel ${kernelId} has been running for ${instance.options.maxExecutionTime}ms and may be stuck/dead.`);
        
        // Get execution metadata for better error reporting
        const metadata = this.executionMetadata.get(kernelId)?.get(executionId);
        const actualRuntime = Date.now() - (metadata?.startTime || startTime);
        
        // Emit a stalled execution event with enhanced information
        super.emit('execution_stalled', {
          kernelId,
          executionId,
          maxExecutionTime: instance.options.maxExecutionTime,
          actualRuntime,
          code: metadata?.code || code,
          startTime: metadata?.startTime || startTime
        });
        
        // Auto-handle stuck execution if configured
        this.handleStuckExecution(kernelId, executionId, actualRuntime, metadata?.code || code);
      }, instance.options.maxExecutionTime);
      
      // Store the timeout ID
      this.executionTimeouts.get(kernelId)!.set(executionId, timeoutId);
      
      // Store metadata including timeout ID
      this.executionMetadata.get(kernelId)!.set(executionId, {
        startTime,
        code,
        timeoutId
      });
    } else {
      // Store metadata without timeout ID
      this.executionMetadata.get(kernelId)!.set(executionId, {
        startTime,
        code
      });
    }
    
    return executionId;
  }
  
  /**
   * Complete tracking for an execution
   * @param kernelId Kernel ID
   * @param executionId Execution ID
   * @private
   */
  private completeExecution(kernelId: string, executionId: string): void {
    // Clear any execution timeout
    if (this.executionTimeouts.has(kernelId)) {
      const timeouts = this.executionTimeouts.get(kernelId)!;
      if (timeouts.has(executionId)) {
        clearTimeout(timeouts.get(executionId));
        timeouts.delete(executionId);
      }
      
      // Clean up empty maps
      if (timeouts.size === 0) {
        this.executionTimeouts.delete(kernelId);
      }
    }
    
    // Clean up execution start times
    if (this.executionStartTimes.has(kernelId)) {
      const startTimes = this.executionStartTimes.get(kernelId)!;
      startTimes.delete(executionId);
      
      // Clean up empty maps
      if (startTimes.size === 0) {
        this.executionStartTimes.delete(kernelId);
      }
    }
    
    // Clean up execution metadata
    if (this.executionMetadata.has(kernelId)) {
      const metadata = this.executionMetadata.get(kernelId)!;
      metadata.delete(executionId);
      
      // Clean up empty maps
      if (metadata.size === 0) {
        this.executionMetadata.delete(kernelId);
      }
    }
    
    // Remove from ongoing executions
    if (this.ongoingExecutions.has(kernelId)) {
      const executions = this.ongoingExecutions.get(kernelId)!;
      executions.delete(executionId);
      
      // Clean up empty sets
      if (executions.size === 0) {
        this.ongoingExecutions.delete(kernelId);
        
        // Update activity timestamp for completed execution
        this.updateKernelActivity(kernelId);
      }
    }
  }
  
  /**
   * Check if a kernel has any ongoing executions
   * @param kernelId Kernel ID
   * @returns True if the kernel has ongoing executions
   * @private
   */
  private hasOngoingExecutions(kernelId: string): boolean {
    return this.ongoingExecutions.has(kernelId) && 
           this.ongoingExecutions.get(kernelId)!.size > 0;
  }
  
  /**
   * Get the count of ongoing executions for a kernel
   * @param id Kernel ID
   * @returns Number of ongoing executions
   */
  public getOngoingExecutionCount(id: string): number {
    if (!this.ongoingExecutions.has(id)) {
      return 0;
    }
    return this.ongoingExecutions.get(id)!.size;
  }
  
  /**
   * Set up an inactivity timeout for a kernel
   * @param id Kernel ID
   * @param timeout Timeout in milliseconds
   * @private
   */
  private setupInactivityTimeout(id: string, timeout: number): void {
    // Don't set up a timer if timeout is 0 or negative
    if (timeout <= 0) {
      return;
    }
    
    // Always clear any existing timer first
    this.clearInactivityTimeout(id);
    
    // Calculate remaining time based on last activity
    const lastActivity = this.lastActivityTime.get(id) || Date.now();
    const elapsed = Date.now() - lastActivity;
    const remainingTime = Math.max(0, timeout - elapsed);
    
    // If no time remaining, destroy immediately
    if (remainingTime === 0) {
      // Check if the kernel has ongoing executions before shutting down
      if (this.hasOngoingExecutions(id)) {
        // Reset the timer to check again later
        this.setupInactivityTimeout(id, timeout);
        return;
      }
      
      // Destroy immediately
      this.destroyKernel(id).catch(error => {
        console.error(`Error destroying inactive kernel ${id}:`, error);
      });
      return;
    }
    
    // Create a timer to destroy the kernel after the remaining timeout
    const timer = setTimeout(() => {
      // Check if the kernel has ongoing executions before shutting down
      if (this.hasOngoingExecutions(id)) {
        // Reset the timer to check again later
        this.setupInactivityTimeout(id, timeout);
        return;
      }
      
      this.destroyKernel(id).catch(error => {
        console.error(`Error destroying inactive kernel ${id}:`, error);
      });
    }, remainingTime);
    
    // Store the timer ID
    this.inactivityTimers.set(id, timer);
  }
  
  /**
   * Clear any existing inactivity timeout for a kernel
   * @param id Kernel ID
   * @private
   */
  private clearInactivityTimeout(id: string): void {
    if (this.inactivityTimers.has(id)) {
      const timerId = this.inactivityTimers.get(id);
      clearTimeout(timerId);
      this.inactivityTimers.delete(id);
    }
  }

  /**
   * Update activity timestamp for a kernel and reset inactivity timer if present
   * @param id Kernel ID
   * @private
   */
  private updateKernelActivity(id: string): void {
    // Update the last activity time
    this.lastActivityTime.set(id, Date.now());
    
    // Get the kernel options
    const instance = this.kernels.get(id);
    if (!instance) return;
    
    const timeout = instance.options.inactivityTimeout;
    
    // Reset the inactivity timer if timeout is enabled (greater than 0)
    if (timeout && timeout > 0) {
      this.setupInactivityTimeout(id, timeout);
    }
  }

  /**
   * Get the last activity time for a kernel
   * @param id Kernel ID
   * @returns Last activity time in milliseconds since epoch, or undefined if not found
   */
  public getLastActivityTime(id: string): number | undefined {
    return this.lastActivityTime.get(id);
  }

  /**
   * Get the inactivity timeout for a kernel
   * @param id Kernel ID
   * @returns Inactivity timeout in milliseconds, or undefined if not set
   */
  public getInactivityTimeout(id: string): number | undefined {
    const instance = this.kernels.get(id);
    if (!instance) return undefined;
    
    return instance.options.inactivityTimeout;
  }

  /**
   * Set or update the inactivity timeout for a kernel
   * @param id Kernel ID
   * @param timeout Timeout in milliseconds, or 0 to disable
   * @returns True if the timeout was set, false if the kernel was not found
   */
  public setInactivityTimeout(id: string, timeout: number): boolean {
    const instance = this.kernels.get(id);
    if (!instance) return false;
    
    // Update the timeout in the options
    instance.options.inactivityTimeout = timeout;
    
    // Clear any existing timer
    this.clearInactivityTimeout(id);
    
    // If timeout is greater than 0, set up a new timer
    if (timeout > 0) {
      this.setupInactivityTimeout(id, timeout);
    }
    
    return true;
  }

  /**
   * Get time until auto-shutdown for a kernel
   * @param id Kernel ID
   * @returns Time in milliseconds until auto-shutdown, or undefined if no timeout is set
   */
  public getTimeUntilShutdown(id: string): number | undefined {
    const instance = this.kernels.get(id);
    if (!instance) return undefined;
    
    const timeout = instance.options.inactivityTimeout;
    if (!timeout || timeout <= 0) return undefined;
    
    const lastActivity = this.lastActivityTime.get(id);
    if (!lastActivity) return undefined;
    
    const elapsedTime = Date.now() - lastActivity;
    const remainingTime = timeout - elapsedTime;
    
    return Math.max(0, remainingTime);
  }

  /**
   * Get the map of inactivity timers (for debugging/testing only)
   * @returns Object with kernel IDs as keys and timer IDs as values
   */
  public getInactivityTimers(): Record<string, number> {
    // Convert Map to Object for easier inspection
    const timers: Record<string, number> = {};
    this.inactivityTimers.forEach((value, key) => {
      timers[key] = value;
    });
    return timers;
  }

  /**
   * Set up a handler for stalled executions
   * @param id Kernel ID
   * @private
   */
  private setupStalledExecutionHandler(id: string): void {
    // Listen for stalled execution events
    super.on(KernelEvents.EXECUTION_STALLED, (event: { kernelId: string, executionId: string, maxExecutionTime: number }) => {
      if (event.kernelId === id) {
        console.warn(`Handling stalled execution ${event.executionId} on kernel ${id} (running longer than ${event.maxExecutionTime}ms)`);
        
        // Emit an event for clients to handle
        const instance = this.kernels.get(id);
        if (instance) {
          super.emit(KernelEvents.EXECUTE_ERROR, {
            kernelId: id,
            data: {
              ename: "ExecutionStalledError",
              evalue: `Execution stalled or potentially deadlocked (running > ${event.maxExecutionTime}ms)`,
              traceback: ["Execution may be stuck in an infinite loop or deadlocked."]
            }
          });
        }
      }
    });
  }

  /**
   * Force terminate a potentially stuck kernel
   * @param id Kernel ID
   * @param reason Optional reason for termination
   * @returns Promise resolving to true if the kernel was terminated
   */
  public async forceTerminateKernel(id: string, reason = "Force terminated due to stalled execution"): Promise<boolean> {
    const instance = this.kernels.get(id);
    
    if (!instance) {
      return false;
    }
    
    try {
      // Log the forced termination
      console.warn(`Force terminating kernel ${id}: ${reason}`);
      
      // Emit an error event to notify clients
      super.emit(KernelEvents.EXECUTE_ERROR, {
        kernelId: id,
        data: {
          ename: "KernelForcedTermination",
          evalue: reason,
          traceback: ["Kernel was forcefully terminated by the system."]
        }
      });
      
      // Destroy the kernel
      await this.destroyKernel(id);
      return true;
    } catch (error) {
      console.error(`Error during forced termination of kernel ${id}:`, error);
      return false;
    }
  }

  /**
   * Get information about ongoing executions for a kernel
   * @param id Kernel ID
   * @returns Information about ongoing executions with accurate timing
   */
  public getExecutionInfo(id: string): { 
    count: number; 
    isStuck: boolean; 
    executionIds: string[];
    longestRunningTime?: number;
    executions: Array<{
      id: string;
      startTime: number;
      runtime: number;
      code?: string;
      isStuck: boolean;
    }>;
  } {
    const instance = this.kernels.get(id);
    if (!instance) {
      return { count: 0, isStuck: false, executionIds: [], executions: [] };
    }
    
    // Handle partially initialized kernels where options may not be fully set
    if (!instance.options) {
      return { count: 0, isStuck: false, executionIds: [], executions: [] };
    }
    
    const executionIds = this.ongoingExecutions.get(id) 
      ? Array.from(this.ongoingExecutions.get(id)!)
      : [];
    
    const count = executionIds.length;
    const currentTime = Date.now();
    const maxExecutionTime = instance.options.maxExecutionTime;
    
    // Build detailed execution information
    const executions: Array<{
      id: string;
      startTime: number;
      runtime: number;
      code?: string;
      isStuck: boolean;
    }> = [];
    
    let longestRunningTime: number | undefined = undefined;
    let anyStuck = false;
    
    // Get execution start times and metadata
    const startTimes = this.executionStartTimes.get(id);
    const metadata = this.executionMetadata.get(id);
    
    for (const executionId of executionIds) {
      const startTime = startTimes?.get(executionId);
      const execMetadata = metadata?.get(executionId);
      
      if (startTime !== undefined) {
        const runtime = currentTime - startTime;
        const isStuck = maxExecutionTime !== undefined && runtime > maxExecutionTime;
        
        executions.push({
          id: executionId,
          startTime,
          runtime,
          code: execMetadata?.code,
          isStuck
        });
        
        // Track longest running time
        if (longestRunningTime === undefined || runtime > longestRunningTime) {
          longestRunningTime = runtime;
        }
        
        // Track if any execution is stuck
        if (isStuck) {
          anyStuck = true;
        }
      } else {
        // Fallback for executions without start time tracking
        console.warn(`No start time found for execution ${executionId} on kernel ${id}`);
        executions.push({
          id: executionId,
          startTime: 0,
          runtime: 0,
          code: execMetadata?.code,
          isStuck: false
        });
      }
    }
    
    // Sort executions by start time (oldest first)
    executions.sort((a, b) => a.startTime - b.startTime);
    
    return {
      count,
      isStuck: anyStuck,
      executionIds,
      longestRunningTime,
      executions
    };
  }

  /**
   * Execute Python code in a kernel
   * Overrides the kernel's execute method to track executions
   * @param kernelId ID of the kernel to use
   * @param code Python code to execute
   * @param parent Optional parent message header
   * @returns Promise resolving to execution result
   */
  public async execute(
    kernelId: string,
    code: string,
    parent: any = {}
  ): Promise<{ success: boolean, result?: any, error?: Error }> {
    const instance = this.getKernel(kernelId);
    
    if (!instance) {
      throw new Error(`Kernel with ID ${kernelId} not found`);
    }
    
    // Update kernel activity
    this.updateKernelActivity(kernelId);
    
    // Track this execution with the code for better monitoring
    const executionId = this.trackExecution(kernelId, code);
    
    try {
      // Execute the code
      const result = await instance.kernel.execute(code, parent);
      
      // Update activity after execution completes
      this.updateKernelActivity(kernelId);
      
      // Complete execution tracking
      this.completeExecution(kernelId, executionId);
      
      return result;
    } catch (error) {
      // Update activity even if there's an error
      this.updateKernelActivity(kernelId);
      
      // Complete execution tracking even on error
      this.completeExecution(kernelId, executionId);
      
      return {
        success: false,
        error: error instanceof Error ? error : new Error(String(error))
      };
    }
  }

  /**
   * Check if a kernel type is allowed
   * @param mode Kernel mode
   * @param language Kernel language
   * @returns True if the kernel type is allowed
   * @private
   */
  private isKernelTypeAllowed(mode: KernelMode, language: KernelLanguage): boolean {
    return this.allowedKernelTypes.some(type => 
      type.mode === mode && type.language === language
    );
  }
  
  /**
   * Get the list of allowed kernel types
   * @returns Array of allowed kernel type configurations
   */
  public getAllowedKernelTypes(): Array<{
    mode: KernelMode;
    language: KernelLanguage;
  }> {
    return [...this.allowedKernelTypes]; // Return a copy to prevent modification
  }

  /**
   * Ping a kernel to reset its activity timer and extend the deadline
   * @param id Kernel ID
   * @returns True if the kernel was pinged successfully, false if not found
   */
  public pingKernel(id: string): boolean {
    const instance = this.kernels.get(id);
    if (!instance) {
      return false;
    }
    
    // Update kernel activity (this will reset the inactivity timer)
    this.updateKernelActivity(id);
    
    return true;
  }

  /**
   * Restart a kernel by destroying it and creating a new one with the same ID and configuration
   * @param id Kernel ID
   * @returns Promise resolving to true if the kernel was restarted successfully, false if not found
   */
  public async restartKernel(id: string): Promise<boolean> {
    const instance = this.kernels.get(id);
    if (!instance) {
      console.warn(`Cannot restart kernel ${id}: kernel not found`);
      return false;
    }
    
    try {
      // Store the current configuration
      const currentConfig = {
        mode: instance.mode,
        language: instance.language,
        options: { ...instance.options }
      };
      
      // Extract namespace from ID if present
      let namespace: string | undefined;
      let baseId: string;
      
      if (id.includes(':')) {
        const parts = id.split(':');
        namespace = parts[0];
        baseId = parts[1];
      } else {
        baseId = id;
      }
      
      // Destroy the existing kernel
      await this.destroyKernel(id);
      
      // Create a new kernel with the same configuration
      const restartOptions: IManagerKernelOptions = {
        id: baseId,
        mode: currentConfig.mode,
        lang: currentConfig.language,
        namespace,
        deno: currentConfig.options.deno,
        filesystem: currentConfig.options.filesystem,
        inactivityTimeout: currentConfig.options.inactivityTimeout,
        maxExecutionTime: currentConfig.options.maxExecutionTime
      };
      
      // Create the new kernel
      const newKernelId = await this.createKernel(restartOptions);
      
      // Verify the new kernel has the same ID
      if (newKernelId !== id) {
        console.error(`Kernel restart failed: expected ID ${id}, got ${newKernelId}`);
        return false;
      }
      
      return true;
      
    } catch (error) {
      console.error(`Error restarting kernel ${id}:`, error);
      return false;
    }
  }

  /**
   * Interrupt a running kernel execution
   * @param id Kernel ID
   * @returns Promise resolving to true if the interrupt was successful, false if not found or failed
   */
  public async interruptKernel(id: string): Promise<boolean> {
    const instance = this.kernels.get(id);
    if (!instance) {
      console.warn(`Cannot interrupt kernel ${id}: kernel not found`);
      return false;
    }
    
    try {
      if (instance.mode === KernelMode.WORKER && instance.worker) {
        // For worker kernels, use SharedArrayBuffer interrupt method
        return await this.interruptWorkerKernel(id, instance);
      } else {
        // For main thread kernels, try to interrupt (will throw error if not supported)
        return await this.interruptMainThreadKernel(id, instance);
      }
    } catch (error) {
      console.error(`Error interrupting kernel ${id}:`, error instanceof Error ? error.message : String(error));
      return false;
    }
  }
  
  /**
   * Interrupt a main thread kernel
   * @param id Kernel ID
   * @param instance Kernel instance
   * @returns Promise resolving to interrupt success
   * @private
   */
  private async interruptMainThreadKernel(id: string, instance: IKernelInstance): Promise<boolean> {
    // Main thread kernels don't support proper interruption like worker kernels do
    // Even if they have an interrupt method, it's limited and unreliable
    throw new Error(`Main thread kernel ${id} does not support reliable interruption. Use worker kernels for interruptible execution.`);
  }
  
  /**
   * Interrupt a worker kernel using SharedArrayBuffer according to Pyodide documentation
   * @param id Kernel ID
   * @param instance Kernel instance
   * @returns Promise resolving to interrupt success
   * @private
   */
  private async interruptWorkerKernel(id: string, instance: IKernelInstance): Promise<boolean> {
    try {
      const worker = instance.worker;
      if (!worker) {
        console.error(`Worker not found for kernel ${id}`);
        return false;
      }
      
      // If interruption mode is 'kernel-interrupt', use fallback directly
      if (this.interruptionMode === 'kernel-interrupt') {
        return await this.interruptWorkerKernelFallback(id, worker);
      }
      
      // Check if we already have an interrupt buffer for this kernel
      let interruptBuffer = this.interruptBuffers.get(id);
      
      if (!interruptBuffer) {
        // Create a new SharedArrayBuffer for interrupt control
        try {
          // Try to create SharedArrayBuffer (requires specific security headers)
          const sharedBuffer = new SharedArrayBuffer(1);
          interruptBuffer = new Uint8Array(sharedBuffer);
          
          // Initialize buffer to 0 (no interrupt signal)
          interruptBuffer[0] = 0;
          
          // Store the buffer for future use
          this.interruptBuffers.set(id, interruptBuffer);
          
          // Send the buffer to the worker to set up pyodide.setInterruptBuffer()
          worker.postMessage({
            type: "SET_INTERRUPT_BUFFER",
            buffer: interruptBuffer
          });
          
          // Wait for the worker to confirm buffer setup
          await new Promise<void>((resolve, reject) => {
            const timeout = setTimeout(() => {
              reject(new Error("Timeout waiting for interrupt buffer setup"));
            }, 2000);
            
            const handler = (event: MessageEvent) => {
              if (event.data?.type === "INTERRUPT_BUFFER_SET") {
                worker.removeEventListener("message", handler);
                clearTimeout(timeout);
                resolve();
              }
            };
            
            worker.addEventListener("message", handler);
          });
          
          console.log(`Interrupt buffer set up for kernel ${id}`);
          
        } catch (error) {
          // Handle based on interruption mode
          if (this.interruptionMode === 'shared-array-buffer') {
            // If explicitly set to shared-array-buffer, this is an error
            console.error(`❌ Cannot create SharedArrayBuffer for interrupt handling in kernel ${id}`);
            throw new Error(`SharedArrayBuffer is required for interruption mode 'shared-array-buffer' but is not available.

To fix this issue, either:
1. Configure your web server with these headers:
   - Cross-Origin-Opener-Policy: same-origin
   - Cross-Origin-Embedder-Policy: require-corp

2. Or change the interruption mode when creating KernelManager:
   new KernelManager({ interruptionMode: 'auto' })`);
          } else {
            // Auto mode: fall back to kernel.interrupt()
            console.info(`ℹ️ Using message-based interrupt for kernel ${id} (SharedArrayBuffer not available)`);
            
            // Fallback: use message-based interrupt
            return await this.interruptWorkerKernelFallback(id, worker);
          }
        }
      }
      
      // According to Pyodide docs: Set interrupt signal (2 = SIGINT)
      console.log(`Setting interrupt signal for kernel ${id}...`);
      interruptBuffer[0] = 2;
      
      // Wait for Pyodide to process the interrupt
      // Pyodide will reset the buffer to 0 when it processes the interrupt
      let attempts = 0;
      const maxAttempts = 50; // Check for up to 5 seconds (50 * 100ms)
      
      while (attempts < maxAttempts && interruptBuffer[0] !== 0) {
        await new Promise(resolve => setTimeout(resolve, 100));
        attempts++;
      }
      
      if (interruptBuffer[0] === 0) {
        console.log(`Interrupt processed successfully for kernel ${id} after ${attempts * 100}ms`);
        return true;
      } else {
        console.warn(`Interrupt signal not processed for kernel ${id} after ${maxAttempts * 100}ms`);
        // Still return true as we set the signal - the interrupt may be processed later
        return true;
      }
      
    } catch (error) {
      console.error(`Error interrupting worker kernel ${id}:`, error);
      return false;
    }
  }
  
  /**
   * Fallback interrupt method for worker kernels when SharedArrayBuffer is not available
   * @param id Kernel ID
   * @param worker Worker instance
   * @returns Promise resolving to interrupt success
   * @private
   */
  private async interruptWorkerKernelFallback(id: string, worker: Worker): Promise<boolean> {
    return new Promise<boolean>((resolve) => {
      // Set up a listener for the interrupt response
      const responseHandler = (event: MessageEvent) => {
        if (event.data?.type === "INTERRUPT_TRIGGERED") {
          worker.removeEventListener("message", responseHandler);
          const success = event.data.data?.success || false;
          resolve(success);
        }
      };
      
      // Listen for the response
      worker.addEventListener("message", responseHandler);
      
      // Send the interrupt message
      worker.postMessage({
        type: "INTERRUPT_KERNEL"
      });
      
      // Set a timeout in case we don't get a response
      setTimeout(() => {
        worker.removeEventListener("message", responseHandler);
        console.warn(`⏱️ Interrupt request timed out for kernel ${id} after 5 seconds.
This may happen if:
- The kernel is running code that cannot be interrupted
- The kernel is in an unresponsive state
You may need to restart the kernel if it remains unresponsive.`);
        resolve(false);
      }, 5000); // 5 second timeout
    });
  }

  /**
   * Handle a stuck execution with configurable strategies
   * @param kernelId Kernel ID
   * @param executionId Execution ID that's stuck
   * @param actualRuntime How long the execution has been running
   * @param code The code that was being executed
   * @private
   */
  private async handleStuckExecution(kernelId: string, executionId: string, actualRuntime: number, code?: string): Promise<void> {
    const instance = this.kernels.get(kernelId);
    if (!instance) {
      return;
    }
    
    console.warn(`Handling stuck execution ${executionId} on kernel ${kernelId} (runtime: ${actualRuntime}ms)`);
    
    // Strategy 1: Try to interrupt the kernel first
    const interruptSuccess = await this.interruptKernel(kernelId);
    
    if (interruptSuccess) {
      console.log(`Successfully interrupted kernel ${kernelId}`);
      
      // Emit an execution error to notify clients
      super.emit(KernelEvents.EXECUTE_ERROR, {
        kernelId: kernelId,
        data: {
          ename: "ExecutionInterrupted",
          evalue: `Execution automatically interrupted after ${actualRuntime}ms (exceeded maxExecutionTime)`,
          traceback: [
            `Execution was automatically interrupted due to timeout.`,
            `Runtime: ${actualRuntime}ms`,
            `Max allowed: ${instance.options.maxExecutionTime}ms`,
            code ? `Code: ${code.substring(0, 200)}${code.length > 200 ? '...' : ''}` : 'Code: <unknown>'
          ]
        }
      });
      
      return;
    }
    
    // Strategy 2: If interrupt failed, try restarting the kernel
    console.warn(`Interrupt failed for kernel ${kernelId}, attempting restart...`);
    const restartSuccess = await this.restartKernel(kernelId);
    
    if (restartSuccess) {
      console.log(`Successfully restarted kernel ${kernelId}`);
      
      // Emit a restart notification
      super.emit(KernelEvents.EXECUTE_ERROR, {
        kernelId: kernelId,
        data: {
          ename: "KernelRestarted",
          evalue: `Kernel automatically restarted due to stuck execution (runtime: ${actualRuntime}ms)`,
          traceback: [
            `Kernel was automatically restarted due to stuck execution.`,
            `Runtime: ${actualRuntime}ms`,
            `Max allowed: ${instance.options.maxExecutionTime}ms`,
            `Interrupt attempt failed, kernel was restarted instead.`,
            code ? `Code: ${code.substring(0, 200)}${code.length > 200 ? '...' : ''}` : 'Code: <unknown>'
          ]
        }
      });
      
      return;
    }
    
    // Strategy 3: If restart failed, force terminate the kernel
    console.error(`Restart failed for kernel ${kernelId}, force terminating...`);
    const terminateSuccess = await this.forceTerminateKernel(
      kernelId, 
      `Stuck execution could not be interrupted or restarted (runtime: ${actualRuntime}ms)`
    );
    
    if (terminateSuccess) {
      console.log(`Successfully terminated kernel ${kernelId}`);
    } else {
      console.error(`Failed to terminate kernel ${kernelId} - manual intervention may be required`);
      
      // Emit a critical error
      super.emit('kernel_unrecoverable', {
        kernelId: kernelId,
        executionId: executionId,
        actualRuntime: actualRuntime,
        code: code,
        message: 'Kernel is stuck and could not be recovered through interrupt, restart, or termination'
      });
    }
  }

  /**
   * Get detailed information about stuck executions across all kernels
   * @returns Array of stuck execution details
   */
  public getStuckExecutions(): Array<{
    kernelId: string;
    executionId: string;
    startTime: number;
    runtime: number;
    maxAllowed: number;
    code?: string;
    kernelMode: KernelMode;
    kernelLanguage: KernelLanguage;
  }> {
    const stuckExecutions: Array<{
      kernelId: string;
      executionId: string;
      startTime: number;
      runtime: number;
      maxAllowed: number;
      code?: string;
      kernelMode: KernelMode;
      kernelLanguage: KernelLanguage;
    }> = [];
    
    const currentTime = Date.now();
    
    for (const [kernelId, instance] of this.kernels.entries()) {
      // Skip pool kernels
      if (kernelId.startsWith("pool-")) continue;
      
      // Skip kernels without maxExecutionTime configured
      if (!instance.options?.maxExecutionTime || instance.options.maxExecutionTime <= 0) {
        continue;
      }
      
      const maxExecutionTime = instance.options.maxExecutionTime;
      const startTimes = this.executionStartTimes.get(kernelId);
      const metadata = this.executionMetadata.get(kernelId);
      const ongoingExecs = this.ongoingExecutions.get(kernelId);
      
      if (!ongoingExecs || ongoingExecs.size === 0) {
        continue;
      }
      
      for (const executionId of ongoingExecs) {
        const startTime = startTimes?.get(executionId);
        if (startTime === undefined) continue;
        
        const runtime = currentTime - startTime;
        
        // Check if this execution is stuck
        if (runtime > maxExecutionTime) {
          const execMetadata = metadata?.get(executionId);
          
          stuckExecutions.push({
            kernelId,
            executionId,
            startTime,
            runtime,
            maxAllowed: maxExecutionTime,
            code: execMetadata?.code,
            kernelMode: instance.mode,
            kernelLanguage: instance.language
          });
        }
      }
    }
    
    // Sort by runtime (longest running first)
    stuckExecutions.sort((a, b) => b.runtime - a.runtime);
    
    return stuckExecutions;
  }

  /**
   * Force interrupt all stuck executions across all kernels
   * @returns Promise resolving to array of intervention results
   */
  public async handleAllStuckExecutions(): Promise<Array<{
    kernelId: string;
    executionId: string;
    action: 'interrupted' | 'restarted' | 'terminated' | 'failed';
    success: boolean;
    error?: string;
  }>> {
    const stuckExecutions = this.getStuckExecutions();
    const results: Array<{
      kernelId: string;
      executionId: string;
      action: 'interrupted' | 'restarted' | 'terminated' | 'failed';
      success: boolean;
      error?: string;
    }> = [];
    
    console.log(`Found ${stuckExecutions.length} stuck executions to handle`);
    
    // Group by kernel to avoid multiple interventions on the same kernel
    const kernelGroups = new Map<string, typeof stuckExecutions>();
    for (const exec of stuckExecutions) {
      if (!kernelGroups.has(exec.kernelId)) {
        kernelGroups.set(exec.kernelId, []);
      }
      kernelGroups.get(exec.kernelId)!.push(exec);
    }
    
    // Handle each kernel's stuck executions
    for (const [kernelId, executions] of kernelGroups) {
      try {
        // Pick the longest running execution as the primary one
        const primaryExec = executions[0]; // Already sorted by runtime desc
        
        console.log(`Handling stuck kernel ${kernelId} with ${executions.length} stuck executions (primary: ${primaryExec.runtime}ms)`);
        
        // Use the automated handling system
        await this.handleStuckExecution(
          kernelId, 
          primaryExec.executionId, 
          primaryExec.runtime, 
          primaryExec.code
        );
        
        // Mark all executions for this kernel as handled
        for (const exec of executions) {
          results.push({
            kernelId: exec.kernelId,
            executionId: exec.executionId,
            action: 'interrupted', // We don't know the exact action, but it was handled
            success: true
          });
        }
        
      } catch (error) {
        console.error(`Error handling stuck executions for kernel ${kernelId}:`, error);
        
        // Mark all executions for this kernel as failed
        for (const exec of executions) {
          results.push({
            kernelId: exec.kernelId,
            executionId: exec.executionId,
            action: 'failed',
            success: false,
            error: error instanceof Error ? error.message : String(error)
          });
        }
      }
    }
    
    return results;
  }

  /**
   * Set up interrupt buffer for a worker kernel during creation
   * @param id Kernel ID
   * @param worker Worker instance
   * @private
   */
  private async setupWorkerInterruptBuffer(id: string, worker: Worker): Promise<void> {
    // Skip SharedArrayBuffer setup if mode is 'kernel-interrupt'
    if (this.interruptionMode === 'kernel-interrupt') {
      console.log(`Skipping SharedArrayBuffer setup for kernel ${id} - using kernel.interrupt() mode`);
      return;
    }
    
    try {
      // Python kernels support interrupt buffers
      
      // For Python kernels, create actual SharedArrayBuffer
      const sharedBuffer = new SharedArrayBuffer(1);
      const interruptBuffer = new Uint8Array(sharedBuffer);
      
      // Initialize buffer to 0 (no interrupt signal)
      interruptBuffer[0] = 0;
      
      // Store the buffer for future use
      this.interruptBuffers.set(id, interruptBuffer);
      
      // Send the buffer to the worker to set up pyodide.setInterruptBuffer()
      worker.postMessage({
        type: "SET_INTERRUPT_BUFFER",
        buffer: interruptBuffer
      });
      
      // Wait for the worker to confirm buffer setup
      await new Promise<void>((resolve, reject) => {
        const timeout = setTimeout(() => {
          reject(new Error("Timeout waiting for interrupt buffer setup"));
        }, 5000);
        
        const handler = (event: MessageEvent) => {
          if (event.data?.type === "INTERRUPT_BUFFER_SET") {
            worker.removeEventListener("message", handler);
            clearTimeout(timeout);
            resolve();
          }
        };
        
        worker.addEventListener("message", handler);
      });
      
    } catch (error) {
      // Handle based on interruption mode
      if (this.interruptionMode === 'shared-array-buffer') {
        // If explicitly set to shared-array-buffer, this is an error
        console.error(`❌ SharedArrayBuffer required but not available for kernel ${id}`);
        throw new Error(`SharedArrayBuffer is required but not available. To enable SharedArrayBuffer, your server must set these headers:
- Cross-Origin-Opener-Policy: same-origin
- Cross-Origin-Embedder-Policy: require-corp

Alternatively, use interruptionMode: 'kernel-interrupt' or 'auto' in KernelManager options.`);
      } else {
        // Auto mode: fall back to kernel.interrupt()
        console.info(`ℹ️ SharedArrayBuffer not available for kernel ${id}. Using alternative interrupt method.

To enable faster interrupts, configure your server with these headers:
- Cross-Origin-Opener-Policy: same-origin
- Cross-Origin-Embedder-Policy: require-corp

Note: Some development servers (e.g., Vite, webpack-dev-server) can be configured to add these headers.
The alternative interrupt method will still work but may be less responsive for long-running code.`);
        // Don't throw - kernel can still work without interrupt buffer
      }
    }
  }
}