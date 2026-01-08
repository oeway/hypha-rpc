"""Provide utility functions for RPC."""

import asyncio
import contextlib
import inspect
import io
import re
import secrets
import string
import traceback
from functools import partial
import html
import uuid
from inspect import Parameter, Signature
from collections.abc import Mapping
from types import BuiltinFunctionType, FunctionType
from munch import Munch, munchify


def ensure_event_loop():
    """
    Ensure there's an event loop available for the current thread.
    
    This function checks if there's a running event loop or an event loop
    set for the current thread. If neither exists, it creates a new one.
    
    This is useful for preventing RuntimeError when calling asyncio.Future()
    or other asyncio operations from threads without event loops.
    """
    try:
        # First check if there's a running loop
        asyncio.get_running_loop()
        return  # Running loop exists, nothing to do
    except RuntimeError:
        pass
    
    try:
        # Check if there's a loop set for this thread
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            # Loop exists but is closed, create a new one
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        # No event loop exists, create one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)


def safe_create_future():
    """
    Safely create an asyncio.Future() that works from any thread context.
    
    This is a more targeted approach than ensure_event_loop() - it only
    creates an event loop if absolutely necessary for Future creation.
    """
    try:
        # First check if there's a running loop
        asyncio.get_running_loop()
        return asyncio.Future()
    except RuntimeError:
        # No running loop, check if there's a loop set for this thread
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                # Loop exists but is closed, create a new one
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            return asyncio.Future()
        except RuntimeError:
            # No event loop exists at all, create one
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return asyncio.Future()


async def run_in_executor(func, *args, executor=None, **kwargs):
    """
    Run a synchronous CPU-bound function in an executor to avoid blocking the event loop.

    This is critical for maintaining RPC stability when services perform CPU-intensive
    operations. Blocking the event loop can prevent:
    - Heartbeat messages from being sent/received
    - Reconnection logic from executing
    - Other concurrent RPC calls from progressing

    Example usage:
        # Instead of:
        result = cpu_intensive_function(data)

        # Use:
        from hypha_rpc.utils import run_in_executor
        result = await run_in_executor(cpu_intensive_function, data)

    Args:
        func: The synchronous function to run
        *args: Positional arguments to pass to the function
        executor: Optional concurrent.futures.Executor. If None, uses the default
                  ThreadPoolExecutor. For truly CPU-bound tasks, consider using
                  a ProcessPoolExecutor.
        **kwargs: Keyword arguments to pass to the function

    Returns:
        The result of calling func(*args, **kwargs)
    """
    loop = asyncio.get_running_loop()
    if kwargs:
        func = partial(func, **kwargs)
    return await loop.run_in_executor(executor, func, *args)


# The following code adopted from the munch library,
# By Copyright (c) 2010 David Schoonover
# We changed the way the keys being generated to cope with
# keys such as `pop`, `get`, `update`, etc.
def unmunchify(x):
    """Recursively converts a Munch into a dictionary."""
    # Munchify x, using `seen` to track object cycles
    seen = dict()

    def unmunchify_cycles(obj):
        # If we've already begun unmunchifying obj, just return the already-created unmunchified obj
        try:
            return seen[id(obj)]
        except KeyError:
            pass

        # Otherwise, first partly unmunchify obj (but without descending into any lists or dicts) and save that
        seen[id(obj)] = partial = pre_unmunchify(obj)
        # Then finish unmunchifying lists and dicts inside obj (reusing unmunchified obj if cycles are encountered)
        return post_unmunchify(partial, obj)

    def pre_unmunchify(obj):
        # Here we return a skeleton of unmunchified obj, which is enough to save for later (in case
        # we need to break cycles) but it needs to filled out in post_unmunchify
        if isinstance(obj, Mapping):
            return dict()
        elif isinstance(obj, list):
            return type(obj)()
        elif isinstance(obj, tuple):
            type_factory = getattr(obj, "_make", type(obj))
            return type_factory(unmunchify_cycles(item) for item in obj)
        else:
            return obj

    def post_unmunchify(partial, obj):
        # Here we finish unmunchifying the parts of obj that were deferred by pre_unmunchify because they
        # might be involved in a cycle
        if isinstance(obj, Mapping):
            # We need to use dict.keys(obj) instead of obj.keys()
            partial.update((k, unmunchify_cycles(obj[k])) for k in dict.keys(obj))
        elif isinstance(obj, list):
            partial.extend(unmunchify_cycles(v) for v in obj)
        elif isinstance(obj, tuple):
            for value_partial, value in zip(partial, obj):
                post_unmunchify(value_partial, value)

        return partial

    return unmunchify_cycles(x)


class ObjectProxy(Munch):
    """Object proxy with dot attribute access."""

    def __getattribute__(self, k):
        # Check if the attribute is in the dictionary
        if not k.startswith("_") and k in self:
            return self[k]
        # If not, proceed with the usual attribute access
        return super().__getattribute__(k)

    def __repr__(self):
        return str(self.toDict())

    def __str__(self):
        return repr(self.toDict())

    @classmethod
    def fromDict(cls, d):
        if isinstance(d, cls):
            return munchify(unmunchify(d), cls)
        return munchify(d, cls)

    def toDict(self):
        return unmunchify(self)

    def _repr_html_(self):
        obj_id = f"object-proxy-{uuid.uuid4().hex}"
        html_content = self._render_html(self.toDict(), level=0, label=f"{type(self).__name__} at {hex(id(self))}")
        style = f"""
    <style>
    #{obj_id} ul {{
        list-style-type: none;
        padding-left: 1em;
        margin: 0;
        font-family: monospace;
    }}
    #{obj_id} details {{
        padding-left: 1em;
        margin: 0.2em 0;
    }}
    #{obj_id} summary {{
        font-weight: bold;
        cursor: pointer;
        user-select: none;
    }}
    #{obj_id} li {{
        margin: 2px 0;
    }}
    </style>
    """
        return f'{style}<div id="{obj_id}" class="object-proxy">{html_content}</div>'

    def _render_html(self, data, level=0, label="dict"):
        parts = [f'<details><summary>{html.escape(label)}</summary><ul>']

        # Handle lists
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    item_label = f"{type(item).__name__} at {hex(id(item))}"
                    parts.append(f"<li>{self._render_html(item, level + 1, label=item_label)}</li>")
                elif isinstance(item, list):
                    item_label = f"list at {hex(id(item))}"
                    parts.append(f"<li>{self._render_html(item, level + 1, label=item_label)}</li>")
                else:
                    parts.append(f"<li>{html.escape(str(item))}</li>")
        # Handle dicts
        elif isinstance(data, dict):
            for key, value in data.items():
                key_str = html.escape(str(key))
                if isinstance(value, dict):
                    nested_label = f"{type(value).__name__} at {hex(id(value))}"
                    nested = self._render_html(value, level + 1, label=nested_label)
                    parts.append(f"<li><strong>{key_str}</strong>: {nested}</li>")
                elif isinstance(value, list):
                    nested_label = f"list at {hex(id(value))}"
                    nested = self._render_html(value, level + 1, label=nested_label)
                    parts.append(f"<li><strong>{key_str}</strong>: {nested}</li>")
                else:
                    val_str = html.escape(str(value))
                    parts.append(f"<li><strong>{key_str}</strong>: {val_str}</li>")
        else:
            # Handle primitive values
            parts.append(f"<li>{html.escape(str(data))}</li>")

        parts.append("</ul></details>")
        return "\n".join(parts)


class DefaultObjectProxy(ObjectProxy):
    """Object proxy with default None value."""

    def __getattr__(self, k):
        """Gets key if it exists, otherwise returns the default value."""
        try:
            return super().__getattr__(k)
        except AttributeError:
            return None

    def __getitem__(self, k):
        """Gets key if it exists, otherwise returns the default value."""
        try:
            return super().__getitem__(k)
        except KeyError:
            return None


def generate_password(length=50):
    """Generate a password."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for i in range(length))


def to_camel_case(snake_str):
    # Check if the string is already in camelCase
    if "_" not in snake_str:
        return snake_str[0].lower() + snake_str[1:]
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
                if hasattr(value, "__schema__") and isinstance(value.__schema__, dict):
                    new_obj[camel_key].__schema__ = value.__schema__.copy()
                    new_obj[camel_key].__schema__["name"] = camel_key
        elif case_type == "snake":
            new_obj[snake_key] = convert_case(value, case_type)
            if callable(value):
                new_obj[snake_key].__name__ = snake_key
                if hasattr(value, "__schema__") and isinstance(value.__schema__, dict):
                    new_obj[snake_key].__schema__ = value.__schema__.copy()
                    new_obj[snake_key].__schema__["name"] = snake_key
        else:
            # TODO: handle __schema__ for camel+snake
            if "camel" in case_type:
                new_obj[camel_key] = convert_case(value, "camel")
            if "snake" in case_type:
                new_obj[snake_key] = convert_case(value, "snake")

    if isinstance(obj, ObjectProxy):
        return ObjectProxy.fromDict(new_obj)
    return new_obj


def parse_service_url(url):
    """Parse the service URL and return server_url, workspace, client_id, service_id, app_id."""
    # Ensure no trailing slash
    url = url.rstrip("/")

    # Regex pattern to match the URL structure
    pattern = re.compile(
        r"^(https?://[^/]+)"  # server_url (http or https followed by domain)
        r"/([a-z0-9_-]+)"  # workspace (lowercase letters, numbers, - or _)
        r"/services/"  # static part of the URL
        r"(?:(?P<client_id>[a-zA-Z0-9_-]+):)?"  # optional client_id
        r"(?P<service_id>[a-zA-Z0-9_-]+)"  # service_id
        r"(?:@(?P<app_id>[a-zA-Z0-9_-]+))?"  # optional app_id
    )

    match = pattern.match(url)
    if not match:
        raise ValueError("URL does not match the expected pattern")

    server_url = match.group(1)
    workspace = match.group(2)
    client_id = match.group("client_id") or "*"
    service_id = match.group("service_id")
    app_id = match.group("app_id") or "*"

    return server_url, workspace, client_id, service_id, app_id


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

    async def wait_for(self, event, timeout):
        """Wait for an event to be emitted, or timeout."""
        future = asyncio.get_event_loop().create_future()

        def handler(data):
            if not future.done():
                future.set_result(data)

        self.once(event, handler)

        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as err:
            self.off(event, handler)
            raise err


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
