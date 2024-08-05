import { assert } from ".";

export function schemaFunction(
  func,
  { schema_type = "auto", name = null, description = null, parameters = null },
) {
  if (!func || typeof func !== "function") {
    throw Error("func should be a function");
  }
  assert(schema_type === "auto", "schema_type should be auto");
  assert(name, "name should not be null");
  assert(
    parameters && parameters.type === "object",
    "parameters should be an object",
  );
  func.__schema__ = {
    name: name,
    description: description,
    parameters: parameters || [],
  };
  return func;
}
