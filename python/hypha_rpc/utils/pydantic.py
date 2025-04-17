from typing import Any, Optional, Union

from pydantic import BaseModel, Field, create_model

TYPE_MAPPING: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
    "null": type(None),
}

CONSTRAINT_MAPPING: dict[str, str] = {
    "minimum": "ge",
    "maximum": "le",
    "exclusiveMinimum": "gt",
    "exclusiveMaximum": "lt",
    "inclusiveMinimum": "ge",
    "inclusiveMaximum": "le",
    "minItems": "min_length",
    "maxItems": "max_length",
}


def get_field_params_from_field_schema(field_schema: dict) -> dict:
    """Gets Pydantic field parameters from a JSON schema field."""
    field_params = {}
    for constraint, constraint_value in CONSTRAINT_MAPPING.items():
        if constraint in field_schema:
            field_params[constraint_value] = field_schema[constraint]
    if "description" in field_schema:
        field_params["description"] = field_schema["description"]
    if "default" in field_schema:
        field_params["default"] = field_schema["default"]
    return field_params


def create_model_from_schema(schema: dict) -> type[BaseModel]:  # noqa: C901
    """Create Pydantic model from a JSON schema generated by `Model.model_json_schema()`."""
    models: dict[str, type[BaseModel]] = {}

    def resolve_field_type(field_schema: dict) -> type[Any]:
        """Resolves field type, including optional types and nullability."""
        if "$ref" in field_schema:
            model_reference = field_schema["$ref"].split("/")[-1]
            return models.get(model_reference, Any)  # type: ignore[arg-type]

        if "anyOf" in field_schema:
            types = [
                TYPE_MAPPING.get(t["type"], Any)
                for t in field_schema["anyOf"]
                if t.get("type")
            ]
            if type(None) in types:
                types.remove(type(None))
                if len(types) == 1:
                    return Optional[types[0]]
                return Optional[Union[tuple(types)]]  # type: ignore[return-value]
            else:
                return Union[tuple(types)]  # type: ignore[return-value]
        field_type = TYPE_MAPPING.get(field_schema.get("type"), Any)  # type: ignore[arg-type]

        # Handle arrays (lists)
        if field_schema.get("type") == "array":
            items = field_schema.get("items", {})
            item_type = resolve_field_type(items)
            return list[item_type]  # type: ignore[valid-type]

        # Handle objects (dicts with specified value types)
        if field_schema.get("type") == "object":
            additional_props = field_schema.get("additionalProperties")
            value_type = (
                resolve_field_type(additional_props) if additional_props else Any
            )
            return dict[str, value_type]  # type: ignore[valid-type]

        return field_type  # type: ignore[return-value]

    # First, create models for definitions
    definitions = schema.get("$defs", {})
    for model_name, model_schema in definitions.items():
        fields = {}
        for field_name, field_schema in model_schema.get("properties", {}).items():
            field_type = resolve_field_type(field_schema=field_schema)
            field_params = get_field_params_from_field_schema(field_schema=field_schema)
            fields[field_name] = (field_type, Field(**field_params))

        models[model_name] = create_model(model_name, **fields, __doc__=model_schema.get("description", ""))  # type: ignore[call-overload]

    # Now, create the main model, resolving references
    main_fields = {}
    for field_name, field_schema in schema.get("properties", {}).items():
        if "$ref" in field_schema:
            model_reference = field_schema["$ref"].split("/")[-1]
            field_type = models.get(model_reference, Any)  # type: ignore[arg-type]
        else:
            field_type = resolve_field_type(field_schema=field_schema)

        field_params = get_field_params_from_field_schema(field_schema=field_schema)
        main_fields[field_name] = (field_type, Field(**field_params))

    return create_model(schema.get("title", "MainModel"), **main_fields, __doc__=schema.get("description", ""))  # type: ignore[call-overload]


def pydantic_encoder(obj: BaseModel) -> dict:
    """Encode Pydantic model to a serializable dict."""
    return {
        "_rtype": "pydantic_model",
        "_rvalue": obj.model_dump(mode="json"),
        "_rschema": obj.model_json_schema(),
    }


def pydantic_decoder(encoded_obj: dict) -> BaseModel:
    """Decode dict back to a Pydantic model instance."""
    model_type = create_model_from_schema(encoded_obj["_rschema"])
    return model_type(**encoded_obj["_rvalue"])


def register_pydantic_codec(rpc):
    """Register the Pydantic codec with the given RPC instance."""
    # Ensure pydantic is available
    from pydantic import BaseModel

    rpc.register_codec(
        {
            "name": "pydantic_model",
            "type": BaseModel,
            "encoder": pydantic_encoder,
            "decoder": pydantic_decoder,
        }
    )
