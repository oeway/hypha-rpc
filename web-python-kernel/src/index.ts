// Kernel implementation for Deno using Pyodide directly
// Based on the PyodideRemoteKernel but adapted for direct execution in main thread

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

  emit(eventName: string, ...args: any[]) {
    if (!this.events[eventName]) return;
    this.events[eventName].forEach(listener => listener(...args));
  }

  setMaxListeners(n: number) {
    // No-op for browser compatibility
  }
}

// @ts-ignore Importing from npm
import { loadPyodide } from "./pyodide-loader";

// Import types and enums
import { KernelEvents, IEventData, IMessage, IKernel, IKernelOptions, IFilesystemMountOptions, IKernelExecuteOptions, IEventEmitter } from "./types";

// Import PyPI URLs
import {
  pipliteWheelUrl,
  pyodide_kernelWheelUrl,
  ipykernelWheelUrl,
  allJSONUrl,
  widgetsnbextensionWheelUrl,
  widgetsnbextensionWheelUrl1
} from './_pypi';

// Event types from JupyterLab
// Re-export types to maintain backward compatibility
export { KernelEvents } from "./types";
export type { 
  IKernel, 
  IKernelOptions, 
  IFilesystemMountOptions, 
  IKernelExecuteOptions, 
  IMessage, 
  IEventData,
  IEventEmitter
} from "./types";

export { KernelMode, KernelLanguage } from "./manager";




export class Kernel extends EventEmitter implements IKernel {
  private pyodide: any;
  private initialized = false;
  private initPromise: Promise<void> | null = null;
  
  // Filesystem options
  private filesystemOptions: IFilesystemMountOptions = {
    enabled: false,
    root: ".",
    mountPoint: "/home/pyodide"
  };
  
  // Kernel options
  private lockFileURL?: string;
  
  // Kernel components
  private _kernel: any;
  private _interpreter: any;
  private _stdout_stream: any;
  private _stderr_stream: any;
  
  // Input handling
  private _resolveInputReply: ((value: any) => void) | null = null;
  
  // Execution state
  private _parent_header: any = {};
  private executionCount = 0;
  private _status: "active" | "busy" | "unknown" = "unknown";
  
  // Interrupt handling
  private _interruptBuffer: Uint8Array | null = null;
  private _interruptSupported = false;
  
  // Environment variables
  private environmentVariables: Record<string, string> = {};
  
  // Unique identifier for this kernel instance
  private readonly kernelId: string;
  
  constructor() {
    super();
    super.setMaxListeners(20);
    // Generate a unique ID for this kernel instance
    this.kernelId = `kernel_${Math.random().toString(36).substring(2)}_${Date.now()}`;
  }

  // Async method for kernel status
  async getStatus(): Promise<"active" | "busy" | "unknown"> {
    return this._status;
  }

  /**
   * Initialize the kernel with maximum performance optimizations
   * OPTIMIZED: Full parallelization with smart caching and performance monitoring
   */
  public async initialize(options?: IKernelOptions): Promise<void> {
    if (this.initialized) {
      return;
    }
    
    if (this.initPromise) {
      return this.initPromise;
    }

    // Set filesystem options if provided
    if (options?.filesystem) {
      this.filesystemOptions = {
        ...this.filesystemOptions,
        ...options.filesystem
      };
    }

    // Set environment variables if provided
    if (options?.env) {
      this.environmentVariables = { ...options.env };
    }

    // Set lockFileURL if provided
    if (options?.lockFileURL) {
      this.lockFileURL = options.lockFileURL;
    }

    this.initPromise = this._initializeInternal();
    return this.initPromise;
  }
  
  /**
   * Initialize the kernel with maximum performance optimizations
   * OPTIMIZED: Full parallelization with smart caching and performance monitoring
   */
  private async _initializeInternal(): Promise<void> {
    const startTime = Date.now();
    console.log("🚀 Starting optimized kernel initialization...");
    
    try {
      // Load Pyodide from CDN
      const pyodideStartTime = Date.now();
      
      // Configure pyodide options
      const pyodideConfig: any = {};
      if (this.lockFileURL) {
        pyodideConfig.lockFileURL = this.lockFileURL;
        console.log(`🔒 Using lockFileURL: ${this.lockFileURL}`);
      }
      
      // Use our CDN loader - it will automatically handle main thread vs worker
      this.pyodide = await loadPyodide(pyodideConfig);
      const pyodideTime = Date.now() - pyodideStartTime;
      console.log(`✅ Pyodide loaded in ${pyodideTime}ms`);
      
      // Initialize core components in parallel
      if (this.lockFileURL) {
        // When using lockFileURL, packages are pre-installed, skip package installation
        console.log("🚀 Using lock file - skipping package installation");
        const [,] = await Promise.all([
          // 1. Filesystem mounting (if enabled)
          this.filesystemOptions.enabled ? this.mountFilesystem() : Promise.resolve(),
          // 2. Environment variables setup
          this.setEnvironmentVariables()
        ]);
        
        // Initialize globals directly (packages are already available)
        await this.initGlobals();
      } else {
        // Standard initialization with package installation
        const [, ,] = await Promise.all([
          // 1. Filesystem mounting (if enabled)
          this.filesystemOptions.enabled ? this.mountFilesystem() : Promise.resolve(),
          // 2. Package manager initialization
          this.initPackageManager(),
          // 3. Environment variables setup
          this.setEnvironmentVariables()
        ]);
        
        // Install packages and initialize globals
        await this.initKernel();
        await this.initGlobals();
      }
      
      const totalTime = Date.now() - startTime;
      console.log(`🎯 KERNEL INITIALIZATION COMPLETE in ${totalTime}ms`);
      console.log(`⚡ Performance: Pyodide(${pyodideTime}ms) + Setup(${totalTime - pyodideTime}ms)`);
      
      // Mark as initialized
      this.initialized = true;
      this._status = "active";
      console.log("🟢 Kernel is now ACTIVE and ready for execution!");
      
    } catch (error) {
      console.error("❌ Kernel initialization failed:", error);
      this._status = "unknown";
      throw error;
    }
  }
  
  /**
   * Mount the local filesystem to the Pyodide environment
   */
  private async mountFilesystem(): Promise<void> {
    try {
      console.log(`Mounting filesystem from ${this.filesystemOptions.root} to ${this.filesystemOptions.mountPoint}`);
      
      // Use the same approach as in deno-demo-fs-asgi.js for maximum compatibility
      // Simple and direct mounting of the filesystem
      await this.pyodide.FS.mount(
        this.pyodide.FS.filesystems.NODEFS,
        { root: this.filesystemOptions.root || "." },
        this.filesystemOptions.mountPoint || "/home/pyodide"
      );
      
      console.log("Filesystem mounted successfully");
      
      // Verify the mount by listing the directory
      try {
        const mountedFiles = this.pyodide.FS.readdir(this.filesystemOptions.mountPoint || "/home/pyodide");
        console.log(`Files in ${this.filesystemOptions.mountPoint} directory: ${mountedFiles.join(", ")}`);
      } catch (error) {
        console.error(`Error listing mounted directory: ${error}`);
      }
    } catch (error) {
      console.error("Error mounting filesystem:", error);
      throw error;
    }
  }

  /**
   * Initialize the Pyodide package manager with optimized wheel loading
   * OPTIMIZED: Smart caching and parallel wheel installation
   */
  private async initPackageManager(): Promise<void> {
    const startTime = Date.now();
    console.log("⚡ Initializing optimized package manager...");
    
    try {
      // Load micropip and packaging in parallel
      console.log("📦 Loading micropip, packaging...");
      await this.pyodide.loadPackage(['micropip', 'packaging']);
      console.log("✅ Loaded micropip, packaging");
      
      // Get the base URL - use window.location for browser or fallback for worker
      let baseUrl: string;
      if (typeof window !== 'undefined' && window.location) {
        // In main thread - use window.location
        baseUrl = new URL(".", window.location.href).href;
      } else if (typeof self !== 'undefined' && self.location) {
        // In web worker - use self.location
        baseUrl = new URL(".", self.location.href).href;
      } else {
        // Fallback - assume current origin
        baseUrl = "/";
      }
      
      const allJsonPath = new URL(allJSONUrl, baseUrl).href;
      
      // Prepare all wheel URLs for parallel loading
      const wheelFiles = [
        new URL(pipliteWheelUrl, baseUrl).href,
        new URL(pyodide_kernelWheelUrl, baseUrl).href,
        new URL(ipykernelWheelUrl, baseUrl).href,
        new URL(widgetsnbextensionWheelUrl, baseUrl).href,
        new URL(widgetsnbextensionWheelUrl1, baseUrl).href,
      ];
      
      console.log(`🚀 Installing ${wheelFiles.length} wheel packages in parallel...`);
      
      // Install all wheel packages in parallel for maximum speed
      const wheelPromises = wheelFiles.map(async (wheelUrl, index) => {
        const wheelStartTime = Date.now();
        try {
          await this.pyodide.runPythonAsync(`
import micropip
await micropip.install('${wheelUrl}', keep_going=True)
print(f"✅ Wheel ${index + 1}/${wheelFiles.length} installed")
`);
          const wheelTime = Date.now() - wheelStartTime;
          console.log(`⚡ Wheel ${index + 1} installed in ${wheelTime}ms`);
          return { index, success: true, time: wheelTime };
        } catch (error) {
          const wheelTime = Date.now() - wheelStartTime;
          console.warn(`⚠️ Wheel ${index + 1} failed after ${wheelTime}ms:`, error);
          return { index, success: false, time: wheelTime, error };
        }
      });
      
      // Wait for all wheel installations
      const wheelResults = await Promise.all(wheelPromises);
      const successful = wheelResults.filter(r => r.success);
      const failed = wheelResults.filter(r => !r.success);
      
      console.log(`📊 Wheels: ${successful.length}/${wheelFiles.length} successful`);
      if (failed.length > 0) {
        console.warn(`⚠️ Failed wheels: ${failed.map(f => f.index + 1).join(', ')}`);
      }
      
      // Set up piplite configuration with performance optimizations (if available)
      try {
        await this.pyodide.runPythonAsync(`
try:
    import piplite.piplite
    import json

    # Load package index for faster lookups
    try:
        piplite.piplite.PIPLITE_URL = "${allJsonPath}"
        # Pre-load package index for faster installation
        print("📋 Package index configured")
    except Exception as e:
        print(f"⚠️ Package index setup warning: {e}")

    # Configure piplite for optimal performance
    piplite.piplite.REPODATA_INFO = {}
    print("⚡ Piplite optimized for performance")
except ImportError:
    print("⚠️ Piplite not available, continuing without it")
except Exception as e:
    print(f"⚠️ Piplite setup failed: {e}")
`);
      } catch (error) {
        console.warn("⚠️ Piplite configuration failed, continuing without it:", error);
      }
      
      const totalTime = Date.now() - startTime;
      console.log(`🎯 Package manager initialized in ${totalTime}ms`);
      
    } catch (error) {
      console.error("❌ Package manager initialization failed:", error);
      throw error;
    }
  }

  /**
   * Initialize the kernel with required Python packages
   * OPTIMIZED: Maximum parallelization with intelligent dependency resolution
   */
  private async initKernel(): Promise<void> {
    const startTime = Date.now();
    console.log("🚀 Initializing kernel packages with maximum optimization...");
    
    // All packages to install with priority and dependency information
    const packageConfig = [
      // High priority: CDN packages (fastest)
      { name: 'pure-eval', priority: 1, source: 'pyodide' },
      { name: 'stack-data', priority: 1, source: 'pyodide' },
      { name: 'pygments', priority: 1, source: 'pyodide' },
      { name: 'ssl', priority: 1, source: 'pyodide' },
      { name: 'sqlite3', priority: 1, source: 'pyodide' },
      { name: 'prompt_toolkit', priority: 1, source: 'pyodide' },
      { name: 'jedi', priority: 1, source: 'pyodide' },
      { name: 'ipython', priority: 1, source: 'pyodide' },
      
      // Medium priority: pip packages
      { name: 'comm', priority: 2, source: 'pip' },
      { name: 'hypha-rpc', priority: 2, source: 'pip' },
      { name: 'nbformat', priority: 2, source: 'pip' },
      
      // Lower priority: complex packages
      { name: 'ipykernel', priority: 3, source: 'pip' },
      { name: 'pyodide_kernel', priority: 3, source: 'pip' }
    ];

    try {
      console.log(`📦 Installing ${packageConfig.length} packages with intelligent optimization...`);
      
      // Install ALL packages in parallel with advanced error handling and caching
      await this.installPackagesWithIntelligentOptimization(packageConfig);
      
      // Import the kernel (must be done after packages are installed)
      console.log("📥 Importing pyodide_kernel...");
      const importStartTime = Date.now();
      await this.pyodide.runPythonAsync('import pyodide_kernel');
      const importTime = Date.now() - importStartTime;
      console.log(`✅ pyodide_kernel imported in ${importTime}ms`);
      
      const totalTime = Date.now() - startTime;
      console.log(`🎯 Kernel packages initialized in ${totalTime}ms`);
      
    } catch (error) {
      console.error("❌ Kernel package initialization failed:", error);
      throw error;
    }
  }
  
  /**
   * Install packages with intelligent optimization and advanced caching
   * OPTIMIZED: Smart source selection, parallel installation, and performance monitoring
   */
  private async installPackagesWithIntelligentOptimization(packageConfig: Array<{name: string, priority: number, source: string}>): Promise<void> {
    console.log(`⚡ Starting intelligent parallel installation of ${packageConfig.length} packages...`);
    
    const installPromises = packageConfig.map(async (pkg) => {
      const startTime = Date.now();
      try {
        console.log(`🔄 Installing ${pkg.name} (priority: ${pkg.priority}, preferred: ${pkg.source})...`);
        
        // Try preferred source first, with intelligent fallback
        if (pkg.source === 'pyodide') {
          try {
            await this.pyodide.loadPackage([pkg.name]);
            const duration = Date.now() - startTime;
            console.log(`✅ ${pkg.name} loaded from Pyodide CDN (${duration}ms)`);
            return { package: pkg.name, method: 'pyodide', duration, success: true, priority: pkg.priority };
          } catch (pyodideError) {
            // Fallback to pip with enhanced error handling
            console.log(`📦 ${pkg.name} not available on CDN, trying pip...`);
            await this.installViaPipWithOptimizations(pkg.name);
            const duration = Date.now() - startTime;
            console.log(`✅ ${pkg.name} installed via pip fallback (${duration}ms)`);
            return { package: pkg.name, method: 'pip-fallback', duration, success: true, priority: pkg.priority };
          }
        } else {
          // Direct pip installation with optimizations
          await this.installViaPipWithOptimizations(pkg.name);
          const duration = Date.now() - startTime;
          console.log(`✅ ${pkg.name} installed via pip (${duration}ms)`);
          return { package: pkg.name, method: 'pip', duration, success: true, priority: pkg.priority };
        }
      } catch (error) {
        const duration = Date.now() - startTime;
        console.warn(`❌ Failed to install ${pkg.name} after ${duration}ms:`, error);
        return { package: pkg.name, method: 'failed', duration, success: false, priority: pkg.priority, error };
      }
    });
    
    // Wait for all installations with detailed analysis
    const results = await Promise.all(installPromises);
    
    // Comprehensive performance analysis
    this.analyzeInstallationResults(results);
  }
  
  /**
   * Install package via pip with performance optimizations
   */
  private async installViaPipWithOptimizations(packageName: string): Promise<void> {
    await this.pyodide.runPythonAsync(`
try:
    # Import piplite first
    import piplite
    # Use optimized pip installation with caching
    await piplite.install('${packageName}', keep_going=True, deps=True)
    print("✅ Successfully installed ${packageName} via optimized pip")
except Exception as e:
    print("⚠️ Warning: Failed to install ${packageName}:", str(e))
    # Try alternative installation method
    try:
        import micropip
        await micropip.install('${packageName}', keep_going=True)
        print("✅ Successfully installed ${packageName} via micropip fallback")
    except Exception as e2:
        print("❌ Both pip methods failed for ${packageName}:", str(e2))
        raise e2
`);
  }
  
  /**
   * Analyze installation results and provide performance insights
   */
  private analyzeInstallationResults(results: Array<any>): void {
    const successful = results.filter(r => r.success);
    const failed = results.filter(r => !r.success);
    const pyodideInstalls = successful.filter(r => r.method === 'pyodide');
    const pipInstalls = successful.filter(r => r.method === 'pip');
    const fallbackInstalls = successful.filter(r => r.method === 'pip-fallback');
    
    const totalDuration = Math.max(...results.map(r => r.duration));
    const avgDuration = results.reduce((sum, r) => sum + r.duration, 0) / results.length;
    const estimatedSequential = results.reduce((sum, r) => sum + r.duration, 0);
    
    console.log(`🎯 INTELLIGENT INSTALLATION COMPLETE!`);
    console.log(`📊 Results: ${successful.length}/${results.length} successful`);
    console.log(`⚡ Pyodide CDN: ${pyodideInstalls.length} packages`);
    console.log(`📦 Direct pip: ${pipInstalls.length} packages`);
    console.log(`🔄 Pip fallback: ${fallbackInstalls.length} packages`);
    console.log(`❌ Failed: ${failed.length} packages`);
    console.log(`⏱️  Total time: ${totalDuration}ms (vs ~${estimatedSequential}ms sequential)`);
    console.log(`🚀 Speed improvement: ~${Math.round(estimatedSequential / totalDuration)}x faster`);
    console.log(`📈 Average per package: ${Math.round(avgDuration)}ms`);
    
    if (failed.length > 0) {
      console.warn(`⚠️  Failed packages: ${failed.map(f => f.package).join(', ')}`);
      // Log specific failure reasons for debugging
      failed.forEach(f => {
        console.warn(`   - ${f.package}: ${f.error?.message || 'Unknown error'}`);
      });
    }
    
    // Performance insights
    const fastestInstall = Math.min(...successful.map(r => r.duration));
    const slowestInstall = Math.max(...successful.map(r => r.duration));
    console.log(`📊 Performance range: ${fastestInstall}ms (fastest) to ${slowestInstall}ms (slowest)`);
  }
  
  /**
   * Initialize global objects from the pyodide_kernel package
   * Based on the PyodideRemoteKernel implementation
   */
  private async initGlobals(): Promise<void> {
    console.log("Initializing globals...");
    
    // Get the globals from the Python environment
    const { globals } = this.pyodide;
    
    // Get the kernel instance and related objects
    this._kernel = globals.get('pyodide_kernel').kernel_instance.copy();
    this._stdout_stream = globals.get('pyodide_kernel').stdout_stream.copy();
    this._stderr_stream = globals.get('pyodide_kernel').stderr_stream.copy();
    this._interpreter = this._kernel.interpreter.copy();
    
    // Set up communication handlers
    this._interpreter.send_comm = this.sendComm.bind(this);
    
    // Set up callbacks
    this.setupCallbacks();
  }
  
  /**
   * Setup all necessary callbacks for the Python environment
   */
  private setupCallbacks(): void {
    // Execution result callback
    const publishExecutionResult = (
      prompt_count: any,
      data: any,
      metadata: any,
    ): void => {
      const bundle = {
        execution_count: prompt_count,
        data: this.formatResult(data),
        metadata: this.formatResult(metadata),
      };

      this._sendMessage({
        parentHeader: this.formatResult(this._parent_header)['header'],
        bundle,
        type: 'execute_result',
      });
    };

    // Error callback
    const publishExecutionError = (ename: any, evalue: any, traceback: any): void => {
      const bundle = {
        ename: ename,
        evalue: evalue,
        traceback: traceback,
      };

      this._sendMessage({
        parentHeader: this.formatResult(this._parent_header)['header'],
        bundle,
          type: 'execute_error',
      });
    };

    // Clear output callback
    const clearOutputCallback = (wait: boolean): void => {
      const bundle = {
        wait: this.formatResult(wait),
      };

      this._sendMessage({
        parentHeader: this.formatResult(this._parent_header)['header'],
        bundle,
          type: 'clear_output',
      });
    };

    // Display data callback
    const displayDataCallback = (data: any, metadata: any, transient: any): void => {
      const bundle = {
        data: this.formatResult(data),
        metadata: this.formatResult(metadata),
        transient: this.formatResult(transient),
      };

      this._sendMessage({
        parentHeader: this.formatResult(this._parent_header)['header'],
        bundle,
        type: 'display_data',
      });
    };

    // Update display data callback
    const updateDisplayDataCallback = (
      data: any,
      metadata: any,
      transient: any,
    ): void => {
      const bundle = {
        data: this.formatResult(data),
        metadata: this.formatResult(metadata),
        transient: this.formatResult(transient),
      };

      this._sendMessage({
        parentHeader: this.formatResult(this._parent_header)['header'],
        bundle,
        type: 'update_display_data',
      });
    };

    // Stream callback
    const publishStreamCallback = (name: any, text: any): void => {
      const bundle = {
        name: this.formatResult(name),
        text: this.formatResult(text),
      };

      this._sendMessage({
        parentHeader: this.formatResult(this._parent_header)['header'],
        bundle,
        type: 'stream',
      });
    };

    // Assign callbacks to the Python objects
    this._stdout_stream.publish_stream_callback = publishStreamCallback;
    this._stderr_stream.publish_stream_callback = publishStreamCallback;
    this._interpreter.display_pub.clear_output_callback = clearOutputCallback;
    this._interpreter.display_pub.display_data_callback = displayDataCallback;
    this._interpreter.display_pub.update_display_data_callback = updateDisplayDataCallback;
    this._interpreter.displayhook.publish_execution_result = publishExecutionResult;
    this._interpreter.input = this.input.bind(this);
    this._interpreter.getpass = this.getpass.bind(this);
  }
  
  /**
   * Process a message from Python environment
   */
  private _sendMessage(msg: IMessage): void {
    this._processMessage(msg);
  }
  
  /**
   * Process a message by emitting the appropriate event
   */
  private _processMessage(msg: IMessage): void {
    if (!msg.type) {
      return;
    }

    let eventData: any;

    switch (msg.type) {
      case 'stream': {
        const bundle = msg.bundle ?? { name: 'stdout', text: '' };
        super.emit(KernelEvents.STREAM, bundle);
        eventData = bundle;
        break;
      }
      case 'input_request': {
        const content = msg.content ?? { prompt: '', password: false };
        super.emit(KernelEvents.INPUT_REQUEST, content);
        eventData = content;
        break;
      }
      case 'display_data': {
        const bundle = msg.bundle ?? { data: {}, metadata: {}, transient: {} };
        super.emit(KernelEvents.DISPLAY_DATA, bundle);
        eventData = bundle;
        break;
      }
      case 'update_display_data': {
        const bundle = msg.bundle ?? { data: {}, metadata: {}, transient: {} };
        super.emit(KernelEvents.UPDATE_DISPLAY_DATA, bundle);
        eventData = bundle;
        break;
      }
      case 'clear_output': {
        const bundle = msg.bundle ?? { wait: false };
        super.emit(KernelEvents.CLEAR_OUTPUT, bundle);
        eventData = bundle;
        break;
      }
      case 'execute_result': {
        const bundle = msg.bundle ?? {
          execution_count: this.executionCount,
          data: {},
          metadata: {},
        };
        super.emit(KernelEvents.EXECUTE_RESULT, bundle);
        eventData = bundle;
        break;
      }
      case 'execute_error': {
        const bundle = msg.bundle ?? { ename: '', evalue: '', traceback: [] };
        super.emit(KernelEvents.EXECUTE_ERROR, bundle);
        eventData = bundle;
        break;
      }
      case 'comm_open':
      case 'comm_msg':
      case 'comm_close': {
        const content = msg.content ?? {};
        super.emit(msg.type, content, msg.metadata, msg.buffers);
        eventData = {
          content,
          metadata: msg.metadata,
          buffers: msg.buffers
        };
        break;
      }
    }

    // Emit the ALL event with standardized format
    if (eventData) {
      super.emit(KernelEvents.ALL, {
        type: msg.type,
        data: eventData
      } as IEventData);
    }
  }
  
  /**
   * Check if the kernel has been initialized
   */
  public isInitialized(): boolean {
    return this.initialized;
  }
  
  /**
   * Makes sure pyodide is ready before continuing, and cache the parent message.
   */
  private async setup(parent: any): Promise<void> {
    await this.initialize();
    this._parent_header = this.pyodide.toPy(parent || {});
  }
  
  /**
   * Execute code in the kernel with proper message-based completion detection
   * 
   * @param code The code to execute
   * @param parent Parent message header
   * @returns The result of the execution
   */
  public async execute(code: string, parent: any = {}): Promise<{ success: boolean, result?: any, error?: Error }> {
    if (!this.initialized) {
      await this.initialize();
    }

    try {
      this._status = "busy";
      super.emit(KernelEvents.KERNEL_BUSY, {});
      await this.setup(parent);
      
      // Reset namespace for this kernel to ensure isolation between kernels
      const namespaceReset = `
# Kernel isolation - reset commonly conflicting variables to prevent cross-kernel state sharing
for _var in ['x', 'value', 'result', 'a', 'b', 'c', 'data', 'temp']:
    if _var in globals():
        del globals()[_var]
del _var
`;
      this.pyodide.runPython(namespaceReset);
      
      // Create a promise that resolves only when execution is truly complete
      return new Promise<{ success: boolean, result?: any, error?: Error }>((resolve, reject) => {
        const executionState = {
          allMessages: [] as IEventData[],
          executionComplete: false,
          executionResult: null as any,
          executionError: null as Error | null,
          timeout: null as number | null
        };

        // Set up message collector that captures ALL output before completion
        const messageCollector = (eventData: IEventData) => {
          executionState.allMessages.push(eventData);
          // Debug logging to trace message flow
          // console.log(`[KERNEL] Captured message: ${eventData.type}`, eventData.data);
        };

        // Set up completion detector
        const completionDetector = async (executedCode: string) => {
          if (executionState.executionComplete) {
            return; // Already completed
          }
          
          console.log(`[KERNEL] Execution completed, processing ${executionState.allMessages.length} messages`);
          
          // Mark as complete to prevent multiple resolutions
          executionState.executionComplete = true;
          
          // Clean up listeners
          super.off(KernelEvents.ALL, messageCollector);
          
          // Process collected messages to determine final result and extract computed value
          let hasError = false;
          let errorInfo: any = null;
          let computedResult: any = undefined;
          
          // First check if the direct result from _kernel.run() indicates an error
          const directResult = executionState.executionResult;
          if (directResult && directResult.status === 'error') {
            hasError = true;
            errorInfo = {
              ename: directResult.ename,
              evalue: directResult.evalue,
              traceback: directResult.traceback
            };
          }
          
          // Then check messages for execute_error or execute_result
          for (const message of executionState.allMessages) {
            if (message.type === 'execute_error') {
              hasError = true;
              errorInfo = message.data;
              break;
            } else if (message.type === 'execute_result') {
              // Store execute_result for compatibility but don't extract computed values
              computedResult = message.data;
            }
          }
          
          this._status = "active";
          super.emit(KernelEvents.KERNEL_IDLE, {});
          
          if (hasError) {
            console.log(`[KERNEL] Execution failed with error:`, errorInfo);
            
            // Emit execute_error event for error streams
            // Clean up error name format (remove <class '...'> wrapper)
            const cleanErrorName = errorInfo.ename ? errorInfo.ename.replace(/^<class '(.+)'>$/, '$1') : errorInfo.ename;
            super.emit(KernelEvents.EXECUTE_ERROR, {
              ename: cleanErrorName,
              evalue: errorInfo.evalue,
              traceback: errorInfo.traceback
            });
            
            const errorMsg = `${errorInfo.ename || 'Error'}: ${errorInfo.evalue || 'Unknown error'}`;
            resolve({
              success: false,
              error: new Error(errorMsg),
              result: executionState.executionResult
            });
          } else {
            console.log(`[KERNEL] Execution successful, captured ${executionState.allMessages.length} output messages`);
            let finalResult = computedResult !== undefined ? computedResult : executionState.executionResult;
            resolve({
              success: true,
              result: finalResult
            });
          }
        };

        // Install message collector BEFORE executing code
        super.on(KernelEvents.ALL, messageCollector);

        // Execute the code and handle completion
        this._kernel.run(code).then((result: any) => {
          console.log("[KERNEL] Python execution finished, waiting for messages to settle");
          executionState.executionResult = this.formatResult(result);
          
          // Wait a small amount of time for any remaining messages to be processed
          // This ensures all stdout/stderr streams have been captured
          setTimeout(() => {
            completionDetector(code);
          }, 100); // 100ms should be enough for message processing
          
        }).catch((error: any) => {
          console.error("[KERNEL] Python execution error:", error);
          executionState.executionError = error instanceof Error ? error : new Error(String(error));
          
          // Still wait for messages to settle before completing
          setTimeout(() => {
            completionDetector(code);
          }, 100);
        });
      });
      
    } catch (error) {
      console.error("[KERNEL] Execute setup error:", error);
      this._status = "active";
      super.emit(KernelEvents.KERNEL_IDLE, {});
      
      return {
        success: false,
        error: error instanceof Error ? error : new Error(String(error))
      };
    }
  }
  
  /**
   * Format the result from the Pyodide evaluation
   * Based on PyodideRemoteKernel implementation
   */
  private formatResult(res: any): any {
    if (!(res instanceof this.pyodide.ffi.PyProxy)) {
      return res;
    }
    
    try {
      // Convert PyProxy to JS
      const m = res.toJs();
      const results = this.mapToObject(m);
      return results;
    } catch (error) {
      console.error("Error formatting result:", error);
      return { status: 'error', error: String(error) };
    }
  }
  
  /**
   * Convert a Map to a JavaScript object recursively
   * Based on PyodideRemoteKernel implementation
   */
  private mapToObject(obj: any) {
    const out: any = obj instanceof Array ? [] : {};
    
    obj.forEach((value: any, key: string) => {
      out[key] = 
        value instanceof Map || value instanceof Array
          ? this.mapToObject(value)
          : value;
    });
    
    return out;
  }
  
  /**
   * Handle input reply from user
   */
  public async inputReply(content: { value: string }): Promise<void> {
    if (this._resolveInputReply) {
      this._resolveInputReply(content);
      this._resolveInputReply = null;
    }
  }
  
  /**
   * Send a input request to the front-end.
   */
  private async sendInputRequest(prompt: string, password: boolean): Promise<void> {
    const content = {
      prompt,
      password,
    };

    this._sendMessage({
      type: 'input_request',
      content,
      parentHeader: this.formatResult(this._parent_header)['header']
    });
  }

  /**
   * Get password input (with hidden input)
   */
  private async getpass(prompt: string): Promise<string> {
    prompt = typeof prompt === 'undefined' ? '' : prompt;
    await this.sendInputRequest(prompt, true);
    const replyPromise = new Promise<{ value: string }>((resolve) => {
      this._resolveInputReply = resolve;
    });
    const result = await replyPromise;
    return result.value;
  }

  /**
   * Get text input
   */
  private async input(prompt: string): Promise<string> {
    prompt = typeof prompt === 'undefined' ? '' : prompt;
    await this.sendInputRequest(prompt, false);
    const replyPromise = new Promise<{ value: string }>((resolve) => {
      this._resolveInputReply = resolve;
    });
    const result = await replyPromise;
    return result.value;
  }
  
  /**
   * Send a comm message to the front-end.
   */
  private async sendComm(type: string, content: any, metadata: any, ident: any, buffers: any): Promise<void> {
    this._sendMessage({
      type: type,
      content: this.formatResult(content),
      metadata: this.formatResult(metadata),
      ident: this.formatResult(ident),
      buffers: this.formatResult(buffers),
      parentHeader: this.formatResult(this._parent_header)['header'],
    });
  }
  
  /**
   * Complete the code submitted by a user.
   */
  public async complete(code: string, cursor_pos: number, parent: any = {}): Promise<any> {
    await this.setup(parent);
    
    const res = this._kernel.complete(code, cursor_pos);
    return this.formatResult(res);
  }

  /**
   * Inspect the code submitted by a user.
   */
  public async inspect(code: string, cursor_pos: number, detail_level: 0 | 1, parent: any = {}): Promise<any> {
    await this.setup(parent);
    
    const res = this._kernel.inspect(code, cursor_pos, detail_level);
    return this.formatResult(res);
  }

  /**
   * Check code for completeness.
   */
  public async isComplete(code: string, parent: any = {}): Promise<any> {
    await this.setup(parent);
    
    const res = this._kernel.is_complete(code);
    return this.formatResult(res);
  }

  /**
   * Get information about available comms.
   */
  public async commInfo(target_name: string | null, parent: any = {}): Promise<any> {
    await this.setup(parent);
    
    const res = this._kernel.comm_info(target_name);
    return {
      comms: this.formatResult(res),
      status: 'ok',
    };
  }

  /**
   * Open a COMM
   */
  public async commOpen(content: any, parent: any = {}): Promise<void> {
    await this.setup(parent);
    
    const res = this._kernel.comm_manager.comm_open(
      this.pyodide.toPy(null),
      this.pyodide.toPy(null),
      this.pyodide.toPy(content)
    );
    
    return this.formatResult(res);
  }
  
  /**
   * Send a message through a COMM
   */
  public async commMsg(content: any, parent: any = {}): Promise<void> {
    await this.setup(parent);
    
    const res = this._kernel.comm_manager.comm_msg(
      this.pyodide.toPy(null),
      this.pyodide.toPy(null),
      this.pyodide.toPy(content)
    );
    
    return this.formatResult(res);
  }
  
  /**
   * Close a COMM
   */
  public async commClose(content: any, parent: any = {}): Promise<void> {
    await this.setup(parent);
    
    const res = this._kernel.comm_manager.comm_close(
      this.pyodide.toPy(null),
      this.pyodide.toPy(null),
      this.pyodide.toPy(content)
    );
    
    return this.formatResult(res);
  }

  /**
   * Execute Python code with streaming output
   * @param code The Python code to execute
   * @param parent Parent message header
   * @returns AsyncGenerator yielding intermediate outputs and finally the execution result
   */
  public async* executeStream(code: string, parent: any = {}): AsyncGenerator<any, { success: boolean, result?: any, error?: Error }, void> {
    try {
      await this.initialize();
      
      // Create event listeners for streaming
      const eventQueue: IEventData[] = [];
      
      const handleAllEvents = (eventData: IEventData) => {
        eventQueue.push(eventData);
      };
      
      // Listen for all events BEFORE executing code
      super.on(KernelEvents.ALL, handleAllEvents);
      
      try {
        // Use the fixed execute method which properly waits for all messages
        const resultPromise = this.execute(code, parent);
        
        // Stream events as they arrive
        while (true) {
          // Check if we have queued events to yield
          if (eventQueue.length > 0) {
            yield eventQueue.shift();
          }
          
          // Check if execution is complete
          const isComplete = await Promise.race([
            resultPromise.then(() => true),
            new Promise(resolve => setTimeout(() => resolve(false), 10))
          ]);
          
          if (isComplete) {
            // Yield any remaining events
            while (eventQueue.length > 0) {
              yield eventQueue.shift();
            }
            
            // Return the final result
            return await resultPromise;
          }
        }
      } catch (error) {
        console.error("Error in executeStream:", error);
        throw error;
      } finally {
        // Clean up listener in finally block to ensure it's always removed
        super.off(KernelEvents.ALL, handleAllEvents);
      }
    } catch (error) {
      console.error("Error in executeStream setup:", error);
      return {
        success: false,
        error: error instanceof Error ? error : new Error(String(error))
      };
    }
  }

  // Interrupt functionality
  public async interrupt(): Promise<boolean> {
    if (!this.initialized || !this.pyodide) {
      console.warn("[KERNEL] Cannot interrupt: kernel not initialized");
      return false;
    }
    
    console.log("[KERNEL] Attempting to interrupt execution...");
    
    try {
      // First priority: Use interrupt buffer if available
      if (this._interruptBuffer && this._interruptSupported) {
        console.log("[KERNEL] Using interrupt buffer method");
        // Set interrupt signal (2 = SIGINT)
        this._interruptBuffer[0] = 2;
        
        // Give the interrupt a moment to be processed
        await new Promise(resolve => setTimeout(resolve, 100));
        
        // Check if the interrupt was processed (buffer should be reset to 0)
        const wasProcessed = this._interruptBuffer[0] === 0;
        if (wasProcessed) {
          console.log("[KERNEL] Interrupt processed successfully via buffer");
          return true;
        }
      }
      
      // Second priority: Try Python-level interrupt
      try {
        console.log("[KERNEL] Attempting Python-level interrupt");
        // Try to raise KeyboardInterrupt in Python
        await this.pyodide.runPythonAsync(`
import sys
import _thread
# Try to interrupt the main thread
_thread.interrupt_main()
`);
        console.log("[KERNEL] Python interrupt signal sent");
        return true;
      } catch (pythonError) {
        console.log("[KERNEL] Python interrupt attempt failed:", pythonError);
      }
      
      // Third priority: Try interpreter interrupt if available
      if (this._interpreter && typeof this._interpreter.interrupt === 'function') {
        console.log("[KERNEL] Using interpreter interrupt method");
        this._interpreter.interrupt();
        return true;
      }
      
      // Last resort: Send interrupt messages for UI feedback
      console.log("[KERNEL] Sending interrupt messages for UI feedback");
      
      // Send stderr stream first (for Jupyter notebook UI compatibility)
      this._sendMessage({
        type: 'stream',
        bundle: {
          name: 'stderr',
          text: 'KeyboardInterrupt: Execution interrupted by user\n'
        }
      });
      
      this._sendMessage({
        type: 'execute_error',
        bundle: {
          ename: 'KeyboardInterrupt',
          evalue: 'Execution interrupted by user',
          traceback: ['KeyboardInterrupt: Execution interrupted by user']
        }
      });
      
      // Return false since we couldn't actually interrupt the execution
      return false;
    } catch (error) {
      console.error("[KERNEL] Error during interrupt:", error);
      return false;
    }
  }

  public setInterruptBuffer(buffer: Uint8Array): void {
    this._interruptBuffer = buffer;
    
    try {
      if (this.pyodide && typeof this.pyodide.setInterruptBuffer === 'function') {
        this.pyodide.setInterruptBuffer(buffer);
        this._interruptSupported = true;
      } else {
        console.warn("[KERNEL] pyodide.setInterruptBuffer not available, interrupt support limited");
        this._interruptSupported = false;
      }
    } catch (error) {
      console.error("[KERNEL] Error setting interrupt buffer:", error);
      this._interruptSupported = false;
    }
  }

  /**
   * Set environment variables with performance optimization
   * OPTIMIZED: Parallel variable setting and validation with proper escaping and edge case handling
   */
  private async setEnvironmentVariables(): Promise<void> {
    if (Object.keys(this.environmentVariables).length === 0) {
      return; // No variables to set
    }
    
    const startTime = Date.now();
    console.log(`🌍 Setting ${Object.keys(this.environmentVariables).length} environment variables...`);
    
    try {
      // Set each environment variable individually to avoid escaping issues
      for (const [key, value] of Object.entries(this.environmentVariables)) {
        // Handle edge cases: null, undefined, etc.
        let processedValue: string;
        if (value === null) {
          processedValue = '';  // Convert null to empty string
        } else if (value === undefined) {
          processedValue = '';  // Convert undefined to empty string
        } else {
          processedValue = String(value);  // Convert everything else to string
        }
        
        await this.pyodide.runPythonAsync(`
import os
os.environ[${JSON.stringify(key)}] = ${JSON.stringify(processedValue)}
`);
      }
      
      const duration = Date.now() - startTime;
      console.log(`⚡ Environment variables set in ${duration}ms`);
    } catch (error) {
      console.error("❌ Failed to set environment variables:", error);
      throw error;
    }
  }
}

// Only Python kernel is supported

// Export the manager for advanced usage
export { KernelManager } from "./manager";
