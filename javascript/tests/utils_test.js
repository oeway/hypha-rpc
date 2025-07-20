import { expect } from "chai";
import {
  parseServiceUrl,
  isGenerator,
  isAsyncGenerator,
} from "../src/utils/index.js";
import { schemaFunction, z } from "../src/utils/schema.js";

describe("Test utilities", async () => {
  it("parse service url", async () => {
    // Test case 1: Basic service URL
    let result = parseServiceUrl(
      "https://hypha.aicell.io/public/services/hypha-login",
    );
    expect(result).to.deep.equal({
      serverUrl: "https://hypha.aicell.io",
      workspace: "public",
      clientId: "*",
      serviceId: "hypha-login",
      appId: "*",
    });

    // Test case 2: Service URL with client_id
    result = parseServiceUrl(
      "https://hypha.aicell.io/public/services/client:hypha-login",
    );
    expect(result).to.deep.equal({
      serverUrl: "https://hypha.aicell.io",
      workspace: "public",
      clientId: "client",
      serviceId: "hypha-login",
      appId: "*",
    });

    // Test case 3: Service URL with app_id
    result = parseServiceUrl(
      "https://hypha.aicell.io/public/services/hypha-login@app",
    );
    expect(result).to.deep.equal({
      serverUrl: "https://hypha.aicell.io",
      workspace: "public",
      clientId: "*",
      serviceId: "hypha-login",
      appId: "app",
    });

    // Test case 4: Service URL with both client_id and app_id
    result = parseServiceUrl(
      "https://hypha.aicell.io/public/services/client:hypha-login@app",
    );
    expect(result).to.deep.equal({
      serverUrl: "https://hypha.aicell.io",
      workspace: "public",
      clientId: "client",
      serviceId: "hypha-login",
      appId: "app",
    });

    // Test case 5: Service URL with trailing slash
    result = parseServiceUrl(
      "https://hypha.aicell.io/public/services/hypha-login/",
    );
    expect(result).to.deep.equal({
      serverUrl: "https://hypha.aicell.io",
      workspace: "public",
      clientId: "*",
      serviceId: "hypha-login",
      appId: "*",
    });

    // Test case 6: Invalid service URL (should throw an error)
    expect(() =>
      parseServiceUrl("https://hypha.aicell.io/public/hypha-login"),
    ).to.throw(Error, "URL does not match the expected pattern");
  }).timeout(20000);

  it("test isGenerator and isAsyncGenerator functions", async () => {
    // Test isGenerator
    // Test case 1: Regular generator function
    function* regularGenerator() {
      yield 1;
      yield 2;
    }
    const genObj = regularGenerator();
    expect(isGenerator(genObj)).to.be.true;

    // Test case 2: Regular function (not a generator)
    function regularFunction() {
      return 1;
    }
    expect(isGenerator(regularFunction())).to.be.false;

    // Test case 3: Null/undefined values
    expect(isGenerator(null)).to.be.false;
    expect(isGenerator(undefined)).to.be.false;

    // Test case 4: Plain object with similar interface but not a generator
    const fakeGenerator = {
      next: () => {},
      throw: () => {},
      return: () => {},
    };
    expect(isGenerator(fakeGenerator)).to.be.true; // Note: This is true because it matches the interface

    // Test isAsyncGenerator
    // Test case 1: Async generator function
    async function* asyncGenerator() {
      yield await Promise.resolve(1);
      yield await Promise.resolve(2);
    }
    const asyncGenObj = asyncGenerator();
    expect(isAsyncGenerator(asyncGenObj)).to.be.true;

    // Test case 2: Regular generator (not async)
    expect(isAsyncGenerator(genObj)).to.be.false;

    // Test case 3: Regular async function (not a generator)
    async function asyncFunction() {
      return await Promise.resolve(1);
    }
    expect(isAsyncGenerator(asyncFunction())).to.be.false;

    // Test case 4: Null/undefined values
    expect(isAsyncGenerator(null)).to.be.false;
    expect(isAsyncGenerator(undefined)).to.be.false;

    // Test case 5: Plain object with similar interface but not an async generator
    const fakeAsyncGenerator = {
      next: () => {},
      throw: () => {},
      return: () => {},
      [Symbol.asyncIterator]: () => {},
      [Symbol.toStringTag]: "AsyncGenerator",
    };
    expect(isAsyncGenerator(fakeAsyncGenerator)).to.be.true; // Note: This is true because it matches the interface
  }).timeout(20000);

  it("test Zod-like schema builder", async () => {
    // Test basic types
    expect(z.string()).to.deep.include({ type: "string", _optional: false });
    expect(z.number()).to.deep.include({ type: "number", _optional: false });
    expect(z.integer()).to.deep.include({ type: "integer", _optional: false });
    expect(z.boolean()).to.deep.include({ type: "boolean", _optional: false });

    // Test optional
    const optionalString = z.optional(z.string());
    expect(optionalString._optional).to.be.true;

    // Test object schema
    const userSchema = z.object({
      name: z.string(),
      age: z.optional(z.number()),
      active: z.boolean(),
    });

    expect(userSchema.type).to.equal("object");
    expect(userSchema.properties.name).to.deep.include({
      type: "string",
      _optional: false,
    });
    expect(userSchema.properties.age).to.deep.include({
      type: "number",
      _optional: true,
    });
    expect(userSchema.properties.active).to.deep.include({
      type: "boolean",
      _optional: false,
    });
    expect(userSchema.required).to.deep.equal(["name", "active"]);
  }).timeout(5000);

  it("test schemaFunction with Zod-like API", async () => {
    function processUser(name, age = 25, active = true) {
      /* Process user data
       * Args:
       *   name: User's full name
       *   age: User's age in years
       *   active: Whether user is active
       */
      return { name, age, active };
    }

    const schema = z.object({
      name: z.string().describe("User's full name"),
      age: z.optional(z.number().describe("User's age in years")),
      active: z.optional(z.boolean().describe("Whether user is active")),
    });

    const schemaFunc = schemaFunction(processUser, {
      name: "processUser",
      description: "Process user data",
      parameters: schema,
    });

    expect(schemaFunc.__schema__).to.exist;
    expect(schemaFunc.__schema__.name).to.equal("processUser");
    expect(schemaFunc.__schema__.description).to.equal("Process user data");
    expect(schemaFunc.__schema__.parameters.type).to.equal("object");
    expect(schemaFunc.__schema__.parameters.properties.name).to.deep.include({
      type: "string",
      description: "User's full name",
    });
    expect(schemaFunc.__schema__.parameters.required).to.deep.equal(["name"]);
  }).timeout(5000);
});
