"""Provide utility functions for RPC."""

import ast
import asyncio
import contextlib
import copy
import inspect
import io
import re
import secrets
import string
import traceback
import collections.abc
from functools import partial
from inspect import Parameter, Signature
from types import BuiltinFunctionType, FunctionType
from typing import Any
from munch import DefaultMunch, Munch


def generate_password(length=50):
    """Generate a password."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for i in range(length))


def to_camel_case(snake_str):
    # Check if the string is already in camelCase
    if "_" not in snake_str:
        return snake_str
    # Convert from snake_case to camelCase
    components = snake_str.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


def to_snake_case(camel_str):
    # Convert from camelCase to snake_case
    snake_str = "".join(
        ["_" + i.lower() if i.isupper() else i for i in camel_str]
    ).lstrip("_")
    return snake_str


def convert_case(obj, case_type):
    """Convert the keys of a dictionary to camelCase or snake_case.
    case type can be 'camel', 'snake', 'camel+snake', 'snake+camel' or None.
    """
    if not isinstance(obj, dict) or case_type is None:
        return obj  # Return the value if obj is not a dictionary

    new_obj = {}

    for key, value in obj.items():
        camel_key = to_camel_case(key)
        snake_key = to_snake_case(key)

        if case_type == "camel":
            new_obj[camel_key] = convert_case(value, case_type)
            if callable(value):
                new_obj[camel_key].__name__ = camel_key
                if hasattr(value, "__schema__"):
                    new_obj[camel_key].__schema__["name"] = camel_key
        elif case_type == "snake":
            new_obj[snake_key] = convert_case(value, case_type)
            if callable(value):
                new_obj[snake_key].__name__ = snake_key
                if hasattr(value, "__schema__"):
                    new_obj[snake_key].__schema__ = value.__schema__.copy()
                    new_obj[snake_key].__schema__["name"] = snake_key
        else:
            # TODO: handle __schema__ for camel+snake
            if "camel" in case_type:
                new_obj[camel_key] = convert_case(value, "camel")
            if "snake" in case_type:
                new_obj[snake_key] = convert_case(value, "snake")

    if isinstance(obj, Munch):
        return DefaultMunch.fromDict(new_obj)
    return new_obj


def format_traceback(traceback_string):
    """Format traceback."""
    formatted_lines = traceback_string.splitlines()
    # remove the second and third line
    formatted_lines.pop(1)
    formatted_lines.pop(1)
    formatted_error_string = "\n".join(formatted_lines)
    formatted_error_string = formatted_error_string.replace(
        'File "<string>"', "Plugin script"
    )
    return formatted_error_string


class MessageEmitter:
    """Represent a message emitter."""

    def __init__(self, logger=None):
        """Set up instance."""
        self._event_handlers = {}
        self._logger = logger

    def on(self, event, handler):
        """Register an event handler."""
        if event not in self._event_handlers:
            self._event_handlers[event] = []
        self._event_handlers[event].append(handler)

    def once(self, event, handler):
        """Register an event handler that should only run once."""

        # wrap the handler function,
        # this is needed because setting property
        # won't work for member function of a class instance
        def wrap_func(*args, **kwargs):
            return handler(*args, **kwargs)

        wrap_func.___event_run_once = True
        self.on(event, wrap_func)

    def off(self, event=None, handler=None):
        """Reset one or all event handlers."""
        if event is None and handler is None:
            self._event_handlers = {}
        elif event is not None and handler is None:
            if event in self._event_handlers:
                self._event_handlers[event] = []
        else:
            if event in self._event_handlers:
                self._event_handlers[event].remove(handler)

    def emit(self, msg):
        """Emit a message."""
        raise NotImplementedError

    def _fire(self, event, data=None):
        """Fire an event handler."""
        if event in self._event_handlers:
            for handler in self._event_handlers[event]:
                try:
                    ret = handler(data)
                    if inspect.isawaitable(ret):
                        asyncio.ensure_future(ret)
                except Exception as err:
                    traceback_error = traceback.format_exc()
                    if self._logger:
                        self._logger.exception(err)
                    self.emit({"type": "error", "message": traceback_error})
                finally:
                    if hasattr(handler, "___event_run_once"):
                        self._event_handlers[event].remove(handler)
        else:
            if self._logger and self._logger.debug:
                self._logger.debug("Unhandled event: {}, data: {}".format(event, data))


def encode_zarr_store(zobj):
    """Encode the zarr store."""
    import zarr

    path_prefix = f"{zobj.path}/" if zobj.path else ""

    def getItem(key, options=None):
        return zobj.store[path_prefix + key]

    def setItem(key, value):
        zobj.store[path_prefix + key] = value

    def containsItem(key, options=None):
        if path_prefix + key in zobj.store:
            return True

    return {
        "_rintf": True,
        "_rtype": "zarr-array" if isinstance(zobj, zarr.Array) else "zarr-group",
        "getItem": getItem,
        "setItem": setItem,
        "containsItem": containsItem,
    }


def register_default_codecs(api, options=None):
    """Register default codecs."""

    if options is None or "zarr-array" in options:
        import zarr

        api.registerCodec(
            {"name": "zarr-array", "type": zarr.Array, "encoder": encode_zarr_store}
        )

    if options is None or "zarr-group" in options:
        import zarr

        api.registerCodec(
            {"name": "zarr-group", "type": zarr.Group, "encoder": encode_zarr_store}
        )


def extract_function_info(func):
    """Extract function info."""
    # Create an in-memory text stream
    f = io.StringIO()

    # Redirect the output of help to the text stream
    with contextlib.redirect_stdout(f):
        help(func)
    help_string = f.getvalue()
    match = re.search(r"(\w+)\((.*?)\)\n\s*(.*)", help_string, re.DOTALL)
    if match:
        func_name, func_signature, docstring = match.groups()
        # Clean up the docstring
        docstring = func.__doc__ or re.sub(r"\n\s*", " ", docstring).strip()
        return {"name": func_name, "sig": func_signature, "doc": docstring}
    else:
        return None


def callable_sig(any_callable, skip_context=False):
    """Return the signature of a callable."""
    try:
        if isinstance(any_callable, partial):
            signature = inspect.signature(any_callable.func)
            name = any_callable.func.__name__
            fixed = set(any_callable.keywords)
        elif inspect.isclass(any_callable):
            signature = inspect.signature(any_callable.__call__)
            name = any_callable.__name__
            fixed = set()
        elif hasattr(any_callable, "__call__") and not isinstance(
            any_callable, (FunctionType, BuiltinFunctionType)
        ):
            signature = inspect.signature(any_callable)
            name = type(any_callable).__name__
            fixed = set()
        else:
            signature = inspect.signature(any_callable)
            name = any_callable.__name__
            fixed = set()
    except ValueError:
        # Provide a default signature for built-in functions
        signature = Signature(
            parameters=[
                Parameter(name="args", kind=Parameter.VAR_POSITIONAL),
                Parameter(name="kwargs", kind=Parameter.VAR_KEYWORD),
            ]
        )
        name = any_callable.__name__
        fixed = set()

    if skip_context:
        fixed.add("context")

    params = [p for name, p in signature.parameters.items() if name not in fixed]
    signature = Signature(parameters=params)

    # Remove invalid characters from name
    # e.g. <lambda> -> lambda
    name = re.sub(r"\W", "", name)

    primitive = True
    for p in signature.parameters.values():
        if (
            p.default is not None
            and p.default != inspect._empty
            and not isinstance(p.default, (str, int, float, bool, list, dict, tuple))
        ):
            primitive = False
    if primitive:
        sig_str = str(signature)
    else:
        sig_str = f"({', '.join([p.name for p in signature.parameters.values()])})"
    return f"{name}{sig_str}"


def callable_doc(any_callable):
    """Return the docstring of a callable."""
    if isinstance(any_callable, partial):
        return any_callable.func.__doc__

    try:
        return any_callable.__doc__
    except AttributeError:
        return None
