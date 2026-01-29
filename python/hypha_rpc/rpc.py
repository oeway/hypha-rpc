"""Provide the RPC."""

import asyncio
import inspect
import json
import io
import os
import logging
import math
import time
import sys
import traceback
import weakref
from collections import OrderedDict
from functools import partial, reduce
from typing import Union
from .utils import ObjectProxy, DefaultObjectProxy

import msgpack
import shortuuid

from .utils import (
    MessageEmitter,
    format_traceback,
    callable_doc,
    convert_case,
)
from .utils.schema import schema_function
from .utils import ensure_event_loop, safe_create_future

try:
    from pydantic import BaseModel
    from .utils.pydantic import pydantic_encoder, pydantic_decoder

    HAS_PYDANTIC = True
except ImportError:
    HAS_PYDANTIC = False

CHUNK_SIZE = 1024 * 256
API_VERSION = 3
ALLOWED_MAGIC_METHODS = ["__enter__", "__exit__"]
IO_PROPS = [
    "name",  # file name
    "size",  # size in bytes
    "path",  # file path
    "type",  # type type
    "fileno",
    "seek",
    "truncate",
    "detach",
    "write",
    "read",
    "read1",
    "readall",
    "close",
    "closed",
    "__enter__",
    "__exit__",
    "flush",
    "isatty",
    "__iter__",
    "__next__",
    "readable",
    "readline",
    "readlines",
    "seekable",
    "tell",
    "writable",
    "writelines",
]

LOGLEVEL = os.environ.get("HYPHA_LOGLEVEL", "WARNING").upper()
logging.basicConfig(stream=sys.stdout)
logger = logging.getLogger("RPC")
logger.setLevel(LOGLEVEL)

CONCURRENCY_LIMIT = int(os.environ.get("HYPHA_CONCURRENCY_LIMIT", "30"))


def index_object(obj, ids):
    """Index an object."""
    if isinstance(ids, str):
        return index_object(obj, ids.split("."))
    elif len(ids) == 0:
        return obj
    else:
        if isinstance(obj, dict):
            _obj = obj[ids[0]]
        elif isinstance(obj, (list, tuple)):
            _obj = obj[int(ids[0])]
        else:
            _obj = getattr(obj, ids[0])
        return index_object(_obj, ids[1:])


def _parse_docstring(doc_string):
    """
    Parse docstring into main description and parameter descriptions.
    Adapted from Ollama implementation (https://github.com/ollama/ollama-python)
    Copyright (c) Ollama
    Licensed under MIT License
    """
    from collections import defaultdict
    import re

    parsed_docstring = defaultdict(str)
    if not doc_string:
        return parsed_docstring

    key = str(hash(doc_string))
    for line in doc_string.splitlines():
        lowered_line = line.lower().strip()
        if (
            lowered_line.startswith("args:")
            or lowered_line.startswith("arguments:")
            or lowered_line.startswith("parameters:")
        ):
            key = "args"
        elif lowered_line.startswith(("returns:", "yields:", "raises:")):
            key = "_"
        else:
            parsed_docstring[key] += f"{line.strip()}\n"

    last_key = None
    for line in parsed_docstring["args"].splitlines():
        line = line.strip()
        if ":" in line:
            parts = re.split(r"(?:\(([^)]*)\)|:)\s*", line, maxsplit=1)
            arg_name = parts[0].strip()
            last_key = arg_name
            arg_description = parts[-1].strip()
            if len(parts) > 2 and parts[1]:
                arg_description = parts[-1].split(":", 1)[-1].strip()
            parsed_docstring[last_key] = arg_description
        elif last_key and line:
            parsed_docstring[last_key] += " " + line

    return parsed_docstring


def _convert_function_to_schema(func, name=None):
    """
    Convert a function to function schema using type annotations and docstring.
    Adapted from Ollama implementation (https://github.com/ollama/ollama-python)
    Copyright (c) Ollama
    Licensed under MIT License
    """
    import inspect

    func_name = name or func.__name__
    doc_string = inspect.getdoc(func)
    doc_string_hash = str(hash(doc_string))
    parsed_docstring = _parse_docstring(doc_string)

    sig = inspect.signature(func)
    properties = {}
    required = []

    for param_name, param in sig.parameters.items():
        # Skip context parameter if it exists
        if param_name == "context":
            continue

        param_type = "string"  # default type
        if param.annotation != inspect._empty:
            if param.annotation == int:
                param_type = "integer"
            elif param.annotation == float:
                param_type = "number"
            elif param.annotation == bool:
                param_type = "boolean"
            elif param.annotation == str:
                param_type = "string"
            elif param.annotation == list:
                param_type = "array"
            elif param.annotation == dict:
                param_type = "object"

        properties[param_name] = {
            "type": param_type,
            "description": parsed_docstring.get(param_name, ""),
        }

        # If no default value, it's required
        if param.default == inspect._empty:
            required.append(param_name)

    return {
        "name": func_name,
        "description": parsed_docstring.get(doc_string_hash, "").strip(),
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


def _get_schema(obj, name=None, skip_context=False):
    """Get schema."""
    if isinstance(obj, dict):
        schema = {}
        for k, v in obj.items():
            schema[k] = _get_schema(v, k, skip_context=skip_context)
        return schema
    elif isinstance(obj, (list, tuple)):
        return [
            _get_schema(
                v,
                skip_context=skip_context,
            )
            for v in obj
        ]
    elif callable(obj):
        if hasattr(obj, "__schema__"):
            schema = obj.__schema__.copy()
            if name:
                schema["name"] = name
                obj.__schema__["name"] = name
            if skip_context:
                if "parameters" in schema:
                    if "properties" in schema["parameters"]:
                        schema["parameters"]["properties"].pop("context", None)
            return {"type": "function", "function": schema}
        else:
            # Generate schema from function signature and docstring
            try:
                schema = _convert_function_to_schema(obj, name)
                if skip_context:
                    if "parameters" in schema:
                        if "properties" in schema["parameters"]:
                            schema["parameters"]["properties"].pop("context", None)
                        if "context" in schema["parameters"].get("required", []):
                            schema["parameters"]["required"].remove("context")
                return {"type": "function", "function": schema}
            except Exception:
                # Fallback to simple schema if conversion fails
                return {"type": "function"}
    elif isinstance(obj, (int, float)):
        return {"type": "number"}
    elif isinstance(obj, str):
        return {"type": "string"}
    elif isinstance(obj, bool):
        return {"type": "boolean"}
    elif obj is None:
        return {"type": "null"}
    else:
        return {}


def _annotate_service(service, service_type_info):
    service_type_info = ObjectProxy.toDict(service_type_info)

    def validate_keys(service_dict, schema_dict, path="root"):
        # Validate that all keys in schema_dict exist in service_dict
        for key in schema_dict:
            if key not in service_dict:
                raise KeyError(f"Missing key '{key}' in service at path '{path}'")

        # Check for any unexpected keys in service_dict
        for key in service_dict:
            if key != "type" and key not in schema_dict:
                raise KeyError(f"Unexpected key '{key}' in service at path '{path}'")

    def annotate_recursive(new_service, schema_info, path="root"):
        if isinstance(new_service, dict):
            validate_keys(new_service, schema_info, path)
            for k, v in new_service.items():
                new_path = f"{path}.{k}"
                if isinstance(v, dict):
                    annotate_recursive(v, schema_info[k], new_path)
                elif callable(v):
                    if k in schema_info:
                        assert schema_info[k]["type"] == "function"
                        if schema_info[k].get("function"):
                            annotation = schema_info[k]["function"]
                        else:
                            annotation = {"name": k, "parameters": {}}

                        new_service[k] = schema_function(
                            v,
                            name=annotation["name"],
                            description=annotation.get("description", ""),
                            parameters=annotation["parameters"],
                        )
                    else:
                        raise KeyError(
                            f"Missing schema for function '{k}' at path '{new_path}'"
                        )
        elif isinstance(new_service, (list, tuple)):
            if len(new_service) != len(schema_info):
                raise ValueError(f"Length mismatch at path '{path}'")
            for k, v in enumerate(new_service):
                new_path = f"{path}[{k}]"
                if isinstance(v, (dict, list, tuple)):
                    annotate_recursive(v, schema_info[k], new_path)
                elif callable(v):
                    if k in schema_info:
                        assert schema_info[k]["type"] == "function"
                        if schema_info[k].get("function"):
                            annotation = schema_info[k]["function"]
                        else:
                            annotation = {"name": k, "parameters": {}}

                        new_service[k] = schema_function(
                            v,
                            name=annotation["name"],
                            description=annotation.get("description", ""),
                            parameters=annotation["parameters"],
                        )
                    else:
                        raise KeyError(
                            f"Missing schema for function at index {k} in path '{new_path}'"
                        )

    validate_keys(service, service_type_info["definition"])
    annotate_recursive(service, service_type_info["definition"])
    return service


class RemoteException(Exception):
    """Represent a remote exception."""

    pass


class RemoteService(ObjectProxy):
    """Wrapper for remote service."""

    pass


class Timer:
    """Represent a timer."""

    def __init__(self, timeout, callback, *args, label="timer", **kwargs):
        """Set up instance."""
        self._timeout = timeout
        self._callback = callback
        self._task = None
        self._args = args
        self._kwrags = kwargs
        self._label = label
        self.started = False

    def start(self):
        """Start the timer."""
        if not self.started:
            self._task = asyncio.ensure_future(self._job())
            self.started = True
        else:
            self.reset()

    async def _job(self):
        """Handle a job."""
        await asyncio.sleep(self._timeout)
        ret = self._callback(*self._args, **self._kwrags)
        if ret is not None and inspect.isawaitable(ret):
            await ret

    def clear(self):
        """Clear the timer."""
        if self._task and self.started:
            self._task.cancel()
            self._task = None
            self.started = False
        else:
            logger.warning("Clearing a timer (%s) which is not started", self._label)

    def reset(self):
        """Reset the timer."""
        if self._task is None:
            self.start()
        else:
            self._task.cancel()
            self._task = asyncio.ensure_future(self._job())


class RemoteFunction:
    def __init__(
        self,
        rpc_instance,
        encoded_method,
        remote_parent=None,
        local_parent=None,
        remote_workspace=None,
        local_workspace=None,
        description=None,
        with_promise=None,
    ):
        self._rpc = rpc_instance
        self._encoded_method = encoded_method
        self._remote_parent = remote_parent
        self._local_parent = local_parent
        self._remote_workspace = remote_workspace
        self._local_workspace = local_workspace
        self._description = description or ""
        self._with_promise = with_promise

        self.__rpc_object__ = encoded_method.copy()
        method_id = encoded_method["_rmethod"]
        self.__name__ = encoded_method.get("_rname") or method_id.split(".")[-1]
        if "#" in self.__name__:
            self.__name__ = self.__name__.split("#")[-1]
        self.__doc__ = encoded_method.get("_rdoc", f"Remote method: {method_id}")
        self.__schema__ = encoded_method.get("_rschema")
        self.__no_chunk__ = (
            encoded_method.get("_rmethod") == "services.built-in.message_cache.append"
        )

    def __call__(self, *arguments, **kwargs):
        arguments = list(arguments)
        if kwargs:
            arguments = arguments + [kwargs]

        fut = safe_create_future()

        def resolve(result):
            if fut.done():
                return
            fut.set_result(result)

        def reject(error):
            if fut.done():
                return
            fut.set_exception(error)

        local_session_id = shortuuid.uuid()
        if self._local_parent:
            local_session_id = self._local_parent + "." + local_session_id
        store = self._rpc._get_session_store(local_session_id, create=True)
        store["target_id"] = self._encoded_method["_rtarget"]
        args = self._rpc._encode(
            arguments,
            session_id=local_session_id,
            local_workspace=self._local_workspace,
        )
        if self._rpc._local_workspace is None:
            from_client = self._rpc._client_id
        else:
            from_client = self._rpc._local_workspace + "/" + self._rpc._client_id

        main_message = {
            "type": "method",
            "from": from_client,
            "to": self._encoded_method["_rtarget"],
            "method": self._encoded_method["_rmethod"],
        }
        extra_data = {}
        if args:
            extra_data["args"] = args
        if kwargs:
            extra_data["with_kwargs"] = bool(kwargs)

        if self._remote_parent:
            main_message["parent"] = self._remote_parent

        timer = None
        if self._with_promise:
            main_message["session"] = local_session_id
            method_name = f"{self._encoded_method['_rtarget']}:{self._encoded_method['_rmethod']}"
            # Timer will be started after message is sent
            # Heartbeat will keep resetting it, allowing methods to run indefinitely
            # IMPORTANT: When timeout occurs, we must clean up the session to prevent memory leaks
            async def timeout_callback(error_msg):
                # First reject the promise
                if asyncio.iscoroutinefunction(reject):
                    await reject(error_msg)
                else:
                    reject(error_msg)
                # Then clean up the entire session to stop all callbacks
                if local_session_id in self._rpc._object_store:
                    del self._rpc._object_store[local_session_id]
                    logger.debug(f"Cleaned up session {local_session_id} after timeout")
            
            timer = Timer(
                self._rpc._method_timeout,
                timeout_callback,
                f"Method call timed out: {method_name}, context: {self._description}",
                label=method_name,
            )

            def has_interface_object(obj):
                if not obj or not isinstance(obj, (dict, list, tuple)):
                    return False
                if isinstance(obj, dict):
                    if obj.get("_rintf") == True:
                        return True
                    return any(
                        has_interface_object(value) for value in obj.values()
                    )
                elif isinstance(obj, (list, tuple)):
                    return any(has_interface_object(item) for item in obj)
                return False

            clear_after_called = not has_interface_object(args)

            promise_data = self._rpc._encode_promise(
                resolve=resolve,
                reject=reject,
                session_id=local_session_id,
                clear_after_called=clear_after_called,
                timer=timer,
                local_workspace=self._local_workspace,
                description=self._description,
            )
            if self._with_promise == True:
                extra_data["promise"] = promise_data
            elif self._with_promise == "*":
                extra_data["promise"] = "*"
                extra_data["t"] = self._rpc._method_timeout / 2
            else:
                raise RuntimeError(f"Unsupported promise type: {self._with_promise}")

        message_package = msgpack.packb(main_message)
        if extra_data:
            message_package = message_package + msgpack.packb(extra_data)
        total_size = len(message_package)
        if (
            total_size <= self._rpc._long_message_chunk_size + 1024
            or self.__no_chunk__
        ):
            emit_task = asyncio.create_task(self._rpc._emit_message(message_package))
        else:
            emit_task = asyncio.create_task(
                self._rpc._send_chunks(message_package, self._encoded_method["_rtarget"], self._remote_parent)
            )
        background_tasks.add(emit_task)

        def handle_result(fut):
            background_tasks.discard(fut)
            if fut.exception():
                error_msg = (
                    "Failed to send the request when calling method "
                    f"({self._encoded_method['_rtarget']}:{self._encoded_method['_rmethod']}), error: {fut.exception()}"
                )
                if reject:
                    reject(Exception(error_msg))
                else:
                    logger.warning("Unhandled RPC method call error: %s", error_msg)
                if timer:
                    timer.clear()
            else:
                if timer:
                    timer.start()
        emit_task.add_done_callback(handle_result)
        return fut

    def __repr__(self):
        return f"<RemoteFunction {self.__name__} ({(self._description[:100] + '...') if len(self._description) > 100 else self._description})>"

    def __str__(self):
        return self.__repr__()

background_tasks = set()


class RPC(MessageEmitter):
    """Represent the RPC."""

    def __init__(
        self,
        connection,
        client_id=None,
        default_context=None,
        name=None,
        codecs=None,
        method_timeout=None,
        max_message_buffer_size=0,
        loop=None,
        workspace=None,
        silent=False,
        app_id=None,
        server_base_url=None,
        long_message_chunk_size=None,
    ):
        """Set up instance."""
        self._codecs = codecs or {}
        assert client_id and isinstance(client_id, str)
        assert client_id is not None, "client_id is required"
        self._client_id = client_id
        self._name = name
        self._app_id = app_id or "*"
        self._local_workspace = workspace
        self._silent = silent
        self.default_context = default_context or {}
        self._method_annotations = weakref.WeakKeyDictionary()
        self._max_message_buffer_size = max_message_buffer_size
        self._chunk_store = {}
        self._method_timeout = 30 if method_timeout is None else method_timeout
        self._remote_logger = logger
        self._server_base_url = server_base_url
        self.loop = loop or asyncio.get_event_loop()
        self._long_message_chunk_size = long_message_chunk_size or CHUNK_SIZE
        super().__init__(self._remote_logger)

        # Set up exception handler for unhandled asyncio futures
        def handle_exception(loop, context):
            exception = context.get('exception')
            if isinstance(exception, Exception):
                # Check if this is a "Method not found" error that we can ignore
                if "Method not found" in str(exception) or "Session not found" in str(exception):
                    logger.debug("Ignoring expected method/session not found error: %s", exception)
                else:
                    logger.debug("Unhandled asyncio exception: %s", context)
            else:
                logger.debug("Unhandled asyncio exception: %s", context)
        
        # Only set the exception handler if we haven't already set one
        if not hasattr(self.loop, '_hypha_exception_handler_set'):
            self.loop.set_exception_handler(handle_exception)
            self.loop._hypha_exception_handler_set = True

        self._services = {}
        self._object_store = {
            "services": self._services,
        }

        if HAS_PYDANTIC:
            self.register_codec(
                {
                    "name": "pydantic_model",
                    "type": BaseModel,
                    "encoder": pydantic_encoder,
                    "decoder": pydantic_decoder,
                }
            )

        if connection:
            self.add_service(
                {
                    "id": "built-in",
                    "type": "built-in",
                    "name": f"Builtin services for {self._local_workspace}/{self._client_id}",
                    "config": {
                        "require_context": True,
                        "visibility": "public",
                        "api_version": API_VERSION,
                    },
                    "ping": self._ping,
                    "get_service": self.get_local_service,
                    "message_cache": {
                        "create": self._create_message,
                        "append": self._append_message,
                        "set": self._set_message,
                        "process": self._process_message,
                        "remove": self._remove_message,
                    },
                }
            )
            self.on("method", self._handle_method)
            self.on("error", self._error)

            assert hasattr(connection, "emit_message") and hasattr(
                connection, "on_message"
            )
            assert hasattr(connection, "manager_id"), "Connection must have manager_id"
            self._emit_message = connection.emit_message
            connection.on_message(self._on_message)
            self._connection = connection

            async def on_connected(connection_info):
                if not self._silent and self._connection.manager_id:
                    logger.info("Connection established, reporting services...")
                    try:
                        # Retry getting manager service with exponential backoff
                        manager = await self._get_manager_with_retry()
                        services_count = len(self._services)
                        registered_count = 0
                        failed_services = []

                        # Use timeout for service registration to prevent hanging
                        service_registration_timeout = self._method_timeout or 30

                        for service in list(self._services.values()):
                            try:
                                service_info = self._extract_service_info(service)
                                await asyncio.wait_for(
                                    manager.register_service(service_info),
                                    timeout=service_registration_timeout
                                )
                                registered_count += 1
                                logger.debug(
                                    f"Successfully registered service: {service.get('id', 'unknown')}"
                                )
                            except asyncio.TimeoutError:
                                failed_services.append(service.get("id", "unknown"))
                                logger.error(
                                    f"Timeout registering service {service.get('id', 'unknown')}"
                                )
                            except Exception as service_error:
                                failed_services.append(service.get("id", "unknown"))
                                logger.error(
                                    f"Failed to register service {service.get('id', 'unknown')}: {service_error}"
                                )

                        if registered_count == services_count:
                            logger.info(
                                f"Successfully registered all {registered_count} services with the server"
                            )
                        else:
                            logger.warning(
                                f"Only registered {registered_count} out of {services_count} services with the server. Failed services: {failed_services}"
                            )

                        # Fire event with registration status
                        self._fire(
                            "services_registered",
                            {
                                "total": services_count,
                                "registered": registered_count,
                                "failed": failed_services,
                            },
                        )
                        
                        # Subscribe to client_disconnected events if the manager supports it
                        try:
                            manager_dict = ObjectProxy.toDict(manager)
                            if "subscribe" in manager_dict:
                                logger.debug("Subscribing to client_disconnected events")

                                async def handle_client_disconnected(event):
                                    client_id = event.get("client")
                                    if client_id:
                                        logger.debug(f"Client {client_id} disconnected, cleaning up sessions")
                                        await self._handle_client_disconnected(client_id)

                                # Subscribe to the event topic first with timeout
                                self._client_disconnected_subscription = await asyncio.wait_for(
                                    manager.subscribe(["client_disconnected"]),
                                    timeout=service_registration_timeout
                                )

                                # Then register the local event handler
                                self.on("client_disconnected", handle_client_disconnected)

                                logger.debug("Successfully subscribed to client_disconnected events")
                            else:
                                logger.debug("Manager does not support subscribe method, skipping client_disconnected handling")
                                self._client_disconnected_subscription = None
                        except asyncio.TimeoutError:
                            logger.warning("Timeout subscribing to client_disconnected events")
                            self._client_disconnected_subscription = None
                        except Exception as subscribe_error:
                            logger.warning(f"Failed to subscribe to client_disconnected events: {subscribe_error}")
                            self._client_disconnected_subscription = None
                        
                    except Exception as manager_error:
                        logger.error(
                            f"Failed to get manager service for registering services: {manager_error}"
                        )
                        # Fire event with error status
                        self._fire(
                            "services_registration_failed",
                            {
                                "error": str(manager_error),
                                "total_services": len(self._services),
                            },
                        )
                else:
                    logger.info("Connection established: %s", connection_info)
                if connection_info:
                    if connection_info.get("public_base_url"):
                        self._server_base_url = connection_info.get("public_base_url")
                    self._fire("connected", connection_info)

            connection.on_connected(on_connected)
            task = self.loop.create_task(on_connected(None))
            # Store the background task reference for proper cleanup
            self._background_task = task
            # Mark the task with RPC reference for easier cleanup
            task._rpc_ref = self
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)
        else:

            async def _emit_message(_):
                logger.info("No connection to emit message")

            self._emit_message = _emit_message

        self.check_modules()

    def register_codec(self, config: dict):
        """Register codec."""
        assert "name" in config
        assert "encoder" in config or "decoder" in config
        if "type" in config:
            for tp in list(self._codecs.keys()):
                codec = self._codecs[tp]
                if codec.type == config["type"] or tp == config["name"]:
                    logger.debug("Removing duplicated codec: " + tp)
                    del self._codecs[tp]

        self._codecs[config["name"]] = DefaultObjectProxy.fromDict(config)

    async def _ping(self, msg: str, context=None):
        """Handle ping."""
        assert msg == "ping"
        return "pong"

    async def ping(self, client_id: str, timeout=1):
        """Send a ping."""
        method = self._generate_remote_method(
            {
                "_rserver": self._server_base_url,
                "_rtarget": client_id,
                "_rmethod": "services.built-in.ping",
                "_rpromise": True,
                "_rdoc": "Ping the remote client",
            }
        )
        assert (await asyncio.wait_for(method("ping"), timeout)) == "pong"

    def _create_message(
        self,
        key: str,
        heartbeat: bool = False,
        overwrite: bool = False,
        context=None,
    ):
        """Create a message."""
        if heartbeat:
            if key not in self._object_store:
                raise Exception(f"session does not exist anymore: {key}")
            self._object_store[key]["timer"].reset()

        if "message_cache" not in self._object_store:
            self._object_store["message_cache"] = {}
        if not overwrite and key in self._object_store["message_cache"]:
            raise Exception(
                "Message with the same key (%s) already exists in the cache store, "
                "please use overwrite=True or remove it first.",
                key,
            )
        self._object_store["message_cache"][key] = {}

    def _append_message(
        self, key: str, data: bytes, heartbeat: bool = False, context=None
    ):
        """Append a message."""
        if heartbeat:
            if key not in self._object_store:
                raise Exception(f"session does not exist anymore: {key}")
            self._object_store[key]["timer"].reset()
        cache = self._object_store["message_cache"]
        if key not in cache:
            raise KeyError(f"Message with key {key} does not exists.")
        assert isinstance(data, bytes)
        cache[key][len(cache[key])] = data

    def _set_message(
        self, key: str, index: int, data: bytes, heartbeat: bool = False, context=None
    ):
        """Append a message."""
        if heartbeat:
            if key not in self._object_store:
                raise Exception(f"session does not exist anymore: {key}")
            self._object_store[key]["timer"].reset()
        cache = self._object_store["message_cache"]
        if key not in cache:
            raise KeyError(f"Message with key {key} does not exists.")
        assert isinstance(data, bytes)
        assert isinstance(cache[key], dict)
        cache[key][index] = data

    def _remove_message(self, key: str, context=None):
        """Remove a message."""
        cache = self._object_store["message_cache"]
        if key not in cache:
            raise KeyError(f"Message with key {key} does not exists.")
        del cache[key]

    def _process_message(self, key: str, heartbeat: bool = False, context=None):
        """Process a message."""
        if heartbeat:
            if key not in self._object_store:
                raise Exception(f"session does not exist anymore: {key}")
            self._object_store[key]["timer"].reset()
        cache = self._object_store["message_cache"]
        assert context is not None, "Context is required"
        if key not in cache:
            raise KeyError(f"Message with key {key} does not exists.")
        data = cache[key]
        # concatenate all the chunks
        total = len(data)
        content = b""
        for i in range(total):
            content += data[i]
        data = content
        logger.debug("Processing message %s (size=%d)", key, len(data))
        unpacker = msgpack.Unpacker(
            io.BytesIO(data), max_buffer_size=self._max_message_buffer_size
        )
        main = unpacker.unpack()
        # Make sure the fields are from trusted source
        main.update(
            {
                "from": context["from"],
                "to": context["to"],
                "ws": context["ws"],
                "user": context["user"],
            }
        )
        main["ctx"] = main.copy()
        main["ctx"].update(self.default_context)
        try:
            extra = unpacker.unpack()
            main.update(extra)
        except msgpack.exceptions.OutOfData:
            pass
        self._fire(main["type"], main)
        del cache[key]

    def _add_context_to_message(self, main):
        """Add trusted context to a message if it doesn't already have it."""
        if "ctx" not in main:
            # Create context from message fields + default_context
            main["ctx"] = main.copy()
            main["ctx"].update(self.default_context)
        return main

    def _on_message(self, message):
        """Handle message."""
        if isinstance(message, str):
            main = json.loads(message)
            # Add trusted context to the method call
            main = self._add_context_to_message(main)
            self._fire(main["type"], main)
        elif isinstance(message, bytes):
            unpacker = msgpack.Unpacker(
                io.BytesIO(message),
                max_buffer_size=max(512000, self._long_message_chunk_size * 2),
            )
            main = unpacker.unpack()
            # Add trusted context to the method call
            main = self._add_context_to_message(main)
            try:
                extra = unpacker.unpack()
                main.update(extra)
            except msgpack.exceptions.OutOfData:
                pass
            self._fire(main["type"], main)
        elif isinstance(message, dict):
            # Add trusted context to the method call
            message = self._add_context_to_message(message)
            self._fire(message["type"], message)
        else:
            raise Exception(f"Invalid message type: {type(message)}")

    def reset(self):
        """Reset."""
        self._event_handlers = {}
        self._services = {}

    def _close_sessions(self, store):
        for key, value in list(store.items()):
            if key in ("services", "message_cache"):
                continue
            if isinstance(value, dict):
                if value.get("heartbeat_task"):
                    value["heartbeat_task"].cancel()
                if value.get("timer"):
                    value["timer"].clear()
                self._close_sessions(value)

    def close(self):
        """Close the RPC connection and clean up resources."""
        # Clean up all pending sessions before closing
        self._cleanup_on_disconnect()
        self._close_sessions(self._object_store)
        
        # Clean up background tasks to prevent memory leaks
        # Cancel any background tasks that might be holding references to this RPC
        if hasattr(self, '_background_task') and self._background_task:
            try:
                if not self._background_task.done():
                    self._background_task.cancel()
                background_tasks.discard(self._background_task)
            except Exception as e:
                logger.debug(f"Error cleaning up background task: {e}")
        
        # Clean up any other background tasks that might reference this RPC
        tasks_to_remove = []
        for task in list(background_tasks):
            try:
                if hasattr(task, '_rpc_ref') and task._rpc_ref is self:
                    tasks_to_remove.append(task)
                elif hasattr(task, 'get_coro') and task.get_coro():
                    # Check if task is related to this RPC by examining the coroutine
                    coro = task.get_coro()
                    if hasattr(coro, 'cr_frame') and coro.cr_frame:
                        # Look for references to this RPC in the task's frame
                        frame_locals = coro.cr_frame.f_locals
                        if 'self' in frame_locals and frame_locals['self'] is self:
                            tasks_to_remove.append(task)
            except Exception as e:
                logger.debug(f"Error checking background task: {e}")
        
        for task in tasks_to_remove:
            try:
                if not task.done():
                    task.cancel()
                background_tasks.discard(task)
            except Exception as e:
                logger.debug(f"Error removing background task: {e}")
        
        # Remove the local event handler for client_disconnected
        # Note: Actual unsubscription from server is done in async disconnect() method
        if hasattr(self, '_client_disconnected_subscription') and self._client_disconnected_subscription:
            try:
                # Remove the local event handler
                self.off("client_disconnected")
            except Exception as e:
                logger.debug(f"Error removing client_disconnected handler: {e}")
        
        # Clear connection reference to break circular references
        if hasattr(self, '_connection'):
            self._connection = None
        
        # Clear emit_message reference to break circular references
        if hasattr(self, '_emit_message'):
            self._emit_message = None
        
        self._fire("disconnected")

    async def disconnect(self):
        """Disconnect."""
        # Store connection reference before closing for unsubscribe
        connection = getattr(self, '_connection', None)
        manager_id = connection.manager_id if connection else None

        # Unsubscribe from client_disconnected events before closing
        if hasattr(self, '_client_disconnected_subscription') and self._client_disconnected_subscription:
            try:
                if connection and manager_id:
                    manager = await asyncio.wait_for(
                        self.get_remote_service(f"*/{manager_id}"),
                        timeout=5.0
                    )
                    if hasattr(manager, 'unsubscribe') and callable(manager.unsubscribe):
                        if asyncio.iscoroutinefunction(manager.unsubscribe):
                            await asyncio.wait_for(
                                manager.unsubscribe("client_disconnected"),
                                timeout=5.0
                            )
                        else:
                            manager.unsubscribe("client_disconnected")
                    logger.debug("Successfully unsubscribed from client_disconnected events")
            except asyncio.TimeoutError:
                logger.debug("Timeout unsubscribing from client_disconnected events")
            except Exception as e:
                logger.debug(f"Error unsubscribing from client_disconnected: {e}")
            finally:
                self._client_disconnected_subscription = None

        self.close()

        # Disconnect the underlying connection if it exists
        if connection:
            try:
                await connection.disconnect()
            except Exception as e:
                logger.debug(f"Error disconnecting underlying connection: {e}")
    
    async def _handle_client_disconnected(self, client_id):
        """Handle cleanup when a remote client disconnects."""
        try:
            logger.debug(f"Handling disconnection for client: {client_id}")
            
            # Clean up all sessions for the disconnected client
            sessions_cleaned = self._cleanup_sessions_for_client(client_id)
            
            if sessions_cleaned > 0:
                logger.debug(f"Cleaned up {sessions_cleaned} sessions for disconnected client: {client_id}")
            
            # Fire an event to notify about the client disconnection
            self._fire("remote_client_disconnected", {"client_id": client_id, "sessions_cleaned": sessions_cleaned})
            
        except Exception as e:
            logger.error(f"Error handling client disconnection for {client_id}: {e}")
    
    def _cleanup_sessions_for_client(self, client_id):
        """Clean up all sessions for a specific client."""
        sessions_cleaned = 0
        
        # Iterate through all top-level session keys
        for session_key in list(self._object_store.keys()):
            if session_key in ("services", "message_cache"):
                continue
            
            session = self._object_store.get(session_key)
            if not isinstance(session, dict):
                continue
            
            # Check if this session belongs to the disconnected client
            # Sessions have a target_id property that identifies which client they're calling
            if session.get("target_id") == client_id:
                logger.debug(f"Found session {session_key} for disconnected client: {client_id}")
                
                # Reject any pending promises in this session
                if "reject" in session and callable(session["reject"]):
                    logger.debug(f"Rejecting session {session_key}")
                    try:
                        session["reject"](RemoteException(f"Client disconnected: {client_id}"))
                    except Exception as e:
                        logger.warning(f"Error rejecting session {session_key}: {e}")
                
                if "resolve" in session and callable(session["resolve"]):
                    logger.debug(f"Resolving session {session_key} with error")
                    try:
                        session["resolve"](RemoteException(f"Client disconnected: {client_id}"))
                    except Exception as e:
                        logger.warning(f"Error resolving session {session_key}: {e}")
                
                # Clear any timers
                if session.get("timer"):
                    try:
                        session["timer"].clear()
                    except Exception as e:
                        logger.warning(f"Error clearing timer for {session_key}: {e}")
                
                # Clear heartbeat tasks  
                if session.get("heartbeat_task"):
                    try:
                        session["heartbeat_task"].cancel()
                    except Exception as e:
                        logger.warning(f"Error clearing heartbeat for {session_key}: {e}")
                
                # Remove the entire session
                del self._object_store[session_key]
                sessions_cleaned += 1
                logger.debug(f"Cleaned up session: {session_key}")
        
        return sessions_cleaned
    
    def _cleanup_on_disconnect(self):
        """Clean up all pending sessions when the local RPC disconnects."""
        try:
            logger.debug("Cleaning up all sessions due to local RPC disconnection")
            
            # Get all keys to delete (everything except services)
            keys_to_delete = []
            
            for key in list(self._object_store.keys()):
                if key == "services":
                    continue
                
                value = self._object_store.get(key)
                
                if isinstance(value, dict):
                    # Reject any pending promises
                    if "reject" in value and callable(value["reject"]):
                        try:
                            value["reject"](RemoteException("RPC connection closed"))
                        except Exception as e:
                            logger.debug(f"Error rejecting promise during cleanup: {e}")
                    
                    # Clean up timers and tasks
                    if value.get("heartbeat_task"):
                        value["heartbeat_task"].cancel()
                    if value.get("timer"):
                        value["timer"].clear()
                
                # Mark ALL keys for deletion except services
                keys_to_delete.append(key)
            
            # Delete all marked sessions
            for key in keys_to_delete:
                del self._object_store[key]
            
        except Exception as e:
            logger.error(f"Error during cleanup on disconnect: {e}")

    async def _get_manager_with_retry(self, max_retries=20):
        """Get manager service with exponential backoff retry."""
        base_delay = 0.5
        max_delay = 10.0
        last_error = None

        for attempt in range(max_retries):
            try:
                svc = await self.get_remote_service(
                    f"*/{self._connection.manager_id}:default",
                    {"timeout": 20, "case_conversion": "snake"},
                )
                return svc
            except Exception as e:
                last_error = e
                logger.warning(
                    f"Failed to get manager service (attempt {attempt+1}/{max_retries}): {e}"
                )
                if attempt < max_retries - 1:
                    # Exponential backoff with maximum delay
                    delay = min(base_delay * (2**attempt), max_delay)
                    await asyncio.sleep(delay)

        # If we get here, all retries failed
        raise last_error

    async def get_manager_service(self, config=None):
        """Get remote root service."""
        config = config or {}
        assert self._connection.manager_id, "Manager id is not set"

        max_retries = 20
        retry_delay = 0.5
        last_error = None

        for attempt in range(max_retries):
            try:
                svc = await self.get_remote_service(
                    f"*/{self._connection.manager_id}:default", config
                )
                return svc
            except Exception as e:
                last_error = e
                logger.warning(
                    f"Failed to get manager service (attempt {attempt+1}/{max_retries}): {e}"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)

        # If we get here, all retries failed
        raise last_error

    def get_all_local_services(self):
        """Get all the local services."""
        return self._services

    def get_local_service(self, service_id: str, context=None):
        """Get a local service."""
        assert service_id is not None and context is not None
        ws, client_id = context["to"].split("/")
        assert client_id == self._client_id, "Services can only be accessed locally"

        service = self._services.get(service_id)
        if not service:
            raise KeyError("Service not found: %s", service_id)
        # Note: Do NOT mutate service["config"]["workspace"] here!
        # Doing so would corrupt the stored service config when called from
        # a different workspace (e.g., "public"), causing reconnection to fail
        # because _extract_service_info would use the wrong workspace value.

        # allow access for the same workspace
        if service["config"].get("visibility", "protected") in ["public", "unlisted"]:
            return service

        # allow access for the same workspace
        if context["ws"] == ws:
            return service
        
        # Check if user is from an authorized workspace
        authorized_workspaces = service["config"].get("authorized_workspaces")
        if authorized_workspaces and context["ws"] in authorized_workspaces:
            return service

        raise Exception(
            f"Permission denied for getting protected service: {service_id}, workspace mismatch: {ws} != {context['ws']}"
        )

    async def get_remote_service(self, service_uri=None, config=None, **kwargs):
        """Get a remote service."""
        config = config or {}
        config.update(kwargs)
        timeout = config.get("timeout", self._method_timeout)
        case_conversion = config.get("case_conversion")
        if service_uri is None and self._connection.manager_id:
            service_uri = "*/" + self._connection.manager_id
        elif ":" not in service_uri:
            service_uri = self._client_id + ":" + service_uri
        provider, service_id = service_uri.split(":")
        if "@" in service_id:
            service_id, app_id = service_id.split("@")
            if self._app_id and self._app_id != "*":
                assert app_id == str(
                    self._app_id
                ), f"App id mismatch: {app_id} != {self._app_id}"
        assert provider
        try:
            method = self._generate_remote_method(
                {
                    "_rserver": self._server_base_url,
                    "_rtarget": provider,
                    "_rmethod": "services.built-in.get_service",
                    "_rpromise": True,
                    "_rdoc": "Get a remote service",
                }
            )
            svc = await asyncio.wait_for(method(service_id), timeout=timeout)
            svc["id"] = service_uri
            if isinstance(svc, ObjectProxy):
                svc = svc.toDict()
            if case_conversion:
                return RemoteService.fromDict(convert_case(svc, case_conversion))
            else:
                return RemoteService.fromDict(svc)
        except Exception as exp:
            logger.warning("Failed to get remote service: %s: %s", service_id, exp)
            raise exp

    def _annotate_service_methods(
        self,
        a_object,
        object_id,
        require_context=False,
        run_in_executor=False,
        visibility="protected",
        authorized_workspaces=None,
    ):
        if callable(a_object):
            # mark the method as a remote method that requires context
            method_name = ".".join(object_id.split(".")[1:])
            self._method_annotations[a_object] = {
                "require_context": (
                    (method_name in require_context)
                    if isinstance(require_context, (list, tuple))
                    else bool(require_context)
                ),
                "run_in_executor": run_in_executor,
                "method_id": "services." + object_id,
                "visibility": visibility,
                "authorized_workspaces": authorized_workspaces,
            }
        elif isinstance(a_object, (dict, list, tuple)):
            if isinstance(a_object, ObjectProxy):
                a_object = ObjectProxy.toDict(a_object)
            items = (
                a_object.items() if isinstance(a_object, dict) else enumerate(a_object)
            )
            for key, val in items:
                if callable(val) and hasattr(val, "__rpc_object__"):
                    client_id = val.__rpc_object__["_rtarget"]
                    if "/" in client_id:
                        client_id = client_id.split("/")[1]
                    if self._client_id == client_id:
                        # Make sure we can modify the object
                        if isinstance(a_object, tuple):
                            a_object = list(a_object)
                        # recover local method
                        a_object[key] = index_object(
                            self._object_store, val.__rpc_object__["_rmethod"]
                        )
                        val = a_object[key]  # make sure it's annotated later
                    else:
                        raise Exception(
                            "Local method not found: "
                            f"{val.__rpc_object__['_rmethod']}, "
                            f"client id mismatch {self._client_id} != {client_id}"
                        )
                self._annotate_service_methods(
                    val,
                    object_id + "." + str(key),
                    require_context=require_context,
                    run_in_executor=run_in_executor,
                    visibility=visibility,
                    authorized_workspaces=authorized_workspaces,
                )

    def add_service(self, api, overwrite=False):
        """Add a service (silently without triggering notifications)."""
        # convert and store it in a docdict
        # such that the methods are hashable
        if isinstance(api, dict):
            api = ObjectProxy.fromDict(
                {
                    a: api[a]
                    for a in api.keys()
                    if not a.startswith("_") or a in ALLOWED_MAGIC_METHODS
                }
            )
        elif inspect.isclass(type(api)):
            api = ObjectProxy.fromDict(
                {
                    a: getattr(api, a)
                    for a in dir(api)
                    if not a.startswith("_") or a in ALLOWED_MAGIC_METHODS
                }
            )
            # For class instance, we need set a default id
            api["id"] = api.get("id", "default")
        else:
            raise Exception("Invalid service object type: {}".format(type(api)))

        assert "id" in api and isinstance(
            api["id"], str
        ), f"Service id not found: {api}"

        if "name" not in api:
            api["name"] = api["id"]

        if "config" not in api:
            api["config"] = {}

        if "type" not in api:
            api["type"] = "generic"

        # require_context only applies to the top-level functions
        require_context, run_in_executor = False, False
        if bool(api["config"].get("require_context")):
            require_context = api["config"]["require_context"]
        if bool(api["config"].get("run_in_executor")):
            run_in_executor = True
        visibility = api["config"].get("visibility", "protected")
        assert visibility in ["protected", "public", "unlisted"]
        
        # Validate authorized_workspaces
        authorized_workspaces = api["config"].get("authorized_workspaces")
        if authorized_workspaces is not None:
            if visibility != "protected":
                raise ValueError(
                    f"authorized_workspaces can only be set when visibility is 'protected', got visibility='{visibility}'"
                )
            if not isinstance(authorized_workspaces, list):
                raise ValueError("authorized_workspaces must be a list of workspace ids")
            for ws_id in authorized_workspaces:
                if not isinstance(ws_id, str):
                    raise ValueError(f"Each workspace id in authorized_workspaces must be a string, got {type(ws_id)}")
        self._annotate_service_methods(
            api,
            api["id"],
            require_context=require_context,
            run_in_executor=run_in_executor,
            visibility=visibility,
            authorized_workspaces=authorized_workspaces,
        )
        if not overwrite and api["id"] in self._services:
            raise Exception(
                f"Service already exists: {api['id']}, please specify"
                f" a different id (not {api['id']}) or overwrite=True"
            )
        self._services[api["id"]] = api
        return api

    def _extract_service_info(self, service):
        service = ObjectProxy.toDict(service)
        config = service.get("config", {})
        config["workspace"] = config.get(
            "workspace", self._local_workspace or self._connection.workspace
        )
        if config["workspace"] is None:
            raise ValueError(
                "Workspace is not set. Please ensure the connection has a workspace or set local_workspace."
            )
        skip_context = config.get("require_context", False)
        exclude_keys = ["id", "config", "name", "description", "type", "docs", "app_id", "service_schema"]
        filtered_service = {k: v for k, v in service.items() if k not in exclude_keys}
        service_schema = _get_schema(filtered_service, skip_context=skip_context)
        service_info = {
            "config": ObjectProxy.fromDict(config),
            "id": f"{config['workspace']}/{self._client_id}:{service['id']}",
            "name": service.get("name", service["id"]),
            "description": service.get("description", None),
            "type": service.get("type", "generic"),
            "docs": service.get("docs", None),
            "app_id": self._app_id,
            "service_schema": service_schema,
        }
        return ObjectProxy.fromDict(service_info)

    async def get_service_schema(self, service):
        """Get service schema."""
        skip_context = service.get("config", {}).get("require_context", False)
        service_schema = _get_schema(service, skip_context=skip_context)
        return service_schema

    async def register_service(
        self,
        api: dict,
        config: dict = None,
        **kwargs,
    ):
        """Register a service."""
        config = config or {}
        config.update(kwargs)
        overwrite = config.get("overwrite", False)
        notify = config.get("notify", True)
        check_type = config.get("check_type", False)
        if isinstance(api, ObjectProxy):
            api = ObjectProxy.toDict(api)
        manager = None
        if check_type and api.get("type"):
            try:
                manager = await self.get_manager_service(
                    {"timeout": 20, "case_conversion": "snake"}
                )
                type_info = await manager.get_service_type(api["type"])
                api = _annotate_service(api, type_info)
            except Exception as exp:
                raise Exception(
                    f"Failed to get service type {api['type']}, error: {exp}"
                )

        service = self.add_service(api, overwrite=overwrite)
        service_info = self._extract_service_info(service)
        if notify:
            try:
                manager = manager or await self.get_manager_service(
                    {"timeout": 20, "case_conversion": "snake"}
                )
                await manager.register_service(service_info)
            except Exception as exp:
                raise Exception(f"Failed to notify workspace manager: {exp}")
        return service_info

    async def unregister_service(self, service: Union[dict, str], notify: bool = True):
        """Register a service."""
        if isinstance(service, dict):
            service_id = service["id"]
        else:
            service_id = service
        assert isinstance(service_id, str), f"Invalid service id: {service_id}"
        if ":" in service_id:
            service_id = service_id.split(":")[1]
        if "@" in service_id:
            service_id = service_id.split("@")[0]
        if service_id not in self._services:
            raise Exception(f"Service not found: {service_id}")
        if notify:
            manager = await self.get_manager_service(
                {"timeout": 20, "case_conversion": "snake"}
            )
            await manager.unregister_service(service_id)
        del self._services[service_id]

    def check_modules(self):
        """Check if all the modules exists."""
        try:
            import numpy as np

            self.NUMPY_MODULE = np
        except ImportError:
            self.NUMPY_MODULE = False
            logger.warning(
                "Failed to import numpy, ndarray encoding/decoding will not work"
            )

    def _encode_callback(
        self,
        name,
        callback,
        session_id,
        clear_after_called=False,
        timer=None,
        local_workspace=None,
        description=None,
    ):
        method_id = f"{session_id}.{name}"
        encoded = {
            "_rtype": "method",
            "_rtarget": (
                f"{local_workspace}/{self._client_id}"
                if local_workspace
                else self._client_id
            ),
            "_rmethod": method_id,
            "_rpromise": False,
        }

        def wrapped_callback(*args, **kwargs):
            try:
                callback(*args, **kwargs)
            except asyncio.InvalidStateError:
                # This probably means the task was cancelled
                logger.debug(
                    "Invalid state error in callback: %s (context: %s)",
                    method_id,
                    description,
                )
            except Exception as exp:
                logger.error(
                    "Error in callback: %s (context: %s), error: %s",
                    method_id,
                    description,
                    exp,
                )
            finally:
                if timer and timer.started:
                    timer.clear()  # Clear the timer first before deleting the session
                if clear_after_called and self._get_session_store(
                    session_id, create=False
                ):
                    # Clean delegation - no complex logic here
                    self._cleanup_session_if_needed(session_id, name)

        return encoded, wrapped_callback

    def _create_promise_manager(self):
        """Create a Promise Manager - encapsulates all promise lifecycle logic."""

        class PromiseManager:
            def __init__(self):
                self.settled = False

            def settle(self):
                self.settled = True

            def is_settled(self):
                return self.settled

            def should_cleanup_on_callback(self, callback_name):
                return callback_name in ("resolve", "reject")

        return PromiseManager()

    def _is_promise_method_call(self, method_path):
        """Clean helper to identify promise method calls by session type."""
        session_id = method_path.split(".")[0]
        session = self._get_session_store(session_id, create=False)
        return session and "_promise_manager" in session

    def _cleanup_session_if_needed(self, session_id, callback_name):
        """Clean session management - all logic in one place."""
        if not session_id:
            logger.debug("Cannot cleanup session: session_id is empty")
            return

        try:
            store = self._get_session_store(session_id, create=False)
            if not store:
                logger.debug(f"Session {session_id} not found for cleanup")
                return

            should_cleanup = False

            # Promise sessions: let the promise manager decide cleanup
            if "_promise_manager" in store:
                try:
                    promise_manager = store["_promise_manager"]
                    if hasattr(promise_manager, 'should_cleanup_on_callback') and \
                       promise_manager.should_cleanup_on_callback(callback_name):
                        if hasattr(promise_manager, 'settle'):
                            promise_manager.settle()
                        should_cleanup = True
                        logger.debug(f"Promise session {session_id} settled and marked for cleanup")
                except Exception as e:
                    logger.warning(f"Error in promise manager cleanup for {session_id}: {e}")
                    # Still try to cleanup if promise manager fails
                    should_cleanup = True
            else:
                # Regular sessions: cleanup immediately
                should_cleanup = True
                logger.debug(f"Regular session {session_id} marked for cleanup")

            if should_cleanup:
                self._delete_session_completely(session_id)

        except Exception as e:
            logger.error(f"Unexpected error in session cleanup for {session_id}: {e}")
            # Try a basic cleanup as fallback
            try:
                self._delete_session_safely(session_id)
            except Exception as fallback_error:
                logger.error(f"Fallback cleanup also failed for {session_id}: {fallback_error}")

    def _delete_session_completely(self, session_id):
        """Completely delete a session and clean up empty parent containers."""
        if not session_id:
            return

        try:
            levels = session_id.split(".")
            
            # Navigate to the session and delete it safely
            if len(levels) == 1:
                # Top-level session - delete directly from object store
                session_key = levels[0]
                if session_key in self._object_store:
                    session_data = self._object_store[session_key]
                    
                    # Clear any timers or resources in the session before deletion
                    self._cleanup_session_resources(session_data)
                    
                    # Delete the session
                    del self._object_store[session_key]
                    logger.debug(f"Deleted top-level session: {session_id}")
                else:
                    logger.debug(f"Top-level session {session_id} already deleted")
            else:
                # Nested session - navigate and delete safely
                current_store = self._object_store
                path_exists = True
                
                # Navigate to parent container
                for i, level in enumerate(levels[:-1]):
                    if level not in current_store:
                        path_exists = False
                        logger.debug(f"Parent path broken at level '{level}' for session {session_id}")
                        break
                    if not isinstance(current_store[level], dict):
                        path_exists = False
                        logger.debug(f"Non-dict container at level '{level}' for session {session_id}")
                        break
                    current_store = current_store[level]
                
                if path_exists and levels[-1] in current_store:
                    session_data = current_store[levels[-1]]
                    
                    # Clear resources before deletion
                    if isinstance(session_data, dict):
                        self._cleanup_session_resources(session_data)
                    
                    # Delete the session
                    del current_store[levels[-1]]
                    logger.debug(f"Deleted nested session: {session_id}")
                    
                    # Clean up empty parent containers from bottom up
                    self._cleanup_empty_parent_containers(levels[:-1])
                else:
                    logger.debug(f"Nested session {session_id} already deleted or path invalid")
                    
        except KeyError as e:
            logger.debug(f"Session {session_id} already deleted: {e}")
        except Exception as e:
            logger.warning(f"Error during session cleanup for {session_id}: {e}")

    def _delete_session_safely(self, session_id):
        """Fallback method for basic session deletion without full cleanup."""
        try:
            levels = session_id.split(".")
            if len(levels) == 1 and levels[0] in self._object_store:
                del self._object_store[levels[0]]
                logger.debug(f"Fallback cleanup: deleted session {session_id}")
        except Exception as e:
            logger.error(f"Fallback cleanup failed for {session_id}: {e}")

    def _cleanup_session_resources(self, session_dict):
        """Clean up resources within a session (timers, etc.) before deletion."""
        if not isinstance(session_dict, dict):
            return
            
        cleanup_errors = []
        
        try:
            # Clear any active timers
            if "timer" in session_dict and session_dict["timer"]:
                try:
                    timer = session_dict["timer"]
                    if hasattr(timer, "clear") and hasattr(timer, "started"):
                        if timer.started:
                            timer.clear()
                            logger.debug("Cleared session timer during cleanup")
                    elif hasattr(timer, "cancel"):
                        # Some timers might have a cancel method instead
                        timer.cancel()
                        logger.debug("Cancelled session timer during cleanup")
                except Exception as timer_error:
                    cleanup_errors.append(f"timer: {timer_error}")
            
            # Cancel any heartbeat tasks  
            if "heartbeat_task" in session_dict and session_dict["heartbeat_task"]:
                try:
                    task = session_dict["heartbeat_task"]
                    if hasattr(task, "cancel") and not task.done():
                        task.cancel()
                        logger.debug("Cancelled heartbeat task during cleanup")
                except Exception as task_error:
                    cleanup_errors.append(f"heartbeat_task: {task_error}")

            # Clean up any other async tasks that might be stored
            for key, value in session_dict.items():
                if key.endswith("_task") and hasattr(value, "cancel"):
                    try:
                        if not value.done():
                            value.cancel()
                            logger.debug(f"Cancelled {key} during cleanup")
                    except Exception as cleanup_error:
                        cleanup_errors.append(f"{key}: {cleanup_error}")

            # Clean up promise manager resources
            if "_promise_manager" in session_dict:
                try:
                    promise_manager = session_dict["_promise_manager"]
                    if hasattr(promise_manager, 'cleanup'):
                        promise_manager.cleanup()
                except Exception as pm_error:
                    cleanup_errors.append(f"promise_manager: {pm_error}")
                    
        except Exception as e:
            cleanup_errors.append(f"general: {e}")

        if cleanup_errors:
            logger.debug(f"Some resource cleanup errors (non-critical): {cleanup_errors}")

    def _cleanup_empty_parent_containers(self, parent_levels):
        """Clean up empty parent containers from bottom up."""
        if not parent_levels:
            return
            
        try:
            # Work backwards through the path to clean up empty containers
            for i in range(len(parent_levels), 0, -1):
                path = parent_levels[:i]
                container = self._object_store
                
                # Navigate to the container
                for level in path[:-1]:
                    if level not in container:
                        logger.debug(f"Parent container path broken at '{level}', stopping cleanup")
                        return  # Path doesn't exist, nothing to clean
                    if not isinstance(container[level], dict):
                        logger.debug(f"Non-dict parent container at '{level}', stopping cleanup")
                        return
                    container = container[level]
                
                target_key = path[-1]
                if target_key in container and isinstance(container[target_key], dict):
                    # Only delete if the container is empty (excluding system keys)
                    remaining_keys = [k for k in container[target_key].keys() 
                                    if k not in ["services", "message_cache"]]
                    if not remaining_keys:
                        del container[target_key]
                        logger.debug(f"Cleaned up empty parent container: {'.'.join(path)}")
                    else:
                        # Container has content, stop cleanup
                        logger.debug(f"Parent container {'.'.join(path)} has content, stopping cleanup")
                        break
                        
        except Exception as e:
            logger.debug(f"Error cleaning empty parent containers: {e}")

    def _force_cleanup_all_sessions(self):
        """Emergency cleanup method to remove all sessions (for testing/debugging)."""
        if not hasattr(self, "_object_store"):
            return
            
        sessions_to_remove = []
        for key in self._object_store.keys():
            if key not in ["services", "message_cache"]:
                sessions_to_remove.append(key)
        
        for session_key in sessions_to_remove:
            try:
                session_data = self._object_store[session_key]
                self._cleanup_session_resources(session_data)
                del self._object_store[session_key]
                logger.debug(f"Force cleaned session: {session_key}")
            except Exception as e:
                logger.warning(f"Error in force cleanup of session {session_key}: {e}")

    def get_session_stats(self):
        """Get statistics about current sessions (for debugging/monitoring)."""
        if not hasattr(self, "_object_store"):
            return {"error": "No object store"}
        
        stats = {
            "total_sessions": 0,
            "promise_sessions": 0,
            "regular_sessions": 0,
            "sessions_with_timers": 0,
            "sessions_with_heartbeat": 0,
            "system_stores": {},
            "session_ids": []
        }
        
        for key, value in self._object_store.items():
            if key in ["services", "message_cache"]:
                stats["system_stores"][key] = {
                    "size": len(value) if hasattr(value, "__len__") else "unknown"
                }
            else:
                stats["total_sessions"] += 1
                stats["session_ids"].append(key)
                
                if isinstance(value, dict):
                    if "_promise_manager" in value:
                        stats["promise_sessions"] += 1
                    else:
                        stats["regular_sessions"] += 1
                    
                    if "timer" in value:
                        stats["sessions_with_timers"] += 1
                    
                    if "heartbeat_task" in value:
                        stats["sessions_with_heartbeat"] += 1
        
        return stats

    def _encode_promise(
        self,
        resolve,
        reject,
        session_id,
        clear_after_called=False,
        timer=None,
        local_workspace=None,
        description=None,
    ):
        """Encode a group of callbacks without promise."""
        store = self._get_session_store(session_id, create=True)
        if store is None:
            # Handle the case where session store creation failed gracefully
            logger.warning(
                f"Failed to create session store {session_id}, session management may be impaired"
            )
            # Create a minimal fallback store
            store = {}

        # Clean promise lifecycle management - TYPE-BASED, not string-based
        store["_promise_manager"] = self._create_promise_manager()
        encoded = {}

        if timer and reject and self._method_timeout:
            encoded["heartbeat"], store["heartbeat"] = self._encode_callback(
                "heartbeat",
                timer.reset,
                session_id,
                clear_after_called=False,
                timer=None,
                local_workspace=local_workspace,
                # description=f"heartbeat ({description})",
            )
            store["timer"] = timer
            encoded["interval"] = self._method_timeout / 2
        else:
            timer = None

        encoded["resolve"], store["resolve"] = self._encode_callback(
            "resolve",
            resolve,
            session_id,
            clear_after_called=clear_after_called,
            timer=timer,
            local_workspace=local_workspace,
            description=f"resolve ({description})",
        )
        encoded["reject"], store["reject"] = self._encode_callback(
            "reject",
            reject,
            session_id,
            clear_after_called=clear_after_called,
            timer=timer,
            local_workspace=local_workspace,
            description=f"reject ({description})",
        )
        return encoded

    async def _send_chunks(self, package, target_id, session_id):
        remote_services = await self.get_remote_service(f"{target_id}:built-in")
        assert (
            remote_services.message_cache
        ), "Remote client does not support message caching for long message."
        message_cache = remote_services.message_cache
        message_id = session_id or shortuuid.uuid()
        total_size = len(package)
        start_time = time.time()
        chunk_num = int(math.ceil(float(total_size) / self._long_message_chunk_size))
        if remote_services.config.api_version >= 3:
            # use concurrent sending
            await message_cache.create(message_id, bool(session_id))
            semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

            async def append_chunk(idx, chunk):
                # Acquire the semaphore
                async with semaphore:
                    await message_cache.set(message_id, idx, chunk, bool(session_id))
                    logger.debug(
                        "Sending chunk %d/%d (total=%d bytes)",
                        idx + 1,
                        chunk_num,
                        total_size,
                    )

            tasks = []
            for idx in range(chunk_num):
                start_byte = idx * self._long_message_chunk_size
                chunk = package[start_byte : start_byte + self._long_message_chunk_size]
                tasks.append(asyncio.create_task(append_chunk(idx, chunk)))

            # Wait for all chunks to finish uploading
            try:
                await asyncio.gather(*tasks)
            except Exception as error:
                # If any chunk fails, clean up the message cache
                try:
                    await message_cache.remove(message_id)
                except Exception as cleanup_error:
                    logger.error(
                        f"Failed to clean up message cache after error: {cleanup_error}"
                    )
                raise error
        else:
            await message_cache.create(message_id, bool(session_id))
            for idx in range(chunk_num):
                start_byte = idx * self._long_message_chunk_size
                await message_cache.append(
                    message_id,
                    package[start_byte : start_byte + self._long_message_chunk_size],
                    bool(session_id),
                )
                logger.debug(
                    "Sending chunk %d/%d (%d bytes)",
                    idx + 1,
                    chunk_num,
                    total_size,
                )
        await message_cache.process(message_id, bool(session_id))
        logger.debug(
            "All chunks (%d bytes) sent in %d s", total_size, time.time() - start_time
        )

    def emit(self, main_message, extra_data=None):
        """Emit a message."""
        assert (
            isinstance(main_message, dict) and "type" in main_message
        ), "Invalid message, must be an object with a `type` fields"
        if "to" not in main_message:
            self._fire(main_message["type"], main_message)
            return
        message_package = msgpack.packb(main_message)
        if extra_data:
            message_package = message_package + msgpack.packb(extra_data)
        total_size = len(message_package)
        if total_size > self._long_message_chunk_size + 1024:
            logger.warning(f"Sending large message (size={total_size})")
        return self.loop.create_task(self._emit_message(message_package))

    def _generate_remote_method(
        self,
        encoded_method,
        remote_parent=None,
        local_parent=None,
        remote_workspace=None,
        local_workspace=None,
    ):
        """Return remote method."""
        target_id = encoded_method["_rtarget"]
        if remote_workspace and "/" not in target_id:
            if remote_workspace != target_id:
                target_id = remote_workspace + "/" + target_id
            encoded_method["_rtarget"] = target_id
        method_id = encoded_method["_rmethod"]
        with_promise = encoded_method.get("_rpromise", False)
        description = f"method: {method_id}, docs: {encoded_method.get('_rdoc')}"
        return RemoteFunction(
            rpc_instance=self,
            encoded_method=encoded_method,
            remote_parent=remote_parent,
            local_parent=local_parent,
            remote_workspace=remote_workspace,
            local_workspace=local_workspace,
            description=description,
            with_promise=with_promise,
        )

    def _log(self, info):
        logger.info("RPC Info: %s", info)

    def _error(self, error):
        logger.error("RPC Error: %s", error)

    def _call_method(
        self,
        method,
        args,
        kwargs,
        resolve=None,
        reject=None,
        heartbeat_task=None,
        method_name=None,
        run_in_executor=False,
    ):
        if not inspect.iscoroutinefunction(method) and run_in_executor:
            result = self.loop.run_in_executor(None, partial(method, *args, **kwargs))
        else:
            result = method(*args, **kwargs)
        if result is not None and inspect.isawaitable(result):

            async def _wait(result):
                try:
                    result = await result
                    if heartbeat_task:
                        heartbeat_task.cancel()
                    if resolve is not None:
                        return resolve(result)
                    elif result is not None:
                        logger.debug("Returned value (%s): %s", method_name, result)
                except Exception as err:
                    traceback_error = traceback.format_exc()
                    if reject is not None:
                        return reject(Exception(format_traceback(traceback_error)))
                    else:
                        logger.error(
                            "Error in method (%s): %s", method_name, traceback_error
                        )

            return asyncio.ensure_future(_wait(result))
        else:
            if heartbeat_task:
                heartbeat_task.cancel()
            if resolve is not None:
                return resolve(result)

    def get_client_info(self):
        """Get client info."""
        return {
            "id": self._client_id,
            "services": [
                self._extract_service_info(service)
                for service in self._services.values()
            ],
        }

    def _handle_method(self, data):
        """Handle RPC method call."""
        reject = None
        method_task = None
        heartbeat_task = None
        try:
            assert "method" in data and "from" in data
            # Ensure context is available - create it if missing (for local service calls)
            if "ctx" not in data:
                data = self._add_context_to_message(data)
            method_name = f'{data["from"]}:{data["method"]}'
            remote_workspace = data["from"].split("/")[0]
            remote_client_id = data["from"].split("/")[1]
            # Make sure the target id is an absolute id
            data["to"] = (
                data["to"] if "/" in data["to"] else remote_workspace + "/" + data["to"]
            )
            data["ctx"]["to"] = data["to"]
            if self._local_workspace is None:
                local_workspace = data.get("to").split("/")[0]
            else:
                if self._local_workspace and self._local_workspace != "*":
                    assert data.get("to").split("/")[0] == self._local_workspace, (
                        "Workspace mismatch: "
                        f"{data.get('to').split('/')[0]} != {self._local_workspace}"
                    )
                local_workspace = self._local_workspace

            local_parent = data.get("parent")

            if "promise" in data:
                # Decode the promise with the remote session id
                # such that the session id will be passed to the remote
                # as a parent session id.
                promise = self._decode(
                    (
                        self._expand_promise(data)
                        if data["promise"] == "*"
                        else data["promise"]
                    ),
                    remote_parent=data.get("session"),
                    local_parent=local_parent,
                    remote_workspace=remote_workspace,
                    local_workspace=local_workspace,
                )
                resolve, reject = promise["resolve"], promise["reject"]
                if "heartbeat" in promise and "interval" in promise:

                    async def heartbeat(interval):
                        while True:
                            try:
                                logger.debug(
                                    "Reset heartbeat timer: %s", data["method"]
                                )
                                await promise["heartbeat"]()
                            except asyncio.CancelledError:
                                if method_task and not method_task.done():
                                    method_task.cancel()
                                break
                            except Exception as exp:
                                logger.error(
                                    "Failed to reset the heartbeat timer: %s, error: %s",
                                    data["method"],
                                    exp,
                                )
                                if method_task and not method_task.done():
                                    method_task.cancel()
                                break
                            if method_task and method_task.done():
                                logger.debug(
                                    "Stopping heartbeat as the method task is done"
                                )
                                break  # Stop the heartbeat if the task is done
                            await asyncio.sleep(interval)

                    heartbeat_task = asyncio.ensure_future(
                        heartbeat(promise["interval"])
                    )
                    store = self._get_session_store(data["session"], create=False)
                    if store:
                        store["heartbeat_task"] = heartbeat_task
            else:
                resolve, reject = None, None

            try:
                method = index_object(self._object_store, data["method"])
            except Exception:
                # Clean promise method detection - NO STRING MATCHING!
                if self._is_promise_method_call(data["method"]):
                    logger.debug(
                        "Promise method %s not available (detected by session type), ignoring: %s",
                        data["method"],
                        method_name,
                    )
                    return

                # Check if this is a session-based method call that might have expired
                method_parts = data["method"].split(".")
                if len(method_parts) > 1:
                    session_id = method_parts[0]
                    # Check if the session exists but the specific method doesn't
                    if session_id in self._object_store:
                        logger.debug(
                            "Session %s exists but method %s not found, likely expired callback: %s",
                            session_id,
                            data["method"],
                            method_name,
                        )
                        # For expired callbacks, don't raise an exception, just log and return
                        if callable(reject):
                            reject(Exception(f"Method expired or not found: {method_name}"))
                        return
                    else:
                        logger.debug(
                            "Session %s not found for method %s, likely cleaned up: %s",
                            session_id,
                            data["method"],
                            method_name,
                        )
                        # For cleaned up sessions, just log and return without raising
                        if callable(reject):
                            reject(Exception(f"Session not found: {method_name}"))
                        return

                logger.debug(
                    "Failed to find method %s at %s", method_name, self._client_id
                )
                error = Exception(f"Method not found: {method_name} at {self._client_id}")
                if callable(reject):
                    reject(error)
                else:
                    # Log the error instead of raising to prevent unhandled exceptions
                    logger.warning("Method not found and no reject callback: %s", error)
                return
            assert callable(method), f"Invalid method: {method_name}"

            # Check permission
            if method in self._method_annotations:
                # For services, it should not be protected
                if (
                    self._method_annotations[method].get("visibility", "protected")
                    == "protected"
                ):
                    # Allow access from same workspace
                    if local_workspace == remote_workspace:
                        pass  # Access granted
                    # Check if remote workspace is in authorized_workspaces list
                    elif (
                        self._method_annotations[method].get("authorized_workspaces")
                        and remote_workspace in self._method_annotations[method]["authorized_workspaces"]
                    ):
                        pass  # Access granted
                    # Allow manager access
                    elif remote_workspace == "*" and remote_client_id == self._connection.manager_id:
                        pass  # Access granted
                    else:
                        raise PermissionError(
                            f"Permission denied for invoking protected method {method_name}, "
                            "workspace mismatch: "
                            f"{local_workspace} != {remote_workspace}"
                        )
            else:
                # For sessions, the target_id should match exactly
                session_target_id = self._object_store[data["method"].split(".")[0]][
                    "target_id"
                ]
                if (
                    local_workspace == remote_workspace
                    and session_target_id
                    and "/" not in session_target_id
                ):
                    session_target_id = local_workspace + "/" + session_target_id
                if session_target_id != data["from"]:
                    raise PermissionError(
                        f"Access denied for method call ({method_name}) "
                        f"from {data['from']}"
                        f" to target {session_target_id}"
                    )

            # Make sure the parent session is still open
            if local_parent:
                # The parent session should be a session
                # that generate the current method call.
                assert (
                    self._get_session_store(local_parent, create=False) is not None
                ), f"Parent session was closed: {local_parent}"
            if data.get("args"):
                args = self._decode(
                    data["args"],
                    remote_parent=data.get("session"),
                    remote_workspace=remote_workspace,
                )
            else:
                args = []
            if data.get("with_kwargs"):
                kwargs = args.pop()
            else:
                kwargs = {}

            if method in self._method_annotations and self._method_annotations[
                method
            ].get("require_context"):
                kwargs["context"] = data["ctx"]

            run_in_executor = (
                method in self._method_annotations
                and self._method_annotations[method].get("run_in_executor")
            )
            logger.debug("Executing method: %s (%s)", method_name, data["method"])
            method_task = self._call_method(
                method,
                args,
                kwargs,
                resolve,
                reject,
                heartbeat_task=heartbeat_task,
                method_name=method_name,
                run_in_executor=run_in_executor,
            )
            store = self._get_session_store(data.get("session"), create=False)
            if store and "heartbeat_task" in store:
                del store["heartbeat_task"]

        except Exception as err:
            # make sure we clear the heartbeat timer
            if (
                heartbeat_task
                and not heartbeat_task.cancelled()
                and not heartbeat_task.done()
            ):
                heartbeat_task.cancel()
            if callable(reject):
                reject(err)
            logger.debug("Error during calling method: %s", err)

    def encode(self, a_object, session_id=None):
        """Encode object."""
        return self._encode(
            a_object,
            session_id=session_id,
        )

    def _get_session_store(self, session_id, create=False):
        if session_id is None:
            return None
        store = self._object_store
        levels = session_id.split(".")
        if create:
            # Create intermediate sessions if they don't exist
            for level in levels[:-1]:
                if level not in store:
                    store[level] = {}
                store = store[level]

            # Create the last level
            if levels[-1] not in store:
                store[levels[-1]] = {}

            return store[levels[-1]]
        else:
            for level in levels:
                if level not in store:
                    return None
                store = store[level]
            return store

    def _encode(
        self,
        a_object,
        session_id=None,
        local_workspace=None,
    ):
        """Encode object."""
        if isinstance(a_object, (int, float, bool, str, bytes)) or a_object is None:
            return a_object

        if isinstance(a_object, tuple):
            a_object = list(a_object)

        if isinstance(a_object, dict):
            a_object = dict(a_object)

        if isinstance(a_object, ObjectProxy):
            a_object = ObjectProxy.toDict(a_object)

        # Reuse the remote object
        if hasattr(a_object, "__rpc_object__"):
            # we will skip the encoding if the function is on the same server
            _server = a_object.__rpc_object__.get("_rserver", self._server_base_url)
            if _server == self._server_base_url:
                return a_object.__rpc_object__
            # else:
            #     logger.debug(
            #         f"Encoding remote function from a different server {_server}, current server: {self._server_base_url}"
            #     )

        # skip if already encoded
        if isinstance(a_object, dict) and "_rtype" in a_object:
            # make sure the interface functions are encoded
            temp = a_object["_rtype"]
            del a_object["_rtype"]
            b_object = self._encode(
                a_object,
                session_id=session_id,
                local_workspace=local_workspace,
            )
            b_object["_rtype"] = temp
            return b_object

        if callable(a_object):
            if a_object in self._method_annotations:
                annotation = self._method_annotations[a_object]
                b_object = {
                    "_rtype": "method",
                    "_rserver": self._server_base_url,
                    "_rtarget": (
                        f"{local_workspace}/{self._client_id}"
                        if local_workspace
                        else self._client_id
                    ),
                    "_rmethod": annotation["method_id"],
                    "_rpromise": "*",
                    "_rname": getattr(a_object, "__name__", None),
                    "_rasync": inspect.iscoroutinefunction(a_object),
                }
            else:
                assert isinstance(session_id, str)
                if hasattr(a_object, "__name__"):
                    object_id = f"{shortuuid.uuid()}#{a_object.__name__}"
                else:
                    object_id = shortuuid.uuid()
                b_object = {
                    "_rtype": "method",
                    "_rserver": self._server_base_url,
                    "_rtarget": (
                        f"{local_workspace}/{self._client_id}"
                        if local_workspace
                        else self._client_id
                    ),
                    "_rmethod": f"{session_id}.{object_id}",
                    "_rname": getattr(a_object, "__name__", None),
                    "_rpromise": "*",
                }
                store = self._get_session_store(session_id, create=True)
                store[object_id] = a_object

            b_object["_rdoc"] = callable_doc(a_object)
            if hasattr(a_object, "__schema__"):
                b_object["_rschema"] = a_object.__schema__
            return b_object

        isarray = isinstance(a_object, list)
        b_object = None

        encoded_obj = None
        for tp in self._codecs:
            codec = self._codecs[tp]
            if codec.encoder and isinstance(a_object, codec.type):
                # TODO: what if multiple encoders found
                encoded_obj = codec.encoder(a_object)
                if isinstance(encoded_obj, dict) and "_rtype" not in encoded_obj:
                    encoded_obj["_rtype"] = codec.name
                # encode the functions in the interface object
                if isinstance(encoded_obj, dict):
                    temp = encoded_obj["_rtype"]
                    del encoded_obj["_rtype"]
                    encoded_obj = self._encode(
                        encoded_obj,
                        session_id=session_id,
                        local_workspace=local_workspace,
                    )
                    encoded_obj["_rtype"] = temp
                b_object = encoded_obj
                return b_object

        if self.NUMPY_MODULE and isinstance(
            a_object, (self.NUMPY_MODULE.ndarray, self.NUMPY_MODULE.generic)
        ):
            v_bytes = a_object.tobytes()
            b_object = {
                "_rtype": "ndarray",
                "_rvalue": v_bytes,
                "_rshape": a_object.shape,
                "_rdtype": str(a_object.dtype),
            }

        elif isinstance(a_object, Exception):
            exc_traceback = "".join(
                traceback.format_exception(
                    type(a_object), value=a_object, tb=a_object.__traceback__
                )
            )
            b_object = {
                "_rtype": "error",
                "_rvalue": str(a_object),
                "_rtrace": exc_traceback,
            }
        elif isinstance(a_object, memoryview):
            b_object = {"_rtype": "memoryview", "_rvalue": a_object.tobytes()}
        elif isinstance(
            a_object, (io.IOBase, io.TextIOBase, io.BufferedIOBase, io.RawIOBase)
        ):
            b_object = {
                m: getattr(a_object, m) for m in IO_PROPS if hasattr(a_object, m)
            }
            b_object["_rtype"] = "iostream"
            b_object["_rnative"] = "py:" + str(type(a_object))
            b_object = self._encode(
                b_object,
                session_id=session_id,
                local_workspace=local_workspace,
            )

        # NOTE: "typedarray" is not used
        elif isinstance(a_object, OrderedDict):
            b_object = {
                "_rtype": "orderedmap",
                "_rvalue": self._encode(
                    list(a_object),
                    session_id=session_id,
                    local_workspace=local_workspace,
                ),
            }
        elif isinstance(a_object, set):
            b_object = {
                "_rtype": "set",
                "_rvalue": self._encode(
                    list(a_object),
                    session_id=session_id,
                    local_workspace=local_workspace,
                ),
            }
        elif inspect.isgenerator(a_object) or inspect.isasyncgen(a_object):
            # Handle generator objects by storing them in the session and returning a generator method
            assert isinstance(
                session_id, str
            ), "Session ID is required for generator encoding"
            object_id = shortuuid.uuid()

            # Store the generator in the session
            store = self._get_session_store(session_id, create=True)

            # Check if it's an async generator
            is_async = inspect.isasyncgen(a_object)

            # Define method to get next item from the generator
            async def next_item_method():
                if is_async:
                    try:
                        return await a_object.__anext__()
                    except StopAsyncIteration:
                        # Remove it from the session
                        del store[object_id]
                        return {"_rtype": "stop_iteration"}
                else:
                    try:
                        return next(a_object)
                    except StopIteration:
                        del store[object_id]
                        return {"_rtype": "stop_iteration"}

            # Register the next_item method in the session
            store[object_id] = next_item_method

            # Create a method that will be used to fetch the next item from the generator
            b_object = {
                "_rtype": "generator",
                "_rserver": self._server_base_url,
                "_rtarget": (
                    f"{local_workspace}/{self._client_id}"
                    if local_workspace
                    else self._client_id
                ),
                "_rmethod": f"{session_id}.{object_id}",
                "_rpromise": "*",
                "_rdoc": "Remote generator",
            }
        elif isinstance(a_object, (list, dict)):
            keys = range(len(a_object)) if isarray else a_object.keys()
            b_object = [] if isarray else {}
            for key in keys:
                encoded = self._encode(
                    a_object[key],
                    session_id=session_id,
                    local_workspace=local_workspace,
                )
                if isarray:
                    b_object.append(encoded)
                else:
                    b_object[key] = encoded
        else:
            raise Exception(
                "hypha-rpc: Unsupported data type:"
                f" {type(a_object)}, you can register a custom"
                " codec to encode/decode the object."
            )
        return b_object

    def decode(self, a_object):
        """Decode object."""
        return self._decode(a_object)

    def _decode(
        self,
        a_object,
        remote_parent=None,
        local_parent=None,
        remote_workspace=None,
        local_workspace=None,
    ):
        """Decode object."""
        if a_object is None:
            return a_object
        if isinstance(a_object, dict) and "_rtype" in a_object:
            b_object = None
            if (
                self._codecs.get(a_object["_rtype"])
                and self._codecs[a_object["_rtype"]].decoder
            ):
                temp = a_object["_rtype"]
                del a_object["_rtype"]
                a_object = self._decode(
                    a_object,
                    remote_parent=remote_parent,
                    local_parent=local_parent,
                    remote_workspace=remote_workspace,
                    local_workspace=local_workspace,
                )
                a_object["_rtype"] = temp
                b_object = self._codecs[a_object["_rtype"]].decoder(a_object)
            elif a_object["_rtype"] == "method":
                b_object = self._generate_remote_method(
                    a_object,
                    remote_parent=remote_parent,
                    local_parent=local_parent,
                    remote_workspace=remote_workspace,
                    local_workspace=local_workspace,
                )
            elif a_object["_rtype"] == "ndarray":
                # create build array/tensor if used in the plugin
                try:
                    if isinstance(a_object["_rvalue"], (list, tuple)):
                        a_object["_rvalue"] = reduce(
                            (lambda x, y: x + y), a_object["_rvalue"]
                        )
                    # make sure we have bytes instead of memoryview, e.g. for Pyodide
                    elif isinstance(a_object["_rvalue"], memoryview):
                        a_object["_rvalue"] = a_object["_rvalue"].tobytes()
                    elif not isinstance(a_object["_rvalue"], bytes):
                        raise Exception(
                            "Unsupported data type: " + str(type(a_object["_rvalue"]))
                        )
                    if self.NUMPY_MODULE:
                        b_object = self.NUMPY_MODULE.frombuffer(
                            a_object["_rvalue"], dtype=a_object["_rdtype"]
                        ).reshape(tuple(a_object["_rshape"]))

                    else:
                        b_object = a_object
                        logger.warning(
                            "numpy is not available, failed to decode ndarray"
                        )

                except Exception as exc:
                    logger.debug("Error in converting: %s", exc)
                    b_object = a_object
                    raise exc
            elif a_object["_rtype"] == "memoryview":
                b_object = memoryview(a_object["_rvalue"])
            elif a_object["_rtype"] == "iostream":
                b_object = ObjectProxy.fromDict(
                    {
                        k: self._decode(
                            a_object[k],
                            remote_parent=remote_parent,
                            local_parent=local_parent,
                            remote_workspace=remote_workspace,
                            local_workspace=local_workspace,
                        )
                        for k in a_object
                        if not k.startswith("_")
                    }
                )
                b_object["__rpc_object__"] = a_object

            # Pydantic model decoding is now handled by the registered codec
            # elif a_object["_rtype"] == "pydantic_model":
            #     # This block should technically not be reached if the codec is registered
            #     # but left here as a fallback/for clarity during transition
            #     if HAS_PYDANTIC:
            #         model_type = create_model_from_schema(a_object["_rschema"])
            #         b_object = model_type(**a_object["_rvalue"])
            #     else:
            #         logger.warning("Received Pydantic model but Pydantic is not installed.")
            #         b_object = a_object

            elif a_object["_rtype"] == "typedarray":
                if self.NUMPY_MODULE:
                    b_object = self.NUMPY_MODULE.frombuffer(
                        a_object["_rvalue"], dtype=a_object["_rdtype"]
                    )
                else:
                    b_object = a_object["_rvalue"]
            elif a_object["_rtype"] == "orderedmap":
                b_object = OrderedDict(
                    self._decode(
                        a_object["_rvalue"],
                        remote_parent=remote_parent,
                        local_parent=local_parent,
                        remote_workspace=remote_workspace,
                        local_workspace=local_workspace,
                    )
                )
            elif a_object["_rtype"] == "set":
                b_object = set(
                    self._decode(
                        a_object["_rvalue"],
                        remote_parent=remote_parent,
                        local_parent=local_parent,
                        remote_workspace=remote_workspace,
                        local_workspace=local_workspace,
                    )
                )
            elif a_object["_rtype"] == "error":
                b_object = RemoteException(
                    "RemoteError:"
                    + a_object["_rvalue"]
                    + "\n"
                    + (a_object.get("_rtrace") if a_object.get("_rtrace") else "")
                )
            elif a_object["_rtype"] == "generator":
                # Create an async generator function that will produce items from the remote generator
                gen_method = self._generate_remote_method(
                    a_object,
                    remote_parent=remote_parent,
                    local_parent=local_parent,
                    remote_workspace=remote_workspace,
                    local_workspace=local_workspace,
                )

                # Create an async generator proxy
                async def async_generator_proxy():
                    while True:
                        try:
                            next_item = await gen_method()
                            # Check for StopIteration signal
                            if (
                                isinstance(next_item, dict)
                                and next_item.get("_rtype") == "stop_iteration"
                            ):
                                break
                            yield next_item
                        except StopAsyncIteration:
                            break
                        except StopIteration:
                            break
                        except Exception as e:
                            # Properly propagate exceptions
                            logger.error(f"Error in generator: {e}")
                            raise

                b_object = async_generator_proxy()
            else:
                # make sure all the interface functions are decoded
                temp = a_object["_rtype"]
                del a_object["_rtype"]
                a_object = self._decode(
                    a_object,
                    remote_parent=remote_parent,
                    local_parent=local_parent,
                    remote_workspace=remote_workspace,
                    local_workspace=local_workspace,
                )
                a_object["_rtype"] = temp
                b_object = a_object
        elif isinstance(a_object, (dict, list, tuple)):
            if isinstance(a_object, tuple):
                a_object = list(a_object)
            isarray = isinstance(a_object, list)
            b_object = [] if isarray else ObjectProxy()
            keys = range(len(a_object)) if isarray else a_object.keys()
            for key in keys:
                val = a_object[key]
                if isarray:
                    b_object.append(
                        self._decode(
                            val,
                            remote_parent=remote_parent,
                            local_parent=local_parent,
                            remote_workspace=remote_workspace,
                            local_workspace=local_workspace,
                        )
                    )
                else:
                    b_object[key] = self._decode(
                        val,
                        remote_parent=remote_parent,
                        local_parent=local_parent,
                        remote_workspace=remote_workspace,
                        local_workspace=local_workspace,
                    )
        # make sure we have bytes instead of memoryview, e.g. for Pyodide
        # elif isinstance(a_object, memoryview):
        #     b_object = a_object.tobytes()
        # elif isinstance(a_object, bytearray):
        #     b_object = bytes(a_object)
        else:
            b_object = a_object
        return b_object

    def _expand_promise(self, data):
        return {
            "heartbeat": {
                "_rtype": "method",
                "_rtarget": data["from"].split("/")[1],
                "_rmethod": data["session"] + ".heartbeat",
                "_rdoc": f"heartbeat callback for method: {data['method']}",
            },
            "resolve": {
                "_rtype": "method",
                "_rtarget": data["from"].split("/")[1],
                "_rmethod": data["session"] + ".resolve",
                "_rdoc": f"resolve callback for method: {data['method']}",
            },
            "reject": {
                "_rtype": "method",
                "_rtarget": data["from"].split("/")[1],
                "_rmethod": data["session"] + ".reject",
                "_rdoc": f"reject callback for method: {data['method']}",
            },
            "interval": data["t"],
        }
