"""Provide common pytest fixtures."""

import os
import subprocess
import sys
import time
import uuid

import pytest
import requests
from requests import RequestException
from . import WS_PORT

JWT_SECRET = str(uuid.uuid4())
os.environ["JWT_SECRET"] = JWT_SECRET
test_env = os.environ.copy()


class RestartableServer:
    """A server that can be restarted during tests."""
    
    def __init__(self, port=WS_PORT):
        self.port = port
        self.proc = None
        self.server_args = [
            sys.executable, 
            "-m", "hypha.server", 
            f"--port={self.port}"
        ]
    
    def start(self, timeout=10):
        """Start the server."""
        if self.proc is not None:
            self.stop()
        
        self.proc = subprocess.Popen(self.server_args, env=test_env)
        
        # Wait for server to be ready
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = requests.get(f"http://127.0.0.1:{self.port}/health/liveness", timeout=1)
                if response.ok:
                    return True
            except RequestException:
                pass
            time.sleep(0.1)
        
        raise RuntimeError(f"Failed to start server on port {self.port}")
    
    def stop(self):
        """Stop the server."""
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
            self.proc = None
    
    def restart(self, stop_delay=0.1, start_timeout=10):
        """Restart the server with optional delay."""
        self.stop()
        if stop_delay > 0:
            time.sleep(stop_delay)
        self.start(start_timeout)
    
    def is_running(self):
        """Check if server is running."""
        if self.proc is None:
            return False
        try:
            response = requests.get(f"http://127.0.0.1:{self.port}/health/liveness", timeout=1)
            return response.ok
        except RequestException:
            return False
    
    def __enter__(self):
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()


@pytest.fixture(name="restartable_server", scope="function")
def restartable_server_fixture():
    """Provide a server that can be restarted during tests."""
    server = RestartableServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture(name="hypha_server", scope="session")
def hypha_server_fixture():
    """Start unified Hypha server with full S3 support as test fixture."""
    server_args = [
        sys.executable,
        "-m", "hypha.server",
        f"--port={WS_PORT}",
        "--workspace-bucket=test-workspaces", 
        "--start-minio-server",
        "--minio-workdir=/tmp/minio_data",
        "--minio-port=9002",
        "--minio-root-user=myuser",
        "--minio-root-password=mypassword",
        "--s3-cleanup-period=3",
        "--enable-s3-for-anonymous-users"
    ]
    
    proc = subprocess.Popen(server_args, env=test_env, 
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    # Check if process failed immediately (e.g., port in use)
    time.sleep(0.5)  # Give it a moment to fail
    if proc.poll() is not None:
        # Process exited, capture error output
        stdout, stderr = proc.communicate()
        raise RuntimeError(f"Failed to start hypha server. Exit code: {proc.returncode}\n"
                          f"STDOUT: {stdout.decode()}\n"
                          f"STDERR: {stderr.decode()}")
    
    # Wait for server to be ready
    timeout = 20
    while timeout > 0:
        try:
            response = requests.get(f"http://127.0.0.1:{WS_PORT}/health/readiness", timeout=5)
            if response.ok:
                break
        except RequestException as exc:
            # Check if process died during startup
            if proc.poll() is not None:
                stdout, stderr = proc.communicate()
                raise RuntimeError(f"Hypha server died during startup. Exit code: {proc.returncode}\n"
                                  f"STDOUT: {stdout.decode()}\n"
                                  f"STDERR: {stderr.decode()}") from exc
        timeout -= 0.1
        time.sleep(0.1)
        
    if timeout <= 0:
        proc.terminate()
        stdout, stderr = proc.communicate(timeout=5)
        raise RuntimeError(f"Failed to start hypha server within timeout\n"
                          f"STDOUT: {stdout.decode()}\n"
                          f"STDERR: {stderr.decode()}")
    
    try:
        yield proc  # Yield the process object instead of None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(name="fastapi_server", scope="session") 
def fastapi_server_fixture(hypha_server):
    """Alias for the unified hypha server fixture."""
    yield hypha_server


@pytest.fixture(name="test_user_token", scope="session")
def generate_test_user_token():
    """Generate a test user token."""
    from hypha.core.auth import generate_presigned_token, create_scope, UserInfo, UserPermission
    
    user_info = UserInfo(
        id="test-user",
        is_anonymous=False,
        email="test-user@test.com",
        parent=None,
        roles=[],
        scope=create_scope(workspaces={"ws-user-test-user": UserPermission.admin}),
        expires_at=None,
    )
    token = generate_presigned_token(user_info, 1800)
    yield token
