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

# Module-level numpy check (done once, not per RPC instance)
try:
    import numpy as _numpy_module
except ImportError:
    _numpy_module = None

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

try:
    from .crypto import (
        generate_encryption_keypair,
        encrypt_payload,
        decrypt_payload,
        public_key_to_hex,
    )

    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

CHUNK_SIZE = 1024 * 256
API_VERSION = 3
ALLOWED_MAGIC_METHODS = ["__enter__", "__exit__", "_dispose"]
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

# Sentinel to distinguish "not provided" from None (silent)
_UNSET = object()


def _make_logger(logger_cfg, default_logger):
    """Create a logger from a config value, matching the JS interface.

    - _UNSET / not provided → use the default module-level logger
    - None → silent (no output)
    - custom object → use directly (duck-typed)
    """
    if logger_cfg is _UNSET:
        return default_logger
    if logger_cfg is None:
        null_logger = logging.getLogger(default_logger.name + ".null")
        null_logger.handlers = [logging.NullHandler()]
        null_logger.propagate = False
        null_logger.setLevel(logging.CRITICAL + 1)
        return null_logger
    return logger_cfg


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


# Error patterns indicating a stale service reference that can be retried
_STALE_SERVICE_ERROR_PATTERNS = (
    "Method expired or not found",
    "Session not found",
    "Peer",  # "Peer X is not connected"
    "Method not found",
    "Connection was closed",
    "Connection is closed",
)


def _is_stale_service_error(error):
    """Check if an error indicates a stale service reference."""
    msg = str(error)
    return any(pattern in msg for pattern in _STALE_SERVICE_ERROR_PATTERNS)


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
        self._target_encryption_pub = encoded_method.get("_renc_pub")
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
        # Update target_id index for fast session cleanup
        top_key = local_session_id.split(".")[0]
        target_id = self._encoded_method["_rtarget"]
        if target_id not in self._rpc._target_id_index:
            self._rpc._target_id_index[target_id] = set()
        self._rpc._target_id_index[target_id].add(top_key)
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
            method_name = (
                f"{self._encoded_method['_rtarget']}:{self._encoded_method['_rmethod']}"
            )

            # Timer will be started after message is sent
            # Heartbeat will keep resetting it, allowing methods to run indefinitely
            # IMPORTANT: When timeout occurs, we must clean up the session to prevent memory leaks
            async def timeout_callback(error_msg):
                # First reject the promise - must pass an Exception, not a string
                error = TimeoutError(error_msg)
                try:
                    if asyncio.iscoroutinefunction(reject):
                        await reject(error)
                    else:
                        reject(error)
                except Exception as e:
                    self._rpc._log.debug("Error rejecting timed-out call: %s", e)
                # Clean up resources in the session before deleting it
                session = self._rpc._object_store.get(local_session_id)
                if isinstance(session, dict):
                    heartbeat = session.get("heartbeat_task")
                    if heartbeat and not getattr(heartbeat, "done", lambda: True)():
                        heartbeat.cancel()
                    t = session.get("timer")
                    if t and getattr(t, "started", False):
                        try:
                            t.clear()
                        except Exception:
                            pass
                    self._rpc._remove_from_target_id_index(local_session_id)
                    del self._rpc._object_store[local_session_id]
                    self._rpc._log.debug(
                        "Cleaned up session %s after timeout", local_session_id
                    )
                elif local_session_id in self._rpc._object_store:
                    self._rpc._remove_from_target_id_index(local_session_id)
                    del self._rpc._object_store[local_session_id]

            # Use shorter timeout for _rintf_ callbacks to fail fast
            # when the peer holding the callback has disconnected
            effective_timeout = (
                self._rpc._rintf_timeout
                if "_rintf_" in self._encoded_method["_rmethod"]
                else self._rpc._method_timeout
            )
            timer = Timer(
                effective_timeout,
                timeout_callback,
                f"Method call timed out: {method_name}, context: {self._description}",
                label=method_name,
            )

            clear_after_called = True

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
                extra_data["t"] = effective_timeout / 2
            else:
                raise RuntimeError(f"Unsupported promise type: {self._with_promise}")

        # E2E encrypt extra_data if both sides support encryption
        if (
            extra_data
            and self._rpc._encryption_enabled
            and self._target_encryption_pub
        ):
            plaintext = msgpack.packb(extra_data)
            nonce, ciphertext = encrypt_payload(
                self._rpc._encryption_private_key,
                bytes(self._target_encryption_pub),
                plaintext,
            )
            extra_data = {
                "_enc": {
                    "v": 2,
                    "pub": self._rpc._encryption_public_key,
                    "nonce": nonce,
                },
                "data": ciphertext,
            }

        # TWO-SEGMENT MSGPACK PROTOCOL:
        # Encode main message (routing + metadata) and extra data (args/kwargs) separately
        # This allows efficient routing without deserializing the entire payload
        message_package = msgpack.packb(main_message)
        if extra_data:
            # Concatenate two msgpack objects: [msgpack(main)][msgpack(extra)]
            message_package = message_package + msgpack.packb(extra_data)

        total_size = len(message_package)
        if total_size <= self._rpc._long_message_chunk_size + 1024 or self.__no_chunk__:
            emit_task = asyncio.create_task(self._rpc._emit_message(message_package))
        else:
            emit_task = asyncio.create_task(
                self._rpc._send_chunks(
                    message_package,
                    self._encoded_method["_rtarget"],
                    self._remote_parent,
                )
            )
        self._rpc._background_tasks.add(emit_task)

        def handle_result(emit_fut):
            self._rpc._background_tasks.discard(emit_fut)
            if emit_fut.exception():
                error_msg = (
                    "Failed to send the request when calling method "
                    f"({self._encoded_method['_rtarget']}:{self._encoded_method['_rmethod']}), error: {emit_fut.exception()}"
                )
                if reject:
                    reject(Exception(error_msg))
                else:
                    self._rpc._log.warning("Unhandled RPC method call error: %s", error_msg)
                if timer and timer.started:
                    timer.clear()
            else:
                if timer:
                    timer.start()
                if not self._with_promise:
                    # Fire-and-forget: resolve immediately after message is sent
                    # Without this, the future never resolves because no response
                    # is expected. This is critical for heartbeat callbacks which
                    # use _rpromise=False and are awaited in a loop.
                    resolve(None)
                    # Clean up the session created for encoding args since no
                    # response will come back to trigger cleanup.
                    # Only clean up if this is a top-level session (no parent),
                    # otherwise we'd delete the parent session and all siblings.
                    # Also skip cleanup if the session contains encoded callables
                    # (e.g. generators) that need to persist for remote calls.
                    if not self._local_parent:
                        session = self._rpc._object_store.get(local_session_id)
                        if isinstance(session, dict):
                            has_live_entries = any(
                                callable(v) or isinstance(v, dict)
                                for k, v in session.items()
                                if not k.startswith("_") and k != "target_id"
                            )
                            if not has_live_entries:
                                self._rpc._remove_from_target_id_index(local_session_id)
                                del self._rpc._object_store[local_session_id]

        emit_task.add_done_callback(handle_result)
        return fut

    def __repr__(self):
        return f"<RemoteFunction {self.__name__} ({(self._description[:100] + '...') if len(self._description) > 100 else self._description})>"

    def __str__(self):
        return self.__repr__()


def _apply_encryption_key_to_service(svc, enc_pub_bytes):
    """Apply an out-of-band encryption public key to all RemoteFunction objects in a service."""
    if isinstance(svc, dict):
        for value in svc.values():
            if isinstance(value, RemoteFunction):
                value._target_encryption_pub = enc_pub_bytes
            elif isinstance(value, (dict, list)):
                _apply_encryption_key_to_service(value, enc_pub_bytes)
    elif isinstance(svc, (list, tuple)):
        for item in svc:
            if isinstance(item, RemoteFunction):
                item._target_encryption_pub = enc_pub_bytes
            elif isinstance(item, (dict, list)):
                _apply_encryption_key_to_service(item, enc_pub_bytes)


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
        rintf_timeout=None,
        max_message_buffer_size=0,
        loop=None,
        workspace=None,
        silent=False,
        logger=_UNSET,
        app_id=None,
        server_base_url=None,
        long_message_chunk_size=None,
        encryption=False,
        encryption_private_key=None,
        encryption_public_key=None,
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
        # Configurable logger: None = silent, custom object = use it,
        # _UNSET/not provided = module-level default logger
        if silent and logger is _UNSET:
            logger = None  # silent mode implies no logging
        self._log = _make_logger(logger, globals()["logger"])
        self.default_context = default_context or {}
        self._method_annotations = weakref.WeakKeyDictionary()
        self._max_message_buffer_size = max_message_buffer_size
        self._chunk_store = {}
        self._method_timeout = 30 if method_timeout is None else method_timeout
        self._rintf_timeout = 10 if rintf_timeout is None else rintf_timeout
        self._remote_logger = self._log
        self._server_base_url = server_base_url
        self.loop = loop or asyncio.get_event_loop()
        self._long_message_chunk_size = long_message_chunk_size or CHUNK_SIZE
        self._session_gc_task = None
        self._session_ttl = 10 * 60  # 10 minutes default TTL for interface sessions
        self._background_tasks = set()
        self._client_disconnected_subscription = None
        self._bound_handle_client_disconnected = None
        super().__init__(self._remote_logger)

        # Set up exception handler for unhandled asyncio futures
        def handle_exception(loop, context):
            exception = context.get("exception")
            if isinstance(exception, Exception):
                # Check if this is a "Method not found" error that we can ignore
                if "Method not found" in str(exception) or "Session not found" in str(
                    exception
                ):
                    self._log.debug(
                        "Ignoring expected method/session not found error: %s",
                        exception,
                    )
                else:
                    self._log.debug("Unhandled asyncio exception: %s", context)
            else:
                self._log.debug("Unhandled asyncio exception: %s", context)

        # Only set the exception handler if we haven't already set one
        if not hasattr(self.loop, "_hypha_exception_handler_set"):
            self.loop.set_exception_handler(handle_exception)
            self.loop._hypha_exception_handler_set = True

        self._services = {}
        self._object_store = {
            "services": self._services,
        }
        # Index: target_id -> set of top-level session keys for fast cleanup
        self._target_id_index = {}
        # Index: allowed_caller -> set of _rintf service IDs for lifecycle cleanup
        self._rintf_caller_index = {}
        # Track last known manager_id for stale call rejection on reconnection
        self._last_manager_id = None

        # Encryption support (X25519 ECDH + AES-256-GCM)
        self._encryption_enabled = False
        self._encryption_private_key = None
        self._encryption_public_key = None
        if encryption:
            if not HAS_CRYPTO:
                raise ImportError(
                    "The 'cryptography' package is required for encryption. "
                    "Install it with: pip install cryptography"
                )
            if encryption_private_key and encryption_public_key:
                self._encryption_private_key = encryption_private_key
                self._encryption_public_key = encryption_public_key
            else:
                self._encryption_private_key, self._encryption_public_key = (
                    generate_encryption_keypair()
                )
            self._encryption_enabled = True
            self._log.info("Encryption enabled with X25519 keypair")

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
            self.on("peer_not_found", self._handle_peer_not_found)

            assert hasattr(connection, "emit_message") and hasattr(
                connection, "on_message"
            )
            assert hasattr(connection, "manager_id"), "Connection must have manager_id"
            self._emit_message = connection.emit_message
            connection.on_message(self._on_message)
            self._connection = connection

            async def on_connected(connection_info):
                if not self._silent and self._connection.manager_id:
                    # Immediately reject all pending calls targeting the old manager.
                    # After server restart, the manager_id changes. Any in-flight RPC
                    # calls to the old manager will never get a response (the server
                    # silently drops messages to unknown */{id} targets). Rejecting
                    # them here avoids waiting for the full method timeout (~10-30s).
                    current_manager_id = self._connection.manager_id
                    if (
                        self._last_manager_id
                        and self._last_manager_id != current_manager_id
                    ):
                        old_target = f"*/{self._last_manager_id}"
                        cleaned = self._cleanup_sessions_for_client(old_target)
                        if cleaned > 0:
                            self._log.info(
                                f"Rejected {cleaned} stale call(s) to old manager {self._last_manager_id}"
                            )
                    self._last_manager_id = current_manager_id

                    self._log.info("Connection established, reporting services...")
                    try:
                        # Get fresh manager service (one RPC roundtrip, ~50-100ms)
                        manager = await self.get_manager_service(
                            {"timeout": 20, "case_conversion": "snake"}
                        )

                        # Fire manager_refreshed IMMEDIATELY — before service
                        # re-registration. This allows connect_to_server's wm proxy
                        # to be updated as soon as possible, minimizing the window
                        # where stale methods exist.
                        self._fire("manager_refreshed", {"manager": manager})

                        services_count = len(self._services)
                        registered_count = 0
                        failed_services = []

                        # Use timeout for service registration to prevent hanging
                        service_registration_timeout = self._method_timeout or 30

                        for service in list(self._services.values()):
                            # Skip local-only services (e.g. _rintf_ callback
                            # proxies) — they must never be registered with the
                            # server; doing so creates zombie entries in Redis.
                            svc_cfg = service.get("config") or {}
                            if isinstance(svc_cfg, dict) and svc_cfg.get("_local_only"):
                                services_count -= 1
                                continue
                            try:
                                service_info = self._extract_service_info(service)
                                await asyncio.wait_for(
                                    manager.register_service(service_info),
                                    timeout=service_registration_timeout,
                                )
                                registered_count += 1
                                self._log.debug(
                                    f"Successfully registered service: {service.get('id', 'unknown')}"
                                )
                            except asyncio.TimeoutError:
                                failed_services.append(service.get("id", "unknown"))
                                self._log.error(
                                    f"Timeout registering service {service.get('id', 'unknown')}"
                                )
                            except Exception as service_error:
                                failed_services.append(service.get("id", "unknown"))
                                self._log.error(
                                    f"Failed to register service {service.get('id', 'unknown')}: {service_error}"
                                )

                        if registered_count == services_count:
                            self._log.info(
                                f"Successfully registered all {registered_count} services with the server"
                            )
                        else:
                            self._log.warning(
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
                                # Clean up previous subscription and handler to prevent
                                # duplicates on reconnection (listener leak fix)
                                if self._client_disconnected_subscription:
                                    try:
                                        if hasattr(
                                            self._client_disconnected_subscription,
                                            "unsubscribe",
                                        ):
                                            self._client_disconnected_subscription.unsubscribe()
                                    except Exception as e:
                                        self._log.debug(
                                            f"Error unsubscribing old client_disconnected: {e}"
                                        )
                                    self._client_disconnected_subscription = None
                                if self._bound_handle_client_disconnected:
                                    try:
                                        self.off(
                                            "client_disconnected",
                                            self._bound_handle_client_disconnected,
                                        )
                                    except ValueError:
                                        # Handler may not be in list if previous
                                        # subscription setup was interrupted
                                        pass
                                    self._bound_handle_client_disconnected = None

                                self._log.debug(
                                    "Subscribing to client_disconnected events"
                                )

                                # Store handler at instance level so it can be
                                # properly removed on reconnection or close
                                async def handle_client_disconnected(event):
                                    # Event data: {"id": client_id, "workspace": ws}
                                    # May arrive as top-level or nested in event.data
                                    data = event.get("data", event)
                                    raw_id = data.get("id") or event.get("client")
                                    workspace = data.get("workspace")
                                    if raw_id and workspace:
                                        client_id = f"{workspace}/{raw_id}"
                                    elif raw_id:
                                        client_id = raw_id
                                    else:
                                        return
                                    self._log.debug(
                                        f"Client {client_id} disconnected, cleaning up sessions"
                                    )
                                    await self._handle_client_disconnected(
                                        client_id
                                    )

                                self._bound_handle_client_disconnected = (
                                    handle_client_disconnected
                                )

                                # Subscribe to the event topic first with timeout
                                self._client_disconnected_subscription = (
                                    await asyncio.wait_for(
                                        manager.subscribe(["client_disconnected"]),
                                        timeout=service_registration_timeout,
                                    )
                                )

                                # Then register the local event handler
                                self.on(
                                    "client_disconnected",
                                    self._bound_handle_client_disconnected,
                                )

                                self._log.debug(
                                    "Successfully subscribed to client_disconnected events"
                                )
                            else:
                                self._log.debug(
                                    "Manager does not support subscribe method, skipping client_disconnected handling"
                                )
                                self._client_disconnected_subscription = None
                        except asyncio.TimeoutError:
                            self._log.warning(
                                "Timeout subscribing to client_disconnected events"
                            )
                            self._client_disconnected_subscription = None
                        except Exception as subscribe_error:
                            self._log.warning(
                                f"Failed to subscribe to client_disconnected events: {subscribe_error}"
                            )
                            self._client_disconnected_subscription = None

                    except Exception as manager_error:
                        self._log.error(
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
                    self._log.info("Connection established: %s", connection_info)
                if connection_info:
                    if connection_info.get("public_base_url"):
                        self._server_base_url = connection_info.get("public_base_url")
                    self._fire("connected", connection_info)

                # Start session GC task if not already running
                if self._session_gc_task is None or self._session_gc_task.done():
                    self._session_gc_task = asyncio.ensure_future(
                        self._session_gc_loop()
                    )

            connection.on_connected(on_connected)

            # Register disconnect handler to reject all pending RPC calls
            # This ensures no remote function call hangs forever when the connection drops
            # But only reject if reconnection is NOT enabled - during reconnection,
            # let the timer/timeout mechanism handle pending calls so they can
            # succeed after reconnection
            if hasattr(connection, "on_disconnected"):

                def on_connection_lost(reason=None):
                    # If reconnection is enabled, don't reject pending calls immediately
                    # The timeout mechanism will handle them if reconnection fails
                    if getattr(connection, "_enable_reconnect", False):
                        self._log.info(
                            "Connection lost (%s), reconnection enabled - pending calls will be handled by timeout",
                            reason,
                        )
                        return
                    self._log.warning(
                        "Connection lost (%s), rejecting all pending RPC calls",
                        reason,
                    )
                    self._reject_pending_calls(
                        f"Connection lost: {reason or 'unknown reason'}"
                    )

                connection.on_disconnected(on_connection_lost)

            task = self.loop.create_task(on_connected(None))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        else:

            async def _emit_message(_):
                self._log.info("No connection to emit message")

            self._emit_message = _emit_message

        self.NUMPY_MODULE = _numpy_module if _numpy_module is not None else False

    def get_public_key(self):
        """Return this client's X25519 public key as a 64-char hex string.

        Returns None if encryption is not enabled.
        """
        if not self._encryption_enabled or self._encryption_public_key is None:
            return None
        return self._encryption_public_key.hex()

    def register_codec(self, config: dict):
        """Register codec."""
        assert "name" in config
        assert "encoder" in config or "decoder" in config
        if "type" in config:
            for tp in list(self._codecs.keys()):
                codec = self._codecs[tp]
                if codec.type == config["type"] or tp == config["name"]:
                    self._log.debug("Removing duplicated codec: " + tp)
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
        # concatenate all the chunks efficiently using join (avoids O(n^2) copy)
        data = b"".join(data[i] for i in range(len(data)))
        self._log.debug("Processing message %s (size=%d)", key, len(data))
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
        """Handle incoming message using the two-segment msgpack protocol.

        Supports three message formats:
        1. JSON string: Single object (no extra segment possible)
        2. Msgpack bytes: Two-segment format [msgpack(main)][msgpack(extra)]
        3. Direct dict: Already deserialized (local/in-process messages)

        The two-segment msgpack format allows separating routing information
        from payload data for efficiency and modularity.
        """
        if isinstance(message, str):
            # JSON format: Single object only (extra segment not supported)
            main = json.loads(message)
            # Add trusted context to the method call
            main = self._add_context_to_message(main)
            self._fire(main["type"], main)

        elif isinstance(message, bytes):
            # TWO-SEGMENT MSGPACK: Parse concatenated msgpack objects
            unpacker = msgpack.Unpacker(
                io.BytesIO(message),
                max_buffer_size=max(512000, self._long_message_chunk_size * 2),
            )

            # First segment: Main message (routing, type, target, etc.)
            main = unpacker.unpack()
            # Add trusted context to the method call
            main = self._add_context_to_message(main)

            # Second segment: Extra data (args, kwargs, promise) - optional
            try:
                extra = unpacker.unpack()
                main.update(extra)  # Merge extra data into main message
            except msgpack.exceptions.OutOfData:
                # No extra segment - this is valid, extra is optional
                pass

            self._fire(main["type"], main)

        elif isinstance(message, dict):
            # Direct dict: Already deserialized (local/in-process messages)
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
                try:
                    heartbeat_task = value.get("heartbeat_task")
                    if (
                        heartbeat_task
                        and not getattr(heartbeat_task, "done", lambda: True)()
                    ):
                        heartbeat_task.cancel()
                except Exception as e:
                    self._log.debug("Error cancelling heartbeat task: %s", e)
                try:
                    timer = value.get("timer")
                    if timer and getattr(timer, "started", False):
                        timer.clear()
                except Exception as e:
                    self._log.debug("Error clearing timer: %s", e)
                self._close_sessions(value)

    def close(self):
        """Close the RPC connection and clean up resources."""
        # Cancel session GC task
        if self._session_gc_task and not self._session_gc_task.done():
            self._session_gc_task.cancel()
            self._session_gc_task = None

        # Cancel any tracked background tasks (chunk sends, etc.)
        for task in list(self._background_tasks):
            if not task.done():
                task.cancel()
        # Clear the set immediately to release task references
        self._background_tasks.clear()

        # Give the event loop a chance to process cancellations
        # This helps release coroutine references captured in tasks
        if hasattr(self, "loop") and self.loop and not self.loop.is_closed():
            try:
                # Process any pending callbacks without blocking
                self.loop.call_soon(lambda: None)
                # If loop is not running, we can't force processing
                # The tasks will be cleaned up when the loop eventually runs
            except Exception as e:
                self._log.debug(f"Error processing loop callbacks during close: {e}")

        # Clean up all pending sessions before closing
        self._cleanup_on_disconnect()
        self._close_sessions(self._object_store)

        # Cancel all tracked background tasks (emit tasks, chunk sends, etc.)
        for task in list(self._background_tasks):
            if not task.done():
                task.cancel()
        self._background_tasks.clear()

        # Remove the local event handler for client_disconnected
        # Note: Actual unsubscription from server is done in async disconnect() method
        if self._client_disconnected_subscription:
            try:
                if hasattr(
                    self._client_disconnected_subscription, "unsubscribe"
                ):
                    self._client_disconnected_subscription.unsubscribe()
            except Exception as e:
                self._log.debug(
                    f"Error unsubscribing client_disconnected: {e}"
                )
            self._client_disconnected_subscription = None
        if self._bound_handle_client_disconnected:
            try:
                self.off(
                    "client_disconnected",
                    self._bound_handle_client_disconnected,
                )
            except Exception as e:
                self._log.debug(
                    f"Error removing client_disconnected handler: {e}"
                )
            self._bound_handle_client_disconnected = None

        # Clear connection callbacks to break circular references
        # The on_connected callback captures 'self' in its closure
        if hasattr(self, "_connection") and self._connection:
            # Clear event handlers on the connection that might hold references
            if hasattr(self._connection, "_on_connected_handler"):
                self._connection._on_connected_handler = None
            if hasattr(self._connection, "_on_message_handler"):
                self._connection._on_message_handler = None
            if hasattr(self._connection, "_on_disconnected_handler"):
                self._connection._on_disconnected_handler = None

        # Clear connection reference to break circular references
        if hasattr(self, "_connection"):
            self._connection = None

        # Clear emit_message reference to break circular references
        if hasattr(self, "_emit_message"):
            self._emit_message = None

        # Clear event loop exception handler to break closure reference
        # The handle_exception closure defined in __init__ captures 'self'
        if hasattr(self, "loop") and self.loop:
            try:
                self.loop.set_exception_handler(None)
            except Exception as e:
                self._log.debug(f"Error clearing exception handler: {e}")

        self._fire("disconnected")

        # Cancel all fire-and-forget tasks from MessageEmitter._fire()
        # These tasks hold references to coroutines that capture 'self' in closures
        if hasattr(self, "_fire_and_forget_tasks"):
            for task in list(self._fire_and_forget_tasks):
                if not task.done():
                    task.cancel()
            self._fire_and_forget_tasks.clear()

        # Clear all event handlers to prevent circular references
        # This must be done AFTER firing disconnected event
        self._event_handlers.clear()

        # Clear services to release bound method references
        # Services contain bound methods (self._ping, self.get_local_service, etc.)
        # which hold references to the RPC instance, preventing GC
        if hasattr(self, "_services"):
            self._services.clear()

        # Clear the entire object store to release all references
        if hasattr(self, "_object_store"):
            self._object_store.clear()

        # Clear additional data structures that may hold references
        if hasattr(self, "_target_id_index"):
            self._target_id_index.clear()
        if hasattr(self, "_rintf_caller_index"):
            self._rintf_caller_index.clear()
        if hasattr(self, "_chunk_store"):
            self._chunk_store.clear()
        if hasattr(self, "_codecs"):
            self._codecs.clear()
        # Note: _method_annotations is a WeakKeyDictionary and will auto-cleanup
        # but we can explicitly clear it to help GC
        if hasattr(self, "_method_annotations"):
            self._method_annotations.clear()

    async def disconnect(self):
        """Disconnect."""
        connection = getattr(self, "_connection", None)

        # Clear the subscription flag — the server automatically cleans up
        # subscriptions when the websocket closes, no need to explicitly
        # unsubscribe (which would require contacting the manager and
        # produces a noisy warning if the manager is already gone).
        if hasattr(self, "_client_disconnected_subscription"):
            self._client_disconnected_subscription = None

        #  Store background tasks before close() for proper cleanup
        tasks_to_cleanup = (
            list(self._background_tasks) if hasattr(self, "_background_tasks") else []
        )

        # Include the session GC task so it gets properly awaited after cancel
        if (
            hasattr(self, "_session_gc_task")
            and self._session_gc_task
            and not self._session_gc_task.done()
        ):
            tasks_to_cleanup.append(self._session_gc_task)

        # Also store fire-and-forget tasks from MessageEmitter._fire()
        if hasattr(self, "_fire_and_forget_tasks"):
            tasks_to_cleanup.extend(list(self._fire_and_forget_tasks))

        self.close()

        # Await cancelled tasks to ensure coroutines are properly cleaned up
        # This prevents memory leaks from task/coroutine references
        if tasks_to_cleanup:
            try:
                # Gather cancelled tasks with short timeout
                # Most will raise CancelledError, which is expected
                await asyncio.gather(*tasks_to_cleanup, return_exceptions=True)
            except Exception as e:
                self._log.debug(f"Error awaiting cancelled tasks during disconnect: {e}")

        # Disconnect the underlying connection if it exists
        if connection:
            try:
                await connection.disconnect()
            except Exception as e:
                self._log.debug(f"Error disconnecting underlying connection: {e}")

    def _unregister_rintf_service(self, service_id, allowed_caller=None):
        """Unregister a single _rintf service and remove it from the caller index.

        Safe to call even if the service was already removed.
        """
        if service_id in self._services:
            self._remove_service_annotations(self._services[service_id])
            del self._services[service_id]
        # Remove from caller index
        if allowed_caller and allowed_caller in self._rintf_caller_index:
            self._rintf_caller_index[allowed_caller].discard(service_id)
            if not self._rintf_caller_index[allowed_caller]:
                del self._rintf_caller_index[allowed_caller]

    def _cleanup_rintf_for_caller(self, client_id):
        """Remove all _rintf services whose allowed caller is the given client.

        This is the passive lifecycle cleanup: when a client disconnects,
        any _rintf callbacks that only it could invoke become dead resources.
        Returns the number of _rintf services cleaned up.
        """
        service_ids = self._rintf_caller_index.pop(client_id, None)
        if not service_ids:
            return 0
        cleaned = 0
        for sid in list(service_ids):
            if sid in self._services:
                self._remove_service_annotations(self._services[sid])
                del self._services[sid]
                cleaned += 1
        return cleaned

    async def _handle_client_disconnected(self, client_id):
        """Handle cleanup when a remote client disconnects."""
        try:
            self._log.debug(f"Handling disconnection for client: {client_id}")

            # Clean up all sessions for the disconnected client
            sessions_cleaned = self._cleanup_sessions_for_client(client_id)

            # Clean up _rintf services whose allowed caller is the disconnected client
            rintf_cleaned = self._cleanup_rintf_for_caller(client_id)

            if sessions_cleaned > 0 or rintf_cleaned > 0:
                self._log.debug(
                    f"Cleaned up {sessions_cleaned} sessions and "
                    f"{rintf_cleaned} _rintf services for disconnected client: {client_id}"
                )

            # Fire an event to notify about the client disconnection
            self._fire(
                "remote_client_disconnected",
                {
                    "client_id": client_id,
                    "sessions_cleaned": sessions_cleaned,
                    "rintf_cleaned": rintf_cleaned,
                },
            )

        except Exception as e:
            self._log.error(f"Error handling client disconnection for {client_id}: {e}")

    def _remove_from_target_id_index(self, session_id):
        """Remove a session from the target_id index.

        Call this whenever a session is removed from _object_store to keep
        the index consistent and prevent stale entries from accumulating.
        """
        top_key = session_id.split(".")[0]
        # Look up the session's target_id from the store before deletion
        session = self._object_store.get(top_key)
        if isinstance(session, dict):
            target_id = session.get("target_id")
            if target_id and target_id in self._target_id_index:
                self._target_id_index[target_id].discard(top_key)
                if not self._target_id_index[target_id]:
                    del self._target_id_index[target_id]

    def _cleanup_session_entry(self, session, reject_reason=None):
        """Clean up a single session entry: reject promise, clear timer, cancel heartbeat.

        Centralizes the cleanup logic used by multiple methods.
        """
        if not isinstance(session, dict):
            return
        if reject_reason and "reject" in session and callable(session["reject"]):
            try:
                session["reject"](ConnectionError(reject_reason))
            except Exception as e:
                self._log.debug("Error rejecting session: %s", e)
        heartbeat = session.get("heartbeat_task")
        if heartbeat and not getattr(heartbeat, "done", lambda: True)():
            try:
                heartbeat.cancel()
            except Exception:
                pass
        timer = session.get("timer")
        if timer and getattr(timer, "started", False):
            try:
                timer.clear()
            except Exception:
                pass

    def _cleanup_sessions_for_client(self, client_id):
        """Clean up all sessions for a specific client using target_id index."""
        sessions_cleaned = 0

        session_keys = self._target_id_index.pop(client_id, None)
        if not session_keys:
            return 0

        reason = f"Client disconnected: {client_id}"
        for session_key in session_keys:
            session = self._object_store.get(session_key)
            if not isinstance(session, dict):
                continue
            if session.get("target_id") != client_id:
                continue

            self._cleanup_session_entry(session, reason)
            self._object_store.pop(session_key, None)
            sessions_cleaned += 1
            self._log.debug("Cleaned up session: %s", session_key)

        return sessions_cleaned

    def _reject_pending_calls(self, reason="Connection lost"):
        """Reject all pending RPC calls when the connection is lost.

        Does NOT remove sessions (connection might be re-established).
        """
        try:
            rejected_count = 0
            for key in list(self._object_store.keys()):
                if key in ("services", "message_cache"):
                    continue
                value = self._object_store.get(key)
                if isinstance(value, dict):
                    if "reject" in value and callable(value["reject"]):
                        rejected_count += 1
                    self._cleanup_session_entry(value, reason)
            if rejected_count > 0:
                self._log.warning(
                    "Rejected %d pending RPC call(s) due to: %s",
                    rejected_count,
                    reason,
                )
        except Exception as e:
            self._log.error("Error rejecting pending calls: %s", e)

    def _cleanup_on_disconnect(self):
        """Clean up all pending sessions when the local RPC disconnects."""
        try:
            self._log.debug("Cleaning up all sessions due to local RPC disconnection")

            keys_to_delete = []
            for key in list(self._object_store.keys()):
                if key == "services":
                    continue
                value = self._object_store.get(key)
                if isinstance(value, dict):
                    self._cleanup_session_entry(value, "RPC connection closed")
                keys_to_delete.append(key)

            for key in keys_to_delete:
                self._object_store.pop(key, None)

            self._target_id_index.clear()
            self._rintf_caller_index.clear()
        except Exception as e:
            self._log.error("Error during cleanup on disconnect: %s", e)

    async def _session_gc_loop(self):
        """Periodically sweep sessions with no activity for longer than _session_ttl."""
        try:
            while True:
                await asyncio.sleep(self._session_ttl / 2)
                now = time.time()
                keys_to_gc = []
                for key in list(self._object_store.keys()):
                    if key in ("services", "message_cache"):
                        continue
                    session = self._object_store.get(key)
                    if not isinstance(session, dict):
                        continue
                    # Use last-activity time if available, fall back to creation time
                    last_activity = session.get(
                        "_last_activity_at", session.get("_created_at")
                    )
                    if last_activity is None:
                        continue
                    if now - last_activity > self._session_ttl:
                        # Skip sessions with active timers (in use via heartbeat)
                        timer = session.get("timer")
                        if timer and hasattr(timer, "started") and timer.started:
                            continue
                        # Skip sessions with unsettled promises
                        if callable(session.get("resolve")) or callable(
                            session.get("reject")
                        ):
                            continue
                        keys_to_gc.append(key)
                for key in keys_to_gc:
                    session = self._object_store.get(key)
                    if session is None:
                        continue
                    self._log.debug("Session GC: cleaning up expired session %s", key)
                    if "reject" in session and callable(session["reject"]):
                        try:
                            session["reject"](
                                TimeoutError(
                                    f"Session expired (TTL={self._session_ttl}s): {key}"
                                )
                            )
                        except Exception:
                            pass
                    self._cleanup_session_resources(session)
                    self._remove_from_target_id_index(key)
                    try:
                        del self._object_store[key]
                    except KeyError:
                        pass
                if keys_to_gc:
                    self._log.debug(
                        "Session GC: cleaned up %d expired sessions", len(keys_to_gc)
                    )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._log.debug("Session GC loop error: %s", e)

    async def get_manager_service(self, config=None, max_retries=20):
        """Get remote root service with retry."""
        config = config or {}
        assert self._connection.manager_id, "Manager id is not set"

        base_delay = 0.5
        max_delay = 10.0
        last_error = None

        for attempt in range(max_retries):
            try:
                svc = await self.get_remote_service(
                    f"*/{self._connection.manager_id}:default", config
                )
                return svc
            except Exception as e:
                last_error = e
                self._log.warning(
                    "Failed to get manager service (attempt %d/%d): %s",
                    attempt + 1,
                    max_retries,
                    e,
                )
                if attempt < max_retries - 1:
                    delay = min(base_delay * (2**attempt), max_delay)
                    await asyncio.sleep(delay)

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
            raise KeyError(f"Service not found: {service_id}")
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
        no_retry = config.pop("_no_retry", False)
        encryption_public_key_hex = config.pop("encryption_public_key", None)
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
            if not isinstance(svc, (dict, ObjectProxy)):
                raise TypeError(
                    f"Expected service dict from server, got {type(svc).__name__}: {svc!r}"
                )
            svc["id"] = service_uri
            if isinstance(svc, ObjectProxy):
                svc = svc.toDict()
            if encryption_public_key_hex:
                enc_pub_bytes = bytes.fromhex(encryption_public_key_hex)
                _apply_encryption_key_to_service(svc, enc_pub_bytes)
            if case_conversion:
                svc = convert_case(svc, case_conversion)
            result = RemoteService.fromDict(svc)
            if not no_retry:
                self._wrap_service_methods_with_retry(result, service_uri, config)
            return result
        except Exception as exp:
            self._log.warning("Failed to get remote service: %s: %s", service_id, exp)
            raise exp

    def _wrap_service_methods_with_retry(self, svc, service_uri, config):
        """Wrap each callable method on a RemoteService with transparent retry.

        When a method call fails with a stale service error (e.g. "Method expired
        or not found" after provider reconnection), the wrapper automatically
        re-fetches the service and retries the call once.
        """
        rpc = self
        # Use dict.keys() directly to avoid shadowing by service methods
        # named "keys", "items", etc. (RemoteService extends dict)
        for key in list(dict.keys(svc)):
            val = dict.__getitem__(svc, key)
            if not callable(val):
                continue
            method_name = key
            original_method = val

            async def _retry_wrapper(
                *args,
                _original=original_method,
                _name=method_name,
                **kwargs,
            ):
                try:
                    return await _original(*args, **kwargs)
                except Exception as e:
                    if _is_stale_service_error(e):
                        logger.info(
                            "Stale service error on %s:%s, refreshing and retrying: %s",
                            service_uri,
                            _name,
                            e,
                        )
                        retry_config = {**config, "_no_retry": True}
                        refreshed = await rpc.get_remote_service(
                            service_uri, retry_config
                        )
                        new_method = getattr(refreshed, _name, None)
                        if new_method is None:
                            raise
                        return await new_method(*args, **kwargs)
                    raise

            # Preserve metadata from the original method
            _retry_wrapper.__rpc_object__ = getattr(
                original_method, "__rpc_object__", None
            )
            _retry_wrapper.__name__ = getattr(
                original_method, "__name__", method_name
            )
            _retry_wrapper.__doc__ = getattr(original_method, "__doc__", None)
            _retry_wrapper.__schema__ = getattr(
                original_method, "__schema__", None
            )
            dict.__setitem__(svc, key, _retry_wrapper)

    def _annotate_service_methods(
        self,
        a_object,
        object_id,
        require_context=False,
        run_in_executor=False,
        visibility="protected",
        authorized_workspaces=None,
        trusted_keys=None,
        rintf_allowed_caller=None,
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
                "trusted_keys": trusted_keys,
                "rintf_allowed_caller": rintf_allowed_caller,
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
                    trusted_keys=trusted_keys,
                    rintf_allowed_caller=rintf_allowed_caller,
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
                raise ValueError(
                    "authorized_workspaces must be a list of workspace ids"
                )
            for ws_id in authorized_workspaces:
                if not isinstance(ws_id, str):
                    raise ValueError(
                        f"Each workspace id in authorized_workspaces must be a string, got {type(ws_id)}"
                    )
        trusted_keys_hex = api["config"].get("trusted_keys")
        trusted_keys = None
        if trusted_keys_hex:
            if not isinstance(trusted_keys_hex, (list, tuple)):
                raise ValueError(
                    "trusted_keys must be a list of hex-encoded public keys"
                )
            trusted_keys = set()
            for key_hex in trusted_keys_hex:
                if not isinstance(key_hex, str) or len(key_hex) != 64:
                    raise ValueError(
                        f"Each trusted key must be a 64-char hex string, "
                        f"got: {key_hex!r}"
                    )
                trusted_keys.add(key_hex)
        rintf_allowed_caller = api["config"].get("_rintf_allowed_caller")
        self._annotate_service_methods(
            api,
            api["id"],
            require_context=require_context,
            run_in_executor=run_in_executor,
            visibility=visibility,
            authorized_workspaces=authorized_workspaces,
            trusted_keys=trusted_keys,
            rintf_allowed_caller=rintf_allowed_caller,
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
        exclude_keys = [
            "id",
            "config",
            "name",
            "description",
            "type",
            "docs",
            "app_id",
            "service_schema",
        ]
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

    def _remove_service_annotations(self, service_obj):
        """Remove _method_annotations entries for all callables in a service."""
        if isinstance(service_obj, dict):
            for val in service_obj.values():
                if callable(val):
                    try:
                        del self._method_annotations[val]
                    except KeyError:
                        pass
                elif isinstance(val, (dict, list, tuple)):
                    self._remove_service_annotations(val)
        elif isinstance(service_obj, (list, tuple)):
            for val in service_obj:
                if callable(val):
                    try:
                        del self._method_annotations[val]
                    except KeyError:
                        pass
                elif isinstance(val, (dict, list, tuple)):
                    self._remove_service_annotations(val)

    async def unregister_service(self, service: Union[dict, str], notify: bool = True):
        """Unregister a service."""
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
        # Auto-detect _rintf services (local-only, never registered with server)
        if service_id.startswith("_rintf_"):
            notify = False
            # Also clean up from the caller index
            for caller, sids in list(self._rintf_caller_index.items()):
                sids.discard(service_id)
                if not sids:
                    del self._rintf_caller_index[caller]
        if notify:
            manager = await self.get_manager_service(
                {"timeout": 20, "case_conversion": "snake"}
            )
            await manager.unregister_service(service_id)
        # Clean up method annotations before removing the service
        self._remove_service_annotations(self._services[service_id])
        del self._services[service_id]

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
                self._log.debug(
                    "Invalid state error in callback: %s (context: %s)",
                    method_id,
                    description,
                )
            except Exception as exp:
                self._log.error(
                    "Error in callback: %s (context: %s), error: %s",
                    method_id,
                    description,
                    exp,
                )
            finally:
                if timer and timer.started:
                    timer.clear()
                if clear_after_called and self._get_session_store(
                    session_id, create=False
                ):
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
            self._log.debug("Cannot cleanup session: session_id is empty")
            return

        try:
            store = self._get_session_store(session_id, create=False)
            if not store:
                self._log.debug(f"Session {session_id} not found for cleanup")
                return

            should_cleanup = False

            # Promise sessions: let the promise manager decide cleanup
            if "_promise_manager" in store:
                try:
                    promise_manager = store["_promise_manager"]
                    if hasattr(
                        promise_manager, "should_cleanup_on_callback"
                    ) and promise_manager.should_cleanup_on_callback(callback_name):
                        if hasattr(promise_manager, "settle"):
                            promise_manager.settle()
                        should_cleanup = True
                        self._log.debug(
                            f"Promise session {session_id} settled and marked for cleanup"
                        )
                except Exception as e:
                    self._log.warning(
                        f"Error in promise manager cleanup for {session_id}: {e}"
                    )
                    # Still try to cleanup if promise manager fails
                    should_cleanup = True
            else:
                # Regular sessions: cleanup immediately
                should_cleanup = True
                self._log.debug(f"Regular session {session_id} marked for cleanup")

            if should_cleanup:
                self._delete_session_completely(session_id)

        except Exception as e:
            self._log.error(f"Unexpected error in session cleanup for {session_id}: {e}")
            # Try a basic cleanup as fallback
            try:
                self._delete_session_safely(session_id)
            except Exception as fallback_error:
                self._log.error(
                    f"Fallback cleanup also failed for {session_id}: {fallback_error}"
                )

    def _delete_session_completely(self, session_id):
        """Completely delete a session and clean up empty parent containers."""
        if not session_id:
            return

        try:
            levels = session_id.split(".")

            # Clean up target_id index before deleting the session
            self._remove_from_target_id_index(session_id)

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
                    self._log.debug(f"Deleted top-level session: {session_id}")
                else:
                    self._log.debug(f"Top-level session {session_id} already deleted")
            else:
                # Nested session - navigate and delete safely
                current_store = self._object_store
                path_exists = True

                # Navigate to parent container
                for i, level in enumerate(levels[:-1]):
                    if level not in current_store:
                        path_exists = False
                        self._log.debug(
                            f"Parent path broken at level '{level}' for session {session_id}"
                        )
                        break
                    if not isinstance(current_store[level], dict):
                        path_exists = False
                        self._log.debug(
                            f"Non-dict container at level '{level}' for session {session_id}"
                        )
                        break
                    current_store = current_store[level]

                if path_exists and levels[-1] in current_store:
                    session_data = current_store[levels[-1]]

                    # Clear resources before deletion
                    if isinstance(session_data, dict):
                        self._cleanup_session_resources(session_data)

                    # Delete the session
                    del current_store[levels[-1]]
                    self._log.debug(f"Deleted nested session: {session_id}")

                    # Clean up empty parent containers from bottom up
                    self._cleanup_empty_parent_containers(levels[:-1])
                else:
                    self._log.debug(
                        f"Nested session {session_id} already deleted or path invalid"
                    )

        except KeyError as e:
            self._log.debug(f"Session {session_id} already deleted: {e}")
        except Exception as e:
            self._log.warning(f"Error during session cleanup for {session_id}: {e}")

    def _delete_session_safely(self, session_id):
        """Fallback method for basic session deletion without full cleanup."""
        try:
            levels = session_id.split(".")
            if len(levels) == 1 and levels[0] in self._object_store:
                # Clean up target_id index before deleting
                self._remove_from_target_id_index(session_id)
                del self._object_store[levels[0]]
                self._log.debug(f"Fallback cleanup: deleted session {session_id}")
        except Exception as e:
            self._log.error(f"Fallback cleanup failed for {session_id}: {e}")

    def _cleanup_session_resources(self, session_dict):
        """Clean up resources within a session (timers, heartbeats, tasks) before deletion."""
        if not isinstance(session_dict, dict):
            return

        # Reuse common timer/heartbeat cleanup (without rejecting promise)
        self._cleanup_session_entry(session_dict)

        # Also cancel any other async tasks stored with _task suffix
        for key, value in list(session_dict.items()):
            if (
                key.endswith("_task")
                and key != "heartbeat_task"
                and hasattr(value, "cancel")
            ):
                try:
                    if not value.done():
                        value.cancel()
                except Exception:
                    pass

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
                        self._log.debug(
                            f"Parent container path broken at '{level}', stopping cleanup"
                        )
                        return  # Path doesn't exist, nothing to clean
                    if not isinstance(container[level], dict):
                        self._log.debug(
                            f"Non-dict parent container at '{level}', stopping cleanup"
                        )
                        return
                    container = container[level]

                target_key = path[-1]
                if target_key in container and isinstance(container[target_key], dict):
                    # Only delete if the container is empty (excluding system keys)
                    remaining_keys = [
                        k
                        for k in container[target_key].keys()
                        if k not in ["services", "message_cache"]
                    ]
                    if not remaining_keys:
                        del container[target_key]
                        self._log.debug(
                            f"Cleaned up empty parent container: {'.'.join(path)}"
                        )
                    else:
                        # Container has content, stop cleanup
                        self._log.debug(
                            f"Parent container {'.'.join(path)} has content, stopping cleanup"
                        )
                        break

        except Exception as e:
            self._log.debug(f"Error cleaning empty parent containers: {e}")

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
                self._log.debug(f"Force cleaned session: {session_key}")
            except Exception as e:
                self._log.warning(f"Error in force cleanup of session {session_key}: {e}")

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
            "session_ids": [],
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
            self._log.warning(
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
                    self._log.debug(
                        "Sending chunk %d/%d (total=%d bytes)",
                        idx + 1,
                        chunk_num,
                        total_size,
                    )

            tasks = []
            for idx in range(chunk_num):
                start_byte = idx * self._long_message_chunk_size
                chunk = package[start_byte : start_byte + self._long_message_chunk_size]
                t = asyncio.create_task(append_chunk(idx, chunk))
                self._background_tasks.add(t)
                t.add_done_callback(self._background_tasks.discard)
                tasks.append(t)

            # Wait for all chunks to finish uploading
            try:
                await asyncio.gather(*tasks)
            except Exception as error:
                # Cancel remaining tasks on failure
                for t in tasks:
                    if not t.done():
                        t.cancel()
                # If any chunk fails, clean up the message cache
                try:
                    await message_cache.remove(message_id)
                except Exception as cleanup_error:
                    self._log.error(
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
                self._log.debug(
                    "Sending chunk %d/%d (%d bytes)",
                    idx + 1,
                    chunk_num,
                    total_size,
                )
        await message_cache.process(message_id, bool(session_id))
        self._log.debug(
            "All chunks (%d bytes) sent in %d s", total_size, time.time() - start_time
        )

    def emit(self, main_message, extra_data=None):
        """Emit a message using the two-segment msgpack protocol.

        The two-segment format concatenates two separate msgpack objects:
        1. Main message: Contains routing, type, and control information
        2. Extra data: Contains args, kwargs, and optional promise data

        Why two segments?
        - Separation of concerns: routing info separate from payload
        - Efficiency: Can parse routing without deserializing large payloads
        - Backward compatibility: Extra segment is optional

        Format: [msgpack(main)] + [msgpack(extra)]  # Two separate msgpack objects concatenated

        IMPORTANT: This only works with msgpack/binary transport. JSON format loses
        the extra segment because JSON can't represent concatenated objects.
        """
        assert (
            isinstance(main_message, dict) and "type" in main_message
        ), "Invalid message, must be an object with a `type` fields"
        if "to" not in main_message:
            self._fire(main_message["type"], main_message)
            return

        # TWO-SEGMENT PROTOCOL: Encode main message (routing, type, target, etc.)
        message_package = msgpack.packb(main_message)

        # Optionally append extra data segment (args, kwargs, promise)
        # This creates: [msgpack_obj_1][msgpack_obj_2] - two concatenated msgpack objects
        if extra_data:
            message_package = message_package + msgpack.packb(extra_data)

        total_size = len(message_package)
        if total_size > self._long_message_chunk_size + 1024:
            self._log.warning(f"Sending large message (size={total_size})")
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
        self._log.info("RPC Info: %s", info)

    def _error(self, error):
        self._log.error("RPC Error: %s", error)

    def _handle_peer_not_found(self, data):
        """Handle server notification that target peer is not connected.

        When the server detects that an RPC message targets a disconnected
        client, it sends back a 'peer_not_found' message instead of silently
        dropping it.  This allows pending calls to fail immediately rather
        than waiting for the full method timeout.
        """
        session_id = data.get("session")
        peer_id = data.get("peer_id", data.get("from", "unknown"))
        error_msg = data.get("error", f"Peer {peer_id} is not connected")
        self._log.debug("Peer not found: %s (session=%s)", peer_id, session_id)

        # Reject the specific pending call identified by session_id
        if session_id:
            session = self._object_store.get(session_id)
            if session and isinstance(session, dict):
                self._cleanup_session_entry(session, error_msg)
                if session_id in self._object_store:
                    del self._object_store[session_id]
                self._remove_from_target_id_index(session_id)

        # Also clean up all other sessions targeting this peer
        if peer_id:
            self._cleanup_sessions_for_client(peer_id)

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
                        self._log.debug("Returned value (%s): %s", method_name, result)
                except Exception as err:
                    traceback_error = traceback.format_exc()
                    if reject is not None:
                        return reject(Exception(format_traceback(traceback_error)))
                    else:
                        self._log.error(
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
                if not (service.get("config") or {}).get("_local_only")
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

            # E2E Decryption: if extra_data was encrypted, decrypt it now
            caller_encryption_pub = None
            if "_enc" in data and self._encryption_enabled:
                enc_info = data["_enc"]
                if isinstance(enc_info, dict) and enc_info.get("v") == 2:
                    caller_encryption_pub = bytes(enc_info["pub"])
                    nonce = bytes(enc_info["nonce"])
                    ciphertext = bytes(data["data"])
                    try:
                        plaintext = decrypt_payload(
                            self._encryption_private_key,
                            caller_encryption_pub,
                            nonce,
                            ciphertext,
                        )
                    except Exception:
                        raise PermissionError(
                            f"Decryption failed for method {method_name}. "
                            "Invalid key or tampered data."
                        )
                    # Unpack the original extra_data and merge back
                    original_extra = msgpack.unpackb(plaintext)
                    # Remove encryption envelope, merge original fields
                    del data["_enc"]
                    del data["data"]
                    data.update(original_extra)
                    # Store caller's encryption pub in context
                    data["ctx"]["encryption"] = True
                    data["ctx"]["caller_public_key"] = public_key_to_hex(
                        caller_encryption_pub
                    )

            if "promise" in data:
                # Decode the promise with the remote session id
                # such that the session id will be passed to the remote
                # as a parent session id.
                promise = self._decode(
                    (
                        self._expand_promise(data, caller_encryption_pub)
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
                                self._log.debug(
                                    "Reset heartbeat timer: %s", data["method"]
                                )
                                await promise["heartbeat"]()
                            except asyncio.CancelledError:
                                if method_task and not method_task.done():
                                    method_task.cancel()
                                break
                            except Exception as exp:
                                self._log.error(
                                    "Failed to reset the heartbeat timer: %s, error: %s",
                                    data["method"],
                                    exp,
                                )
                                if method_task and not method_task.done():
                                    method_task.cancel()
                                break
                            if method_task and method_task.done():
                                self._log.debug(
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
                        # Track in background_tasks so it can be cancelled on close
                        self._background_tasks.add(heartbeat_task)
                        heartbeat_task.add_done_callback(self._background_tasks.discard)
            else:
                resolve, reject = None, None

            try:
                method = index_object(self._object_store, data["method"])
                # Update last-activity time for session GC
                method_parts = data["method"].split(".")
                if len(method_parts) > 1:
                    top_key = method_parts[0]
                    if top_key not in ("services", "message_cache"):
                        # Skip system stores — they are not GC-managed sessions
                        top_session = self._object_store.get(top_key)
                        if isinstance(top_session, dict):
                            top_session["_last_activity_at"] = time.time()
            except Exception:
                # Clean promise method detection - NO STRING MATCHING!
                if self._is_promise_method_call(data["method"]):
                    self._log.debug(
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
                        self._log.debug(
                            "Session %s exists but method %s not found, likely expired callback: %s",
                            session_id,
                            data["method"],
                            method_name,
                        )
                        # For expired callbacks, don't raise an exception, just log and return
                        if callable(reject):
                            reject(
                                Exception(f"Method expired or not found: {method_name}")
                            )
                        return
                    else:
                        self._log.debug(
                            "Session %s not found for method %s, likely cleaned up: %s",
                            session_id,
                            data["method"],
                            method_name,
                        )
                        # For cleaned up sessions, just log and return without raising
                        if callable(reject):
                            reject(Exception(f"Session not found: {method_name}"))
                        return

                self._log.debug(
                    "Failed to find method %s at %s", method_name, self._client_id
                )
                error = Exception(
                    f"Method not found: {method_name} at {self._client_id}"
                )
                if callable(reject):
                    reject(error)
                else:
                    # Log the error instead of raising to prevent unhandled exceptions
                    self._log.warning("Method not found and no reject callback: %s", error)
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
                        and remote_workspace
                        in self._method_annotations[method]["authorized_workspaces"]
                    ):
                        pass  # Access granted
                    # Allow manager access
                    elif (
                        remote_workspace == "*"
                        and remote_client_id == self._connection.manager_id
                    ):
                        pass  # Access granted
                    # Allow _rintf callbacks from the specific client they were sent to
                    elif self._method_annotations[method].get("rintf_allowed_caller"):
                        allowed = self._method_annotations[method]["rintf_allowed_caller"]
                        caller = data.get("from", "")
                        if caller == allowed:
                            pass  # Access granted — caller matches the _rintf target
                        else:
                            raise PermissionError(
                                f"Permission denied for _rintf callback {method_name}, "
                                f"caller {caller} is not the allowed caller {allowed}"
                            )
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

            # Check trusted_keys (encryption-based authentication)
            if method in self._method_annotations:
                trusted_keys = self._method_annotations[method].get("trusted_keys")
                if trusted_keys is not None:
                    if caller_encryption_pub is None:
                        raise PermissionError(
                            f"Encryption required for method {method_name} "
                            "(trusted_keys is set)"
                        )
                    caller_hex = public_key_to_hex(caller_encryption_pub)
                    if caller_hex not in trusted_keys:
                        raise PermissionError(
                            f"Caller's public key is not in the trusted keys "
                            f"list for {method_name}"
                        )

            # Make sure the parent session is still open
            # Skip for service methods — services are persistent and don't
            # depend on the originating session being alive.
            if local_parent and not data["method"].startswith("services."):
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
            self._log.debug("Executing method: %s (%s)", method_name, data["method"])
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
            self._log.debug("Error during calling method: %s", err)

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
                # Track creation and last-activity time for session GC
                if len(levels) == 1:
                    now = time.time()
                    store[levels[-1]]["_created_at"] = now
                    store[levels[-1]]["_last_activity_at"] = now

            return store[levels[-1]]
        else:
            for level in levels:
                if level not in store:
                    return None
                store = store[level]
            return store

    @staticmethod
    def _is_primitive(v):
        """Check if value is a primitive type that needs no encoding."""
        return v is None or isinstance(v, (int, float, bool, str, bytes))

    @staticmethod
    def _all_scalars_list(lst):
        """Check if a list contains only scalar primitives (no dicts).

        Used by _decode fast path: dicts must always go through recursive
        decoding to be wrapped as ObjectProxy, so they are not "scalar".
        """
        for v in lst:
            if v is None or isinstance(v, (int, float, bool, str, bytes)):
                continue
            if type(v) is list:
                if not RPC._all_scalars_list(v):
                    return False
                continue
            # dicts and anything else disqualify the fast path
            return False
        return True

    @staticmethod
    def _is_custom_async_iterator(obj):
        """Check if obj is a custom async iterator (has __aiter__ and __anext__) but not an async generator."""
        return (
            hasattr(obj, "__aiter__")
            and hasattr(obj, "__anext__")
            and not inspect.isasyncgen(obj)
        )

    @staticmethod
    def _is_custom_sync_iterator(obj):
        """Check if obj is a custom sync iterator (has __iter__ and __next__) but not a generator or built-in type."""
        return (
            hasattr(obj, "__iter__")
            and hasattr(obj, "__next__")
            and not inspect.isgenerator(obj)
            and not isinstance(obj, (list, tuple, dict, set, str, bytes, range))
        )

    @staticmethod
    def _all_primitives_list(lst):
        """Check if a list (and nested lists/dicts) contains only primitives."""
        for v in lst:
            if v is None or isinstance(v, (int, float, bool, str, bytes)):
                continue
            # Use exact type check to exclude subclasses (e.g. ObjectProxy)
            if type(v) is list:
                if not RPC._all_primitives_list(v):
                    return False
                continue
            if type(v) is dict:
                if "_rtype" in v:
                    return False
                if not RPC._all_primitives_dict(v):
                    return False
                continue
            return False
        return True

    @staticmethod
    def _all_primitives_dict(d):
        """Check if a dict (and nested dicts/lists) contains only primitives."""
        for v in d.values():
            if v is None or isinstance(v, (int, float, bool, str, bytes)):
                continue
            # Use exact type check to exclude subclasses (e.g. ObjectProxy)
            if type(v) is list:
                if not RPC._all_primitives_list(v):
                    return False
                continue
            if type(v) is dict:
                if "_rtype" in v:
                    return False
                if not RPC._all_primitives_dict(v):
                    return False
                continue
            return False
        return True

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

        _original_dict = None
        if isinstance(a_object, dict):
            _original_dict = a_object
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
                # Attach encryption public key for session methods (e.g. resolve/reject)
                if self._encryption_enabled:
                    b_object["_renc_pub"] = self._encryption_public_key
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
        elif (
            inspect.isgenerator(a_object)
            or inspect.isasyncgen(a_object)
            or self._is_custom_async_iterator(a_object)
            or self._is_custom_sync_iterator(a_object)
        ):
            # Handle generator/iterator objects by storing them in the session
            assert isinstance(
                session_id, str
            ), "Session ID is required for generator encoding"
            object_id = shortuuid.uuid()
            close_id = object_id + ":close"

            # Store the generator in the session
            store = self._get_session_store(session_id, create=True)

            # Check if it's an async generator/iterator
            is_async = inspect.isasyncgen(a_object) or self._is_custom_async_iterator(
                a_object
            )

            # Define method to get next item from the generator/iterator
            async def next_item_method():
                if is_async:
                    try:
                        return await a_object.__anext__()
                    except StopAsyncIteration:
                        # Remove it from the session
                        store.pop(object_id, None)
                        store.pop(close_id, None)
                        return {"_rtype": "stop_iteration"}
                else:
                    try:
                        return next(a_object)
                    except StopIteration:
                        store.pop(object_id, None)
                        store.pop(close_id, None)
                        return {"_rtype": "stop_iteration"}

            # Define method to close/cleanup the generator/iterator early
            async def close_generator_method():
                try:
                    if inspect.isasyncgen(a_object):
                        await a_object.aclose()
                    elif inspect.isgenerator(a_object):
                        a_object.close()
                    elif hasattr(a_object, "aclose"):
                        await a_object.aclose()
                    elif hasattr(a_object, "close"):
                        a_object.close()
                except Exception:
                    pass
                finally:
                    store.pop(object_id, None)
                    store.pop(close_id, None)
                return True

            # Register both methods in the session
            store[object_id] = next_item_method
            store[close_id] = close_generator_method

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
                "_rclose_method": f"{session_id}.{close_id}",
                "_rpromise": "*",
                "_rdoc": "Remote generator",
            }
        elif isinstance(a_object, (list, dict)):
            # Auto-register _rintf objects as local services
            if (
                isinstance(a_object, dict)
                and a_object.get("_rintf") is True
                and any(
                    callable(v)
                    for k, v in a_object.items()
                    if not k.startswith("_")
                )
            ):
                service_id = f"_rintf_{shortuuid.uuid()}"
                # Resolve the allowed caller from the session's target_id.
                # The _rintf is being sent to a specific remote client (the
                # session target), so only that client should be able to
                # call these callbacks back.
                allowed_caller = None
                if session_id:
                    top_key = session_id.split(".")[0]
                    session_store = self._object_store.get(top_key)
                    if isinstance(session_store, dict):
                        allowed_caller = session_store.get("target_id")
                service_api = {"id": service_id}
                service_api["config"] = {
                    "visibility": "protected",
                    "_local_only": True,
                }
                if allowed_caller:
                    service_api["config"]["_rintf_allowed_caller"] = allowed_caller

                # Add _dispose method for active lifecycle management.
                # The remote side (allowed caller) can call _dispose() to
                # actively unregister this _rintf service when it's done.
                _rpc_ref = self
                _sid = service_id
                _caller = allowed_caller

                async def _dispose():
                    _rpc_ref._unregister_rintf_service(_sid, _caller)

                service_api["_dispose"] = _dispose

                for k, v in a_object.items():
                    if not k.startswith("_") and callable(v):
                        service_api[k] = v
                self.add_service(service_api, overwrite=True)
                # Track in caller index for passive cleanup on disconnect
                if allowed_caller:
                    if allowed_caller not in self._rintf_caller_index:
                        self._rintf_caller_index[allowed_caller] = set()
                    self._rintf_caller_index[allowed_caller].add(service_id)
                # Store service_id on the original dict (before copy) so the
                # caller can later call rpc.unregister_service(service_id).
                if _original_dict is not None:
                    _original_dict["_rintf_service_id"] = service_id
                # Encode all values — callables are now annotated as service methods
                b_object = {}
                for key in a_object.keys():
                    b_object[key] = self._encode(
                        a_object[key],
                        session_id=session_id,
                        local_workspace=local_workspace,
                    )
                # Encode _dispose so the remote side can call it
                b_object["_dispose"] = self._encode(
                    service_api["_dispose"],
                    session_id=session_id,
                    local_workspace=local_workspace,
                )
                b_object["_rintf_service_id"] = service_id
                return b_object

            # Fast path: if all values are primitives, return as-is
            # Only for plain list/dict, not subclasses like ObjectProxy
            if type(a_object) is list:
                if self._all_primitives_list(a_object):
                    return a_object
            elif type(a_object) is dict:
                if "_rtype" not in a_object and self._all_primitives_dict(a_object):
                    return a_object
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
                        self._log.warning(
                            "numpy is not available, failed to decode ndarray"
                        )

                except Exception as exc:
                    self._log.debug("Error in converting: %s", exc)
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

                # Create close method if available
                close_method = None
                if "_rclose_method" in a_object:
                    close_obj = {
                        "_rtype": "method",
                        "_rserver": a_object.get("_rserver"),
                        "_rtarget": a_object.get("_rtarget"),
                        "_rmethod": a_object["_rclose_method"],
                        "_rpromise": "*",
                    }
                    close_method = self._generate_remote_method(
                        close_obj,
                        remote_parent=remote_parent,
                        local_parent=local_parent,
                        remote_workspace=remote_workspace,
                        local_workspace=local_workspace,
                    )

                # Create an async generator proxy with cleanup support
                async def async_generator_proxy(_close=close_method):
                    completed_normally = False
                    try:
                        while True:
                            next_item = await gen_method()
                            # Check for StopIteration signal
                            if (
                                isinstance(next_item, dict)
                                and next_item.get("_rtype") == "stop_iteration"
                            ):
                                completed_normally = True
                                break
                            yield next_item
                    except (StopAsyncIteration, StopIteration):
                        completed_normally = True
                    except Exception as e:
                        # Properly propagate exceptions
                        self._log.error(f"Error in generator: {e}")
                        raise
                    finally:
                        # If not completed normally, send close signal to clean up remote generator
                        if not completed_normally and _close:
                            try:
                                await _close()
                            except Exception:
                                pass

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
            # Fast path: skip recursive descent if all values are primitives
            # Only for plain list/dict, not subclasses
            if type(a_object) is list:
                # For decode, lists can only be fast-pathed if they contain
                # no dicts at any level (dicts must be wrapped as ObjectProxy)
                if self._all_scalars_list(a_object):
                    return a_object
            elif type(a_object) is dict:
                if self._all_primitives_dict(a_object):
                    # fromDict recursively converts nested dicts to ObjectProxy
                    return ObjectProxy.fromDict(a_object)
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

    def _expand_promise(self, data, caller_encryption_pub=None):
        target = data["from"].split("/")[1]
        session = data["session"]
        method = data["method"]
        heartbeat = {
            "_rtype": "method",
            "_rtarget": target,
            "_rmethod": f"{session}.heartbeat",
            "_rdoc": f"heartbeat callback for method: {method}",
        }
        resolve = {
            "_rtype": "method",
            "_rtarget": target,
            "_rmethod": f"{session}.resolve",
            "_rdoc": f"resolve callback for method: {method}",
        }
        reject = {
            "_rtype": "method",
            "_rtarget": target,
            "_rmethod": f"{session}.reject",
            "_rdoc": f"reject callback for method: {method}",
        }
        # Attach caller's encryption pubkey so responses are encrypted back
        if caller_encryption_pub is not None:
            heartbeat["_renc_pub"] = caller_encryption_pub
            resolve["_renc_pub"] = caller_encryption_pub
            reject["_renc_pub"] = caller_encryption_pub
        return {
            "heartbeat": heartbeat,
            "resolve": resolve,
            "reject": reject,
            "interval": data["t"],
        }
