// Kernel Stream Tests - Real Implementation
import { expect } from 'chai';
import { KernelManager, KernelMode, KernelEvents, KernelLanguage, IKernelManagerOptions } from '../src/manager';

describe('Kernel Stream Tests', function() {
  this.timeout(120000); // Generous timeout for real Pyodide

  let manager: KernelManager;
  let kernelId: string;

  const streamTestOptions: IKernelManagerOptions = {
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

  // Helper function to wait for an event
  function waitForEvent(eventType: KernelEvents, timeout: number = 10000): Promise<any> {
    return new Promise((resolve, reject) => {
      const timeoutId = setTimeout(() => {
        manager.offKernelEvent(kernelId, eventType, listener);
        reject(new Error(`Timeout waiting for event: ${eventType}`));
      }, timeout);

      const listener = (data: any) => {
        clearTimeout(timeoutId);
        manager.offKernelEvent(kernelId, eventType, listener);
        resolve(data);
      };
      manager.onKernelEvent(kernelId, eventType, listener);
    });
  }

  beforeEach('Initialize kernel manager and create kernel', async function() {
    manager = new KernelManager(streamTestOptions);
    kernelId = await manager.createKernel({
      mode: KernelMode.MAIN_THREAD,
      lang: KernelLanguage.PYTHON
    });
  });

  afterEach('Cleanup kernel manager', async function() {
    if (manager) {
      await manager.destroyAll();
    }
  });

  describe('Stream Output Handling', function() {
    it('should capture print statement output', async function() {
      const code = "print('Hello World!')";
      
      // Listen for stream output
      const streamPromise = waitForEvent(KernelEvents.STREAM);
      
      // Execute the code
      const result = await manager.execute(kernelId, code);
      
      // Check execution result
      expect(result.success).to.be.true;
      
      // Check stream output
      const streamData = await streamPromise;
      expect(streamData.name).to.equal('stdout');
      expect(streamData.text).to.include('Hello World!');
    });

    it('should capture multiple print statements', async function() {
      const code = `
print("Line 1")
print("Line 2") 
print("Line 3")
`;
      
      const events: any[] = [];
      
      // Collect all stream events with timeout
      const allEventsPromise = new Promise((resolve) => {
        const listener = (data: any) => {
          events.push(data);
        };
        manager.onKernelEvent(kernelId, KernelEvents.STREAM, listener);
        
        // Stop listening after a reasonable timeout
        setTimeout(() => {
          manager.offKernelEvent(kernelId, KernelEvents.STREAM, listener);
          resolve(events);
        }, 2000);
      });
      
      // Execute the code
      const result = await manager.execute(kernelId, code);
      expect(result.success).to.be.true;
      
      // Wait for all events
      await allEventsPromise;
      
      // Combine all output text from all events
      const allOutputText = events.map(e => e.text || '').join('');
      
      // Check that all lines are present in the output
      expect(allOutputText).to.include('Line 1');
      expect(allOutputText).to.include('Line 2'); 
      expect(allOutputText).to.include('Line 3');
      expect(events.length).to.be.greaterThan(0);
    });

    it('should handle Python system information output', async function() {
      const code = `
import sys
print(f"Python version: {sys.version_info.major}.{sys.version_info.minor}")
`;
      
      // Listen for stream output
      const streamPromise = waitForEvent(KernelEvents.STREAM);
      
      // Execute the code
      const result = await manager.execute(kernelId, code);
      
      // Check execution result
      expect(result.success).to.be.true;
      
      // Check stream output
      const streamData = await streamPromise;
      expect(streamData.name).to.equal('stdout');
      expect(streamData.text).to.include('Python version:');
    });
  });

  describe('Stream Execution API', function() {
    it('should support streaming execution with executeStream', async function() {
      const code = `
for i in range(3):
    print(f"Line {i}")
`;
      
      const events: any[] = [];
      
      // Use executeStream to capture all events
      const streamGenerator = manager.executeStream(kernelId, code);
      
      for await (const event of streamGenerator) {
        events.push(event);
      }
      
      // Check that we got stream events
      const streamEvents = events.filter(e => e.type === 'stream');
      expect(streamEvents.length).to.be.greaterThan(0);
      
      // Check that we got the expected output
      const outputText = streamEvents.map(e => e.data.text).join('');
      expect(outputText).to.include('Line 0');
      expect(outputText).to.include('Line 1');
      expect(outputText).to.include('Line 2');
    });

    it('should handle mixed output and results in streaming', async function() {
      const code = `
print("Starting calculation")
result = 5 * 6
print(f"Result is {result}")
result
`;
      
      const events: any[] = [];
      
      // Use executeStream to capture all events
      const streamGenerator = manager.executeStream(kernelId, code);
      
      for await (const event of streamGenerator) {
        events.push(event);
      }
      
      // Check that we got both stream and result events
      const streamEvents = events.filter(e => e.type === 'stream');
      const resultEvents = events.filter(e => e.type === 'execute_result');
      
      expect(streamEvents.length).to.be.greaterThan(0);
      expect(resultEvents.length).to.be.greaterThan(0);
      
      // Check stream content
      const outputText = streamEvents.map(e => e.data.text).join('');
      expect(outputText).to.include('Starting calculation');
      expect(outputText).to.include('Result is 30');
      
      // Check result
      const result = resultEvents[resultEvents.length - 1];
      expect(result.data).to.exist;
    });
  });

  describe('Event Management', function() {
    it('should handle multiple event listeners for streams', async function() {
      const code = "print('Testing multiple listeners')";
      
      let listener1Called = false;
      let listener2Called = false;
    
      const listener1 = (data: any) => {
        listener1Called = true;
        expect(data.text).to.include('Testing multiple listeners');
      };
    
      const listener2 = (data: any) => {
        listener2Called = true;
        expect(data.name).to.equal('stdout');
      };
      
      // Add multiple listeners
      manager.onKernelEvent(kernelId, KernelEvents.STREAM, listener1);
      manager.onKernelEvent(kernelId, KernelEvents.STREAM, listener2);
    
      // Execute code
      const result = await manager.execute(kernelId, code);
      
      // Give listeners time to be called
      await new Promise(resolve => setTimeout(resolve, 100));
      
      // Check that both listeners were called
      expect(listener1Called).to.be.true;
      expect(listener2Called).to.be.true;
      
      // Clean up listeners
      manager.offKernelEvent(kernelId, KernelEvents.STREAM, listener1);
      manager.offKernelEvent(kernelId, KernelEvents.STREAM, listener2);
    });

    it('should remove event listeners correctly', async function() {
      const code = "print('Testing listener removal')";
      
      let listenerCalled = false;
      
      const listener = (data: any) => {
        listenerCalled = true;
      };
      
      // Add listener
      manager.onKernelEvent(kernelId, KernelEvents.STREAM, listener);
      
      // Execute code - listener should be called
      await manager.execute(kernelId, code);
      await new Promise(resolve => setTimeout(resolve, 100));
      expect(listenerCalled).to.be.true;
      
      // Remove listener
      manager.offKernelEvent(kernelId, KernelEvents.STREAM, listener);
      
      // Reset flag
      listenerCalled = false;
      
      // Execute code again - listener should not be called
      await manager.execute(kernelId, code);
      await new Promise(resolve => setTimeout(resolve, 100));
      expect(listenerCalled).to.be.false;
    });
  });

  describe('Error Streams', function() {
    it('should capture error output through events', async function() {
      const code = "print(undefined_variable)";
      
      // Listen for error output
      const errorPromise = waitForEvent(KernelEvents.EXECUTE_ERROR);
      
      // Execute the code
      const result = await manager.execute(kernelId, code);
      
      // Check execution result
      expect(result.success).to.be.false;
      expect(result.error).to.exist;
      
      // Check error output
      const errorData = await errorPromise;
      expect(errorData.ename).to.equal('NameError');
      expect(errorData.evalue).to.include('undefined_variable');
    });

    it('should handle streaming with errors', async function() {
      const code = `
print("Before error")
1 / 0
print("After error")
`;
      
      const streamEvents: any[] = [];
      const errorEvents: any[] = [];
      
      // Listen for both stream and error events
      const eventsPromise = new Promise((resolve) => {
        const streamListener = (data: any) => {
          streamEvents.push(data);
        };
        const errorListener = (data: any) => {
          errorEvents.push(data);
        };
        
        manager.onKernelEvent(kernelId, KernelEvents.STREAM, streamListener);
        manager.onKernelEvent(kernelId, KernelEvents.EXECUTE_ERROR, errorListener);
        
        // Stop listening after execution completes
        setTimeout(() => {
          manager.offKernelEvent(kernelId, KernelEvents.STREAM, streamListener);
          manager.offKernelEvent(kernelId, KernelEvents.EXECUTE_ERROR, errorListener);
          resolve({ streamEvents, errorEvents });
        }, 3000);
      });
      
      // Execute the code (should fail due to division by zero)
      const result = await manager.execute(kernelId, code);
      expect(result.success).to.be.false; // Should fail due to error
      
      // Wait for events
      await eventsPromise;
      
      // Check that we captured the print statement before the error
      expect(streamEvents.length).to.be.greaterThan(0);
      const allStreamText = streamEvents.map(e => e.text || '').join('');
      expect(allStreamText).to.include('Before error');
      
      // Check that we captured the error
      expect(errorEvents.length).to.be.greaterThan(0);
    });
  });

  describe('Execution Result Events', function() {
    it('should capture execution results through events', async function() {
      const code = "2 + 3";
      
      // Listen for execution result
      const resultPromise = waitForEvent(KernelEvents.EXECUTE_RESULT);
      
      // Execute the code
      const result = await manager.execute(kernelId, code);
      
      // Check execution result
      expect(result.success).to.be.true;
      
      // Check result event
      const resultData = await resultPromise;
      expect(resultData.execution_count).to.be.a('number');
      expect(resultData.data).to.exist;
    });

    it('should handle complex calculations with result events', async function() {
      const code = `
import math
math.factorial(5) * 2
`;
      
      // Listen for execution result
      const resultPromise = waitForEvent(KernelEvents.EXECUTE_RESULT);
      
      // Execute the code
      const result = await manager.execute(kernelId, code);
      
      // Check execution result
      expect(result.success).to.be.true;
      
      // Check result event
      const resultData = await resultPromise;
      expect(resultData.execution_count).to.be.a('number');
      expect(resultData.data).to.exist;
    });
  });
}); 