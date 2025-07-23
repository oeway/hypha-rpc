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


@pytest.fixture(name="fastapi_server", scope="session")
def fastapi_server_fixture():
    """Start server with S3 support as test fixture and tear down after test."""

    server_args = [
        sys.executable,
        "-m", "hypha.server",
        f"--port={WS_PORT}",
        "--workspace-bucket=test-workspaces",
        "--start-minio-server",
        "--minio-workdir=./minio_data",
        "--minio-port=9002",
        "--minio-root-user=myuser",
        "--minio-root-password=mypassword",
        "--minio-file-system-mode",
        "--s3-cleanup-period=3"
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
