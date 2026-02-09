/**
 * Memory Leak Tests
 *
 * Test-Driven Development approach to verify and fix memory leaks in the Hypha RPC client.
 *
 * These tests verify that resources (event listeners, timers, sessions) are properly
 * cleaned up when connections are closed or clients are disconnected.
 */

import { expect } from "chai";
import { connectToServer } from "../src/websocket-client.js";

const SERVER_URL = "http://127.0.0.1:9394";

describe("Memory Leak Prevention", function () {
  this.timeout(30000);

  describe("Event Listener Cleanup", function () {
    it("should remove all custom event listeners on disconnect", async function () {
      const client = await connectToServer({
        server_url: SERVER_URL,
        client_id: "test-memory-leak-listeners",
      });

      const rpc = client._rpc || client.rpc;

      // Register multiple custom event listeners
      const eventHandlerCallCounts = {
        custom_event_1: 0,
        custom_event_2: 0,
        custom_event_3: 0,
      };

      const handler1 = () => eventHandlerCallCounts.custom_event_1++;
      const handler2 = () => eventHandlerCallCounts.custom_event_2++;
      const handler3 = () => eventHandlerCallCounts.custom_event_3++;

      rpc.on("custom_event_1", handler1);
      rpc.on("custom_event_2", handler2);
      rpc.on("custom_event_3", handler3);

      // Verify handlers are registered
      expect(rpc._event_handlers["custom_event_1"]).to.have.lengthOf(1);
      expect(rpc._event_handlers["custom_event_2"]).to.have.lengthOf(1);
      expect(rpc._event_handlers["custom_event_3"]).to.have.lengthOf(1);

      // Trigger events to confirm they work
      rpc._fire("custom_event_1", {});
      rpc._fire("custom_event_2", {});
      rpc._fire("custom_event_3", {});

      expect(eventHandlerCallCounts.custom_event_1).to.equal(1);
      expect(eventHandlerCallCounts.custom_event_2).to.equal(1);
      expect(eventHandlerCallCounts.custom_event_3).to.equal(1);

      // Disconnect (this should clean up ALL event listeners)
      await client.disconnect();

      // After disconnect, all event handlers should be cleared
      // This prevents memory leaks when RPC instances are disconnected
      expect(Object.keys(rpc._event_handlers).length).to.equal(
        0,
        "All event handlers should be cleared after disconnect"
      );

      // Verify handlers are actually removed by trying to fire events
      // (they should not be called)
      rpc._fire("custom_event_1", {});
      rpc._fire("custom_event_2", {});
      rpc._fire("custom_event_3", {});

      // Call counts should remain unchanged
      expect(eventHandlerCallCounts.custom_event_1).to.equal(1);
      expect(eventHandlerCallCounts.custom_event_2).to.equal(1);
      expect(eventHandlerCallCounts.custom_event_3).to.equal(1);
    });

    it("should remove unhandledrejection listener on disconnect", async function () {
      // Skip in Node.js environment where we use process.on instead
      if (typeof window === "undefined") {
        this.skip();
        return;
      }

      // Get initial listener count for unhandledrejection
      // Note: We can't directly count DOM event listeners, so we'll track the internal counter
      const RPC = (await import("../src/rpc.js")).RPC;
      const initialCount = RPC._rejectionHandlerCount || 0;

      // Connect to server (this should add a listener)
      const client = await connectToServer({
        server_url: SERVER_URL,
      });

      // Verify listener was added
      const afterConnectCount = RPC._rejectionHandlerCount;
      expect(afterConnectCount).to.equal(
        initialCount + 1,
        "Listener should be added on connect"
      );

      // Disconnect (this should remove the listener)
      await client.disconnect();

      // Verify listener was removed
      const afterDisconnectCount = RPC._rejectionHandlerCount;
      expect(afterDisconnectCount).to.equal(
        initialCount,
        "Listener should be removed on disconnect"
      );
    });

    it("should handle multiple concurrent connections correctly", async function () {
      // Skip in Node.js
      if (typeof window === "undefined") {
        this.skip();
        return;
      }

      const RPC = (await import("../src/rpc.js")).RPC;
      const initialCount = RPC._rejectionHandlerCount || 0;

      // Create multiple connections
      const client1 = await connectToServer({
        server_url: SERVER_URL,
      });
      expect(RPC._rejectionHandlerCount).to.equal(initialCount + 1);

      const client2 = await connectToServer({
        server_url: SERVER_URL,
      });
      // Counter should increment for second client
      expect(RPC._rejectionHandlerCount).to.equal(initialCount + 2);

      // Disconnect first client
      await client1.disconnect();
      // Counter should decrement but listener should still exist
      expect(RPC._rejectionHandlerCount).to.equal(initialCount + 1);

      // Disconnect second client
      await client2.disconnect();
      // Now counter should be back to initial
      expect(RPC._rejectionHandlerCount).to.equal(initialCount);
    });
  });

  describe("Timer Cleanup", function () {
    it("should clear session sweep interval on disconnect", async function () {
      const client = await connectToServer({
        server_url: SERVER_URL,
      });

      // Access the RPC instance (it's exposed as client._rpc or similar)
      const rpc = client._rpc || client.rpc;

      // Verify interval is set (session sweep runs periodically)
      // Note: This might not be set immediately, give it a moment
      await new Promise((resolve) => setTimeout(resolve, 100));

      const intervalBefore = rpc._sessionSweepInterval;

      // Disconnect
      await client.disconnect();

      // Verify interval is cleared
      const intervalAfter = rpc._sessionSweepInterval;
      expect(intervalAfter).to.be.null;
    });
  });

  describe("Session Cleanup on Remote Disconnect", function () {
    it("should clean up sessions when remote client disconnects", async function () {
      // Create two clients - let them use their default workspaces
      // Service is public, so cross-workspace access will work
      const client1 = await connectToServer({
        server_url: SERVER_URL,
        client_id: "test-session-cleanup-client1",
      });

      const client2 = await connectToServer({
        server_url: SERVER_URL,
        client_id: "test-session-cleanup-client2",
      });

      // Client 1 registers a service with a long-running method
      await client1.registerService({
        id: "test-service",
        type: "test",
        config: {
          visibility: "public",
        },
        ping: async () => {
          // Simulate long-running operation
          await new Promise((resolve) => setTimeout(resolve, 5000));
          return "pong";
        },
      });

      // Client 2 calls the service (this creates a session)
      const service = await client2.getService("test-service");

      // Start the call but don't await it yet
      const callPromise = service.ping();

      // Give it a moment to start
      await new Promise((resolve) => setTimeout(resolve, 100));

      // Now disconnect client 1 (the service provider)
      await client1.disconnect();

      // The call should be rejected due to client disconnect
      try {
        await callPromise;
        expect.fail("Call should have been rejected when client disconnected");
      } catch (error) {
        expect(error.message).to.match(/disconnect|closed|timeout/i);
      }

      // Clean up client 2
      await client2.disconnect();
    });
  });

  describe("AbortController Cleanup (HTTP only)", function () {
    it("should abort and clean up AbortController on disconnect", async function () {
      // This test is only relevant for HTTP transport
      // We'll skip it for now as the test suite primarily uses WebSocket
      this.skip();

      // TODO: Implement when HTTP transport testing is set up
      // Should verify:
      // 1. AbortController is created for fetch()
      // 2. abort() is called on disconnect
      // 3. Controller reference is nulled out
    });
  });
});
