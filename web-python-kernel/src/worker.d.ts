// Type declarations for worker-loader and webpack globals

declare module "*.worker.ts" {
  class WebpackWorker extends Worker {
    constructor();
  }
  export default WebpackWorker;
}

declare module "*.worker.js" {
  class WebpackWorker extends Worker {
    constructor();
  }
  export default WebpackWorker;
}

// Webpack globals
declare const __webpack_public_path__: string;
declare const __webpack_require__: any;

// Worker-loader runtime
declare module "!!*" {
  const value: any;
  export default value;
}

// Browser globals
declare global {
  interface Window {
    WebPythonKernel: any;
  }
}

export {}; 