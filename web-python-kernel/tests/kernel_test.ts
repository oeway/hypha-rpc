// Individual Kernel Tests - Real Pyodide Implementation
import { expect } from 'chai';
import { KernelManager, KernelMode, KernelEvents, KernelLanguage, IKernelManagerOptions } from '../src/manager';

describe('Individual Kernel Tests', function() {
  this.timeout(120000); // Generous timeout for real Pyodide

  let manager: KernelManager;

  // Simplified options for individual kernel testing
  const kernelTestOptions: IKernelManagerOptions = {
  allowedKernelTypes: [
      { mode: KernelMode.MAIN_THREAD, language: KernelLanguage.PYTHON }
  ],
  pool: {
      enabled: false,
      poolSize: 1,
      autoRefill: false,
    preloadConfigs: []
  }
};

  beforeEach('Initialize kernel manager', function() {
    manager = new KernelManager(kernelTestOptions);
  });

  afterEach('Cleanup kernel manager', async function() {
    if (manager) {
      await manager.destroyAll();
    }
  });

  describe('Basic Kernel Operations', function() {
    it('should create a kernel with real Pyodide', async function() {
      const kernelId = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });
      
      expect(kernelId).to.be.a('string');
      expect(kernelId).to.not.be.empty;
      
      const kernel = manager.getKernel(kernelId);
      expect(kernel).to.exist;
      expect(kernel!.kernel.isInitialized()).to.be.true;
    });

    it('should execute basic Python arithmetic', async function() {
      const kernelId = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });
      
      const result = await manager.execute(kernelId, 'print(2 + 2)');
      expect(result.success).to.be.true;
    });

    it('should handle Python variables across executions', async function() {
      const kernelId = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });
      
      // Set variable
      await manager.execute(kernelId, 'x = 10');
      
      // Use variable  
      const result = await manager.execute(kernelId, 'print(x * 3)');
      expect(result.success).to.be.true;
    });

    it('should handle Python imports', async function() {
      const kernelId = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });
      
      const result = await manager.execute(kernelId, 'import math; print(math.sqrt(16))');
      expect(result.success).to.be.true;
    });

    it('should handle Python print statements', async function() {
      const kernelId = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });
      
      const result = await manager.execute(kernelId, 'print("Hello from real Python!")');
      expect(result.success).to.be.true;
    });
  });

  describe('Error Handling', function() {
    it('should handle Python syntax errors', async function() {
      const kernelId = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });
      
      const result = await manager.execute(kernelId, 'invalid_syntax +');
      expect(result.success).to.be.false;
      expect(result.error).to.exist;
    });

    it('should handle Python runtime errors', async function() {
      const kernelId = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });
      
      const result = await manager.execute(kernelId, '1 / 0');
      expect(result.success).to.be.false;
      expect(result.error).to.exist;
    });

    it('should handle undefined variable errors', async function() {
      const kernelId = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });
      
      const result = await manager.execute(kernelId, 'undefined_variable');
      expect(result.success).to.be.false;
      expect(result.error).to.exist;
    });
  });

  describe('Kernel State and Isolation', function() {
    it('should maintain state within a kernel', async function() {
      const kernelId = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });
      
      // Set multiple variables
      await manager.execute(kernelId, 'a = 5');
      await manager.execute(kernelId, 'b = 10');
      
      // Use both variables
      const result = await manager.execute(kernelId, 'print(a + b)');
      expect(result.success).to.be.true;
    });

    it('should handle complex Python code', async function() {
      const kernelId = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });
      
      const code = `
def factorial(n):
    if n <= 1:
        return 1
    return n * factorial(n - 1)

print(factorial(5))
`;
      
      const result = await manager.execute(kernelId, code);
      expect(result.success).to.be.true;
    });

    it('should handle Python loops', async function() {
      const kernelId = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });
      
      const code = `
total = 0
for i in range(5):
    total += i
print(total)
`;
      
      const result = await manager.execute(kernelId, code);
      expect(result.success).to.be.true;
    });
  });
});
