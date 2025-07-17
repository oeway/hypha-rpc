// Kernel Manager Tests - Real Implementation
import { expect } from 'chai';
import { KernelManager, KernelMode, KernelEvents, KernelLanguage, IKernelManagerOptions } from '../src/manager';

describe('Kernel Manager Tests', function() {
  this.timeout(120000); // Generous timeout for real Pyodide

  let manager: KernelManager;

  const managerTestOptions: IKernelManagerOptions = {
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
    manager = new KernelManager(managerTestOptions);
  });

  afterEach('Cleanup kernel manager', async function() {
    if (manager) {
      await manager.destroyAll();
    }
  });

  describe('Kernel Lifecycle Management', function() {
    it('should create kernels with unique IDs', async function() {
      const kernelId1 = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });
      
      const kernelId2 = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });
      
      expect(kernelId1).to.be.a('string');
      expect(kernelId2).to.be.a('string');
      expect(kernelId1).to.not.equal(kernelId2);
    });

    it('should list kernels correctly', async function() {
      const kernelId = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });
      
      const kernels = await manager.listKernels();
      expect(kernels).to.have.length(1);
      expect(kernels[0].id).to.equal(kernelId);
      expect(kernels[0].mode).to.equal(KernelMode.MAIN_THREAD);
      expect(kernels[0].language).to.equal(KernelLanguage.PYTHON);
    });

    it('should destroy kernels properly', async function() {
      const kernelId = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });
      
      const initialKernels = await manager.listKernels();
      expect(initialKernels).to.have.length(1);

      await manager.destroyKernel(kernelId);
      
      const afterKernels = await manager.listKernels();
      expect(afterKernels).to.have.length(0);
    });

    it('should destroy all kernels at once', async function() {
      const kernelId1 = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });
      
      const kernelId2 = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });

      const initialKernels = await manager.listKernels();
      expect(initialKernels).to.have.length(2);

      await manager.destroyAll();
      
      const afterKernels = await manager.listKernels();
      expect(afterKernels).to.have.length(0);
    });
  });

  describe('Multiple Kernel Management', function() {
    it('should handle multiple kernels independently', async function() {
      const kernelId1 = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });
      
      const kernelId2 = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });

      // Execute different operations on each kernel
      const result1 = await manager.execute(kernelId1, 'print(5 + 5)');
      const result2 = await manager.execute(kernelId2, 'print(7 * 7)');

      expect(result1.success).to.be.true;
      expect(result2.success).to.be.true;
    });


  });

  describe('Event System', function() {
    it('should emit events during execution', async function() {
      const kernelId = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });
      
      let busyEventReceived = false;
      let idleEventReceived = false;

      manager.onKernelEvent(kernelId, KernelEvents.KERNEL_BUSY, () => {
        busyEventReceived = true;
      });
      
      manager.onKernelEvent(kernelId, KernelEvents.KERNEL_IDLE, () => {
        idleEventReceived = true;
      });

      await manager.execute(kernelId, '1 + 1');
      
      // Give events time to fire
      await new Promise(resolve => setTimeout(resolve, 100));
      
      expect(busyEventReceived).to.be.true;
      expect(idleEventReceived).to.be.true;
    });

    it('should handle event listeners for multiple kernels', async function() {
      const kernelId1 = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });
      
      const kernelId2 = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });

      let kernel1Events = 0;
      let kernel2Events = 0;

      manager.onKernelEvent(kernelId1, KernelEvents.KERNEL_BUSY, () => {
        kernel1Events++;
      });
      
      manager.onKernelEvent(kernelId2, KernelEvents.KERNEL_BUSY, () => {
        kernel2Events++;
      });

      await manager.execute(kernelId1, '1 + 1');
      await manager.execute(kernelId2, '2 + 2');
      
      // Give events time to fire
      await new Promise(resolve => setTimeout(resolve, 100));
      
      expect(kernel1Events).to.equal(1);
      expect(kernel2Events).to.equal(1);
    });

    it('should clean up event listeners when kernels are destroyed', async function() {
      const kernelId = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });

      let eventCount = 0;
      manager.onKernelEvent(kernelId, KernelEvents.KERNEL_BUSY, () => {
        eventCount++;
      });

      await manager.execute(kernelId, '1 + 1');
      await new Promise(resolve => setTimeout(resolve, 100));
      expect(eventCount).to.equal(1);

      // Destroy the kernel
      await manager.destroyKernel(kernelId);

      // Create a new kernel with the same ID shouldn't be possible
      // But if we create a new kernel, old listeners shouldn't fire
      const newKernelId = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });

      await manager.execute(newKernelId, '2 + 2');
      await new Promise(resolve => setTimeout(resolve, 100));
      
      // Event count should still be 1 (from the first kernel only)
      expect(eventCount).to.equal(1);
    });
  });

  describe('Error Handling', function() {
    it('should handle errors in individual kernels without affecting others', async function() {
      const kernelId1 = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });
      
      const kernelId2 = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });

      // Execute invalid code in kernel1
      const errorResult = await manager.execute(kernelId1, 'invalid_syntax +');
      expect(errorResult.success).to.be.false;

      // Execute valid code in kernel2 - should still work
      const successResult = await manager.execute(kernelId2, 'print(3 + 3)');
      expect(successResult.success).to.be.true;
    });

    it('should throw error when executing on non-existent kernel', async function() {
      try {
        await manager.execute('non-existent-kernel', '1 + 1');
        expect.fail('Should have thrown an error');
      } catch (error: any) {
        expect(error.message).to.include('not found');
      }
    });

    it('should handle kernel destruction gracefully', async function() {
      const kernelId = await manager.createKernel({
        mode: KernelMode.MAIN_THREAD,
        lang: KernelLanguage.PYTHON
      });

      // Destroy the kernel
      await manager.destroyKernel(kernelId);

      // Attempting to destroy again should not throw
      await manager.destroyKernel(kernelId);

      // Attempting to get the kernel should return undefined
      const kernel = manager.getKernel(kernelId);
      expect(kernel).to.be.undefined;
    });
  });
}); 