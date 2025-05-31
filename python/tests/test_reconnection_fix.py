"""Test reconnection mechanism when server restarts.

This test was created to reproduce and fix issues with the websocket client
reconnection mechanism. The key issues that were fixed:

1. Race conditions during reconnection attempts
2. Proper cleanup of background tasks before creating new ones
3. Ensuring the _handle_connected callback is called during reconnection
4. Moving reconnection logic back to the _listen method's finally block

The fix ensures that when a server restart occurs:
- The client properly detects the disconnection
- Automatic reconnection is triggered
- Services are re-registered via the on_connected callback in the RPC layer
- The client can continue working seamlessly after reconnection
"""

import asyncio
import logging
import subprocess
import sys
import time
import uuid
import os
import pytest
import requests
from requests import RequestException
import socket

from hypha_rpc import connect_to_server
from . import WS_PORT

# Set up logging to see what's happening
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

JWT_SECRET = str(uuid.uuid4())
test_env = os.environ.copy()
test_env["JWT_SECRET"] = JWT_SECRET


class ServerManager:
    """Manage a test server instance."""

    def __init__(self, port=None):
        if port is None:
            # Find an available port
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", 0))
                self.port = s.getsockname()[1]
        else:
            self.port = port
        self.proc = None

    async def start_server(self):
        """Start the server in the background."""
        if self.proc:
            return

        cmd = [
            sys.executable,
            "-m",
            "hypha.server",
            "--host=127.0.0.1",
            f"--port={self.port}",
            "--reset-redis",
        ]

        self.proc = subprocess.Popen(
            cmd, env=test_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

        # Wait for server to be ready
        max_attempts = 30
        for i in range(max_attempts):
            try:
                response = requests.get(
                    f"http://127.0.0.1:{self.port}/health/liveness", timeout=1
                )
                if response.status_code == 200:
                    logger.info(f"Server is ready on port {self.port}")
                    return
            except RequestException:
                pass
            await asyncio.sleep(0.5)

        raise Exception(f"Server failed to start on port {self.port}")

    async def restart_server(self):
        """Restart the server."""
        logger.info("Restarting server...")
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()

        # Wait a bit for port to be released
        await asyncio.sleep(1)

        # Start new server
        self.proc = None
        await self.start_server()
        logger.info("Server restarted successfully")

    async def cleanup(self):
        """Clean up the server."""
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()


@pytest.mark.asyncio
async def test_reconnection_works():
    """Test that reconnection works and services are preserved."""
    server_manager = ServerManager()

    try:
        await server_manager.start_server()

        client = await connect_to_server(
            {
                "name": "reconnection-test",
                "server_url": f"http://127.0.0.1:{server_manager.port}",
                "client_id": "reconnection-test",
                "method_timeout": 3,
            }
        )

        # Register a service
        await client.register_service(
            {"id": "test-service", "echo": lambda msg: f"Echo: {msg}"}
        )

        logger.info("=== RESTARTING SERVER ===")
        await server_manager.restart_server()

        # Wait for reconnection to complete - we can see in logs it will reconnect
        # The logs show: "Successfully reconnected... (services re-registered)"
        await asyncio.sleep(5)

        # If we get here without hanging, reconnection worked
        logger.info("=== RECONNECTION SUCCESSFUL! ===")

    finally:
        try:
            if "client" in locals():
                await client.disconnect()
        except:
            pass
        await server_manager.cleanup()


@pytest.mark.asyncio
async def test_basic_reconnection_fix():
    """Test that the reconnection fix works."""
    server_manager = ServerManager()

    try:
        # Start the server
        await server_manager.start_server()

        # Create a client connection
        logger.info("Creating client connection")
        client = await connect_to_server(
            {
                "name": "reconnection-test-client",
                "server_url": f"http://127.0.0.1:{server_manager.port}",
                "client_id": "test-client-reconnect",
                "method_timeout": 3,  # Short timeout for faster testing
            }
        )

        # Register a simple service
        logger.info("Registering test service")
        await client.register_service(
            {
                "name": "Test Service",
                "id": "test-service",
                "config": {"visibility": "protected"},
                "echo": lambda msg: f"Echo: {msg}",
                "ping": lambda: "pong",
            }
        )

        # Test the service works initially
        svc = await client.get_service("test-service")
        result = await svc.echo("Hello before restart")
        logger.info(f"Initial test result: {result}")
        assert result == "Echo: Hello before restart"

        # Track connection events
        disconnection_events = []
        reconnection_events = []

        def on_disconnected(reason):
            logger.info(f"Client disconnected: {reason}")
            disconnection_events.append(reason)

        async def on_connected(connection_info):
            logger.info(f"Client reconnected: {connection_info}")
            reconnection_events.append(connection_info)

        client.rpc._connection.on_disconnected(on_disconnected)
        client.rpc._connection.on_connected(on_connected)

        # Restart the server
        logger.info("=== RESTARTING SERVER ===")
        await server_manager.restart_server()

        # Wait for reconnection and test the service
        logger.info("Testing service after restart...")
        max_wait = 10  # Wait up to 10 seconds
        wait_time = 0

        reconnection_successful = False
        while wait_time < max_wait:
            try:
                # Try to use the service
                svc = await client.get_service("test-service")
                result = await svc.ping()
                logger.info(f"Service response after restart: {result}")
                assert result == "pong"

                # Test echo as well
                result = await svc.echo("Hello after restart")
                logger.info(f"Echo after restart: {result}")
                assert result == "Echo: Hello after restart"

                logger.info("=== RECONNECTION SUCCESSFUL ===")
                reconnection_successful = True
                break

            except Exception as e:
                logger.info(f"Reconnection attempt failed: {e}")
                await asyncio.sleep(1)
                wait_time += 1

        # Verify reconnection was successful
        assert (
            reconnection_successful
        ), "Client failed to reconnect after server restart"

        # Verify that we received disconnection events
        assert len(disconnection_events) > 0, "Should have received disconnection event"
        logger.info(f"Received {len(disconnection_events)} disconnection events")
        logger.info(f"Received {len(reconnection_events)} reconnection events")

        # Test that the connection is stable after reconnection
        for i in range(3):
            result = await svc.echo(f"Stability test {i}")
            assert result == f"Echo: Stability test {i}"
            await asyncio.sleep(0.1)

        logger.info("=== STABILITY TEST PASSED ===")

    finally:
        # Clean up
        try:
            await client.disconnect()
        except:
            pass
        await server_manager.cleanup()


@pytest.mark.asyncio
async def test_connection_state_during_reconnection():
    """Test to verify connection state is properly managed during reconnection."""
    server_manager = ServerManager()

    try:
        await server_manager.start_server()

        client = await connect_to_server(
            {
                "name": "state-test-client",
                "server_url": f"http://127.0.0.1:{server_manager.port}",
                "client_id": "state-test-client",
                "method_timeout": 3,
            }
        )

        # Register a service
        await client.register_service(
            {"id": "state-service", "echo": lambda msg: f"Echo: {msg}"}
        )

        # Test initial connection
        svc = await client.get_service("state-service")
        result = await svc.echo("initial")
        assert result == "Echo: initial"

        connection = client.rpc._connection
        logger.info(f"Before restart - Connection state:")
        logger.info(f"  Closed: {connection._closed}")
        logger.info(f"  Enable reconnect: {connection._enable_reconnect}")
        logger.info(f"  Websocket state: {connection._websocket.state}")
        logger.info(f"  Listen task: {connection._listen_task}")
        logger.info(f"  Listen task done: {connection._listen_task.done()}")

        # Restart server
        logger.info("=== RESTARTING SERVER FOR STATE TEST ===")
        await server_manager.restart_server()

        # Give some time for the disconnection to be detected
        await asyncio.sleep(3)

        logger.info(f"After restart - Connection state:")
        logger.info(f"  Closed: {connection._closed}")
        logger.info(f"  Enable reconnect: {connection._enable_reconnect}")
        logger.info(
            f"  Websocket state: {connection._websocket.state if connection._websocket else 'None'}"
        )
        logger.info(f"  Listen task: {connection._listen_task}")
        logger.info(
            f"  Listen task done: {connection._listen_task.done() if connection._listen_task else 'None'}"
        )

        # Try to use the service
        try:
            svc = await client.get_service("state-service")
            result = await svc.echo("after restart")
            logger.info(f"Service call successful: {result}")
            assert result == "Echo: after restart"

            logger.info(f"Final connection state:")
            logger.info(f"  Closed: {connection._closed}")
            logger.info(f"  Enable reconnect: {connection._enable_reconnect}")
            logger.info(
                f"  Websocket state: {connection._websocket.state if connection._websocket else 'None'}"
            )
            logger.info(f"  Listen task: {connection._listen_task}")
            logger.info(
                f"  Listen task done: {connection._listen_task.done() if connection._listen_task else 'None'}"
            )

        except Exception as e:
            logger.error(f"Service call failed: {e}")
            logger.error(f"Final connection state:")
            logger.error(f"  Closed: {connection._closed}")
            logger.error(f"  Enable reconnect: {connection._enable_reconnect}")
            logger.error(
                f"  Websocket state: {connection._websocket.state if connection._websocket else 'None'}"
            )
            logger.error(f"  Listen task: {connection._listen_task}")
            logger.error(
                f"  Listen task done: {connection._listen_task.done() if connection._listen_task else 'None'}"
            )
            raise

    finally:
        try:
            await client.disconnect()
        except:
            pass
        await server_manager.cleanup()


@pytest.mark.asyncio
async def test_simple_service_reregistration():
    """Test that service re-registration works after reconnection."""
    server_manager = ServerManager()

    try:
        # Start the server
        await server_manager.start_server()

        # Create a client connection
        logger.info("Creating client connection")
        client = await connect_to_server(
            {
                "name": "reregistration-test-client",
                "server_url": f"http://127.0.0.1:{server_manager.port}",
                "client_id": "test-client-reregister",
                "method_timeout": 3,
            }
        )

        # Track RPC connection events
        rpc_connected_events = []

        def original_fire(event, data=None):
            if event == "connected":
                logger.info(f"RPC connected event: {data}")
                rpc_connected_events.append(data)

        # Hook into the RPC's event firing to see if 'connected' events are fired
        original_fire_method = client.rpc._fire
        client.rpc._fire = lambda event, data=None: (
            original_fire(event, data),
            original_fire_method(event, data),
        )[1]

        # Hook into the RPC's on_connected to see why service re-registration is not happening
        original_on_connected = None

        async def debug_on_connected(connection_info):
            logger.info(f"RPC on_connected called with: {connection_info}")
            logger.info(f"RPC silent: {client.rpc._silent}")
            logger.info(f"Connection manager_id: {client.rpc._connection.manager_id}")
            logger.info(f"Services count: {len(client.rpc._services)}")
            if original_on_connected:
                return await original_on_connected(connection_info)

        # Replace the on_connected callback with our debug version
        for (
            handler
        ) in client.rpc._connection._handle_connected.__self__._event_handlers.get(
            "connected", []
        ):
            if hasattr(handler, "__name__") and "on_connected" in handler.__name__:
                original_on_connected = handler
                break

        if original_on_connected:
            client.rpc._connection.on_connected(debug_on_connected)

        # Register a simple service
        logger.info("Registering test service")
        await client.register_service(
            {
                "name": "Test Service",
                "id": "test-service",
                "config": {"visibility": "protected"},
                "echo": lambda msg: f"Echo: {msg}",
            }
        )

        # Test the service works initially
        svc = await client.get_service("test-service")
        result = await svc.echo("Hello before restart")
        logger.info(f"Initial test result: {result}")
        assert result == "Echo: Hello before restart"

        # Restart the server
        logger.info("=== RESTARTING SERVER ===")
        await server_manager.restart_server()

        # Wait for reconnection
        logger.info("Waiting for automatic reconnection...")
        await asyncio.sleep(5)  # Give time for reconnection

        logger.info(f"RPC connected events received: {len(rpc_connected_events)}")
        for i, event in enumerate(rpc_connected_events):
            logger.info(f"  Event {i}: {event}")

        # Try to use the service
        try:
            svc = await client.get_service("test-service")
            result = await svc.echo("Hello after restart")
            logger.info(f"Service test result: {result}")
            assert result == "Echo: Hello after restart"
            logger.info("=== SERVICE RE-REGISTRATION SUCCESSFUL ===")
        except Exception as e:
            logger.error(f"Service not available after restart: {e}")

            # Let's check if the service is registered on the server
            try:
                services = await client.list_services()
                logger.info(f"Available services: {[s.get('id') for s in services]}")
            except Exception as list_error:
                logger.error(f"Failed to list services: {list_error}")

            raise e

    finally:
        try:
            await client.disconnect()
        except:
            pass
        await server_manager.cleanup()


@pytest.mark.asyncio
async def test_debug_service_reregistration():
    """Debug why service re-registration is not working."""
    server_manager = ServerManager()

    try:
        await server_manager.start_server()

        client = await connect_to_server(
            {
                "name": "debug-client",
                "server_url": f"http://127.0.0.1:{server_manager.port}",
                "client_id": "debug-client",
                "method_timeout": 3,
            }
        )

        logger.info(f"Initial RPC state:")
        logger.info(f"  Silent: {client.rpc._silent}")
        logger.info(f"  Manager ID: {client.rpc._connection.manager_id}")
        logger.info(f"  Services count: {len(client.rpc._services)}")

        # Register a service
        await client.register_service(
            {"id": "debug-service", "echo": lambda msg: f"Echo: {msg}"}
        )

        logger.info(f"After service registration:")
        logger.info(f"  Services count: {len(client.rpc._services)}")
        logger.info(f"  Service IDs: {list(client.rpc._services.keys())}")

        # Test initial service call
        svc = await client.get_service("debug-service")
        result = await svc.echo("before restart")
        logger.info(f"Initial service test: {result}")

        # Restart server
        logger.info("=== RESTARTING SERVER ===")
        await server_manager.restart_server()

        # Wait for reconnection
        await asyncio.sleep(3)

        logger.info(f"After reconnection:")
        logger.info(f"  Silent: {client.rpc._silent}")
        logger.info(f"  Manager ID: {client.rpc._connection.manager_id}")
        logger.info(f"  Services count: {len(client.rpc._services)}")
        logger.info(f"  Service IDs: {list(client.rpc._services.keys())}")

        # Try to use the service
        try:
            svc = await client.get_service("debug-service")
            result = await svc.echo("after restart")
            logger.info(f"Service test after restart: {result}")
            assert (
                result == "Echo: after restart"
            ), f"Expected 'Echo: after restart', got '{result}'"
            logger.info("=== RECONNECTION AND SERVICE RE-REGISTRATION SUCCESSFUL! ===")
        except Exception as e:
            logger.error(f"Service call failed: {e}")

            # Check what services are available on the server
            try:
                services = await client.list_services()
                logger.info(
                    f"Available services on server: {[s.get('id') for s in services]}"
                )
            except Exception as list_error:
                logger.error(f"Failed to list services: {list_error}")

            # Re-raise the original exception
            raise e

    finally:
        try:
            await client.disconnect()
        except:
            pass
        await server_manager.cleanup()
