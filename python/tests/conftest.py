"""Provide common pytest fixtures."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

import pytest
import requests
from requests import RequestException
from . import WS_PORT, MINIO_PORT, MINIO_SERVER_URL, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD

JWT_SECRET = str(uuid.uuid4())
os.environ["JWT_SECRET"] = JWT_SECRET
test_env = os.environ.copy()


@pytest.fixture(name="minio_server", scope="session")
def minio_server_fixture():
    """Start minio server as test fixture and tear down after test."""
    try:
        from hypha.minio import start_minio_server
        
        proc, server_url, workdir = start_minio_server(
            executable_path="./bin",
            workdir=tempfile.mkdtemp(),
            port=MINIO_PORT,
            console_port=MINIO_PORT + 1,
            root_user=MINIO_ROOT_USER,
            root_password=MINIO_ROOT_PASSWORD,
            timeout=10,
        )

        if not proc:
            raise RuntimeError(f"Failed to start Minio server at {MINIO_SERVER_URL}")

        print(f"Minio server started successfully at {server_url}")
        print(f"Minio data directory: {workdir}")

        yield server_url

        print("Stopping Minio server...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
            print("Minio server stopped.")
        except subprocess.TimeoutExpired:
            print("Minio server did not terminate gracefully, killing...")
            proc.kill()
        finally:
            if workdir and os.path.exists(workdir):
                print(f"Cleaning up Minio data directory: {workdir}")
                try:
                    shutil.rmtree(workdir, ignore_errors=True)
                except Exception as e:
                    print(f"Error removing Minio workdir {workdir}: {e}")
    except ImportError:
        print("Warning: hypha.minio not available, skipping Minio server fixture")
        yield None


@pytest.fixture(name="fastapi_server", scope="session")
def fastapi_server_fixture(minio_server):
    """Start server with S3 support as test fixture and tear down after test."""
    if minio_server is None:
        print("Warning: Minio server not available, starting server without S3")
        server_args = [
            sys.executable, "-m", "hypha.server", f"--port={WS_PORT}"
        ]
    else:
        server_args = [
            sys.executable,
            "-m", "hypha.server",
            f"--port={WS_PORT}",
            "--enable-s3",
            f"--endpoint-url={MINIO_SERVER_URL}",
            f"--access-key-id={MINIO_ROOT_USER}",
            f"--secret-access-key={MINIO_ROOT_PASSWORD}",
            "--workspace-bucket=test-workspaces",
            "--s3-admin-type=minio",
        ]
    
    with subprocess.Popen(server_args, env=test_env) as proc:
        timeout = 20
        while timeout > 0:
            try:
                response = requests.get(f"http://127.0.0.1:{WS_PORT}/health/readiness")
                if response.ok:
                    break
            except RequestException:
                pass
            timeout -= 0.1
            time.sleep(0.1)
        if timeout <= 0:
            raise RuntimeError("Failed to start fastapi server")
        yield
        proc.kill()
        proc.terminate()


@pytest.fixture(name="websocket_server", scope="session")
def websocket_server_fixture():
    """Start server as test fixture and tear down after test."""
    with subprocess.Popen(
        [sys.executable, "-m", "hypha.server", f"--port={WS_PORT}"],
        env=test_env,
    ) as proc:
        timeout = 10
        while timeout > 0:
            try:
                response = requests.get(f"http://127.0.0.1:{WS_PORT}/health/liveness")
                if response.ok:
                    break
            except RequestException:
                pass
            timeout -= 0.1
            time.sleep(0.1)
        if timeout <= 0:
            raise RuntimeError("Failed to start websocket server")
        yield
        proc.kill()
        proc.terminate()


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
        scope=create_scope(workspaces={"ws-test-user": UserPermission.admin}),
        expires_at=None,
    )
    token = generate_presigned_token(user_info, 1800)
    yield token
