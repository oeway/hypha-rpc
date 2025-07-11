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
    function multiply_context(a, b, kwargs) {
      assert(kwargs._rkwargs === true, "kwargs should have _rkwargs flag");
      assert(kwargs.context.user, "context should not be null");
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
        // Function with 0 parameters - should receive kwargs as last argument
        quickTest: function (kwargs) {
          assert(kwargs._rkwargs === true, "kwargs should have _rkwargs flag");
          assert(kwargs.context.user, "context should not be null");
          console.log("quickTest called with context:", !!kwargs.context);
          return {
            success: true,
            hasContext: !!kwargs.context,
            workspace: kwargs.context?.ws,
          };
        },
        // Function with 1 parameter - should receive (userInput, kwargs)
        processInput: function (userInput, kwargs) {
          assert(kwargs._rkwargs === true, "kwargs should have _rkwargs flag");
          assert(kwargs.context.user, "context.user should not be null");
          console.log("processInput called with:", {
            hasContext: !!kwargs.context,
            userInput,
          });
          return {
            success: true,
            processed: userInput,
            workspace: kwargs.context?.ws,
          };
        },
        // Function with 2 parameters - should receive (data, options, kwargs)
        complexMethod: function (data, options, kwargs) {
          assert(kwargs._rkwargs === true, "kwargs should have _rkwargs flag");
          assert(kwargs.context.user, "context.user should not be null");
          console.log("complexMethod called with:", {
            hasContext: !!kwargs.context,
            data,
            options,
          });
          return {
            success: true,
            data,
            options,
            workspace: kwargs.context?.ws,
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

  it("should properly clean up sessions and prevent memory leaks", async () => {
    const client = await connectToServer({
      client_id: "memory-leak-test-client",
      server_url: SERVER_URL,
    });

    // Wait for service registration to complete - this ensures we get a clean baseline
    await new Promise((resolve) => setTimeout(resolve, 500));

    // Helper to get all session keys (excluding permanent stores)
    const getSessionKeys = (rpc) => {
      return Object.keys(rpc._object_store).filter(
        (k) => k !== "services" && k !== "message_cache",
      );
    };

    // Helper to get total session count including nested sessions
    const getTotalSessionCount = (rpc) => {
      let count = 0;
      const countSessions = (obj, path = "") => {
        for (const key in obj) {
          if (key !== "services" && key !== "message_cache") {
            count++;
            if (
              typeof obj[key] === "object" &&
              obj[key] !== null &&
              !Array.isArray(obj[key])
            ) {
              countSessions(obj[key], path + "." + key);
            }
          }
        }
      };
      countSessions(rpc._object_store);
      return count;
    };

    // Test 1: Verify baseline state (should be 0 like Python after registration completes)
    const initialKeys = getSessionKeys(client.rpc);
    const initialCount = getTotalSessionCount(client.rpc);
    console.log("Initial session keys (after registration):", initialKeys);
    console.log("Initial session count (after registration):", initialCount);

    // Now JavaScript should have 0 sessions like Python!
    expect(initialCount).to.equal(0);

    // Test 2: Simple echo call - should not create permanent sessions
    const result = await client.echo("test");
    expect(result).to.equal("test");
    await new Promise((resolve) => setTimeout(resolve, 200)); // Give time for cleanup

    const afterEchoKeys = getSessionKeys(client.rpc);
    const afterEchoCount = getTotalSessionCount(client.rpc);
    console.log("After echo - keys:", afterEchoKeys);
    console.log("After echo - count:", afterEchoCount);

    // Echo should not create any new permanent sessions
    expect(afterEchoCount).to.equal(0);

    // Test 3: List services (which might create sessions)
    const services = await client.listServices();
    expect(Array.isArray(services)).to.be.true;
    await new Promise((resolve) => setTimeout(resolve, 200));

    const afterListKeys = getSessionKeys(client.rpc);
    const afterListCount = getTotalSessionCount(client.rpc);
    console.log("After listServices - keys:", afterListKeys);
    console.log("After listServices - count:", afterListCount);
    expect(afterListCount).to.equal(0);

    // Test 4: Multiple operations
    await Promise.all([
      client.echo("test1"),
      client.echo("test2"),
      client.echo("test3"),
    ]);
    await new Promise((resolve) => setTimeout(resolve, 200));

    const afterMultipleKeys = getSessionKeys(client.rpc);
    const afterMultipleCount = getTotalSessionCount(client.rpc);
    console.log("After multiple ops - keys:", afterMultipleKeys);
    console.log("After multiple ops - count:", afterMultipleCount);
    expect(afterMultipleCount).to.equal(0);

    // Test 5: Verify message cache is clean
    if (client.rpc._object_store.message_cache) {
      expect(
        Object.keys(client.rpc._object_store.message_cache),
      ).to.have.lengthOf(0);
    }

    // Test 6: JavaScript now works exactly like Python!
    console.log("🎉 JavaScript session management now matches Python:");
    console.log("- Initial sessions (after registration): 0 ✓");
    console.log("- After all operations: 0 ✓");
    console.log("- Perfect session cleanup like Python! ✓");

    // Final cleanup
    await client.disconnect();

    console.log(
      "Test completed successfully - JavaScript now behaves like Python!",
    );
  }).timeout(15000);

  it("should handle message batching with small frequent messages", async () => {
    // Create server and client
    const server = await connectToServer({
      client_id: "js-batching-server",
      server_url: SERVER_URL,
    });

    const workspace = server.config.workspace;
    const token = await server.generateToken();

    // Track received messages
    const receivedMessages = [];

    // Service that collects small messages
    function collectMessage(msgId, data) {
      receivedMessages.push({
        id: msgId,
        data: data,
        timestamp: Date.now(),
      });
      return `received_${msgId}`;
    }

    await server.registerService({
      id: "js-message-collector",
      config: { visibility: "public" },
      collect: collectMessage,
    });

    // Connect client
    const client = await connectToServer({
      client_id: "js-batching-client",
      server_url: SERVER_URL,
      workspace: workspace,
      token: token,
    });

    // Get service
    const collector = await client.getService("js-message-collector");

    // Test small frequent messages (should be batched)
    console.log("Sending 20 small frequent messages...");
    const startTime = Date.now();

    // Send messages rapidly
    const promises = [];
    for (let i = 0; i < 20; i++) {
      const smallData = `small_message_${i}`.repeat(10); // ~150 bytes each
      const promise = collector.collect(i, smallData);
      promises.push(promise);
    }

    // Wait for all to complete
    const results = await Promise.all(promises);
    const endTime = Date.now();

    // Verify results
    expect(results.length).to.equal(20);
    expect(receivedMessages.length).to.equal(20);

    // Verify all messages received correctly
    for (let i = 0; i < 20; i++) {
      expect(results).to.contain(`received_${i}`);
      expect(receivedMessages.some((msg) => msg.id === i)).to.equal(true);
    }

    console.log(`Small messages completed in ${endTime - startTime}ms`);

    await server.disconnect();
    await client.disconnect();
  });

  it("should handle large messages with chunking", async () => {
    // Create server and client
    const server = await connectToServer({
      client_id: "js-large-msg-server",
      server_url: SERVER_URL,
    });

    const workspace = server.config.workspace;
    const token = await server.generateToken();

    // Track received messages
    const receivedLargeMessages = [];

    // Service that handles large messages
    function handleLargeMessage(msgId, largeData) {
      receivedLargeMessages.push({
        id: msgId,
        size: largeData.length,
        timestamp: Date.now(),
      });
      return `processed_large_${msgId}`;
    }

    await server.registerService({
      id: "js-large-message-handler",
      config: { visibility: "public" },
      handle_large: handleLargeMessage,
    });

    // Connect client
    const client = await connectToServer({
      client_id: "js-large-msg-client",
      server_url: SERVER_URL,
      workspace: workspace,
      token: token,
    });

    // Get service
    const handler = await client.getService("js-large-message-handler");

    // Test large messages (should use chunking, not batching)
    console.log("Sending 3 large messages...");
    const startTime = Date.now();

    // Send large messages
    const promises = [];
    for (let i = 0; i < 3; i++) {
      // Create large data (~1MB each, well above batch size limit)
      const largeData = "X".repeat(1024 * 1024); // 1MB
      const promise = handler.handle_large(i, largeData);
      promises.push(promise);
    }

    // Wait for all to complete
    const results = await Promise.all(promises);
    const endTime = Date.now();

    // Verify results
    expect(results.length).to.equal(3);
    expect(receivedLargeMessages.length).to.equal(3);

    // Verify all messages received correctly
    for (let i = 0; i < 3; i++) {
      expect(results).to.contain(`processed_large_${i}`);
      expect(
        receivedLargeMessages.some(
          (msg) => msg.id === i && msg.size === 1024 * 1024,
        ),
      ).to.equal(true);
    }

    console.log(`Large messages completed in ${endTime - startTime}ms`);

    await server.disconnect();
    await client.disconnect();
  });

  it("should handle mixed small and large messages intelligently", async () => {
    // Create server and client
    const server = await connectToServer({
      client_id: "js-mixed-msg-server",
      server_url: SERVER_URL,
    });

    const workspace = server.config.workspace;
    const token = await server.generateToken();

    // Track received messages
    const receivedMixedMessages = [];

    // Service that handles mixed messages
    function handleMixedMessage(msgId, data, msgType) {
      receivedMixedMessages.push({
        id: msgId,
        type: msgType,
        size: data.length,
        timestamp: Date.now(),
      });
      return `processed_${msgType}_${msgId}`;
    }

    await server.registerService({
      id: "js-mixed-message-handler",
      config: { visibility: "public" },
      handle_mixed: handleMixedMessage,
    });

    // Connect client
    const client = await connectToServer({
      client_id: "js-mixed-msg-client",
      server_url: SERVER_URL,
      workspace: workspace,
      token: token,
    });

    // Get service
    const handler = await client.getService("js-mixed-message-handler");

    // Test mixed messages
    console.log("Sending mixed small and large messages...");
    const startTime = Date.now();

    // Send mixed messages
    const promises = [];
    for (let i = 0; i < 10; i++) {
      if (i % 3 === 0) {
        // Send large message (should use chunking)
        const largeData = "L".repeat(500 * 1024); // 500KB
        const promise = handler.handle_mixed(i, largeData, "large");
        promises.push(promise);
      } else {
        // Send small message (should be batched)
        const smallData = `small_${i}`.repeat(50); // ~400 bytes
        const promise = handler.handle_mixed(i, smallData, "small");
        promises.push(promise);
      }
    }

    // Wait for all to complete
    const results = await Promise.all(promises);
    const endTime = Date.now();

    // Verify results
    expect(results.length).to.equal(10);
    expect(receivedMixedMessages.length).to.equal(10);

    // Count message types
    const largeCount = receivedMixedMessages.filter(
      (msg) => msg.type === "large",
    ).length;
    const smallCount = receivedMixedMessages.filter(
      (msg) => msg.type === "small",
    ).length;

    // Should have 4 large messages (indices 0, 3, 6, 9) and 6 small messages
    expect(largeCount).to.equal(4);
    expect(smallCount).to.equal(6);

    // Verify large messages have correct size
    receivedMixedMessages.forEach((msg) => {
      if (msg.type === "large") {
        expect(msg.size).to.equal(500 * 1024);
      } else {
        expect(msg.size).to.be.below(1000); // Small messages
      }
    });

    console.log(`Mixed messages completed in ${endTime - startTime}ms`);

    await server.disconnect();
    await client.disconnect();
  });

  it("should handle typed arrays of different sizes", async () => {
    // Create server and client
    const server = await connectToServer({
      client_id: "js-typed-array-server",
      server_url: SERVER_URL,
    });

    const workspace = server.config.workspace;
    const token = await server.generateToken();

    // Track received arrays
    const receivedArrays = [];

    // Service that processes typed arrays
    function processTypedArray(arrayId, arr) {
      receivedArrays.push({
        id: arrayId,
        size: arr.length,
        type: arr.constructor.name,
        byteLength: arr.byteLength,
        timestamp: Date.now(),
      });
      return `processed_array_${arrayId}`;
    }

    await server.registerService({
      id: "js-typed-array-processor",
      config: { visibility: "public" },
      process: processTypedArray,
    });

    // Connect client
    const client = await connectToServer({
      client_id: "js-typed-array-client",
      server_url: SERVER_URL,
      workspace: workspace,
      token: token,
    });

    // Get service
    const processor = await client.getService("js-typed-array-processor");

    // Test arrays of different sizes
    console.log("Sending typed arrays of different sizes...");
    const startTime = Date.now();

    const promises = [];

    // Small arrays (should be batched)
    for (let i = 0; i < 5; i++) {
      const smallArray = new Float32Array(100); // ~400 bytes
      smallArray.fill(i);
      const promise = processor.process(`small_${i}`, smallArray);
      promises.push(promise);
    }

    // Medium arrays (might be batched or sent individually)
    for (let i = 0; i < 3; i++) {
      const mediumArray = new Float32Array(10000); // ~40KB
      mediumArray.fill(i + 100);
      const promise = processor.process(`medium_${i}`, mediumArray);
      promises.push(promise);
    }

    // Large arrays (should use chunking)
    for (let i = 0; i < 2; i++) {
      const largeArray = new Float32Array(1000000); // ~4MB
      largeArray.fill(i + 1000);
      const promise = processor.process(`large_${i}`, largeArray);
      promises.push(promise);
    }

    // Wait for all to complete
    const results = await Promise.all(promises);
    const endTime = Date.now();

    // Verify results
    expect(results.length).to.equal(10);
    expect(receivedArrays.length).to.equal(10);

    // Verify array processing
    const smallArrays = receivedArrays.filter((arr) =>
      arr.id.startsWith("small_"),
    );
    const mediumArrays = receivedArrays.filter((arr) =>
      arr.id.startsWith("medium_"),
    );
    const largeArrays = receivedArrays.filter((arr) =>
      arr.id.startsWith("large_"),
    );

    expect(smallArrays.length).to.equal(5);
    expect(mediumArrays.length).to.equal(3);
    expect(largeArrays.length).to.equal(2);

    // Verify sizes
    smallArrays.forEach((arr) => {
      expect(arr.size).to.equal(100);
      expect(arr.byteLength).to.equal(100 * 4); // Float32Array = 4 bytes per element
    });

    mediumArrays.forEach((arr) => {
      expect(arr.size).to.equal(10000);
      expect(arr.byteLength).to.equal(10000 * 4);
    });

    largeArrays.forEach((arr) => {
      expect(arr.size).to.equal(1000000);
      expect(arr.byteLength).to.equal(1000000 * 4);
    });

    console.log(`Typed arrays completed in ${endTime - startTime}ms`);

    await server.disconnect();
    await client.disconnect();
  });

  it("should compare performance with and without batching", async () => {
    // Test with batching enabled
    const server1 = await connectToServer({
      client_id: "js-batching-perf-server1",
      server_url: SERVER_URL,
    });

    const workspace = server1.config.workspace;
    const token = await server1.generateToken();

    // Verify batching is enabled
    expect(server1.rpc._message_batching_enabled).to.equal(true);

    // Service for performance testing
    const callCount = { count: 0 };

    function fastEcho(msgId, data) {
      callCount.count++;
      return `echo_${msgId}`;
    }

    await server1.registerService({
      id: "js-perf-echo",
      config: { visibility: "public" },
      echo: fastEcho,
    });

    // Connect client with batching enabled
    const client1 = await connectToServer({
      client_id: "js-batching-perf-client1",
      server_url: SERVER_URL,
      workspace: workspace,
      token: token,
    });

    const echoService1 = await client1.getService("js-perf-echo");

    // Test with batching - send 30 small messages rapidly
    console.log("Testing with batching enabled...");
    callCount.count = 0;
    const startTime1 = Date.now();

    const promises1 = [];
    for (let i = 0; i < 30; i++) {
      const smallData = `msg_${i}`.repeat(20); // ~140 bytes
      const promise = echoService1.echo(i, smallData);
      promises1.push(promise);
    }

    const results1 = await Promise.all(promises1);
    const batchingTime = Date.now() - startTime1;
    const batchingCalls = callCount.count;

    expect(results1.length).to.equal(30);
    expect(batchingCalls).to.equal(30);

    // Now test with batching disabled
    const server2 = await connectToServer({
      client_id: "js-no-batching-server",
      server_url: SERVER_URL,
    });

    // Disable batching on server
    server2.rpc._message_batching_enabled = false;

    const workspace2 = server2.config.workspace;
    const token2 = await server2.generateToken();

    callCount.count = 0;

    await server2.registerService({
      id: "js-perf-echo-no-batch",
      config: { visibility: "public" },
      echo: fastEcho,
    });

    // Connect client with batching disabled
    const client2 = await connectToServer({
      client_id: "js-no-batching-client",
      server_url: SERVER_URL,
      workspace: workspace2,
      token: token2,
    });

    // Disable batching on client
    client2.rpc._message_batching_enabled = false;

    const echoService2 = await client2.getService("js-perf-echo-no-batch");

    // Test without batching - send same 30 messages
    console.log("Testing with batching disabled...");
    callCount.count = 0;
    const startTime2 = Date.now();

    const promises2 = [];
    for (let i = 0; i < 30; i++) {
      const smallData = `msg_${i}`.repeat(20); // Same size as before
      const promise = echoService2.echo(i, smallData);
      promises2.push(promise);
    }

    const results2 = await Promise.all(promises2);
    const noBatchingTime = Date.now() - startTime2;
    const noBatchingCalls = callCount.count;

    expect(results2.length).to.equal(30);
    expect(noBatchingCalls).to.equal(30);

    // Print performance comparison
    console.log(`With batching: ${batchingTime}ms`);
    console.log(`Without batching: ${noBatchingTime}ms`);

    // Batching should generally be equal or faster for small messages
    // The improvement depends on network conditions, but we mainly verify it doesn't break functionality

    await server1.disconnect();
    await client1.disconnect();
    await server2.disconnect();
    await client2.disconnect();
  });

  it("should handle batching with binary data", async () => {
    // Create server and client
    const server = await connectToServer({
      client_id: "js-binary-batching-server",
      server_url: SERVER_URL,
    });

    const workspace = server.config.workspace;
    const token = await server.generateToken();

    // Track received binary data
    const receivedBinaryData = [];

    // Service that handles binary data
    function handleBinaryData(dataId, binaryData) {
      receivedBinaryData.push({
        id: dataId,
        size: binaryData.byteLength,
        type: binaryData.constructor.name,
        timestamp: Date.now(),
      });
      return `processed_binary_${dataId}`;
    }

    await server.registerService({
      id: "js-binary-handler",
      config: { visibility: "public" },
      handle_binary: handleBinaryData,
    });

    // Connect client
    const client = await connectToServer({
      client_id: "js-binary-client",
      server_url: SERVER_URL,
      workspace: workspace,
      token: token,
    });

    // Get service
    const handler = await client.getService("js-binary-handler");

    // Test binary data of different sizes
    console.log("Sending binary data of different sizes...");
    const startTime = Date.now();

    const promises = [];

    // Small binary data (should be batched)
    for (let i = 0; i < 5; i++) {
      const smallBuffer = new ArrayBuffer(1024); // 1KB
      const smallView = new Uint8Array(smallBuffer);
      smallView.fill(i);
      const promise = handler.handle_binary(`small_${i}`, smallBuffer);
      promises.push(promise);
    }

    // Medium binary data
    for (let i = 0; i < 3; i++) {
      const mediumBuffer = new ArrayBuffer(50 * 1024); // 50KB
      const mediumView = new Uint8Array(mediumBuffer);
      mediumView.fill(i + 100);
      const promise = handler.handle_binary(`medium_${i}`, mediumBuffer);
      promises.push(promise);
    }

    // Large binary data (should use chunking)
    for (let i = 0; i < 2; i++) {
      const largeBuffer = new ArrayBuffer(2 * 1024 * 1024); // 2MB
      const largeView = new Uint8Array(largeBuffer);
      largeView.fill(i + 200);
      const promise = handler.handle_binary(`large_${i}`, largeBuffer);
      promises.push(promise);
    }

    // Wait for all to complete
    const results = await Promise.all(promises);
    const endTime = Date.now();

    // Verify results
    expect(results.length).to.equal(10);
    expect(receivedBinaryData.length).to.equal(10);

    // Verify binary data processing
    const smallBinaryData = receivedBinaryData.filter((data) =>
      data.id.startsWith("small_"),
    );
    const mediumBinaryData = receivedBinaryData.filter((data) =>
      data.id.startsWith("medium_"),
    );
    const largeBinaryData = receivedBinaryData.filter((data) =>
      data.id.startsWith("large_"),
    );

    expect(smallBinaryData.length).to.equal(5);
    expect(mediumBinaryData.length).to.equal(3);
    expect(largeBinaryData.length).to.equal(2);

    // Verify sizes
    smallBinaryData.forEach((data) => {
      expect(data.size).to.equal(1024);
    });

    mediumBinaryData.forEach((data) => {
      expect(data.size).to.equal(50 * 1024);
    });

    largeBinaryData.forEach((data) => {
      expect(data.size).to.equal(2 * 1024 * 1024);
    });

    console.log(`Binary data completed in ${endTime - startTime}ms`);

    await server.disconnect();
    await client.disconnect();
  });
});
