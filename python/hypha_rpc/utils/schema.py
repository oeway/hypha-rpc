import inspect
from functools import wraps, partial
from inspect import Signature, Parameter, signature
from typing import Any, Dict


try:
    from pydantic import create_model, BaseModel
    from pydantic import Field as PydanticField
    from pydantic.fields import FieldInfo
    from pydantic_core import PydanticUndefined

    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False


class Field:
    def __init__(self, default=inspect._empty, description=None):
        self.default = default
        self.description = description


# https://stackoverflow.com/a/58938747
def remove_a_key(d, remove_key):
    if isinstance(d, dict):
        for key in list(d.keys()):
            if key == remove_key:
                del d[key]
            else:
                remove_a_key(d[key], remove_key)


def get_type_name(tp):
    """Get the JSON schema type name for a given type."""
    if tp in [int, float, str, bool, list, dict]:
        return tp.__name__
    return {}  # Return an empty schema for non-primitive types


def extract_parameter_schema(param):
    """Extract the schema for a given parameter."""
    if param.annotation != inspect._empty:
        param_type = get_type_name(param.annotation)
    else:
        param_type = {}

    param_schema = {"type": param_type} if param_type else {}

    if param.default != inspect._empty:
        if isinstance(param.default, (int, float, str, bool, list, dict, type(None))):
            param_schema["default"] = param.default
        if isinstance(param.default, Field):
            param_schema["description"] = param.default.description

    return param_schema


def fill_missing_args_and_kwargs(original_func_sig, args, kwargs):
    bound_args = original_func_sig.bind_partial(*args, **kwargs)
    for name, param in original_func_sig.parameters.items():
        if name not in kwargs and name not in bound_args.arguments:
            if isinstance(param.default, Field) or (
                PYDANTIC_AVAILABLE and isinstance(param.default, FieldInfo)
            ):
                bound_args.arguments[name] = param.default.default
            else:
                bound_args.arguments[name] = param.default
    bound_args.apply_defaults()
    return bound_args.args, bound_args.kwargs


def schema_function_native(
    original_func, name=None, mode="strict", skip_context=False, skip_self=False
):
    """Decorator to add input/output schema to a function."""
    assert callable(original_func)
    if hasattr(original_func, "__schema__"):
        return original_func

    if isinstance(original_func, partial):
        func_name = name or (original_func.func.__name__)
        func_doc = original_func.func.__doc__
        original_func_sig = signature(original_func.func)
        skip_args = set(original_func.keywords.keys())
    else:
        func_name = name or original_func.__name__
        func_doc = original_func.__doc__
        original_func_sig = signature(original_func)
        skip_args = set()

    if mode == "strict":
        assert (
            func_name != "<lambda>"
        ), f"Lambda functions are not supported (name: {name})"

    parameters = [
        (name, param)
        for name, param in original_func_sig.parameters.items()
        if name not in skip_args
    ]

    if skip_context and parameters:
        parameters = parameters[:-1]  # Skip the last parameter if skip_context is True
    if skip_self and parameters:
        parameters = parameters[1:]

    func_schema = {
        "type": "object",
        "properties": {
            name: extract_parameter_schema(param) for name, param in parameters
        },
        "required": [
            name for name, param in parameters if param.default == inspect._empty
        ],
    }

    if inspect.iscoroutinefunction(original_func):

        @wraps(original_func)
        async def wrapper(*args, **kwargs):
            new_args, new_kwargs = fill_missing_args_and_kwargs(
                original_func_sig, args, kwargs
            )
            return await original_func(*new_args, **new_kwargs)

    else:

        @wraps(original_func)
        def wrapper(*args, **kwargs):
            new_args, new_kwargs = fill_missing_args_and_kwargs(
                original_func_sig, args, kwargs
            )
            return original_func(*new_args, **new_kwargs)

    wrapper.__schema__ = {
        "name": func_name,
        "description": func_doc,
        "parameters": func_schema,
    }
    return wrapper


def dict_to_pydantic_model(name: str, dict_def: Dict[str, Any], doc: str = None):
    fields = {}

    for field_name, value in dict_def.items():
        if isinstance(value, tuple):
            if len(value) == 2 and isinstance(value[1], FieldInfo):
                field_type, field_info = value
                fields[field_name] = (
                    field_type,
                    field_info.default if field_info.default is not Ellipsis else ...,
                )
            else:
                fields[field_name] = value
        elif isinstance(value, dict):
            fields[field_name] = (
                dict_to_pydantic_model(f"{name}_{field_name}", value),
                ...,
            )
        else:
            raise ValueError(f"Field {field_name}:{value} has invalid syntax")

    model = create_model(name, **fields)
    model.__doc__ = doc
    return model


def extract_tool_schemas(
    func,
    func_name=None,
    mode="strict",
    skip_context=False,
    skip_self=False,
    skip_args=set(),
):
    assert PYDANTIC_AVAILABLE, "Pydantic is not available"
    assert callable(func), "Tool function must be callable functions"
    sig = signature(func)
    func_name = func.__name__ if not isinstance(func, partial) else func.func.__name__

    names = [p.name for p in sig.parameters.values() if p.name not in skip_args]

    if skip_context and names:
        names = names[:-1]
    if skip_self and names:
        names = names[1:]

    for name in names:
        assert (
            sig.parameters[name].annotation != inspect._empty or mode == "auto"
        ), f"Argument `{name}` for `{func_name}` must have type annotation"

    types = [
        (
            sig.parameters[name].annotation
            if sig.parameters[name].annotation != inspect._empty
            else Any
        )
        for name in names
    ]
    defaults = []

    for name in names:
        if sig.parameters[name].default == inspect._empty:
            defaults.append(PydanticField(..., description=name))
        else:
            if mode == "strict":
                assert isinstance(
                    sig.parameters[name].default, FieldInfo
                ), "Argument default must be a FieldInfo object with description"
                assert (
                    sig.parameters[name].default.description is not None
                ), f"Argument `{name}` for `{func_name}` must have a description"
            default_value = (
                sig.parameters[name].default
                if isinstance(sig.parameters[name].default, FieldInfo)
                else PydanticField(default=sig.parameters[name].default)
            )
            # check if the actual value is json serializable
            if default_value.default != PydanticUndefined and not isinstance(
                default_value.default, (int, float, str, bool, type(None))
            ):
                raise ValueError(
                    f"Argument `{name}` for `{func_name}` must have a default value that is json serializable"
                )
            defaults.append(default_value)

    func_name = func_name or func.__name__
    return (
        dict_to_pydantic_model(
            func_name,
            {names[i]: (types[i], defaults[i]) for i in range(len(names))},
            func.__doc__,
        ),
        sig.return_annotation,
    )


def schema_function_pydantic(
    original_func,
    input_model=None,
    name=None,
    mode="strict",
    skip_context=False,
    skip_self=False,
):
    """Decorator to add input/output schema to a function."""
    assert PYDANTIC_AVAILABLE, "Pydantic is not available"
    assert callable(original_func)
    if hasattr(original_func, "__schema__"):
        return original_func

    if isinstance(original_func, partial):
        func_name = name or (original_func.func.__name__ + "(partial)")
        original_func_sig = signature(original_func.func)
        skip_args = set(original_func.keywords.keys())
    else:
        func_name = name or original_func.__name__
        original_func_sig = signature(original_func)
        skip_args = set()

    if mode == "strict":
        assert func_name != "<lambda>", "Lambda functions are not supported"
        assert (
            original_func.__doc__ is not None
        ), f"Function `{func_name}` must have a docstring"

    if input_model:
        parameters = []
        for name, field in input_model.model_fields.items():
            if name not in skip_args:
                parameters.append(
                    Parameter(
                        name,
                        kind=Parameter.POSITIONAL_OR_KEYWORD,
                        annotation=field.annotation,
                        default=(
                            field.default
                            if field.default is not PydanticUndefined
                            else ...
                        ),
                    )
                )
        func_sig = Signature(parameters)
    else:
        input_model, _ = extract_tool_schemas(
            original_func.func if isinstance(original_func, partial) else original_func,
            mode=mode,
            skip_context=skip_context,
            skip_self=skip_self,
            skip_args=skip_args,
        )
        func_sig = original_func_sig

    assert input_model is not None

    def process_arguments(args, kwargs):
        new_args, new_kwargs = fill_missing_args_and_kwargs(
            original_func_sig, args, kwargs
        )

        # Convert dictionary inputs to Pydantic model if needed
        final_args = []
        for arg, param in zip(new_args, func_sig.parameters.values()):
            if (
                isinstance(param.default, FieldInfo)
                and isinstance(param.annotation, type)
                and issubclass(param.annotation, BaseModel)
            ):
                if isinstance(arg, dict):
                    arg = param.annotation(**arg)
            final_args.append(arg)

        final_kwargs = {}
        for k, v in new_kwargs.items():
            param = func_sig.parameters[k]
            if (
                isinstance(param.default, FieldInfo)
                and isinstance(param.annotation, type)
                and issubclass(param.annotation, BaseModel)
            ):
                if isinstance(v, dict):
                    v = param.annotation(**v)
            final_kwargs[k] = v
        return final_args, final_kwargs

    if inspect.iscoroutinefunction(original_func):

        @wraps(original_func)
        async def wrapper(*args, **kwargs):
            new_args, new_kwargs = process_arguments(args, kwargs)
            ret = await original_func(*new_args, **new_kwargs)
            return ret

    else:

        @wraps(original_func)
        def wrapper(*args, **kwargs):
            new_args, new_kwargs = process_arguments(args, kwargs)
            ret = original_func(*new_args, **new_kwargs)
            return ret

    spec = input_model.model_json_schema()
    remove_a_key(spec, "title")
    description = spec.get("description", original_func.__doc__)
    if "description" in spec:
        del spec["description"]
    wrapper.__schema__ = {
        "name": func_name,
        "description": description,
        "parameters": spec,
    }
    wrapper.__original__ = original_func
    return wrapper


def schema_function(
    func=None, name=None, schema_type="auto", skip_context=False, skip_self=False
):
    """Decorator to add input/output schema to a function."""
    if ":" in schema_type:
        schema_type, mode = schema_type.split(":")
    else:
        mode = "strict"

    assert schema_type in [
        "pydantic",
        "native",
        "auto",
    ], "Schema type must be 'pydantic' or 'native'"

    if schema_type == "auto":
        if PYDANTIC_AVAILABLE:
            schema_type = "pydantic"
            mode = mode or "auto"
        else:
            schema_type = "native"
    if func is None:
        return partial(
            schema_function,
            name=name,
            schema_type=schema_type + ":" + mode,
            skip_context=skip_context,
            skip_self=skip_self,
        )

    if schema_type == "pydantic":
        return schema_function_pydantic(
            func, name=name, mode=mode, skip_context=skip_context, skip_self=skip_self
        )
    elif schema_type == "native":
        return schema_function_native(
            func, name=name, mode=mode, skip_context=skip_context, skip_self=skip_self
        )
    else:
        raise ValueError(f"Invalid schema type: {schema_type}")


def parse_schema_function(obj, name=None, schema_type="native", skip_context=False):
    """Recursively convert callable with schema_function based on schema type."""
    if ":" in schema_type:
        schema_type, mode = schema_type.split(":")
    else:
        mode = "strict"

    assert schema_type in [
        "pydantic",
        "native",
        "auto",
    ], "Schema type must be 'pydantic' or 'native'"

    if schema_type == "auto":
        if PYDANTIC_AVAILABLE:
            schema_type = "pydantic"
            mode = "auto"
        else:
            schema_type = "native"
    if callable(obj):
        try:
            if schema_type == "pydantic":
                return schema_function_pydantic(
                    obj, name=name, mode=mode, skip_context=skip_context
                )
            elif schema_type == "native":
                return schema_function_native(
                    obj, name=name, mode=mode, skip_context=skip_context
                )
            else:
                raise Exception(f"Invalid schema type: {schema_type}")
        except ValueError as e:
            print(f"Error parsing schema for {name}: {e}")
            return obj

    elif isinstance(obj, dict):
        return {
            k: parse_schema_function(
                v,
                name=k,
                schema_type=schema_type + ":" + mode,
                skip_context=skip_context,
            )
            for k, v in obj.items()
        }
    elif isinstance(obj, list):
        return [
            parse_schema_function(
                x,
                schema_type=schema_type + ":" + mode,
                skip_context=skip_context,
            )
            for x in obj
        ]
    else:
        return obj
