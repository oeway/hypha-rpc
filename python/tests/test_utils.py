"""Tests for the utils module."""

import os
from functools import partial
from hypha_rpc.utils import callable_sig, callable_doc, parse_service_url
import asyncio
import pytest
import random
import httpx
from hypha_rpc import connect_to_server
from hypha_rpc.utils.serve import register_asgi_service, create_openai_chat_server
from fastapi import FastAPI
from openai import AsyncOpenAI
from . import WS_SERVER_URL

from hypha_rpc.utils.launch import launch_external_services

import pytest


def test_parse_service_url():
    # Test case 1: Basic service URL
    assert parse_service_url("https://hypha.aicell.io/public/services/hypha-login") == (
        "https://hypha.aicell.io",
        "public",
        "*",
        "hypha-login",
        "*",
    )

    # Test case 2: Service URL with client_id
    assert parse_service_url(
        "https://hypha.aicell.io/public/services/client:hypha-login"
    ) == ("https://hypha.aicell.io", "public", "client", "hypha-login", "*")

    # Test case 3: Service URL with app_id
    assert parse_service_url(
        "https://hypha.aicell.io/public/services/hypha-login@app"
    ) == ("https://hypha.aicell.io", "public", "*", "hypha-login", "app")

    # Test case 4: Service URL with both client_id and app_id
    assert parse_service_url(
        "https://hypha.aicell.io/public/services/client:hypha-login@app"
    ) == ("https://hypha.aicell.io", "public", "client", "hypha-login", "app")

    # Test case 5: Service URL with trailing slash
    assert parse_service_url(
        "https://hypha.aicell.io/public/services/hypha-login/"
    ) == ("https://hypha.aicell.io", "public", "*", "hypha-login", "*")

    # Test case 6: Invalid service URL (should raise ValueError)
    with pytest.raises(ValueError):
        parse_service_url("https://hypha.aicell.io/public/hypha-login")


def test_callable_sig():
    """Test callable_sig."""

    # Function
    def func(a, b, context=None):
        return a + b

    assert callable_sig(func) == "func(a, b, context=None)"
    assert callable_sig(func, skip_context=True) == "func(a, b)"

    # Lambda function
    def lambda_func(a, b, context=None):
        return a + b

    assert callable_sig(lambda_func) == "lambda_func(a, b, context=None)"
    assert callable_sig(lambda_func, skip_context=True) == "lambda_func(a, b)"

    # Class with a __call__ method
    class CallableClass:
        def __call__(self, a, b, context=None):
            return a + b

    assert callable_sig(CallableClass) == "CallableClass(self, a, b, context=None)"
    assert callable_sig(CallableClass, skip_context=True) == "CallableClass(self, a, b)"

    # Instance of a class with a __call__ method
    callable_instance = CallableClass()
    assert callable_sig(callable_instance) == "CallableClass(a, b, context=None)"
    assert callable_sig(callable_instance, skip_context=True) == "CallableClass(a, b)"

    # Built-in function
    assert callable_sig(print) in [
        "print(*args, **kwargs)",
        "print(*args, sep=' ', end='\\n', file=None, flush=False)",
    ]
    assert callable_sig(print, skip_context=True) in [
        "print(*args, **kwargs)",
        "print(*args, sep=' ', end='\\n', file=None, flush=False)",
    ]

    # Partial function
    partial_func = partial(func, b=3)
    assert callable_sig(partial_func) == "func(a, context=None)"
    assert callable_sig(partial_func, skip_context=True) == "func(a)"


def test_callable_doc():
    """Test callable_doc."""

    # Function with docstring
    def func_with_doc(a, b):
        """This is a function with a docstring."""
        return a + b

    assert callable_doc(func_with_doc) == "This is a function with a docstring."

    # Function without docstring
    def func_without_doc(a, b):
        return a + b

    assert callable_doc(func_without_doc) is None

    # Partial function with docstring
    def partial_func_with_doc(a, b=3):
        """This is a partial function with a docstring"""
        return a + b

    partial_func = partial(partial_func_with_doc, b=3)
    assert callable_doc(partial_func) == "This is a partial function with a docstring"


@pytest.mark.asyncio
async def test_serve_fastapi_app(websocket_server):
    """Test the serve command utility function."""
    # Create a FastAPI app instance
    app = FastAPI()

    @app.get("/")
    async def root():
        return {"message": "Hello, World!"}

    @app.get("/api/v1/test")
    async def test():
        return {"message": "Hello, it works!"}

    # Register the FastAPI app as a service
    service_id = "test-service"

    server = await connect_to_server({"server_url": WS_SERVER_URL})
    workspace = server.config["workspace"]
    token = await server.generate_token()
    await register_asgi_service(server, service_id, app)

    # Test the service using httpx
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{WS_SERVER_URL}/{workspace}/apps/{service_id}/")
        assert response.status_code == 200
        assert response.json() == {"message": "Hello, World!"}

        response = await client.get(
            f"{WS_SERVER_URL}/{workspace}/apps/{service_id}/api/v1/test"
        )
        assert response.status_code == 200
        assert response.json() == {"message": "Hello, it works!"}

    try:
        # Ensure the server is no longer running
        response = await client.get(f"{WS_SERVER_URL}/{workspace}/apps/{service_id}/")
        assert response.status_code == 404
    except RuntimeError as exp:
        assert "the client has been closed" in str(exp)


@pytest.mark.asyncio
async def test_openai_server_proxy(websocket_server):
    """Test the OpenAI server proxy."""

    async def text_generator(request: dict):
        max_tokens = request.get("max_tokens", 50)
        words = [
            "hello",
            "world",
            "foo",
            "bar",
            "chatbot",
            "test",
            "api",
            "response",
            "markdown",
        ]
        # Simulate streaming random markdown text
        for _ in range(5):  # Stream 5 chunks of random text
            delta_text = " ".join(random.choices(words, k=max_tokens // 5))
            yield delta_text
            await asyncio.sleep(0.1)  # Simulate delay between chunks

    model_registry = {"test-chat-model": text_generator}
    app = create_openai_chat_server(model_registry)

    server = await connect_to_server({"server_url": WS_SERVER_URL})
    workspace = server.config["workspace"]
    token = await server.generate_token()

    service_id = "openai-server"

    await register_asgi_service(server, service_id, app)

    # Let's test it using httpx
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{WS_SERVER_URL}/{workspace}/apps/{service_id}/v1/models"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "list"
        assert data["data"][0]["id"] == "test-chat-model"

        response = await client.post(
            f"{WS_SERVER_URL}/{workspace}/apps/{service_id}/v1/chat/completions",
            json={
                "model": "test-chat-model",
                "messages": [{"role": "user", "content": "Tell me a story."}],
                "temperature": 0.8,
                "max_tokens": 50,
                "stream": False,
            },
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        data = response.json()
        assert data["model"] == "test-chat-model"
        assert data["choices"][0]["message"]["role"] == "assistant"

    client = AsyncOpenAI(
        base_url=f"{WS_SERVER_URL}/{workspace}/apps/{service_id}/v1", api_key=token
    )
    response = await client.chat.completions.create(
        model="test-chat-model",
        messages=[{"role": "user", "content": "Tell me a story."}],
        temperature=0.8,
        max_tokens=50,
        stream=True,
    )

    async for chunk in response:
        print("Chunk:", chunk.choices[0].delta.content)
        assert chunk.choices[0].delta.role == "assistant"


@pytest.mark.asyncio
async def test_launch_external_services(websocket_server):
    """Test the launch command utility fuction."""
    server = await connect_to_server(
        {
            "name": "my third app",
            "server_url": WS_SERVER_URL,
        }
    )
    current_dir = os.path.dirname(os.path.abspath(__file__))
    proc = await launch_external_services(
        server,
        "python "
        + current_dir
        + "/example_service_script.py --server-url={server_url} --service-id=external-test-service --workspace={workspace} --token={token}",
        name="example_service_script",
        check_services=["external-test-service"],
    )
    external_service = await server.get_service(f"external-test-service")
    assert external_service.id.endswith(":external-test-service")
    assert await external_service.test(1) == 100
    await proc.kill()
    await asyncio.sleep(0.1)
    try:
        await server.get_service("external-test-service")
    except Exception as e:
        assert "not found" in str(e)
    proc = await launch_external_services(
        server,
        "python -m http.server 9391",
        name="example_service_script",
        check_url="http://127.0.0.1:9391",
    )
    assert await proc.ready()
    await proc.kill()
