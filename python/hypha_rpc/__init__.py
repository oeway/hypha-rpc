"""Provide hypha-rpc to connecting to Hypha server."""

import shortuuid

from .rpc import RPC
from .utils import ObjectProxy
from .sync import connect_to_server as connect_to_server_sync
from .sync import get_remote_service as get_remote_service_sync
from .sync import get_rtc_service as get_rtc_service_sync
from .sync import login as login_sync
from .sync import register_rtc_service as register_rtc_service_sync
from .webrtc_client import get_rtc_service, register_rtc_service
from .websocket_client import (
    connect_to_server,
    get_remote_service,
    login,
    setup_local_client,
)


class API(ObjectProxy):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._registry = {}

    def list(self):
        return list(self._registry.keys())

    def get(self, name):
        return self._registry.get(name)

    def export(self, obj, config=None):
        config = config or {}
        name = config.get("name", shortuuid.uuid())
        self._registry[name] = obj


# An placeholder object for the API
api = API()

__all__ = [
    "api",
    "RPC",
    "login",
    "connect_to_server",
    "get_remote_service",
    "login_sync",
    "connect_to_server_sync",
    "get_remote_service_sync",
    "get_rtc_service",
    "register_rtc_service",
    "register_rtc_service_sync",
    "get_rtc_service_sync",
    "setup_local_client",
]
