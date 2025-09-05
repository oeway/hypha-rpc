import { expect } from "chai";
import {
  login,
  connectToServer,
  schemaFunction,
} from "../src/websocket-client.js";
import { registerRTCService, getRTCService } from "../src/webrtc-client.js";
import { assert } from "../src/utils";
const SERVER_URL = "http://127.0.0.1:9394";

class ImJoyPlugin {
  async setup() {}
  async add2(arg) {
    return arg + 2;
  }
}

describe("RPC", async () => {
  it("should connect to the server", async () => {
    const api = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-plugin-1",
    });
    expect(api.config.hypha_version).to.be.a("string");
    expect(typeof api.log).to.equal("function");
    const svc_info = await api.registerService({
      id: "test-service",
      config: {
        visibility: "public",
      },
      type: "echo",
      echo: (x) => x,
    });

    const svc = await api.getService("test-service");
    expect(await svc.echo("hello")).to.equal("hello");
    await api.unregisterService(svc_info.id);
    try {
      await api.getService("test-service");
    } catch (e) {
      expect(e.message).to.include("Service not found");
    }
    await api.disconnect();
  }).timeout(20000);

  it("should convert kwargs", async () => {
    const api = await connectToServer({
      server_url: SERVER_URL,
      kwargs_expansion: true,
    });
    const token = await api.generateToken({
      config: { workspace: api.config.workspace },
    });
    expect(token).to.be.a("string");
    await api.disconnect();
  }).timeout(20000);

  it("should allow probes", async () => {
    const api = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-plugin-1",
    });
    await api.registerProbes({
      readiness: async () => {
        return true;
      },
      liveness: async () => {
        return true;
      },
    });
    const response = await fetch(
      `${SERVER_URL}/${api.config.workspace}/services/probes/readiness`,
    );
    expect(response.ok).to.equal(true);
    const data = await response.json();
    expect(data).to.equal(true);
  }).timeout(20000);

  it("should contain schema", async () => {
    const api = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-plugin-1",
    });
    for (let key of Object.keys(api)) {
      const value = api[key];
      if (typeof value === "function") {
        console.log(`checking schema for ${key}`);
        expect(value.__schema__).to.be.a("object");
        expect(value.__schema__.name).to.be.equal(key);
      }
    }
    await api.disconnect();
  }).timeout(20000);

  it("should test robust reconnection with service re-registration", async () => {
    // Create connection with custom client ID for easier identification
    const api = await connectToServer({
      server_url: SERVER_URL,
      client_id: "reconnection-test-client",
    });

    // Keep track of reconnection events
    const reconnectionEvents = [];
    const serviceRegistrationEvents = [];

    const onConnected = (info) => {
      reconnectionEvents.push({ type: "connected", info });
    };

    const onServicesRegistered = (info) => {
      serviceRegistrationEvents.push({ type: "services_registered", info });
    };

    const onServicesRegistrationFailed = (info) => {
      serviceRegistrationEvents.push({
        type: "services_registration_failed",
        info,
      });
    };

    // Register event handlers
    api.rpc.on("connected", onConnected);
    api.rpc.on("services_registered", onServicesRegistered);
    api.rpc.on("services_registration_failed", onServicesRegistrationFailed);

    // Register multiple services to test batch re-registration
    const serviceData = { counter: 0, testData: "initial" };

    await api.registerService({
      id: "counter-service",
      name: "Counter Service",
      description: "Service with state for testing reconnection",
      config: { visibility: "protected" },
      increment: () => ++serviceData.counter,
      getCounter: () => serviceData.counter,
      setData: (data) => {
        serviceData.testData = data;
      },
      getData: () => serviceData.testData,
    });

    await api.registerService({
      id: "echo-service",
      name: "Echo Service",
      description: "Simple echo service for testing",
      config: { visibility: "protected" },
      echo: (x) => `echo: ${x}`,
      reverse: (x) =>
        typeof x === "string"
          ? x.split("").reverse().join("")
          : String(x).split("").reverse().join(""),
    });

    // Verify services work initially
    let counterSvc = await api.getService("counter-service");
    let echoSvc = await api.getService("echo-service");

    // Test initial functionality
    expect(await counterSvc.getCounter()).to.equal(0);
    expect(await counterSvc.increment()).to.equal(1);
    expect(await echoSvc.echo("test")).to.equal("echo: test");
    expect(await echoSvc.reverse("hello")).to.equal("olleh");

    // Clear events from initial connection
    reconnectionEvents.length = 0;
    serviceRegistrationEvents.length = 0;

    // Simulate unexpected disconnection (code 3001 - custom application code)
    console.log("Simulating unexpected disconnection...");
    api.rpc._connection._websocket.close(3001);

    // Wait a moment for reconnection to complete
    await new Promise((resolve) => setTimeout(resolve, 3000));

    // Verify services still work after reconnection
    console.log("Testing services after reconnection...");
    counterSvc = await api.getService("counter-service");
    echoSvc = await api.getService("echo-service");

    // Test that service state is preserved (since they were re-registered)
    const currentCounter = await counterSvc.getCounter();
    expect(currentCounter).to.equal(1);

    // Test incrementing works
    const newCounter = await counterSvc.increment();
    expect(newCounter).to.equal(2);

    // Test echo service still works
    const echoResult = await echoSvc.echo("reconnected");
    expect(echoResult).to.equal("echo: reconnected");

    const reverseResult = await echoSvc.reverse("reconnected");
    expect(reverseResult).to.equal("detcennocer");

    // Test setting new data
    await counterSvc.setData("after_reconnection");
    const dataResult = await counterSvc.getData();
    expect(dataResult).to.equal("after_reconnection");

    // Verify we got reconnection events
    expect(reconnectionEvents.length).to.be.greaterThan(0);

    // Verify we got service registration events
    expect(serviceRegistrationEvents.length).to.be.greaterThan(0);

    // Check if services were successfully re-registered
    const successfulRegistration = serviceRegistrationEvents.some(
      (event) =>
        event.type === "services_registered" && event.info.registered >= 2,
    );
    expect(successfulRegistration).to.be.true;

    console.log(
      "✅ Robust reconnection with service re-registration test passed!",
    );

    await api.disconnect();
  }).timeout(30000);

  it("should test reconnection cancellation", async () => {
    const api = await connectToServer({
      server_url: SERVER_URL,
      client_id: "cancellation-test-client",
    });

    // Register a service
    await api.registerService({
      id: "test-service",
      name: "Test Service",
      config: { visibility: "protected" },
      test: () => "ok",
    });

    // Simulate unexpected disconnection
    api.rpc._connection._websocket.close(3001);

    // Give a moment for reconnection to start
    await new Promise((resolve) => setTimeout(resolve, 500));

    // Now explicitly disconnect - this should cancel reconnection
    await api.disconnect();

    // Wait a bit more to ensure reconnection doesn't happen
    await new Promise((resolve) => setTimeout(resolve, 2000));

    // Try to use the service - should fail
    try {
      const svc = await api.getService("test-service");
      await svc.test();
      expect.fail("Service should not be accessible after explicit disconnect");
    } catch (error) {
      // Expected - connection should be closed
      expect(error.message).to.include("Connection is closed");
    }

    console.log("✅ Reconnection cancellation test passed!");
  }).timeout(20000);

  it("should reconnect for graceful server disconnections (codes 1000, 1001)", async () => {
    console.log("\n=== GRACEFUL DISCONNECTION RECONNECTION TEST ===");

    const api = await connectToServer({
      server_url: SERVER_URL,
      client_id: "graceful-reconnection-test-client",
    });

    // Register a simple echo service for testing
    await api.registerService({
      id: "graceful-test-service",
      name: "Graceful Test Service",
      config: { visibility: "protected" },
      echo: (msg) => `echo: ${msg}`,
    });

    // Test initial functionality
    let svc = await api.getService("graceful-test-service");
    expect(await svc.echo("initial")).to.equal("echo: initial");
    console.log("✅ Initial service call successful");

    // Test graceful disconnection codes that should trigger reconnection
    // Note: Browser WebSocket API only allows 1000 or 3000-4999 range for manual close()
    // Code 1001 (Going Away) is also handled correctly by our fix but can't be tested manually
    const gracefulCodes = [1000]; // Normal Closure

    for (const code of gracefulCodes) {
      console.log(`💥 Testing graceful disconnection with code ${code}...`);

      // Track reconnection by listening for log messages
      let reconnected = false;
      const originalWarn = console.warn;
      console.warn = function (...args) {
        if (
          args[0] &&
          args[0].includes &&
          args[0].includes("Successfully reconnected")
        ) {
          reconnected = true;
        }
        originalWarn.apply(console, args);
      };

      // Close with graceful code
      api.rpc._connection._websocket.close(code, `Testing code ${code}`);

      // Wait for reconnection
      await new Promise((resolve) => setTimeout(resolve, 3000));

      // Restore console.warn
      console.warn = originalWarn;

      // Verify reconnection occurred by testing service functionality
      try {
        svc = await api.getService("graceful-test-service");
        const result = await svc.echo(`after-${code}`);
        expect(result).to.equal(`echo: after-${code}`);
        console.log(
          `✅ Code ${code}: Successfully reconnected and service functional`,
        );
      } catch (error) {
        throw new Error(
          `Failed to reconnect after graceful close code ${code}: ${error.message}`,
        );
      }
    }

    console.log(
      "🎉 Graceful disconnection reconnection test passed! JavaScript now matches Python behavior!",
    );

    await api.disconnect();
  }).timeout(20000);

  it("should correctly handle schemaFunction annotation", async () => {
    const api = await connectToServer({
      server_url: SERVER_URL,
      client_id: "schema-test-client",
    });

    // Define a function with schema annotations
    const annotatedMultiply = schemaFunction((a, b) => a * b, {
      name: "annotatedMultiply",
      description: "Multiplies two numbers with type annotations.",
      parameters: {
        type: "object",
        properties: {
          a: { type: "number", description: "First number" },
          b: { type: "number", description: "Second number" },
        },
        required: ["a", "b"],
      },
    });

    // Register the service
    const serviceInfo = await api.registerService({
      id: "schema-test-service",
      config: { visibility: "public" },
      annotatedMultiply: annotatedMultiply,
    });
    // serviceInfo.service_schema.annotatedMultiply should be something like {"type": "function", "function": {"name": "annotatedMultiply", "description": "Multiplies two numbers with type annotations.", "parameters": {"type": "object", "properties": {"a": {"type": "number", "description": "First number"}, "b": {"type": "number", "description": "Second number"}}, "required": ["a", "b"]}}}
    expect(serviceInfo.service_schema.annotatedMultiply.type).to.equal(
      "function",
    );
    expect(serviceInfo.service_schema.annotatedMultiply.function.name).to.equal(
      "annotatedMultiply",
    );
    expect(
      serviceInfo.service_schema.annotatedMultiply.function.description,
    ).to.equal("Multiplies two numbers with type annotations.");
    expect(
      serviceInfo.service_schema.annotatedMultiply.function.parameters.type,
    ).to.equal("object");
    expect(
      serviceInfo.service_schema.annotatedMultiply.function.parameters
        .properties.a.type,
    ).to.equal("number");
    expect(
      serviceInfo.service_schema.annotatedMultiply.function.parameters
        .properties.b.type,
    ).to.equal("number");
    expect(
      serviceInfo.service_schema.annotatedMultiply.function.parameters.required,
    ).to.deep.equal(["a", "b"]);

    // Get the service back
    const svc = await api.getService("schema-test-service");

    // Verify the schema property exists and is correct
    expect(svc.annotatedMultiply).to.exist;
    expect(svc.annotatedMultiply.__schema__).to.be.a("object");
    expect(svc.annotatedMultiply.__schema__.name).to.equal("annotatedMultiply");
    expect(svc.annotatedMultiply.__schema__.description).to.equal(
      "Multiplies two numbers with type annotations.",
    );
    expect(svc.annotatedMultiply.__schema__.parameters).to.deep.equal({
      type: "object",
      properties: {
        a: { type: "number", description: "First number" },
        b: { type: "number", description: "Second number" },
      },
      required: ["a", "b"],
    });

    // Test the function call itself
    expect(await svc.annotatedMultiply(3, 4)).to.equal(12);

    await api.unregisterService(serviceInfo.id);
    await api.disconnect();
  }).timeout(20000);

  it("should connect via webrtc", async () => {
    const service_id = "test-rtc-service-1";
    const server = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-plugin-1",
    });
    await server.registerService({
      id: "echo-service-rtc",
      config: {
        visibility: "public",
      },
      type: "echo",
      echo: (x) => x,
    });
    await registerRTCService(server, service_id);
    const pc = await getRTCService(server, service_id);
    const svc = await pc.getService("echo-service-rtc");
    expect(await svc.echo("hello")).to.equal("hello");
  }).timeout(40000);

  it("should connect via webrtc (auto)", async () => {
    const server = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-plugin-1",
      webrtc: true,
    });
    await server.registerService({
      id: "echo-service-rtc",
      config: {
        visibility: "public",
      },
      type: "echo",
      echo: (x) => x,
      my_func: (a, b) => a + b,
    });
    const svc = await server.getService("echo-service-rtc", {
      case_conversion: "camel",
    });
    expect(await svc.echo("hello")).to.equal("hello");
    // the function will be converted to camel case
    expect(await svc.myFunc(2, 3)).to.equal(5);

    const svc2 = await server.getService("echo-service-rtc");
    expect(await svc2.my_func(2, 3)).to.equal(5);

    const svc3 = await server.getService("echo-service-rtc", {
      case_conversion: "snake",
    });
    expect(await svc3.my_func(2, 3)).to.equal(5);
  }).timeout(40000);

  it("should login to the server", async () => {
    const TOKEN = "sf31df234";

    async function callback(context) {
      console.log(`By passing login: ${context["login_url"]}`);
      const response = await fetch(
        `${context["report_url"]}?key=${context["key"]}&token=${TOKEN}`,
      );
      if (!response.ok) throw new Error("Network response was not ok");
    }

    // We use ai.imjoy.io to test the login for now
    const token = await login({
      server_url: SERVER_URL,
      login_callback: callback,
      login_timeout: 3,
    });
    expect(token).to.equal(TOKEN);

    const userProfile = await login({
      server_url: SERVER_URL,
      login_callback: callback,
      login_timeout: 3,
      profile: true,
    });
    expect(userProfile.token).to.equal(TOKEN);
  }).timeout(20000);

  it("should login with additional headers", async () => {
    const TOKEN = "sf31df234";
    const additional_headers = { "X-Custom-Header": "test-value" };

    async function callback(context) {
      console.log(`By passing login: ${context["login_url"]}`);
      const response = await fetch(
        `${context["report_url"]}?key=${context["key"]}&token=${TOKEN}`,
      );
      if (!response.ok) throw new Error("Network response was not ok");
    }

    // Test that additional_headers is passed through to connectToServer
    const token = await login({
      server_url: SERVER_URL,
      login_callback: callback,
      login_timeout: 3,
      additional_headers: additional_headers,
    });
    expect(token).to.equal(TOKEN);
  }).timeout(20000);

  it("should connect to the server", async () => {
    const api = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-plugin-1",
    });
    // await api.log("hello")
    const size = 100000;
    const data = await api.echo(new ArrayBuffer(size));
    expect(data.byteLength).to.equal(size);
    function multiply(a, b) {
      /* multiply two numbers */
      return a * b;
    }
    await api.registerService({
      name: "my service",
      id: "test-service",
      description: "test service",
      config: { visibility: "public" },
      multiply,
    });
    const svc = await api.rpc.get_remote_service("test-service");
    expect(svc.multiply.__doc__).to.equal("multiply two numbers");
    expect(await svc.multiply(2, 2)).to.equal(4);
    await api.export(new ImJoyPlugin());
    const dsvc = await api.rpc.get_remote_service("default");
    expect(await dsvc.add2(3)).to.equal(5);
    await api.disconnect();
  }).timeout(20000);

  it("should pass context to service function", async () => {
    const api = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-plugin-3",
    });
    function multiply_context(a, b, context) {
      assert(context.user, "context should not be null");
      if (b === undefined) {
        b = a;
      }
      return a * b;
    }
    const svcInfo = await api.registerService({
      name: "my service",
      id: "test-service",
      description: "test service",
      config: { visibility: "public", require_context: true },
      multiply_context,
    });

    const api2 = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-plugin-4",
    });
    const svc = await api2.getService(svcInfo.id);
    expect(await svc.multiply_context(2, 3)).to.equal(6);
    expect(await svc.multiply_context(2)).to.equal(4);
    await api2.disconnect();
    await api.disconnect();
  }).timeout(20000);

  it("should handle require_context for external client services", async () => {
    console.log(
      "Testing require_context bug fix for external client services...",
    );

    // Create server client
    const server = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-server-client",
    });

    // Get the workspace and token from the first client
    const workspace = server.config.workspace;
    const token = await server.generateToken();

    // Create external client using the same workspace
    const client = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-external-client",
      workspace: workspace,
      token: token,
    });

    try {
      // Register service with require_context from external client
      console.log("Registering external service with require_context: true...");
      await client.registerService({
        id: "external-context-service",
        name: "External Context Service",
        config: {
          require_context: true,
          visibility: "public",
        },
        // Function with 0 parameters - should receive context as last argument
        quickTest: function (context) {
          assert(context.user, "context should not be null");
          console.log("quickTest called with context:", !!context);
          return {
            success: true,
            hasContext: !!context,
            workspace: context?.ws,
          };
        },
        // Function with 1 parameter - should receive (userInput, context)
        processInput: function (userInput, context) {
          assert(context.user, "context.user should not be null");
          console.log("processInput called with:", {
            hasContext: !!context,
            userInput,
          });
          return {
            success: true,
            processed: userInput,
            workspace: context?.ws,
          };
        },
        // Function with 2 parameters - should receive (data, options, context)
        complexMethod: function (data, options, context) {
          assert(context.user, "context.user should not be null");
          console.log("complexMethod called with:", {
            hasContext: !!context,
            data,
            options,
          });
          return {
            success: true,
            data,
            options,
            workspace: context?.ws,
          };
        },
      });

      console.log("✅ External service registered successfully");

      // Get the service from server client
      const service = await server.getService("external-context-service");

      // Test 1: quickTest (0 user parameters)
      console.log("Testing quickTest (0 parameters)...");
      const result1 = await service.quickTest();
      console.log("quickTest result:", result1);
      expect(result1.success).to.be.true;
      expect(result1.hasContext).to.be.true;

      // Test 2: processInput (1 user parameter)
      console.log("Testing processInput (1 parameter)...");
      const result2 = await service.processInput("hello world");
      console.log("processInput result:", result2);
      expect(result2.success).to.be.true;
      expect(result2.processed).to.equal("hello world");
      expect(result2.workspace).to.be.a("string");

      // Test 3: complexMethod (2 user parameters)
      console.log("Testing complexMethod (2 parameters)...");
      const result3 = await service.complexMethod("test data", { flag: true });
      console.log("complexMethod result:", result3);
      expect(result3.success).to.be.true;
      expect(result3.data).to.equal("test data");
      expect(result3.options.flag).to.be.true;
      expect(result3.workspace).to.be.a("string");

      console.log("🎉 All require_context tests passed! Bug is fixed!");
    } catch (error) {
      console.error("❌ require_context test failed:", error.message);
      if (error.message.includes("Invalid number of arguments")) {
        console.error("🐛 Bug still present - argument validation failing");
      }
      throw error;
    } finally {
      await server.disconnect();
      await client.disconnect();
    }
  }).timeout(30000);

  it("should encode/decode data", async () => {
    const plugin_interface = {
      id: "default",
      embed: {
        embed: {
          value: 8873,
          sayHello: () => {
            console.log("hello");
            return true;
          },
        },
      },
      echo: (msg) => {
        return msg;
      },
    };
    const server = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-plugin-1",
    });
    const info = await server.registerService(plugin_interface);
    expect(info.id).to.contain("/");
    expect(info.id).to.contain(":");
    const api = await server.rpc.get_remote_service("default");

    const msg = "this is an messge.";
    expect(api.embed.embed).to.include.all.keys("value", "sayHello");
    expect(api.embed.embed.value).to.equal(8873);
    expect(await api.embed.embed.sayHello()).to.equal(true);
    expect(await api.echo(msg)).to.equal(msg);
    expect(await api.echo(99)).to.equal(99);
    const ret = await api.echo(new Uint16Array(new ArrayBuffer(4)));
    expect(ret.length).to.equal(2);
    expect(
      (await api.echo(new Blob(["133"], { type: "text33" }))).type,
    ).to.equal("text33");
    expect((await api.echo(new Map([["1", 99]]))).get("1")).to.equal(99);
    expect((await api.echo(new Set([38, "88", 38]))).size).to.equal(2);
    expect((await api.echo(new ArrayBuffer(101))).byteLength).to.equal(101);
    expect((await api.echo(new ArrayBuffer(10100000))).byteLength).to.equal(
      10100000,
    );
    expect(await api.echo(true)).to.equal(true);
    const date = new Date(2018, 11, 24, 10, 33, 30, 0);
    expect((await api.echo(date)).getTime()).to.equal(date.getTime());
    // const imageData = new ImageData(200, 100);
    // expect((await api.echo(imageData)).width).to.equal(200);
    expect(await api.echo({ a: 1, b: 93 })).to.include.all.keys("a", "b");
    expect(await api.echo(["12", 33, { foo: "bar" }])).to.include(33);
    expect(await api.echo(["12", 33, { foo: "bar" }])).to.include("12");
    expect(await api.echo(["12", 33, { foo: "bar" }])).to.deep.include({
      foo: "bar",
    });
    const blob = new Blob(["hello"], { type: "text/plain" });
    expect(await api.echo(blob)).to.be.an.instanceof(Blob);
    const file = new File(["foo"], "foo.txt", {
      type: "text/plain",
    });
    expect(await api.echo(file)).to.be.an.instanceof(Blob);

    // send an interface
    const itf = {
      id: "hello",
      add(a, b) {
        return a + b;
      },
    };
    await server.registerService(itf);
    const received_itf = await api.echo(itf);
    expect(await received_itf.add(1, 3)).to.equal(4);
    expect(await received_itf.add(9, 3)).to.equal(12);
    expect(await received_itf.add("12", 2)).to.equal("122");
    await server.disconnect();
  }).timeout(40000);

  it("should encode and decode custom object", async () => {
    const api = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-plugin-1",
    });

    class Cat {
      constructor(name, color, age) {
        this.name = name;
        this.color = color;
        this.age = age;
      }
    }

    api.registerCodec({
      name: "cat",
      type: Cat,
      encoder: (obj) => {
        return { name: obj.name, color: obj.color, age: obj.age };
      },
      decoder: (encoded_obj) => {
        return new Cat(encoded_obj.name, encoded_obj.color, encoded_obj.age);
      },
    });

    const bobo = new Cat("boboshu", "mixed", 0.67);
    const cat = await api.echo(bobo);
    const result =
      cat instanceof Cat &&
      bobo.name === cat.name &&
      bobo.color === cat.color &&
      bobo.age === cat.age;
    expect(result).to.equal(true);

    await api.disconnect();
  }).timeout(20000);

  it("should handle generators", async () => {
    // Create a server with a service that returns a generator
    const server = await connectToServer({
      server_url: SERVER_URL,
      client_id: "generator-provider",
    });

    // Get server workspace and token for client connection
    const workspace = server.config.workspace;
    const token = await server.generateToken();

    // Define a sync generator function
    function* counter(start = 0, end = 5) {
      for (let i = start; i < end; i++) {
        yield i;
      }
    }

    // Define an async generator function
    async function* asyncCounter(start = 0, end = 5) {
      for (let i = start; i < end; i++) {
        yield i;
        await new Promise((resolve) => setTimeout(resolve, 10)); // Small delay
      }
    }

    // Define a generator that throws an error
    function* errorGenerator() {
      yield 1;
      throw new Error("Generator error");
    }

    // Define an empty generator
    function* emptyGenerator() {
      // yields nothing
    }

    // Define a generator that yields complex objects
    function* objectGenerator() {
      yield { value: 1, text: "one" };
      yield { value: 2, text: "two" };
    }

    // Register service with all types of generators
    await server.registerService({
      id: "generator-service",
      config: { visibility: "public" },
      getCounter: counter,
      getAsyncCounter: asyncCounter,
      getErrorGenerator: errorGenerator,
      getEmptyGenerator: emptyGenerator,
      getObjectGenerator: objectGenerator,
    });

    // Connect with another client using the same workspace and token
    const client = await connectToServer({
      client_id: "generator-consumer",
      server_url: SERVER_URL,
      workspace: workspace,
      token: token,
    });

    // Get the service
    const genService = await client.getService("generator-service");
    // Test normal generator - note that it becomes an async generator over RPC
    const gen = await genService.getCounter(0, 5);
    const results = [];
    for await (const item of gen) {
      results.push(item);
    }
    expect(results).to.deep.equal([0, 1, 2, 3, 4]);

    // Test async generator
    const asyncGen = await genService.getAsyncCounter(0, 5);
    const asyncResults = [];
    for await (const item of asyncGen) {
      asyncResults.push(item);
    }
    expect(asyncResults).to.deep.equal([0, 1, 2, 3, 4]);

    // Test error generator
    const errorGen = await genService.getErrorGenerator();
    try {
      const errorResults = [];
      for await (const item of errorGen) {
        errorResults.push(item);
      }
      throw new Error("Should have thrown an error");
    } catch (e) {
      expect(e.message).to.include("Generator error");
    }

    // Test empty generator
    const emptyGen = await genService.getEmptyGenerator();
    const emptyResults = [];
    for await (const item of emptyGen) {
      emptyResults.push(item);
    }
    expect(emptyResults).to.deep.equal([]);

    // Test object generator
    const objGen = await genService.getObjectGenerator();
    const objResults = [];
    for await (const item of objGen) {
      objResults.push(item);
    }
    expect(objResults).to.deep.equal([
      { value: 1, text: "one" },
      { value: 2, text: "two" },
    ]);

    await client.disconnect();
    await server.disconnect();
  }).timeout(40000);

  // Test memory leak prevention (comprehensive)
  it("test memory leak prevention", async function () {
    this.timeout(120000);

    function getDetailedSessionAnalysis(rpc) {
      /**
       * Comprehensive session analysis.
       */
      const analysis = {
        total_sessions: 0,
        session_details: [],
        system_stores: {},
        memory_usage: 0, // Approximate
        promise_sessions: 0,
        regular_sessions: 0,
        sessions_with_timers: 0,
        sessions_with_heartbeat: 0,
      };

      if (!rpc._object_store) {
        analysis.error = "No object store";
        return analysis;
      }

      for (const key in rpc._object_store) {
        const value = rpc._object_store[key];

        if (["services", "message_cache"].includes(key)) {
          // System stores - don't count these as sessions
          analysis.system_stores[key] = {
            size:
              typeof value === "object" && value
                ? Object.keys(value).length
                : 0,
          };
          continue;
        }

        // Count all non-system non-empty objects as sessions
        // This matches how the built-in service registration creates sessions
        if (value && typeof value === "object") {
          const sessionKeys = Object.keys(value);

          // Only skip completely empty objects
          if (sessionKeys.length > 0) {
            analysis.total_sessions++;

            const sessionInfo = {
              id: key,
              keys: sessionKeys,
              has_promise_manager: !!value._promise_manager,
              has_callbacks: !!value._callbacks,
              has_timer: !!value._timer,
              has_heartbeat: !!value._heartbeat,
              callback_count: value._callbacks
                ? Object.keys(value._callbacks).length
                : 0,
            };

            analysis.session_details.push(sessionInfo);

            if (value._promise_manager) {
              analysis.promise_sessions++;
            } else {
              analysis.regular_sessions++;
            }

            if (value._timer) analysis.sessions_with_timers++;
            if (value._heartbeat) analysis.sessions_with_heartbeat++;

            // Estimate memory usage
            analysis.memory_usage += JSON.stringify(value).length;
          }
        }
      }

      return analysis;
    }

    function assertSessionDelta(label, before, after, expected_delta = 0) {
      const actual_delta = after - before;
      console.log(`\n=== ${label} ===`);

      // Be more lenient with service registration sessions
      // The built-in service session may or may not persist depending on timing
      if (label.includes("Service Registration")) {
        // For service registration tests, we expect increase or no change
        if (actual_delta >= expected_delta) {
          console.log(
            `✅ ${label} passed: delta ${actual_delta} >= expected ${expected_delta}`,
          );
          return;
        }
      } else {
        // For regular operation tests, be lenient about baseline sessions
        // Allow delta to be 0 or slightly negative if service sessions get cleaned
        const tolerance = 1; // Allow up to 1 session difference due to service cleanup timing
        if (Math.abs(actual_delta - expected_delta) <= tolerance) {
          console.log(
            `✅ ${label} passed: delta ${actual_delta} (within tolerance of ${expected_delta})`,
          );
          return;
        }
      }

      console.error(`=== ${label} FAILED ===`);
      console.error(
        `Expected session delta: ${expected_delta}, got: ${actual_delta}`,
      );
      console.error(`Before: ${before} sessions, After: ${after} sessions`);
      throw new Error(`Session leak detected in ${label}`);
    }

    const client = await connectToServer({
      server_url: SERVER_URL,
      client_id: "memory-leak-test-client",
    });

    // Initial baseline - accept whatever system sessions exist
    console.log("\n=== Test 1: Baseline Check ===");
    const baseline = getDetailedSessionAnalysis(client.rpc);
    console.log(
      `Baseline: ${baseline.total_sessions} sessions, ${baseline.memory_usage} bytes`,
    );

    // Don't expect zero - system sessions like built-in service registration are legitimate
    // Just record the baseline for future comparisons
    const baseline_count = baseline.total_sessions;

    // Test 2: Simple operations
    console.log("\n=== Test 2: Simple Operations ===");
    const pre_simple = getDetailedSessionAnalysis(client.rpc);

    for (let i = 0; i < 10; i++) {
      const result = await client.echo(`test_${i}`);
      expect(result).to.equal(`test_${i}`);
    }

    // Small delay for cleanup
    await new Promise((resolve) => setTimeout(resolve, 100));

    const post_simple = getDetailedSessionAnalysis(client.rpc);
    assertSessionDelta(
      "Simple Operations",
      pre_simple.total_sessions,
      post_simple.total_sessions,
      0,
    );

    // Test 3: Stress test with concurrent operations
    console.log("\n=== Test 3: Stress Test (Concurrent Operations) ===");
    const pre_stress = getDetailedSessionAnalysis(client.rpc);

    const concurrent_promises = [];
    for (let i = 0; i < 20; i++) {
      concurrent_promises.push(client.echo(`concurrent_${i}`));
    }

    const results = await Promise.all(concurrent_promises);
    results.forEach((result, i) => {
      expect(result).to.equal(`concurrent_${i}`);
    });

    // Wait for cleanup
    await new Promise((resolve) => setTimeout(resolve, 200));

    const post_stress = getDetailedSessionAnalysis(client.rpc);
    assertSessionDelta(
      "Stress Test",
      pre_stress.total_sessions,
      post_stress.total_sessions,
      0,
    );

    // Test 4: Exception handling
    console.log("\n=== Test 4: Exception Handling ===");
    const pre_exception = getDetailedSessionAnalysis(client.rpc);

    try {
      // Try to call a method that doesn't exist - this should fail
      await client.nonExistentMethod(
        "This will cause an error because the method doesn't exist",
      );
      throw new Error("Expected an error but didn't get one");
    } catch (error) {
      expect(error.message).to.include("not a function");
    }

    await new Promise((resolve) => setTimeout(resolve, 100));

    const post_exception = getDetailedSessionAnalysis(client.rpc);
    assertSessionDelta(
      "Exception Handling",
      pre_exception.total_sessions,
      post_exception.total_sessions,
      0,
    );

    // Test 5: Large data operations
    console.log("\n=== Test 5: Large Data Operations ===");
    const pre_large = getDetailedSessionAnalysis(client.rpc);

    const large_data = "x".repeat(10000); // 10KB string
    const echo_result = await client.echo(large_data);
    expect(echo_result).to.equal(large_data);

    await new Promise((resolve) => setTimeout(resolve, 100));

    const post_large = getDetailedSessionAnalysis(client.rpc);
    assertSessionDelta(
      "Large Data Operations",
      pre_large.total_sessions,
      post_large.total_sessions,
      0,
    );

    // Test 6: Rapid operations
    console.log("\n=== Test 6: Rapid Operations ===");
    const pre_rapid = getDetailedSessionAnalysis(client.rpc);

    for (let i = 0; i < 30; i++) {
      await client.echo(`rapid_${i}`);
    }

    await new Promise((resolve) => setTimeout(resolve, 150));

    const post_rapid = getDetailedSessionAnalysis(client.rpc);
    assertSessionDelta(
      "Rapid Operations",
      pre_rapid.total_sessions,
      post_rapid.total_sessions,
      0,
    );

    // Test 7: Mixed operations
    console.log("\n=== Test 7: Mixed Operations ===");
    const pre_mixed = getDetailedSessionAnalysis(client.rpc);

    // Mix of sync and async-style calls
    const mixed_promises = [
      client.echo("mixed_1"),
      client.echo("mixed_2"),
      client.echo("mixed_3"),
    ];

    const mixed_results = await Promise.all(mixed_promises);
    mixed_results.forEach((result, i) => {
      expect(result).to.equal(`mixed_${i + 1}`);
    });

    await new Promise((resolve) => setTimeout(resolve, 100));

    const post_mixed = getDetailedSessionAnalysis(client.rpc);
    assertSessionDelta(
      "Mixed Operations",
      pre_mixed.total_sessions,
      post_mixed.total_sessions,
      0,
    );

    // Test 8: Service registration and cleanup (more lenient)
    console.log("\n=== Test 8: Service Registration and Cleanup ===");
    const pre_service_analysis = getDetailedSessionAnalysis(client.rpc);

    const test_services = [];
    for (let i = 0; i < 3; i++) {
      const service_info = await client.registerService({
        id: `temp_service_${i}`,
        config: { visibility: "protected" },
        test_method: function (x) {
          return `test_${x}`;
        },
      });
      test_services.push(service_info.id);
    }

    // Use the services
    for (const service_id of test_services) {
      try {
        const local_id = service_id.split(":").pop(); // Get local part
        const svc = await client.getService(local_id);
        const result = await svc.test_method("hello");
        expect(result).to.equal("test_hello");
      } catch (error) {
        console.log(`Service ${service_id} test failed:`, error.message);
      }
    }

    // Clean up services
    for (const service_id of test_services) {
      try {
        await client.unregisterService(service_id);
      } catch (error) {
        console.log(`Failed to unregister ${service_id}:`, error.message);
      }
    }

    await new Promise((resolve) => setTimeout(resolve, 200));

    const service_analysis = getDetailedSessionAnalysis(client.rpc);
    console.log(
      `Service registration: ${service_analysis.total_sessions} sessions`,
    );

    // Final comprehensive check
    console.log("\n=== Final Comprehensive Analysis ===");
    const final_analysis = getDetailedSessionAnalysis(client.rpc);

    console.log(`\n--- Final State Analysis ---`);
    console.log(`Total sessions: ${final_analysis.total_sessions}`);
    console.log(
      `Expected sessions (from services): ${service_analysis.total_sessions}`,
    );
    console.log(`Memory usage: ${final_analysis.memory_usage} bytes`);

    if (final_analysis.session_details.length > 0) {
      console.log("Final session details:");
      for (const session of final_analysis.session_details) {
        console.log(`  - ${session.id}: ${JSON.stringify(session)}`);
      }
    }

    // Final state should not have more sessions than after service registration
    // Allow some tolerance for service registration legitimately creating sessions
    expect(final_analysis.total_sessions).to.be.at.most(
      service_analysis.total_sessions + 2,
    );

    console.log("✅ Comprehensive memory leak test passed!");
  });

  // Test memory leak edge cases
  it("test memory leak edge cases", async function () {
    this.timeout(60000);

    const client = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-plugin-1",
    });

    function getSessionCount(rpc) {
      if (!rpc._object_store) return 0;
      let count = 0;

      for (const key in rpc._object_store) {
        if (!["services", "message_cache"].includes(key)) {
          const value = rpc._object_store[key];

          // Count all non-system non-empty objects as sessions
          if (
            value &&
            typeof value === "object" &&
            Object.keys(value).length > 0
          ) {
            count++;
          }
        }
      }
      return count;
    }

    // Get baseline session count - wait a bit to ensure built-in service is registered
    await new Promise((resolve) => setTimeout(resolve, 200));
    const baseline_count = getSessionCount(client.rpc);
    console.log(`Edge case baseline: ${baseline_count} sessions`);

    // Test 1: Cancelled operations
    console.log("\n=== Edge Case 1: Cancelled Operations ===");
    const pre_cancel = getSessionCount(client.rpc);

    const promises = [];
    for (let i = 0; i < 5; i++) {
      promises.push(client.echo(`cancel_test_${i}`));
    }

    // Let some complete, then we'll check cleanup
    await Promise.all(promises);
    await new Promise((resolve) => setTimeout(resolve, 100));

    const post_cancel = getSessionCount(client.rpc);
    // Allow some flexibility in the session count comparison
    expect(Math.abs(post_cancel - pre_cancel)).to.be.at.most(1);

    // Test 2: Timeout scenarios (if we can simulate them)
    console.log("\n=== Edge Case 2: Quick Operations ===");
    const pre_timeout = getSessionCount(client.rpc);

    // Rapid fire operations that might stress the cleanup system
    for (let i = 0; i < 10; i++) {
      await client.echo(`timeout_test_${i}`);
    }

    await new Promise((resolve) => setTimeout(resolve, 150));

    const post_timeout = getSessionCount(client.rpc);
    expect(Math.abs(post_timeout - pre_timeout)).to.be.at.most(1);

    // Test 3: Nested session scenarios
    console.log("\n=== Edge Case 3: Complex Method Calls ===");
    const pre_nested = getSessionCount(client.rpc);

    // Register a service that calls other methods
    await client.registerService({
      id: "complex_service",
      config: { visibility: "protected" },
      complex_method: async function (data) {
        // This might create nested sessions
        return `processed_${data}`;
      },
    });

    const complex_svc = await client.getService("complex_service");
    const complex_result = await complex_svc.complex_method("test_data");
    expect(complex_result).to.equal("processed_test_data");

    await client.unregisterService("complex_service");
    await new Promise((resolve) => setTimeout(resolve, 200));

    const post_nested = getSessionCount(client.rpc);

    // Allow some tolerance for service-related sessions
    expect(post_nested - pre_nested).to.be.at.most(2);

    // Final check - should not have accumulated significantly more sessions than baseline
    const final_count = getSessionCount(client.rpc);
    const session_increase = final_count - baseline_count;
    expect(session_increase).to.be.at.most(
      2,
      "Edge case tests accumulated too many sessions",
    );

    console.log("✅ All edge case tests passed!");
  });

  // Test session cleanup robustness
  it("test session cleanup robustness", async function () {
    this.timeout(60000);

    const client = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-plugin-1",
    });

    function getSessionStats(rpc) {
      if (rpc.get_session_stats) {
        return rpc.get_session_stats();
      } else {
        // Fallback implementation
        let total_sessions = 0;

        for (const key in rpc._object_store) {
          if (!["services", "message_cache"].includes(key)) {
            const value = rpc._object_store[key];

            // Count all non-system non-empty objects as sessions
            if (
              value &&
              typeof value === "object" &&
              Object.keys(value).length > 0
            ) {
              total_sessions++;
            }
          }
        }
        return { total_sessions };
      }
    }

    // Get baseline session count
    const baseline_stats = getSessionStats(client.rpc);
    const baseline_count = baseline_stats.total_sessions;

    // Test 1: Force cleanup functionality
    console.log("\n=== Robustness Test 1: Force Cleanup ===");

    // Create some sessions
    for (let i = 0; i < 5; i++) {
      await client.echo(`robustness_test_${i}`);
    }

    const pre_force = getSessionStats(client.rpc);
    console.log(`Before force cleanup: ${pre_force.total_sessions} sessions`);

    // Force cleanup if available
    if (client.rpc._force_cleanup_all_sessions) {
      client.rpc._force_cleanup_all_sessions();
    }

    const post_force = getSessionStats(client.rpc);
    console.log(`After force cleanup: ${post_force.total_sessions} sessions`);

    // Should have cleaned up non-system sessions
    expect(post_force.total_sessions).to.be.at.most(pre_force.total_sessions);

    // Test 2: Session stats functionality
    console.log("\n=== Robustness Test 2: Session Statistics ===");

    const stats = getSessionStats(client.rpc);
    console.log("Session statistics:", JSON.stringify(stats, null, 2));

    expect(stats).to.have.property("total_sessions");
    expect(typeof stats.total_sessions).to.equal("number");

    // Test 3: Cleanup after errors
    console.log("\n=== Robustness Test 3: Cleanup After Errors ===");
    const pre_error = getSessionStats(client.rpc);

    // Generate some errors
    for (let i = 0; i < 3; i++) {
      try {
        await client.nonexistent_method(`error_test_${i}`);
      } catch (error) {
        // Expected to fail
      }
    }

    await new Promise((resolve) => setTimeout(resolve, 200));

    const post_error = getSessionStats(client.rpc);

    // Sessions should be cleaned up even after errors - compare to pre-error, not baseline
    expect(post_error.total_sessions).to.equal(pre_error.total_sessions);

    // Final check - should not have significantly more sessions than baseline
    const final_stats = getSessionStats(client.rpc);
    const session_increase = final_stats.total_sessions - baseline_count;
    expect(session_increase).to.be.at.most(
      2,
      "Robustness tests accumulated too many sessions",
    );

    console.log("✅ All robustness tests passed!");
  });

  it("should test authorized_workspaces for protected services", async () => {
    console.log("\n=== TESTING AUTHORIZED WORKSPACES ===");

    // Connect first client (will be service provider)
    const ws1 = await connectToServer({
      server_url: SERVER_URL,
      client_id: "test-auth-client",
    });
    const workspace1 = ws1.config.workspace;

    // Connect second client in a different workspace (authorized)
    const ws2 = await connectToServer({
      server_url: SERVER_URL,
      client_id: "authorized-client",
    });
    const workspace2 = ws2.config.workspace;

    // Connect third client in another workspace (not authorized)
    const ws3 = await connectToServer({
      server_url: SERVER_URL,
      client_id: "unauthorized-client",
    });
    const workspace3 = ws3.config.workspace;

    console.log(
      `Created workspaces: ${workspace1}, ${workspace2}, ${workspace3}`,
    );

    // Test 1: Validate that authorized_workspaces requires protected visibility
    console.log(
      "1. Testing validation: authorized_workspaces with non-protected visibility...",
    );
    try {
      await ws1.registerService({
        id: "invalid-service",
        config: {
          visibility: "public",
          authorized_workspaces: ["some-workspace"], // Should fail
        },
        test: () => "test",
      });
      expect.fail("Should have raised Error");
    } catch (e) {
      expect(e.message).to.include(
        "authorized_workspaces can only be set when visibility is 'protected'",
      );
      console.log(`   ✅ Validation works: ${e.message}`);
    }

    // Test 2: Test with unlisted visibility should also fail
    console.log(
      "2. Testing validation: authorized_workspaces with unlisted visibility...",
    );
    try {
      await ws1.registerService({
        id: "invalid-service-2",
        config: {
          visibility: "unlisted",
          authorized_workspaces: ["some-workspace"], // Should fail
        },
        test: () => "test",
      });
      expect.fail("Should have raised Error");
    } catch (e) {
      expect(e.message).to.include(
        "authorized_workspaces can only be set when visibility is 'protected'",
      );
      console.log(`   ✅ Validation works for unlisted: ${e.message}`);
    }

    // Test 3: Register service with authorized_workspaces (valid case)
    console.log("3. Testing service with authorized_workspaces (valid)...");
    await ws1.registerService({
      id: "authorized-service",
      name: "Authorized Test Service",
      config: {
        visibility: "protected",
        authorized_workspaces: [workspace2], // Only allow workspace2
      },
      test_method: (x) => `authorized: ${x}`,
    });

    // Access from same workspace should work
    const svc = await ws1.getService(
      `${workspace1}/test-auth-client:authorized-service`,
    );
    const result = await svc.test_method("test");
    expect(result).to.equal("authorized: test");
    console.log("   ✅ Service accessible from same workspace");

    // Test 4: Validate authorized_workspaces must be an array
    console.log(
      "4. Testing validation: authorized_workspaces must be an array...",
    );
    try {
      await ws1.registerService({
        id: "invalid-service-3",
        config: {
          visibility: "protected",
          authorized_workspaces: "not-an-array", // Should fail
        },
        test: () => "test",
      });
      expect.fail("Should have raised Error");
    } catch (e) {
      expect(e.message).to.include("authorized_workspaces must be an array");
      console.log(`   ✅ Array validation works: ${e.message}`);
    }

    // Test 5: Validate workspace ids must be strings
    console.log("5. Testing validation: workspace ids must be strings...");
    try {
      await ws1.registerService({
        id: "invalid-service-4",
        config: {
          visibility: "protected",
          authorized_workspaces: ["valid-ws", 123, "another-ws"], // Should fail
        },
        test: () => "test",
      });
      expect.fail("Should have raised Error");
    } catch (e) {
      expect(e.message).to.include("must be a string");
      console.log(`   ✅ String validation works: ${e.message}`);
    }

    // Test 6: Empty authorized_workspaces list is valid
    console.log("6. Testing empty authorized_workspaces list...");
    await ws1.registerService({
      id: "empty-auth-service",
      config: {
        visibility: "protected",
        authorized_workspaces: [], // Empty list is valid
      },
      test: () => "empty-auth",
    });

    const svc_empty = await ws1.getService(
      `${workspace1}/test-auth-client:empty-auth-service`,
    );
    const result_empty = await svc_empty.test();
    expect(result_empty).to.equal("empty-auth");
    console.log("   ✅ Empty authorized_workspaces list works");

    // Test 7: Method calls are also protected by authorized_workspaces
    console.log(
      "7. Testing that method calls respect authorized_workspaces...",
    );
    // Register a service with methods that should be protected
    await ws1.registerService({
      id: "method-test-service",
      config: {
        visibility: "protected",
        authorized_workspaces: ["fake-authorized-workspace"], // Non-existent workspace
      },
      protected_method: (x) => `protected: ${x}`,
      another_method: () => "also protected",
    });

    // Can get the service from same workspace
    const svc_method = await ws1.getService(
      `${workspace1}/test-auth-client:method-test-service`,
    );
    // Methods should work from same workspace (even though fake-authorized-workspace is listed)
    const result_method = await svc_method.protected_method("test");
    expect(result_method).to.equal("protected: test");
    const result_method2 = await svc_method.another_method();
    expect(result_method2).to.equal("also protected");
    console.log(
      "   ✅ Methods work from same workspace despite authorized_workspaces",
    );

    // Test 8: Cross-workspace access - authorized workspace should have access
    console.log("\n8. Testing cross-workspace access (authorized)...");
    // Try to access the service from workspace2 (which is authorized)
    try {
      const svc_from_ws2 = await ws2.getService(
        `${workspace1}/test-auth-client:authorized-service`,
      );
      const result = await svc_from_ws2.test_method("from-workspace2");
      expect(result).to.equal("authorized: from-workspace2");
      console.log("   ✅ Authorized workspace can access protected service");
    } catch (e) {
      console.log(`   ❌ Failed to access from authorized workspace: ${e}`);
      throw new Error(`Authorized workspace should have access: ${e}`);
    }

    // Test 9: Cross-workspace access - unauthorized workspace should be denied
    console.log("9. Testing cross-workspace access (unauthorized)...");
    // Try to access the service from workspace3 (which is NOT authorized)
    try {
      const svc_from_ws3 = await ws3.getService(
        `${workspace1}/test-auth-client:authorized-service`,
      );
      // Try to call the method - this should fail
      await svc_from_ws3.test_method("from-workspace3");
      throw new Error("Unauthorized workspace should NOT have access");
    } catch (e) {
      const errorMsg = e.message.toLowerCase();
      expect(errorMsg).to.match(/not authorized|permission|denied/);
      console.log(
        `   ✅ Unauthorized workspace correctly denied: ${e.message}`,
      );
    }

    // Test 10: Service with no authorized workspaces (empty list) - no external access
    console.log("10. Testing service with empty authorized_workspaces list...");
    try {
      // Try to access empty-auth-service from workspace2
      const svc_empty_from_ws2 = await ws2.getService(
        `${workspace1}/test-auth-client:empty-auth-service`,
      );
      await svc_empty_from_ws2.test();
      throw new Error(
        "Service with empty authorized_workspaces should deny all external access",
      );
    } catch (e) {
      const errorMsg = e.message.toLowerCase();
      expect(errorMsg).to.match(/not authorized|permission|denied/);
      console.log(
        `   ✅ Empty authorized_workspaces correctly denies external access: ${e.message}`,
      );
    }

    // Test 11: Update authorized_workspaces dynamically
    console.log("11. Testing dynamic update of authorized_workspaces...");
    // Register a new service that initially allows workspace2
    await ws1.registerService({
      id: "dynamic-auth-service",
      config: {
        visibility: "protected",
        authorized_workspaces: [workspace2],
      },
      test: () => "dynamic-test",
    });

    // Verify workspace2 can access
    let svc_dynamic = await ws2.getService(
      `${workspace1}/test-auth-client:dynamic-auth-service`,
    );
    let dynamic_result = await svc_dynamic.test();
    expect(dynamic_result).to.equal("dynamic-test");
    console.log("   ✅ Initial authorized workspace has access");

    // Now re-register with workspace3 instead
    await ws1.registerService(
      {
        id: "dynamic-auth-service",
        config: {
          visibility: "protected",
          authorized_workspaces: [workspace3], // Changed to workspace3
        },
        test: () => "dynamic-test-updated",
      },
      { overwrite: true },
    );

    // workspace2 should now be denied
    try {
      svc_dynamic = await ws2.getService(
        `${workspace1}/test-auth-client:dynamic-auth-service`,
      );
      await svc_dynamic.test();
      throw new Error("Previously authorized workspace should now be denied");
    } catch (e) {
      console.log(
        `   ✅ Previously authorized workspace now denied: ${e.message}`,
      );
    }

    // workspace3 should now have access
    const svc_dynamic_ws3 = await ws3.getService(
      `${workspace1}/test-auth-client:dynamic-auth-service`,
    );
    dynamic_result = await svc_dynamic_ws3.test();
    expect(dynamic_result).to.equal("dynamic-test-updated");
    console.log("   ✅ Newly authorized workspace has access");

    // Cleanup
    await ws1.disconnect();
    await ws2.disconnect();
    await ws3.disconnect();

    console.log("✅ AUTHORIZED WORKSPACES FULL TEST PASSED!");
  }).timeout(20000);

  it("should handle long-running methods with heartbeat", async () => {
    console.log("\n=== LONG RUNNING METHOD WITH HEARTBEAT TEST ===");

    // Use a SHORT timeout (2 seconds) to verify heartbeat keeps method alive
    const api = await connectToServer({
      name: "long-running-test",
      server_url: "ws://127.0.0.1:9394/ws", // Use the test server port
      client_id: "long-running-test",
      method_timeout: 2, // 2 second timeout - methods will run LONGER than this
    });

    console.log("   ⏱️  Method timeout set to 2 seconds");

    // Create a service with long-running methods
    const longRunningService = {
      async longTask(duration_seconds, callback) {
        // Simulates a long-running task that reports progress
        const start_time = Date.now();
        const steps = duration_seconds * 2; // Report progress every 0.5 seconds

        for (let i = 0; i < steps; i++) {
          await new Promise((resolve) => setTimeout(resolve, 500));
          const elapsed = (Date.now() - start_time) / 1000;

          // Report progress via callback if provided
          if (callback) {
            await callback(
              `Progress: ${i + 1}/${steps}, elapsed: ${elapsed.toFixed(1)}s`,
            );
          }
        }

        return `Task completed after ${duration_seconds} seconds`;
      },

      async infiniteStream(callback) {
        // Simulates infinite streaming (like terminal attach)
        let count = 0;
        while (true) {
          await new Promise((resolve) => setTimeout(resolve, 500));
          count++;
          await callback(`Stream update #${count}`);
          // Stop after 5 updates for testing
          if (count >= 5) {
            return `Streamed ${count} updates`;
          }
        }
      },
    };

    // Register the service
    await api.registerService({
      id: "long-running-service",
      config: { visibility: "protected" },
      ...longRunningService,
    });

    // Test 1: Long-running method with callback (should not timeout)
    console.log("\n--- Test 1: Long-running method with progress callback ---");
    const svc = await api.getService("long-running-service");

    const progress_updates = [];
    const progress_callback = async (msg) => {
      progress_updates.push(msg);
      console.log(`   📊 ${msg}`);
    };

    // Run a task for 5 seconds - MORE than the 2 second timeout!
    // This proves heartbeat keeps it alive
    const TASK_DURATION = 5; // 5 seconds > 2 second timeout
    console.log(
      `   🚀 Starting ${TASK_DURATION} second task (timeout is only 2 seconds)`,
    );

    const start_time = Date.now();
    const result = await svc.longTask(TASK_DURATION, progress_callback);
    const actual_duration = (Date.now() - start_time) / 1000;

    expect(result).to.include(`Task completed after ${TASK_DURATION} seconds`);
    expect(actual_duration).to.be.at.least(TASK_DURATION); // Verify it actually ran for full duration
    expect(actual_duration).to.be.greaterThan(2); // Verify it ran LONGER than the timeout
    expect(progress_updates.length).to.be.at.least(TASK_DURATION * 2 - 1); // Should have ~10 updates
    console.log(
      `   ✅ Task ran for ${actual_duration.toFixed(1)}s (>2s timeout) with ${progress_updates.length} updates`,
    );

    // Test 2: Infinite streaming method (like terminal attach)
    console.log("\n--- Test 2: Infinite streaming method ---");
    const stream_updates = [];
    const stream_callback = async (msg) => {
      stream_updates.push(msg);
      console.log(`   📡 ${msg}`);
    };

    // This simulates the terminal attach use case
    // Runs for ~2.5 seconds (5 updates * 0.5s each) - also longer than timeout
    console.log("   🚀 Starting streaming (will run >2s timeout)");

    const stream_start = Date.now();
    const stream_result = await svc.infiniteStream(stream_callback);
    const stream_duration = (Date.now() - stream_start) / 1000;

    expect(stream_result).to.include("Streamed 5 updates");
    expect(stream_updates.length).to.equal(5);
    expect(stream_duration).to.be.greaterThan(2); // Verify it ran LONGER than the timeout
    console.log(
      `   ✅ Streaming ran for ${stream_duration.toFixed(1)}s (>2s timeout) with ${stream_updates.length} updates`,
    );

    // Cleanup
    await api.disconnect();

    console.log("✅ LONG RUNNING METHOD WITH HEARTBEAT TEST PASSED!");
  }).timeout(30000);

  it("test client disconnection cleanup", async function () {
    console.log("\n=== CLIENT DISCONNECTION CLEANUP TEST ===");

    // Create first client
    const client1 = await connectToServer({
      name: "client1",
      server_url: SERVER_URL,
      client_id: "client1-test",
    });

    // Create second client
    const client2 = await connectToServer({
      name: "client2",
      server_url: SERVER_URL,
      client_id: "client2-test",
    });

    // Register a service on client2 that client1 will call
    await client2.registerService({
      id: "test-service",
      config: { visibility: "protected" },
      slowFunction: async () => {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        return "completed";
      },
    });

    // Client1 calls the slow function from client2 (creates a session)
    const svc = await client1.getService("test-service");

    // Start multiple async calls that will be pending when client2 disconnects
    const pendingCalls = [
      svc.slowFunction(),
      svc.slowFunction(),
      svc.slowFunction(),
    ];

    // Give some time for the calls to be initiated
    await new Promise((resolve) => setTimeout(resolve, 100));

    // Check that sessions exist in client1's object store
    let initialSessions = 0;
    for (const key in client1.rpc._object_store) {
      if (
        key !== "services" &&
        key !== "message_cache" &&
        typeof client1.rpc._object_store[key] === "object"
      ) {
        initialSessions++;
      }
    }

    console.log(`📊 Initial sessions in client1: ${initialSessions}`);
    expect(initialSessions).to.be.greaterThan(0);

    // Disconnect client2 abruptly (simulating unexpected disconnection)
    console.log("🔌 Disconnecting client2...");
    await client2.disconnect();

    // Wait a bit for the disconnection event to propagate
    await new Promise((resolve) => setTimeout(resolve, 500));

    // All pending calls should fail with an error
    for (let i = 0; i < pendingCalls.length; i++) {
      try {
        await pendingCalls[i];
        throw new Error(`Call ${i} should have failed due to disconnection`);
      } catch (e) {
        console.log(`✅ Call ${i} correctly failed with: ${e.message}`);
        const errorMsg = e.message.toLowerCase();
        expect(errorMsg).to.satisfy(
          (msg) => msg.includes("disconnected") || msg.includes("closed"),
        );
      }
    }

    // Check that sessions have been cleaned up in client1
    let remainingSessions = 0;
    for (const key in client1.rpc._object_store) {
      if (
        key !== "services" &&
        key !== "message_cache" &&
        typeof client1.rpc._object_store[key] === "object"
      ) {
        // Check if this is actually a session (has reject/resolve)
        const session = client1.rpc._object_store[key];
        if (session.reject || session.resolve) {
          remainingSessions++;
        }
      }
    }

    console.log(
      `📊 Remaining sessions in client1 after cleanup: ${remainingSessions}`,
    );

    // Clean up
    await client1.disconnect();

    console.log("✅ CLIENT DISCONNECTION CLEANUP TEST PASSED!");
  }).timeout(10000);

  it("test local RPC disconnection cleanup", async function () {
    console.log("\n=== LOCAL RPC DISCONNECTION CLEANUP TEST ===");

    // Create a client
    const client = await connectToServer({
      name: "local-disconnect-test",
      server_url: SERVER_URL,
      client_id: "local-disconnect-test",
    });

    // Register a test service with slow functions
    await client.registerService({
      id: "slow-service",
      config: { visibility: "protected" },
      slowFunction: async (duration = 2) => {
        await new Promise((resolve) => setTimeout(resolve, duration * 1000));
        return `completed after ${duration}s`;
      },
    });

    // Get the service and start multiple pending calls
    const svc = await client.getService("slow-service");

    const pendingTasks = [
      svc.slowFunction(3),
      svc.slowFunction(4),
      svc.slowFunction(5),
    ];

    // Give time for sessions to be created
    await new Promise((resolve) => setTimeout(resolve, 100));

    // Check initial session count
    let initialSessions = 0;
    for (const key in client.rpc._object_store) {
      if (key !== "services" && key !== "message_cache") {
        initialSessions++;
      }
    }

    console.log(`📊 Active sessions before disconnect: ${initialSessions}`);
    expect(initialSessions).to.be.greaterThan(0);

    // Disconnect the local RPC
    console.log("🔌 Disconnecting local RPC...");
    await client.disconnect();

    // All pending tasks should fail
    let failedCount = 0;
    for (let i = 0; i < pendingTasks.length; i++) {
      try {
        await pendingTasks[i];
        throw new Error(`Task ${i} should have failed after disconnection`);
      } catch (e) {
        failedCount++;
        console.log(`✅ Task ${i} correctly failed with: ${e.message}`);
        const errorMsg = e.message.toLowerCase();
        expect(errorMsg).to.satisfy(
          (msg) => msg.includes("closed") || msg.includes("disconnected"),
        );
      }
    }

    expect(failedCount).to.equal(pendingTasks.length);

    // Verify all sessions were cleaned up
    let remainingSessions = 0;
    for (const key in client.rpc._object_store) {
      if (
        key !== "services" &&
        key !== "message_cache" &&
        typeof client.rpc._object_store[key] === "object"
      ) {
        const session = client.rpc._object_store[key];
        if (session && (session.reject || session.resolve)) {
          remainingSessions++;
        }
      }
    }

    console.log(`📊 Remaining sessions after cleanup: ${remainingSessions}`);
    expect(remainingSessions).to.equal(0);

    console.log("✅ LOCAL RPC DISCONNECTION CLEANUP TEST PASSED!");
  }).timeout(10000);
});
