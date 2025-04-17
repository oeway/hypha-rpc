import { expect } from "chai";
import {
  login,
  connectToServer,
  schemaFunction,
} from "../src/websocket-client.js";
import { registerRTCService, getRTCService } from "../src/webrtc-client.js";
import { assert } from "../src/utils";
import {
  isAsyncGeneratorFunction,
  isGeneratorFunction,
  isAsyncGenerator,
  isGenerator,
} from "../src/utils";
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
    function multiply_context(a, b, context) {
      assert(context.user, "context should not be null");
      /* multiply two numbers */
      if (b === undefined) {
        b = a;
      }
      return a * b;
    }
    await api.registerService({
      name: "my service",
      id: "test-service",
      description: "test service",
      config: { visibility: "public", require_context: true },
      multiply_context,
    });
    const svc = await api.rpc.get_remote_service("test-service");
    expect(await svc.multiply_context(2, 3)).to.equal(6);
    expect(await svc.multiply_context(2)).to.equal(4);
    await api.disconnect();
  }).timeout(20000);

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
});
