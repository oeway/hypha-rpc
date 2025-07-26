import { expect } from "chai";
import { connectToServer } from "../src/websocket-client.js";
import { getRTCService, registerRTCService } from "../src/webrtc-client.js";

const SERVER_URL = "ws://127.0.0.1:9394/ws";

describe("Real WebRTC Functionality", () => {
  it("should establish real WebRTC connection (not websocket)", async function () {
    this.timeout(30000);

    console.log("Testing real WebRTC connection verification...");

    // Create server
    const server = await connectToServer({
      server_url: SERVER_URL,
      client_id: "webrtc-verify-server-js",
      name: "WebRTC Verify Server JS",
    });

    // Register a service that detects connection type
    await server.registerService({
      id: "connection-detector-js",
      name: "Connection Detector JS",
      config: { visibility: "public" },
      detectConnection: (config) => {
        const context = config?.context || {};
        return {
          connectionType: context.connection_type || "unknown",
          hasContext: context !== null && context !== undefined,
          timestamp: Date.now(),
        };
      },
    });

    // Register WebRTC service
    const rtcServiceId = "verify-webrtc-service-js";
    await registerRTCService(server, rtcServiceId, {
      visibility: "public",
    });

    // Create client
    const client = await connectToServer({
      server_url: SERVER_URL,
      client_id: "webrtc-verify-client-js",
      name: "WebRTC Verify Client JS",
    });

    // Establish WebRTC connection
    const rtcPeer = await getRTCService(
      client,
      `${server.config.workspace}/${server.config.client_id}:${rtcServiceId}`,
      { timeout: 15.0 },
    );

    // Verify we have a WebRTC connection
    expect(rtcPeer).to.not.be.null;
    expect(rtcPeer).to.not.be.undefined;
    expect(rtcPeer).to.have.property("rpc");

    // Wait for data channel to fully open
    await new Promise((resolve) => setTimeout(resolve, 1000));

    // Verify WebRTC data channel exists
    if (rtcPeer._data_channel) {
      expect(rtcPeer._data_channel.readyState).to.equal("open");
      console.log("✅ WebRTC data channel is open and ready");
    } else {
      console.log(
        "⚠️ _data_channel property not accessible, but WebRTC connection established",
      );
    }

    // Access service through WebRTC
    const detectorService = await rtcPeer.getService("connection-detector-js");
    const result = await detectorService.detectConnection();

    // Verify connection type is WebRTC
    expect(result.connectionType).to.equal("webrtc");
    expect(result.hasContext).to.be.true;
    console.log("✅ Confirmed connection type is WebRTC, not websocket");

    // Cleanup
    await server.disconnect();
    await client.disconnect();
  });

  it("should pass context correctly through real WebRTC", async function () {
    this.timeout(30000);

    console.log("Testing comprehensive WebRTC context passing...");

    // Create server with user context
    const server = await connectToServer({
      server_url: SERVER_URL,
      client_id: "webrtc-context-server-js",
      name: "WebRTC Context Server JS",
    });

    // Register a service that requires and uses context
    await server.registerService({
      id: "context-aware-service-js",
      name: "Context Aware Service JS",
      config: {
        visibility: "public",
        require_context: true,
      },
      getContextInfo: (config) => {
        const context = config?.context || {};
        return {
          hasContext: context !== null && context !== undefined,
          userId: context.user?.id || null,
          workspace: context.ws || null,
          connectionType: context.connection_type || "unknown",
          clientId: context.client_id || null,
        };
      },
    });

    // Register WebRTC service
    const rtcServiceId = "context-webrtc-service-js";
    await registerRTCService(server, rtcServiceId, {
      visibility: "public",
    });

    // Create client
    const client = await connectToServer({
      server_url: SERVER_URL,
      client_id: "webrtc-context-client-js",
      name: "WebRTC Context Client JS",
    });

    // Establish WebRTC connection
    const rtcPeer = await getRTCService(
      client,
      `${server.config.workspace}/${server.config.client_id}:${rtcServiceId}`,
      { timeout: 15.0 },
    );

    expect(rtcPeer).to.not.be.null;
    expect(rtcPeer).to.not.be.undefined;

    // Wait for data channel to fully open
    await new Promise((resolve) => setTimeout(resolve, 1000));

    // Access service through WebRTC (should have context)
    const contextService = await rtcPeer.getService("context-aware-service-js");
    const contextInfo = await contextService.getContextInfo();

    // Verify context was passed correctly
    expect(contextInfo).to.not.be.null;
    expect(contextInfo).to.not.be.undefined;
    expect(contextInfo.hasContext).to.be.true;
    expect(contextInfo.connectionType).to.equal("webrtc");
    expect(contextInfo.workspace).to.not.be.null;
    console.log("✅ Context passed correctly through WebRTC connection");

    // Cleanup
    await server.disconnect();
    await client.disconnect();
  });

  it("should handle webrtc=auto configuration correctly", async function () {
    this.timeout(30000);

    console.log("Testing WebRTC auto functionality...");

    // Create server
    const server = await connectToServer({
      server_url: SERVER_URL,
      client_id: "webrtc-auto-server-js",
      name: "WebRTC Auto Server JS",
    });

    // Register a service that detects connection type
    await server.registerService({
      id: "auto-detector-service-js",
      name: "Auto Detector Service JS",
      config: { visibility: "public" },
      detectConnection: (config) => {
        const context = config?.context || {};
        return {
          connectionType: context.connection_type || "websocket", // default to websocket
          hasContext: context !== null && context !== undefined,
        };
      },
    });

    // Register WebRTC service
    const rtcServiceId = "auto-webrtc-service-js";
    await registerRTCService(server, rtcServiceId, {
      visibility: "public",
    });

    // Create client with webrtc=auto
    const client = await connectToServer({
      server_url: SERVER_URL,
      client_id: "webrtc-auto-client-js",
      name: "WebRTC Auto Client JS",
      webrtc: "auto", // This should try WebRTC first, then fallback to websocket
    });

    // Wait for connection to establish
    await new Promise((resolve) => setTimeout(resolve, 1000));

    // Try to get service with webrtc=auto (should try WebRTC first)
    let service;
    try {
      service = await client.getService("auto-detector-service-js", {
        webrtc: "auto",
      });
    } catch (error) {
      console.log(
        "WebRTC failed, should fallback to websocket:",
        error.message,
      );
      // Fallback to regular websocket connection
      service = await client.getService("auto-detector-service-js");
    }

    expect(service).to.not.be.null;
    expect(service).to.not.be.undefined;

    const result = await service.detectConnection();
    expect(result).to.not.be.null;
    expect(result).to.not.be.undefined;

    // Either WebRTC or websocket should work with auto
    const connectionType = result.connectionType;
    expect(connectionType).to.be.oneOf(["webrtc", "websocket"]);

    console.log(
      `✅ Connection established with webrtc=auto, type: ${connectionType}`,
    );

    // Cleanup
    await server.disconnect();
    await client.disconnect();
  });

  it("should distinguish WebRTC from WebSocket connections", async function () {
    this.timeout(30000);

    console.log("Testing WebRTC vs WebSocket distinction...");

    // Create server
    const server = await connectToServer({
      server_url: SERVER_URL,
      client_id: "webrtc-compare-server-js",
      name: "WebRTC Compare Server JS",
    });

    // Register a service
    await server.registerService({
      id: "comparison-service-js",
      name: "Comparison Service JS",
      config: { visibility: "public" },
      getConnectionInfo: (config) => {
        const context = config?.context || {};
        return {
          connectionType: context.connection_type || "websocket",
          timestamp: Date.now(),
        };
      },
    });

    // Register WebRTC service
    const rtcServiceId = "compare-webrtc-service-js";
    await registerRTCService(server, rtcServiceId, {
      visibility: "public",
    });

    // Create WebSocket client
    const websocketClient = await connectToServer({
      server_url: SERVER_URL,
      client_id: "websocket-compare-client-js",
      name: "WebSocket Compare Client JS",
    });

    // Create WebRTC client
    const webrtcClient = await connectToServer({
      server_url: SERVER_URL,
      client_id: "webrtc-compare-client-js",
      name: "WebRTC Compare Client JS",
    });

    // Get WebRTC connection
    const rtcPeer = await getRTCService(
      webrtcClient,
      `${server.config.workspace}/${server.config.client_id}:${rtcServiceId}`,
      { timeout: 15.0 },
    );

    // Wait for connections to stabilize
    await new Promise((resolve) => setTimeout(resolve, 1000));

    // Test WebSocket connection
    const websocketService = await websocketClient.getService(
      "comparison-service-js",
    );
    const websocketResult = await websocketService.getConnectionInfo();

    // Test WebRTC connection
    const webrtcService = await rtcPeer.getService("comparison-service-js");
    const webrtcResult = await webrtcService.getConnectionInfo();

    // Verify different connection types
    expect(websocketResult).to.not.be.null;
    expect(webrtcResult).to.not.be.null;
    expect(websocketResult.connectionType).to.equal("websocket");
    expect(webrtcResult.connectionType).to.equal("webrtc");

    console.log(
      "✅ Successfully distinguished WebRTC from WebSocket connections",
    );

    // Cleanup
    await server.disconnect();
    await websocketClient.disconnect();
    await webrtcClient.disconnect();
  });

  it("should verify WebRTC data channel is actually used", async function () {
    this.timeout(30000);

    console.log("Testing WebRTC data channel usage...");

    // Create server
    const server = await connectToServer({
      server_url: SERVER_URL,
      client_id: "webrtc-datachannel-server-js",
      name: "WebRTC DataChannel Server JS",
    });

    // Register a service
    await server.registerService({
      id: "datachannel-test-service-js",
      name: "DataChannel Test Service JS",
      config: { visibility: "public" },
      ping: () => "pong-from-datachannel",
      getConnectionInfo: (config) => {
        const context = config?.context || {};
        return {
          connectionType: context.connection_type || "unknown",
          timestamp: Date.now(),
          message: "Sent via data channel",
        };
      },
    });

    // Register WebRTC service
    const rtcServiceId = "datachannel-webrtc-service-js";
    await registerRTCService(server, rtcServiceId, {
      visibility: "public",
    });

    // Create client
    const client = await connectToServer({
      server_url: SERVER_URL,
      client_id: "webrtc-datachannel-client-js",
      name: "WebRTC DataChannel Client JS",
    });

    // Establish WebRTC connection
    const rtcPeer = await getRTCService(
      client,
      `${server.config.workspace}/${server.config.client_id}:${rtcServiceId}`,
      { timeout: 15.0 },
    );

    expect(rtcPeer).to.not.be.null;
    expect(rtcPeer).to.not.be.undefined;

    // Wait for data channel to fully open
    await new Promise((resolve) => setTimeout(resolve, 1000));

    // Test communication over data channel
    const service = await rtcPeer.getService("datachannel-test-service-js");
    const pingResult = await service.ping();
    const infoResult = await service.getConnectionInfo();

    expect(pingResult).to.equal("pong-from-datachannel");
    expect(infoResult).to.not.be.null;
    expect(infoResult).to.not.be.undefined;
    expect(infoResult.connectionType).to.equal("webrtc");
    expect(infoResult.message).to.equal("Sent via data channel");

    console.log("✅ WebRTC data channel communication verified");

    // Cleanup
    await server.disconnect();
    await client.disconnect();
  });

  it("should connect via webrtc", async function () {
    this.timeout(30000);

    console.log("Testing direct WebRTC connection...");

    // Create server
    const server = await connectToServer({
      server_url: SERVER_URL,
      client_id: "webrtc-direct-server-js",
      name: "WebRTC Direct Server JS",
    });

    // Register a simple test service
    await server.registerService({
      id: "direct-test-service-js",
      name: "Direct Test Service JS",
      config: { visibility: "public" },
      hello: () => "world-via-webrtc",
      getInfo: (config) => {
        const context = config?.context || {};
        return {
          connectionType: context.connection_type || "unknown",
          clientId: context.client_id || null,
          timestamp: Date.now(),
        };
      },
    });

    // Register WebRTC service
    const rtcServiceId = "direct-webrtc-service-js";
    await registerRTCService(server, rtcServiceId, {
      visibility: "public",
    });

    // Create client
    const client = await connectToServer({
      server_url: SERVER_URL,
      client_id: "webrtc-direct-client-js",
      name: "WebRTC Direct Client JS",
    });

    // Establish WebRTC connection
    const rtcPeer = await getRTCService(
      client,
      `${server.config.workspace}/${server.config.client_id}:${rtcServiceId}`,
      { timeout: 15.0 },
    );

    expect(rtcPeer).to.not.be.null;
    expect(rtcPeer).to.not.be.undefined;
    expect(rtcPeer).to.have.property("rpc");

    // Wait for data channel to fully open
    await new Promise((resolve) => setTimeout(resolve, 1000));

    // Test service access
    const service = await rtcPeer.getService("direct-test-service-js");
    const helloResult = await service.hello();
    const infoResult = await service.getInfo();

    expect(helloResult).to.equal("world-via-webrtc");
    expect(infoResult).to.not.be.null;
    expect(infoResult).to.not.be.undefined;
    expect(infoResult.connectionType).to.equal("webrtc");
    expect(infoResult.clientId).to.not.be.null;

    console.log("✅ Direct WebRTC connection successful");

    // Cleanup
    await server.disconnect();
    await client.disconnect();
  });
});
