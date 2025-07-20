import { assert } from "./index.js";

// Schema builder utility inspired by Zod for consistent API with Python
export const z = {
  object: (properties) => ({
    type: "object",
    properties,
    required: Object.keys(properties).filter(
      (key) => !properties[key]._optional,
    ),
  }),

  string: () => ({ type: "string", _optional: false }),
  number: () => ({ type: "number", _optional: false }),
  integer: () => ({ type: "integer", _optional: false }),
  boolean: () => ({ type: "boolean", _optional: false }),
  array: (items) => ({ type: "array", items, _optional: false }),

  // Make field optional
  optional: (schema) => ({ ...schema, _optional: true }),
};

// Add description method to schema types
["string", "number", "integer", "boolean", "array"].forEach((type) => {
  z[type] = () => {
    const schema = {
      type: type === "integer" ? "integer" : type,
      _optional: false,
    };
    schema.describe = (description) => ({ ...schema, description });
    return schema;
  };
});

export function schemaFunction(
  func,
  { schema_type = "auto", name = null, description = null, parameters = null },
) {
  if (!func || typeof func !== "function") {
    throw Error("func should be a function");
  }
  assert(schema_type === "auto", "schema_type should be auto");

  // If no name provided, try to get it from function
  const funcName = name || func.name;
  assert(funcName, "name should not be null");

  // If parameters is a z.object result, convert it properly
  let processedParameters = parameters;
  if (
    parameters &&
    typeof parameters === "object" &&
    parameters.type === "object"
  ) {
    processedParameters = {
      type: "object",
      properties: parameters.properties || {},
      required: parameters.required || [],
    };

    // Clean up internal _optional flags
    for (const [key, schema] of Object.entries(
      processedParameters.properties,
    )) {
      if (schema._optional !== undefined) {
        delete schema._optional;
      }
    }
  }

  assert(
    processedParameters && processedParameters.type === "object",
    "parameters should be an object schema",
  );

  func.__schema__ = {
    name: funcName,
    description: description || "",
    parameters: processedParameters,
  };
  return func;
}
