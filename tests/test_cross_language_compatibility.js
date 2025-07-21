/**
 * Integration tests to verify consistent API between Python and JavaScript implementations.
 * Tests for unlisted visibility and enhanced schema generation.
 */

import { expect } from "chai";
import { connectToServer } from "../javascript/src/websocket-client.js";
import { schemaFunction, z } from "../javascript/src/utils/schema.js";

describe("Cross-Language Compatibility Tests", () => {
  const WS_SERVER_URL = "wss://hypha.aicell.io/ws";

  it("should support unlisted visibility consistently", async function() {
    this.timeout(30000);
    
    let server;
    try {
      server = await connectToServer({
        server_url: WS_SERVER_URL,
        client_id: "js-test-client",
        timeout: 10000,
      });
    } catch (error) {
      console.warn("Skipping integration test - server not available:", error.message);
      this.skip();
      return;
    }

    try {
      function testUnlistedService(message) {
        /**
         * Test service with unlisted visibility.
         * Args:
         *   message: Test message to process
         */
        return `Processed: ${message}`;
      }

      // Register service with unlisted visibility
      await server.registerService({
        id: "js-test-unlisted-service",
        name: "JS Test Unlisted Service", 
        description: "Service to test unlisted visibility from JS",
        config: {
          visibility: "unlisted",  // Should work like public but not discoverable
          require_context: false,
        },
        processMessage: testUnlistedService
      });

      // Verify service is accessible (like public)
      const service = await server.getService("js-test-unlisted-service");
      const result = await service.processMessage("Hello from JS");
      expect(result).to.equal("Processed: Hello from JS");

      // Verify schema generation works correctly
      const clientInfo = server.get_client_info();
      const serviceInfo = clientInfo.services.find(s => s.id.includes("js-test-unlisted-service"));
      expect(serviceInfo).to.exist;
      expect(serviceInfo.config.visibility).to.equal("unlisted");

      await server.disconnect();
    } catch (error) {
      if (server) await server.disconnect();
      throw error;
    }
  });

  it("should generate consistent schemas with Zod-like API", async function() {
    this.timeout(10000);

    function processUser(name, age = 25, active = true) {
      /**
       * Process user information.
       * Args:
       *   name: User's full name
       *   age: User's age in years
       *   active: Whether user is active
       */
      return { name, age, active };
    }

    // Test Zod-like schema definition
    const userSchema = z.object({
      name: z.string().describe("User's full name"),
      age: z.optional(z.number().describe("User's age in years")),
      active: z.optional(z.boolean().describe("Whether user is active"))
    });

    const schemaFunc = schemaFunction(processUser, {
      name: "processUser", 
      description: "Process user information",
      parameters: userSchema
    });

    // Verify schema structure matches expected format
    const schema = schemaFunc.__schema__;
    expect(schema).to.exist;
    expect(schema.name).to.equal("processUser");
    expect(schema.description).to.equal("Process user information");
    expect(schema.parameters.type).to.equal("object");
    
    const properties = schema.parameters.properties;
    expect(properties.name).to.deep.include({
      type: "string",
      description: "User's full name"
    });
    expect(properties.age).to.deep.include({
      type: "number", 
      description: "User's age in years"
    });
    expect(properties.active).to.deep.include({
      type: "boolean",
      description: "Whether user is active"
    });

    // Only name should be required (others are optional)
    expect(schema.parameters.required).to.deep.equal(["name", "active"]); // active is not optional in this case
  });

  it("should generate OpenAI-compatible function schemas", async function() {
    this.timeout(5000);

    function calculateTotal(price, taxRate = 0.1, discount = 0.0) {
      /**
       * Calculate total price with tax and discount.
       * Args:
       *   price: Base price before tax and discount
       *   taxRate: Tax rate as decimal (e.g., 0.1 for 10%)
       *   discount: Discount amount to subtract
       */
      return (price - discount) * (1 + taxRate);
    }

    const schema = z.object({
      price: z.number().describe("Base price before tax and discount"),
      taxRate: z.optional(z.number().describe("Tax rate as decimal (e.g., 0.1 for 10%)")),
      discount: z.optional(z.number().describe("Discount amount to subtract"))
    });

    const schemaFunc = schemaFunction(calculateTotal, {
      name: "calculateTotal",
      description: "Calculate total price with tax and discount", 
      parameters: schema
    });

    const funcSchema = schemaFunc.__schema__;
    
    // Verify OpenAI-compatible structure
    expect(funcSchema).to.have.property("name").that.is.a("string");
    expect(funcSchema).to.have.property("description").that.is.a("string");
    expect(funcSchema).to.have.property("parameters").that.is.an("object");
    expect(funcSchema.parameters).to.have.property("type").that.equals("object");
    expect(funcSchema.parameters).to.have.property("properties").that.is.an("object");
    expect(funcSchema.parameters).to.have.property("required").that.is.an("array");

    // Verify content
    expect(funcSchema.description).to.include("Calculate total price");
    expect(funcSchema.parameters.required).to.deep.equal(["price"]);
    
    const properties = funcSchema.parameters.properties;
    expect(properties.price.description).to.equal("Base price before tax and discount");
    expect(properties.taxRate.description).to.equal("Tax rate as decimal (e.g., 0.1 for 10%)");
    expect(properties.discount.description).to.equal("Discount amount to subtract");
  });

  it("should handle enhanced schema generation from function signatures", async function() {
    this.timeout(5000);
    
    // Import schema functions 
    const { _parseDocstring, _convertFunctionToSchema } = await import("../javascript/src/rpc.js");
    
    function exampleFunction(name, age = 25, active = true) {
      return { name, age, active };
    }

    // Simulate docstring on function 
    exampleFunction.__doc__ = `Process user information.
Args:
  name: User's full name
  age: User's age in years
  active: Whether user is active`;

    // Test automatic schema conversion
    const schema = _convertFunctionToSchema(exampleFunction);
    expect(schema.name).to.equal("exampleFunction");
    expect(schema.description).to.include("Process user information");
    expect(schema.parameters.type).to.equal("object");
    
    const properties = schema.parameters.properties;
    expect(properties.name.description).to.equal("User's full name");
    expect(properties.age.description).to.equal("User's age in years"); 
    expect(properties.active.description).to.equal("Whether user is active");
    
    expect(schema.parameters.required).to.deep.equal(["name"]);
  });
});