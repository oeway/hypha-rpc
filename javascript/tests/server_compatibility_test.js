import { expect } from "chai";
import { connectToServer } from "../src/websocket-client.js";

const OLD_SERVER_URL = "https://hypha.aicell.io";

describe("Old Server Compatibility", () => {
  it("should connect to old server and test context injection", async function () {
    // Skip this test if we can't connect to the old server
    this.timeout(60000);

    let server;
    try {
      server = await connectToServer({
        server_url: OLD_SERVER_URL,
        client_id: "old-server-test",
        timeout: 30000,
      });
    } catch (error) {
      console.warn(
        "Skipping old server test - connection failed:",
        error.message,
      );
      this.skip();
    }

    try {
      // Register service with require_context
      await server.registerService({
        id: "test-old-server-compat",
        config: { require_context: true, visibility: "public" },
        testContext: function (x, context) {
          return {
            input: x,
            hasContext: context !== null && context !== undefined,
            workspace: context ? context.ws : null,
            from: context ? context.from : null,
          };
        },
        testNormal: function (x, context) {
          return `echo: ${x}`;
        },
      });

      // Test the service with context injection
      const svc = await server.getService("test-old-server-compat");
      const contextResult = await svc.testContext("test");

      expect(contextResult.hasContext).to.be.true;
      expect(contextResult.workspace).to.be.a("string");
      expect(contextResult.from).to.be.a("string");

      // Test normal method without context
      const normalResult = await svc.testNormal("hello");
      expect(normalResult).to.equal("echo: hello");

      console.log("✓ Old server compatibility test passed");
      console.log(`  Workspace: ${contextResult.workspace}`);
      console.log(`  From: ${contextResult.from}`);
    } finally {
      if (server) {
        await server.disconnect();
      }
    }
  });

  it("should handle services without require_context on old server", async function () {
    this.timeout(60000);

    let server;
    try {
      server = await connectToServer({
        server_url: OLD_SERVER_URL,
        client_id: "old-server-no-context-test",
        timeout: 30000,
      });
    } catch (error) {
      console.warn(
        "Skipping old server test - connection failed:",
        error.message,
      );
      this.skip();
    }

    try {
      // Register service without require_context
      await server.registerService({
        id: "test-no-context-old",
        config: { visibility: "public" },
        simpleEcho: (x) => `echo: ${x}`,
        mathAdd: (a, b) => a + b,
      });

      const svc = await server.getService("test-no-context-old");

      expect(await svc.simpleEcho("test")).to.equal("echo: test");
      expect(await svc.mathAdd(2, 3)).to.equal(5);

      console.log("✓ Non-context service test on old server passed");
    } finally {
      if (server) {
        await server.disconnect();
      }
    }
  });
});
