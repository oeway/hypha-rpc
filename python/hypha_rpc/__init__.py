"""Provide hypha-rpc to connecting to Hypha server."""

import shortuuid
import os
import json
import asyncio
import sys

from .rpc import RPC
from .utils import ObjectProxy, run_in_executor
from .sync import connect_to_server as connect_to_server_sync
from .sync import get_remote_service as get_remote_service_sync
from .sync import get_rtc_service as get_rtc_service_sync
from .sync import login as login_sync
from .sync import logout as logout_sync
from .sync import register_rtc_service as register_rtc_service_sync
from .webrtc_client import get_rtc_service, register_rtc_service
from .websocket_client import (
    connect_to_server,
    get_remote_service,
    login,
    logout,
    setup_local_client,
)
from .http_client import HTTPStreamingRPCConnection

# read the version from the VERSION file; but get the path from the __file__
with open(os.path.join(os.path.dirname(__file__), "VERSION"), "r") as f:
    __version__ = json.load(f)["version"]

def is_user_defined_class_instance(obj):
    return (
        not isinstance(obj, type) and              # not a class itself
        hasattr(obj, "__class__") and
        obj.__class__.__module__ != "builtins"     # not a built-in type
    )


class API(ObjectProxy):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._registry = {}
        self._export_handler = self._default_export_handler
        
    async def _register_services(self, obj, config=None, **kwargs):
        if not os.environ.get("HYPHA_SERVER_URL"):
            try:
                from dotenv import load_dotenv, find_dotenv
                load_dotenv(dotenv_path=find_dotenv(usecwd=True))
                # use info from .env file
                print("✅ Loaded connection configuration from .env file.")
            except ImportError:
                print("❌ Missing environment variables. Set HYPHA_SERVER_URL, HYPHA_TOKEN, HYPHA_WORKSPACE", file=sys.stderr)
                sys.exit(1)
        SERVER_URL = os.environ.get("HYPHA_SERVER_URL")
        TOKEN = os.environ.get("HYPHA_TOKEN")
        CLIENT_ID = os.environ.get("HYPHA_CLIENT_ID")
        WORKSPACE = os.environ.get("HYPHA_WORKSPACE")

        server = await connect_to_server({"client_id": CLIENT_ID, "server_url": SERVER_URL, "token": TOKEN, "workspace": WORKSPACE})
        # If obj is a class, instantiate it
        if isinstance(obj, type):
            obj = obj()
        # If obj is a dict
        if isinstance(obj, dict):
            obj = {k: v for k, v in obj.items() if not k.startswith("_")}
        # if obj is a class instance, extract the properties
        elif is_user_defined_class_instance(obj):
            obj = {
                name: value
                for name, value in obj.__dict__.items()
                if not name.startswith("_")  # exclude _ and __ names
            }
        else:
            raise ValueError("obj must be a class, dict, or class instance")
        obj["id"] = "default"
        if config:
            if not isinstance(config, dict):
                raise ValueError("config must be a dict")
            # Merge config into obj
            if "config" not in obj:
                obj["config"] = {}
            obj["config"].update(config)
        await server.register_service(obj, **kwargs)
        if "skip_wait" not in kwargs or not kwargs["skip_wait"]:
            # print with emoji
            print("✅ Service successfully registered, accepting connections...")
            print("Press Ctrl+C to stop the service.")
            await asyncio.Event().wait()

    def _default_export_handler(self, obj, config=None, **kwargs):
        config = config or {}
        name = config.get("name", shortuuid.uuid())
        self._registry[name] = obj
        if asyncio.get_event_loop().is_running():
            asyncio.create_task(self._register_services(obj, config, **kwargs))
        else:
            asyncio.run(self._register_services(obj, config, **kwargs))
            

    def set_export_handler(self, handler):
        self._export_handler = handler

    def export(self, obj, config=None, **kwargs):
        return self._export_handler(obj, config=config, **kwargs)

    def get_registry(self):
        return self._registry


# An placeholder object for the API
api = API()

__all__ = [
    "__version__",
    "api",
    "RPC",
    "login",
    "logout",
    "connect_to_server",
    "get_remote_service",
    "HTTPStreamingRPCConnection",
    "login_sync",
    "logout_sync",
    "connect_to_server_sync",
    "get_remote_service_sync",
    "get_rtc_service",
    "register_rtc_service",
    "register_rtc_service_sync",
    "get_rtc_service_sync",
    "setup_local_client",
    "run_in_executor",
]
