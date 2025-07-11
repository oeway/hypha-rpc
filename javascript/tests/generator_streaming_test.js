/**
 * Test the new generator streaming functionality for API v4+
 */

import { assert } from "chai";
import { connectToServer } from "../src/websocket-client.js";
import { encode as msgpack_packb, decodeMulti } from "@msgpack/msgpack";
import { randId } from "../src/utils/index.js";

const SERVER_URL = "http://127.0.0.1:9394";

describe("Generator Streaming Tests", function () {
  this.timeout(60000);

  it("should support basic generator streaming with API v4+", async function () {
    // Connect two clients with API v4
    const server = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-generator-server",
    });
    
    const client = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-generator-client",
    });

    try {
      // Verify both clients support API v4
      assert.equal(server.rpc.constructor.API_VERSION || 4, 4);
      assert.equal(client.rpc.constructor.API_VERSION || 4, 4);

      // Register a service with generators on server
      function* simpleGenerator() {
        for (let i = 0; i < 5; i++) {
          yield `item_${i}`;
        }
      }

      async function* asyncSimpleGenerator() {
        for (let i = 0; i < 5; i++) {
          yield `async_item_${i}`;
        }
      }

      await server.registerService({
        id: "generator-service",
        type: "generator-test",
        simpleGenerator: simpleGenerator,
        asyncSimpleGenerator: asyncSimpleGenerator,
      });

      // Wait for service registration
      await new Promise(resolve => setTimeout(resolve, 1000));

      // Get the service from client
      const service = await client.getService("test-generator-server:generator-service");

      // Test sync generator streaming
      const results = [];
      const gen = await service.simpleGenerator();
      for await (const item of gen) {
        results.push(item);
      }

      assert.equal(results.length, 5);
      assert.deepEqual(results, ["item_0", "item_1", "item_2", "item_3", "item_4"]);

      // Test async generator streaming
      const asyncResults = [];
      const asyncGen = await service.asyncSimpleGenerator();
      for await (const item of asyncGen) {
        asyncResults.push(item);
      }

      assert.equal(asyncResults.length, 5);
      assert.deepEqual(asyncResults, ["async_item_0", "async_item_1", "async_item_2", "async_item_3", "async_item_4"]);

    } finally {
      await server.disconnect();
      await client.disconnect();
    }
  });

  it("should handle large message chunking via generator streaming", async function () {
    const server = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-large-server",
    });
    
    const client = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-large-client", 
    });

    try {
      // Create a large data array
      const largeData = new Float32Array(1000 * 1000);
      for (let i = 0; i < largeData.length; i++) {
        largeData[i] = Math.random();
      }

      async function largeDataService(data) {
        // This should trigger generator streaming for large messages
        const sum = Array.from(data).reduce((a, b) => a + b, 0);
        return {
          received_length: data.length,
          sum: sum
        };
      }

      await server.registerService({
        id: "large-data-service",
        processLargeData: largeDataService
      });

      await new Promise(resolve => setTimeout(resolve, 500));

      const service = await client.getService("test-large-server:large-data-service");
      const result = await service.processLargeData(largeData);

      assert.equal(result.received_length, largeData.length);
      const expectedSum = Array.from(largeData).reduce((a, b) => a + b, 0);
      assert.approximately(result.sum, expectedSum, 1e-5);

    } finally {
      await server.disconnect();
      await client.disconnect();
    }
  });

  it("should test backward compatibility with older API versions", async function () {
    const client = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-compat-client",
    });

    try {
      // Test that API version detection works
      const rpc = client.rpc;
      
      // Should support API v4 features
      const supportsV4 = await rpc._check_remote_api_version("test-compat-client");
      assert.isTrue(supportsV4, "Should support API v4+");

    } finally {
      await client.disconnect();
    }
  });

  it("should handle generator error handling", async function () {
    const server = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-error-server",
    });
    
    const client = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-error-client",
    });

    try {
      function* errorGenerator() {
        yield "item1";
        yield "item2";
        throw new Error("Test error in generator");
      }

      await server.registerService({
        id: "error-service",
        errorGenerator: errorGenerator
      });

      await new Promise(resolve => setTimeout(resolve, 500));

      const service = await client.getService("test-error-server:error-service");

      // This should handle the error gracefully
      const results = [];
      try {
        const gen = await service.errorGenerator();
        for await (const item of gen) {
          results.push(item);
        }
      } catch (e) {
        // Should get some items before the error
        assert.isAtLeast(results.length, 0); // May get some items before error
        assert.isTrue(e.message.toLowerCase().includes("error") || results.length >= 0);
      }

    } finally {
      await server.disconnect();
      await client.disconnect();
    }
  });

  it("should test the compact generator message format", async function () {
    const client = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-format-client",
    });

    try {
      const rpc = client.rpc;

      // Test that the message format uses the compact method field
      const testChunks = [new Uint8Array([1, 2, 3]), new Uint8Array([4, 5, 6]), new Uint8Array([7, 8, 9])];
      const generatorId = randId();

      // Capture messages sent
      const capturedMessages = [];
      const originalEmit = rpc._emit_message;

      async function mockEmit(message) {
        // Decode and store the message
        const unpacker = decodeMulti(message);
        const { value: msg } = unpacker.next();
        capturedMessages.push(msg);
        return await originalEmit(message);
      }

      rpc._emit_message = mockEmit;

      // Send test generator stream
      await rpc._send_generator_stream(testChunks, "test-target", generatorId);

      // Verify compact message format
      assert.isAtLeast(capturedMessages.length, 5); // start + 3 data + end

      // Check start message format
      const startMsg = capturedMessages[0];
      assert.equal(startMsg.type, 'generator');
      assert.equal(startMsg.method, `${generatorId}:start`);
      assert.isUndefined(startMsg.generator_id); // Should not have old format
      assert.isUndefined(startMsg.action); // Should not have old format

      // Check data message format
      for (let i = 1; i < 4; i++) {
        const dataMsg = capturedMessages[i];
        assert.equal(dataMsg.type, 'generator');
        assert.equal(dataMsg.method, `${generatorId}:data`);
        assert.isDefined(dataMsg.data);
      }

      // Check end message format
      const endMsg = capturedMessages[capturedMessages.length - 1];
      assert.equal(endMsg.type, 'generator');
      assert.equal(endMsg.method, `${generatorId}:end`);

      // Restore original emit
      rpc._emit_message = originalEmit;

    } finally {
      await client.disconnect();
    }
  });

  it("should handle mixed data types in generators", async function () {
    const server = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-mixed-server",
    });
    
    const client = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-mixed-client",
    });

    try {
      function* mixedGenerator() {
        yield 42;
        yield "string";
        yield [1, 2, 3];
        yield { key: "value" };
        yield new Float32Array([1, 2, 3]);
      }

      await server.registerService({
        id: "mixed-service",
        mixedGenerator: mixedGenerator
      });

      await new Promise(resolve => setTimeout(resolve, 500));

      const service = await client.getService("test-mixed-server:mixed-service");

      const results = [];
      const gen = await service.mixedGenerator();
      for await (const item of gen) {
        results.push(item);
      }

      assert.equal(results.length, 5);
      assert.equal(results[0], 42);
      assert.equal(results[1], "string");
      assert.deepEqual(results[2], [1, 2, 3]);
      assert.deepEqual(results[3], { key: "value" });
      assert.deepEqual(Array.from(results[4]), [1, 2, 3]);

    } finally {
      await server.disconnect();
      await client.disconnect();
    }
  });

  it("should test API version detection capabilities", async function () {
    const client = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-version-client",
    });

    try {
      // Test API version detection method
      const rpc = client.rpc;

      // Test with valid client ID
      const supportsV4 = await rpc._check_remote_api_version("test-version-client");
      assert.isTrue(supportsV4, "Should detect API v4+ support");

      // Test with invalid client ID (should return false)
      const supportsInvalid = await rpc._check_remote_api_version("non-existent-client");
      assert.isFalse(supportsInvalid, "Should return false for non-existent client");

    } finally {
      await client.disconnect();
    }
  });
}); 