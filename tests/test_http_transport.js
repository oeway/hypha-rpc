/**
 * Integration tests for HTTP transport functionality.
 * Tests object transmission, callbacks, and binary data over HTTP.
 */

import { expect } from "chai";
import { connectToServer } from "../javascript/src/websocket-client.js";

describe("HTTP Transport Tests", () => {
  const SERVER_URL = process.env.HYPHA_SERVER_URL || "http://127.0.0.1:9520";
  const WS_SERVER_URL = SERVER_URL.replace("http://", "ws://").replace("https://", "wss://") + "/ws";

  let wsServer = null;
  let httpServer = null;

  afterEach(async function() {
    if (httpServer) {
      await httpServer.disconnect();
      httpServer = null;
    }
    if (wsServer) {
      await wsServer.disconnect();
      wsServer = null;
    }
  });

  it("should connect via HTTP transport", async function() {
    this.timeout(30000);

    // Provider via WebSocket
    wsServer = await connectToServer({
      server_url: WS_SERVER_URL,
      client_id: "ws-provider-conn-test",
    });

    const workspace = wsServer.config.workspace;
    const token = await wsServer.generate_token();

    // Consumer via HTTP
    httpServer = await connectToServer({
      server_url: SERVER_URL,
      workspace: workspace,
      client_id: "http-consumer-conn-test",
      transport: "http",
      token: token,
    });

    expect(httpServer).to.exist;
    expect(httpServer.config.workspace).to.equal(workspace);
    expect(httpServer.config.connection_type).to.equal("http_streaming");
  });

  it("should transmit complex nested objects over HTTP", async function() {
    this.timeout(30000);

    // Provider via WebSocket
    wsServer = await connectToServer({
      server_url: WS_SERVER_URL,
      client_id: "ws-provider-nested",
    });

    const workspace = wsServer.config.workspace;

    // Register service
    await wsServer.registerService({
      id: "nested-service",
      name: "Nested Service",
      config: { visibility: "public" },
      processNested: (data) => {
        return {
          receivedKeys: Object.keys(data),
          nestedCount: data.nested ? data.nested.items.length : 0,
          echo: data,
        };
      },
    });

    const token = await wsServer.generate_token();

    // Consumer via HTTP
    httpServer = await connectToServer({
      server_url: SERVER_URL,
      workspace: workspace,
      client_id: "http-consumer-nested",
      transport: "http",
      token: token,
    });

    const service = await httpServer.getService("ws-provider-nested:nested-service");

    const testData = {
      string: "test",
      number: 42,
      array: [1, 2, 3, 4, 5],
      nested: {
        items: [1, 2, 3],
        metadata: {
          name: "test_item",
          values: [10, 20, 30],
        },
      },
    };

    const result = await service.processNested(testData);

    expect(result.receivedKeys).to.have.members(Object.keys(testData));
    expect(result.nestedCount).to.equal(3);
    expect(result.echo.string).to.equal("test");
    expect(result.echo.number).to.equal(42);
    expect(result.echo.nested.metadata.name).to.equal("test_item");
  });

  it("should handle callbacks over HTTP transport", async function() {
    this.timeout(30000);

    // Provider via WebSocket
    wsServer = await connectToServer({
      server_url: WS_SERVER_URL,
      client_id: "ws-provider-callback",
    });

    const workspace = wsServer.config.workspace;

    // Register service with callback support
    await wsServer.registerService({
      id: "callback-service",
      name: "Callback Service",
      config: { visibility: "public" },
      callMultipleTimes: (callback, count) => {
        const results = [];
        for (let i = 0; i < count; i++) {
          results.push(callback(i));
        }
        return results;
      },
      processWithProgress: (data, progressCallback) => {
        for (let i = 0; i < data.length; i++) {
          progressCallback({ step: i, total: data.length });
        }
        return data.reduce((a, b) => a + b, 0);
      },
    });

    const token = await wsServer.generate_token();

    // Consumer via HTTP
    httpServer = await connectToServer({
      server_url: SERVER_URL,
      workspace: workspace,
      client_id: "http-consumer-callback",
      transport: "http",
      token: token,
    });

    const service = await httpServer.getService("ws-provider-callback:callback-service");

    // Test 1: Simple callback
    const callbackResults = [];
    const results = await service.callMultipleTimes((value) => {
      callbackResults.push(value);
      return value * 2;
    }, 5);

    expect(callbackResults).to.have.lengthOf(5);
    expect(callbackResults).to.deep.equal([0, 1, 2, 3, 4]);
    expect(results).to.deep.equal([0, 2, 4, 6, 8]);

    // Test 2: Progress callback
    const progressUpdates = [];
    const testData = [10, 20, 30, 40];
    const sum = await service.processWithProgress(testData, (info) => {
      progressUpdates.push(info);
    });

    expect(sum).to.equal(100);
    expect(progressUpdates).to.have.lengthOf(4);
    expect(progressUpdates[0]).to.deep.equal({ step: 0, total: 4 });
    expect(progressUpdates[3]).to.deep.equal({ step: 3, total: 4 });
  });

  it("should handle async callbacks over HTTP", async function() {
    this.timeout(30000);

    // Provider via WebSocket
    wsServer = await connectToServer({
      server_url: WS_SERVER_URL,
      client_id: "ws-provider-async-callback",
    });

    const workspace = wsServer.config.workspace;

    // Register service
    await wsServer.registerService({
      id: "async-callback-service",
      name: "Async Callback Service",
      config: { visibility: "public" },
      processAsync: async (items, asyncCallback) => {
        const results = [];
        for (const item of items) {
          const result = await asyncCallback(item);
          results.push(result);
        }
        return results;
      },
    });

    const token = await wsServer.generate_token();

    // Consumer via HTTP
    httpServer = await connectToServer({
      server_url: SERVER_URL,
      workspace: workspace,
      client_id: "http-consumer-async-callback",
      transport: "http",
      token: token,
    });

    const service = await httpServer.getService("ws-provider-async-callback:async-callback-service");

    // Async callback
    const testItems = [1, 2, 3, 4, 5];
    const results = await service.processAsync(testItems, async (value) => {
      await new Promise(resolve => setTimeout(resolve, 10)); // Simulate async work
      return value ** 2;
    });

    expect(results).to.deep.equal([1, 4, 9, 16, 25]);
  });

  it("should transmit binary data over HTTP", async function() {
    this.timeout(30000);

    // Provider via WebSocket
    wsServer = await connectToServer({
      server_url: WS_SERVER_URL,
      client_id: "ws-provider-binary",
    });

    const workspace = wsServer.config.workspace;

    // Register service
    await wsServer.registerService({
      id: "binary-service",
      name: "Binary Service",
      config: { visibility: "public" },
      processBinary: (data) => {
        return {
          length: data.length,
          firstBytes: data.slice(0, 10),
          reversed: data.reverse(),
        };
      },
      concatBinary: (parts) => {
        const totalLength = parts.reduce((sum, part) => sum + part.length, 0);
        const result = new Uint8Array(totalLength);
        let offset = 0;
        for (const part of parts) {
          result.set(part, offset);
          offset += part.length;
        }
        return result;
      },
    });

    const token = await wsServer.generate_token();

    // Consumer via HTTP
    httpServer = await connectToServer({
      server_url: SERVER_URL,
      workspace: workspace,
      client_id: "http-consumer-binary",
      transport: "http",
      token: token,
    });

    const service = await httpServer.getService("ws-provider-binary:binary-service");

    // Test 1: Send binary data
    const encoder = new TextEncoder();
    const testData = encoder.encode("Hello, World! This is binary data.");
    const result = await service.processBinary(testData);

    expect(result.length).to.equal(testData.length);
    expect(result.firstBytes).to.deep.equal(testData.slice(0, 10));

    // Test 2: Multiple binary chunks
    const parts = [
      encoder.encode("Part1"),
      encoder.encode("Part2"),
      encoder.encode("Part3"),
    ];
    const concatenated = await service.concatBinary(parts);
    const expectedStr = "Part1Part2Part3";
    const decoder = new TextDecoder();
    expect(decoder.decode(concatenated)).to.equal(expectedStr);
  });

  it("should handle large objects over HTTP", async function() {
    this.timeout(30000);

    // Provider via WebSocket
    wsServer = await connectToServer({
      server_url: WS_SERVER_URL,
      client_id: "ws-provider-large",
    });

    const workspace = wsServer.config.workspace;

    // Register service
    await wsServer.registerService({
      id: "large-service",
      name: "Large Service",
      config: { visibility: "public" },
      processLarge: (data) => {
        return {
          arrayLength: data.largeArray.length,
          sum: data.largeArray.reduce((a, b) => a + b, 0),
          echo: data,
        };
      },
    });

    const token = await wsServer.generate_token();

    // Consumer via HTTP
    httpServer = await connectToServer({
      server_url: SERVER_URL,
      workspace: workspace,
      client_id: "http-consumer-large",
      transport: "http",
      token: token,
    });

    const service = await httpServer.getService("ws-provider-large:large-service");

    // Create large array (10000 elements)
    const largeArray = Array.from({ length: 10000 }, (_, i) => i);
    const testData = {
      largeArray: largeArray,
      metadata: {
        name: "large_test",
        description: "Testing large data transmission",
      },
    };

    const result = await service.processLarge(testData);

    expect(result.arrayLength).to.equal(10000);
    expect(result.sum).to.equal(49995000); // Sum of 0 to 9999
    expect(result.echo.metadata.name).to.equal("large_test");
  });

  it("should handle callbacks with complex objects over HTTP", async function() {
    this.timeout(30000);

    // Provider via WebSocket
    wsServer = await connectToServer({
      server_url: WS_SERVER_URL,
      client_id: "ws-provider-callback-complex",
    });

    const workspace = wsServer.config.workspace;

    // Register service
    await wsServer.registerService({
      id: "callback-complex-service",
      name: "Callback Complex Service",
      config: { visibility: "public" },
      transformBatch: (items, transformCallback) => {
        return items.map(item => transformCallback(item));
      },
    });

    const token = await wsServer.generate_token();

    // Consumer via HTTP
    httpServer = await connectToServer({
      server_url: SERVER_URL,
      workspace: workspace,
      client_id: "http-consumer-callback-complex",
      transport: "http",
      token: token,
    });

    const service = await httpServer.getService("ws-provider-callback-complex:callback-complex-service");

    const testItems = [
      { data: [1, 2, 3], multiplier: 2 },
      { data: [4, 5, 6], multiplier: 3 },
      { data: [7, 8, 9], multiplier: 4 },
    ];

    const results = await service.transformBatch(testItems, (item) => {
      return {
        sum: item.data.reduce((a, b) => a + b, 0),
        modified: item.data.map(x => x * item.multiplier),
      };
    });

    expect(results).to.have.lengthOf(3);
    expect(results[0].sum).to.equal(6);
    expect(results[0].modified).to.deep.equal([2, 4, 6]);
    expect(results[1].sum).to.equal(15);
    expect(results[1].modified).to.deep.equal([12, 15, 18]);
    expect(results[2].sum).to.equal(24);
    expect(results[2].modified).to.deep.equal([28, 32, 36]);
  });
});
