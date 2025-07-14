/**
 * Pyodide CDN Loader Utility
 * Dynamically loads Pyodide from CDN for both main thread and web workers
 */

const PYODIDE_CDN_URL = 'https://cdn.jsdelivr.net/pyodide/v0.28.0/full/pyodide.js';
const PYODIDE_INDEX_URL = 'https://cdn.jsdelivr.net/pyodide/v0.28.0/full/';

// Global flag to track if Pyodide is loaded
let pyodideLoaded = false;
let pyodideLoadPromise: Promise<any> | null = null;

/**
 * Load Pyodide script dynamically in main thread
 */
function loadPyodideScript(): Promise<void> {
  return new Promise((resolve, reject) => {
    // Check if script is already loaded
    if (typeof (globalThis as any).loadPyodide !== 'undefined') {
      resolve();
      return;
    }

    const script = document.createElement('script');
    script.src = PYODIDE_CDN_URL;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error(`Failed to load Pyodide from ${PYODIDE_CDN_URL}`));
    document.head.appendChild(script);
  });
}

/**
 * Load Pyodide in web worker using importScripts
 */
function loadPyodideInWorker(): Promise<void> {
  return new Promise((resolve, reject) => {
    try {
      // Check if we're in a worker environment
      if (typeof importScripts === 'undefined') {
        reject(new Error('importScripts is not available - not in a worker context'));
        return;
      }

      // Check if already loaded
      if (typeof (globalThis as any).loadPyodide !== 'undefined') {
        resolve();
        return;
      }

      importScripts(PYODIDE_CDN_URL);
      resolve();
    } catch (error) {
      reject(new Error(`Failed to import Pyodide in worker: ${error}`));
    }
  });
}

/**
 * Initialize Pyodide with proper configuration
 */
export async function loadPyodide(config: any = {}): Promise<any> {
  // Return existing promise if already loading
  if (pyodideLoadPromise) {
    return pyodideLoadPromise;
  }

  pyodideLoadPromise = (async () => {
    try {
      // Determine if we're in a worker or main thread
      const isWorker = typeof importScripts !== 'undefined';
      
      // Load the Pyodide script
      if (isWorker) {
        await loadPyodideInWorker();
      } else {
        await loadPyodideScript();
      }

      // Get the global loadPyodide function
      const globalLoadPyodide = (globalThis as any).loadPyodide;
      if (!globalLoadPyodide) {
        throw new Error('loadPyodide function not found after script load');
      }

      // Configure default options
      const defaultConfig = {
        indexURL: PYODIDE_INDEX_URL,
        ...config
      };

      // Initialize Pyodide
      const pyodide = await globalLoadPyodide(defaultConfig);
      pyodideLoaded = true;
      
      console.log('✅ Pyodide loaded successfully from CDN');
      return pyodide;
    } catch (error) {
      pyodideLoadPromise = null; // Reset on failure
      throw error;
    }
  })();

  return pyodideLoadPromise;
}

/**
 * Check if Pyodide is already loaded
 */
export function isPyodideLoaded(): boolean {
  return pyodideLoaded && typeof (globalThis as any).loadPyodide !== 'undefined';
}

/**
 * Get Pyodide CDN URL for external use
 */
export function getPyodideCDNUrl(): string {
  return PYODIDE_CDN_URL;
}

/**
 * Get Pyodide index URL for external use
 */
export function getPyodideIndexUrl(): string {
  return PYODIDE_INDEX_URL;
} 