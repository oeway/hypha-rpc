import { expect } from "chai";
import { connectToServerHTTP } from "../src/http-client.js";

const SERVER_URL = "http://127.0.0.1:9394"; // Match the port in package.json test script
// Unique suffix per browser instance to prevent client_id collisions
// when Karma runs Chrome and Firefox simultaneously against the same server.
// The Hypha server assigns the same workspace to all anonymous HTTP clients,
// so concurrent browsers interfere with each other's service registrations.
const SUFFIX = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);

// Skip HTTP tests on Firefox to avoid cross-browser workspace collisions.
// The HTTP transport code is browser-agnostic — Chrome coverage is sufficient.
const isFirefox = typeof navigator !== "undefined" && /Firefox/.test(navigator.userAgent);
const describeHttp = isFirefox ? describe.skip : describe;

describeHttp("HTTP Streaming RPC Client", () => {
  it("should connect to server using HTTP transport", async function () {
    this.timeout(30000);

    const clientId = `http-test-client-${SUFFIX}`;
    const server = await connectToServerHTTP({
      server_url: SERVER_URL,
      client_id: clientId,
      method_timeout: 10,
    });

    expect(server).to.exist;
    expect(server.config).to.exist;
    expect(server.config.workspace).to.be.a("string");
    expect(server.config.client_id).to.equal(clientId);

    console.log("✓ HTTP connection established");
    console.log(`  Workspace: ${server.config.workspace}`);
    console.log(`  Client ID: ${server.config.client_id}`);

    await server.disconnect();
  });

  it("should register and call service via HTTP transport", async function () {
    this.timeout(30000);

    const server = await connectToServerHTTP({
      server_url: SERVER_URL,
      client_id: `http-service-test-${SUFFIX}`,
      method_timeout: 10,
    });

    try {
      // Register a test service
      await server.registerService({
        id: "http-test-service",
        config: { visibility: "public" },
        echo: (message) => `HTTP Echo: ${message}`,
        add: (a, b) => a + b,
        asyncEcho: async (message) => {
          // Simulate async operation
          await new Promise((resolve) => setTimeout(resolve, 100));
          return `Async HTTP Echo: ${message}`;
        },
      });

      // Get the service and test methods
      const svc = await server.getService("http-test-service");

      const echoResult = await svc.echo("test message");
      expect(echoResult).to.equal("HTTP Echo: test message");

      const addResult = await svc.add(5, 3);
      expect(addResult).to.equal(8);

      const asyncResult = await svc.asyncEcho("async test");
      expect(asyncResult).to.equal("Async HTTP Echo: async test");

      console.log("✓ HTTP service calls successful");
    } finally {
      await server.disconnect();
    }
  });

  it("should handle context injection via HTTP transport", async function () {
    this.timeout(30000);

    const server = await connectToServerHTTP({
      server_url: SERVER_URL,
      client_id: `http-context-test-${SUFFIX}`,
      method_timeout: 10,
    });

    try {
      // Register service with require_context
      await server.registerService({
        id: "http-context-service",
        config: { require_context: true, visibility: "public" },
        testContext: function (x, context) {
          return {
            input: x,
            hasContext: context !== null && context !== undefined,
            workspace: context ? context.ws : null,
            from: context ? context.from : null,
            user: context && context.user ? context.user.id : null,
          };
        },
      });

      const svc = await server.getService("http-context-service");
      const result = await svc.testContext("test");

      expect(result.hasContext).to.be.true;
      expect(result.workspace).to.be.a("string");
      expect(result.from).to.be.a("string");

      console.log("✓ HTTP context injection working");
      console.log(`  Workspace: ${result.workspace}`);
      console.log(`  From: ${result.from}`);
    } finally {
      await server.disconnect();
    }
  });

  it("should fail gracefully with HTTPS when server uses HTTP", async function () {
    this.timeout(15000);

    try {
      // This should fail because we're trying HTTPS on HTTP server
      await connectToServerHTTP({
        server_url: "https://127.0.0.1:9394", // Wrong protocol
        client_id: `https-fail-test-${SUFFIX}`,
        method_timeout: 5,
      });

      // If we get here, the test failed (should have thrown)
      expect.fail("Should have thrown an error for HTTPS on HTTP server");
    } catch (error) {
      // Expected to fail
      expect(error).to.exist;
      console.log("✓ HTTPS on HTTP server correctly rejected");
      console.log(`  Error: ${error.message}`);
    }
  });

  it("should handle binary data via HTTP transport", async function () {
    this.timeout(30000);

    const server = await connectToServerHTTP({
      server_url: SERVER_URL,
      client_id: `http-binary-test-${SUFFIX}`,
      method_timeout: 10,
    });

    try {
      // Register service that handles binary data
      await server.registerService({
        id: "http-binary-service",
        config: { visibility: "public" },
        processBuffer: (buffer) => {
          // Echo back the buffer
          return buffer;
        },
        getByteArray: () => {
          // Return a byte array
          return new Uint8Array([1, 2, 3, 4, 5]);
        },
      });

      const svc = await server.getService("http-binary-service");

      // Test with binary data
      const testBuffer = new Uint8Array([10, 20, 30, 40, 50]);
      const result = await svc.processBuffer(testBuffer);

      expect(result).to.be.an.instanceof(Uint8Array);
      expect(result.length).to.equal(testBuffer.length);
      for (let i = 0; i < testBuffer.length; i++) {
        expect(result[i]).to.equal(testBuffer[i]);
      }

      // Test getting binary data
      const byteArray = await svc.getByteArray();
      expect(byteArray).to.be.an.instanceof(Uint8Array);
      expect(byteArray).to.deep.equal(new Uint8Array([1, 2, 3, 4, 5]));

      console.log("✓ HTTP binary data handling successful");
    } finally {
      await server.disconnect();
    }
  });

  it("should handle large payloads via HTTP transport", async function () {
    this.timeout(60000);

    const server = await connectToServerHTTP({
      server_url: SERVER_URL,
      client_id: `http-large-payload-test-${SUFFIX}`,
      method_timeout: 30,
    });

    try {
      await server.registerService({
        id: "http-large-service",
        config: { visibility: "public" },
        processLargeData: (data) => {
          return {
            length: data.length,
            checksum: Array.from(data).reduce((a, b) => a + b, 0),
          };
        },
      });

      const svc = await server.getService("http-large-service");

      // Create a large binary buffer (100KB) - more compact than plain arrays
      const largeBuffer = new Uint8Array(1024 * 100);
      for (let i = 0; i < largeBuffer.length; i++) {
        largeBuffer[i] = i % 256;
      }
      const result = await svc.processLargeData(largeBuffer);

      expect(result.length).to.equal(largeBuffer.length);
      console.log("✓ HTTP large payload handling successful");
      console.log(
        `  Payload size: ${(largeBuffer.length / 1024).toFixed(2)} KB`,
      );
    } finally {
      await server.disconnect();
    }
  });

  it("should handle errors gracefully via HTTP transport", async function () {
    this.timeout(30000);

    const server = await connectToServerHTTP({
      server_url: SERVER_URL,
      client_id: `http-error-test-${SUFFIX}`,
      method_timeout: 20,
    });

    try {
      await server.registerService({
        id: "http-error-service",
        config: { visibility: "public" },
        throwError: () => {
          throw new Error("Intentional error for testing");
        },
      });

      const svc = await server.getService("http-error-service");

      try {
        await svc.throwError();
        expect.fail("Should have thrown an error");
      } catch (error) {
        expect(error).to.exist;
        console.log("✓ HTTP error handling working");
        console.log(`  Error: ${error.toString()}`);
      }
    } finally {
      await server.disconnect();
    }
  });

  it("should support workspace specification via HTTP transport", async function () {
    this.timeout(30000);

    // Connect without specifying workspace to get auto-assigned one
    const server = await connectToServerHTTP({
      server_url: SERVER_URL,
      client_id: `http-workspace-test-${SUFFIX}`,
      method_timeout: 10,
    });

    try {
      // Verify we got a workspace assigned
      expect(server.config.workspace).to.be.a("string");
      expect(server.config.workspace.length).to.be.greaterThan(0);
      console.log("✓ HTTP workspace specification working");
      console.log(`  Workspace: ${server.config.workspace}`);
    } finally {
      await server.disconnect();
    }
  });
});

describeHttp("HTTP Manager Service Interaction", () => {
  it("should access manager service via HTTP transport", async function () {
    this.timeout(30000);

    const server = await connectToServerHTTP({
      server_url: SERVER_URL,
      client_id: `http-manager-access-test-${SUFFIX}`,
      method_timeout: 10,
    });

    try {
      // The server object IS the manager service wrapper
      expect(server).to.exist;
      expect(server.getService).to.be.a("function");
      expect(server.listServices).to.be.a("function");
      expect(server.registerService).to.be.a("function");

      // Call manager service methods
      const services = await server.listServices();
      expect(services).to.be.an("array");

      console.log("✓ HTTP manager service accessible");
      console.log(`  Found ${services.length} services`);
    } finally {
      await server.disconnect();
    }
  });

  it("should register service via HTTP manager service", async function () {
    this.timeout(30000);

    const server = await connectToServerHTTP({
      server_url: SERVER_URL,
      client_id: `http-manager-register-test-${SUFFIX}`,
      method_timeout: 10,
    });

    try {
      // Register a test service via manager
      await server.registerService({
        id: "http-mgr-test-service",
        config: { visibility: "public" },
        echo: (x) => `Manager Echo: ${x}`,
        multiply: (a, b) => a * b,
      });

      // Verify the service was registered (service IDs include workspace prefix)
      const services = await server.listServices();
      const serviceIds = services.map((s) => s.id);
      const ws = server.config.workspace;
      const fullServiceId = `${ws}/${server.config.client_id}:http-mgr-test-service`;
      expect(serviceIds).to.include(fullServiceId);

      // Get and test the service
      const svc = await server.getService("http-mgr-test-service");
      const echoResult = await svc.echo("test");
      expect(echoResult).to.equal("Manager Echo: test");

      const multiplyResult = await svc.multiply(7, 8);
      expect(multiplyResult).to.equal(56);

      console.log("✓ HTTP manager service registration working");
    } finally {
      await server.disconnect();
    }
  });

  it("should access manager from user workspace via HTTP", async function () {
    this.timeout(30000);

    // Connect without specifying workspace (will be assigned a user workspace)
    const server = await connectToServerHTTP({
      server_url: SERVER_URL,
      client_id: `http-user-ws-manager-test-${SUFFIX}`,
      method_timeout: 10,
    });

    try {
      const workspace = server.config.workspace;
      console.log(`  Connected to workspace: ${workspace}`);

      // Even in a user workspace, we should access the manager
      const services = await server.listServices();
      expect(services).to.be.an("array");

      // Register service in our workspace
      await server.registerService({
        id: "user-ws-service",
        config: { visibility: "public" },
        test: () => "ok",
      });

      // Verify via manager (service IDs include workspace prefix)
      const servicesAfter = await server.listServices();
      const serviceIds = servicesAfter.map((s) => s.id);
      const fullServiceId = `${workspace}/${server.config.client_id}:user-ws-service`;
      expect(serviceIds).to.include(fullServiceId);

      console.log(`✓ Cross-workspace manager access from ${workspace}`);
    } finally {
      await server.disconnect();
    }
  });

  it("should get detailed service info via HTTP manager", async function () {
    this.timeout(30000);

    const server = await connectToServerHTTP({
      server_url: SERVER_URL,
      client_id: `http-service-info-test-${SUFFIX}`,
      method_timeout: 10,
    });

    try {
      // Register a service with metadata
      await server.registerService({
        id: "detailed-http-service",
        name: "Detailed HTTP Service",
        description: "A service with detailed metadata",
        config: { visibility: "public" },
        version: "1.0.0",
        echo: (x) => x,
        transform: (x) => (typeof x === "string" ? x.toUpperCase() : x),
      });

      // Get service info from manager (service IDs include workspace prefix)
      const services = await server.listServices();
      const ws = server.config.workspace;
      const fullServiceId = `${ws}/${server.config.client_id}:detailed-http-service`;
      const ourService = services.find((s) => s.id === fullServiceId);

      expect(ourService).to.exist;
      expect(ourService.name).to.equal("Detailed HTTP Service");
      expect(ourService.description).to.equal(
        "A service with detailed metadata",
      );

      // Test the service works
      const svc = await server.getService("detailed-http-service");
      expect(await svc.echo("test")).to.equal("test");
      expect(await svc.transform("hello")).to.equal("HELLO");

      console.log("✓ HTTP manager service info retrieval working");
    } finally {
      await server.disconnect();
    }
  });

  it("should handle manager service errors gracefully via HTTP", async function () {
    this.timeout(30000);

    const server = await connectToServerHTTP({
      server_url: SERVER_URL,
      client_id: `http-manager-error-test-${SUFFIX}`,
      method_timeout: 10,
    });

    try {
      // Try to get a non-existent service
      try {
        await server.getService("non-existent-service-xyz");
        expect.fail("Should have thrown an error for non-existent service");
      } catch (error) {
        expect(error).to.exist;
        console.log("✓ HTTP manager error handling working");
        console.log(`  Error message: ${error.message}`);
      }
    } finally {
      await server.disconnect();
    }
  });

  it("should unregister service via HTTP manager", async function () {
    this.timeout(30000);

    const server = await connectToServerHTTP({
      server_url: SERVER_URL,
      client_id: `http-manager-unregister-test-${SUFFIX}`,
      method_timeout: 10,
    });

    try {
      // Register a service
      await server.registerService({
        id: "temp-http-service",
        config: { visibility: "public" },
        test: () => "temporary",
      });

      // Verify it exists (service IDs include workspace prefix)
      let services = await server.listServices();
      const ws = server.config.workspace;
      let fullServiceId = `${ws}/${server.config.client_id}:temp-http-service`;
      let serviceIds = services.map((s) => s.id);
      expect(serviceIds).to.include(fullServiceId);

      // Unregister the service
      await server.rpc.unregister_service("temp-http-service");

      // Verify it's gone
      services = await server.listServices();
      serviceIds = services.map((s) => s.id);
      expect(serviceIds).to.not.include(fullServiceId);

      console.log("✓ HTTP manager service unregistration working");
    } finally {
      await server.disconnect();
    }
  });

  it("should support concurrent manager operations via HTTP", async function () {
    this.timeout(30000);

    const server = await connectToServerHTTP({
      server_url: SERVER_URL,
      client_id: `http-concurrent-manager-test-${SUFFIX}`,
      method_timeout: 10,
    });

    try {
      // Register multiple services concurrently
      const registrations = [];
      for (let i = 0; i < 5; i++) {
        registrations.push(
          server.registerService({
            id: `concurrent-service-${i}`,
            config: { visibility: "public" },
            index: () => i,
          }),
        );
      }

      await Promise.all(registrations);

      // Verify all were registered (service IDs include workspace prefix)
      const services = await server.listServices();
      const serviceIds = services.map((s) => s.id);
      const ws = server.config.workspace;

      for (let i = 0; i < 5; i++) {
        const fullServiceId = `${ws}/${server.config.client_id}:concurrent-service-${i}`;
        expect(serviceIds).to.include(fullServiceId);
      }

      console.log("✓ HTTP concurrent manager operations working");
    } finally {
      await server.disconnect();
    }
  });
});
