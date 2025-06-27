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
async def test_openai_server_comprehensive(websocket_server):
    """Test comprehensive OpenAI server proxy with different model registry types."""

    # Test different types of model registry functions

    # 1. Simple string response
    def string_model(request: dict):
        messages = request.get("messages", [])
        user_message = messages[-1]["content"] if messages else "No message"
        return f"String response to: {user_message}"

    # 2. Dict response
    def dict_model(request: dict):
        return {
            "model": request["model"],
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Dict response from model",
                    },
                }
            ],
            "created": 1234567890,
        }

    # 3. AsyncGenerator for streaming
    async def streaming_model(request: dict):
        max_tokens = request.get("max_tokens", 50)
        words = ["hello", "world", "test", "streaming"]
        for i in range(min(5, max_tokens)):
            yield f"{words[i % len(words)]} "
            await asyncio.sleep(0.01)  # Small delay for testing

    # 4. Awaitable returning string
    async def async_string_model(request: dict):
        await asyncio.sleep(0.01)  # Simulate async work
        return "Async string response"

    # 5. Awaitable returning dict
    async def async_dict_model(request: dict):
        await asyncio.sleep(0.01)  # Simulate async work
        return {
            "model": request["model"],
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Async dict response"},
                }
            ],
        }

    model_registry = {
        "string-model": string_model,
        "dict-model": dict_model,
        "streaming-model": streaming_model,
        "async-string-model": async_string_model,
        "async-dict-model": async_dict_model,
    }

    app = create_openai_chat_server(model_registry)
    server = await connect_to_server({"server_url": WS_SERVER_URL})
    workspace = server.config["workspace"]
    service_id = "openai-comprehensive-test"

    await register_asgi_service(server, service_id, app)

    async with httpx.AsyncClient() as client:
        base_url = f"{WS_SERVER_URL}/{workspace}/apps/{service_id}"

        # Test models list endpoint
        response = await client.get(f"{base_url}/v1/models")
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "list"
        assert len(data["data"]) == 5
        model_ids = [model["id"] for model in data["data"]]
        assert all(model_id in model_ids for model_id in model_registry.keys())

        # Test string model
        response = await client.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": "string-model",
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 50,
                "stream": False,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["model"] == "string-model"
        assert "String response to: Hello" in data["choices"][0]["message"]["content"]

        # Test dict model
        response = await client.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": "dict-model",
                "messages": [{"role": "user", "content": "Test dict"}],
                "stream": False,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["model"] == "dict-model"
        assert data["choices"][0]["message"]["content"] == "Dict response from model"

        # Test streaming model - non-streaming mode
        response = await client.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": "streaming-model",
                "messages": [{"role": "user", "content": "Test streaming"}],
                "max_tokens": 3,
                "stream": False,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["model"] == "streaming-model"
        content = data["choices"][0]["message"]["content"]
        assert len(content.split()) <= 3  # Should respect max_tokens

        # Test async string model
        response = await client.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": "async-string-model",
                "messages": [{"role": "user", "content": "Test async"}],
                "stream": False,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["model"] == "async-string-model"
        assert data["choices"][0]["message"]["content"] == "Async string response"

        # Test async dict model
        response = await client.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": "async-dict-model",
                "messages": [{"role": "user", "content": "Test async dict"}],
                "stream": False,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["model"] == "async-dict-model"
        assert data["choices"][0]["message"]["content"] == "Async dict response"

        # Test streaming mode
        response = await client.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": "streaming-model",
                "messages": [{"role": "user", "content": "Test streaming mode"}],
                "max_tokens": 3,
                "stream": True,
            },
        )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

        # Test error cases
        # Non-existent model
        response = await client.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": "non-existent",
                "messages": [{"role": "user", "content": "Test"}],
            },
        )
        assert response.status_code == 404

        # Invalid request - no messages
        response = await client.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": "string-model",
                "messages": [],
            },
        )
        assert response.status_code == 400

        # Invalid request - last message is assistant
        response = await client.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": "string-model",
                "messages": [{"role": "assistant", "content": "I am assistant"}],
            },
        )
        assert response.status_code == 400

    # Test with AsyncOpenAI client for real OpenAI client compatibility
    token = await server.generate_token()
    openai_client = AsyncOpenAI(base_url=f"{base_url}/v1", api_key=token)

    # Test non-streaming with AsyncOpenAI
    response = await openai_client.chat.completions.create(
        model="string-model",
        messages=[{"role": "user", "content": "Hello from OpenAI client"}],
        max_tokens=50,
        stream=False,
    )
    assert response.model == "string-model"
    assert response.choices[0].message.role == "assistant"
    assert (
        "String response to: Hello from OpenAI client"
        in response.choices[0].message.content
    )

    # Test streaming with AsyncOpenAI
    response = await openai_client.chat.completions.create(
        model="streaming-model",
        messages=[{"role": "user", "content": "Stream test"}],
        max_tokens=3,
        stream=True,
    )

    chunks_received = 0
    async for chunk in response:
        chunks_received += 1
        assert chunk.choices[0].delta.role in ["assistant", None]
        if chunk.choices[0].delta.content:
            assert isinstance(chunk.choices[0].delta.content, str)
        # Don't collect too many chunks for testing
        if chunks_received >= 3:
            break

    assert chunks_received > 0  # Ensure we received some chunks

    # Test dict model with AsyncOpenAI
    response = await openai_client.chat.completions.create(
        model="dict-model",
        messages=[{"role": "user", "content": "Test dict with OpenAI client"}],
        stream=False,
    )
    assert response.model == "dict-model"
    assert response.choices[0].message.content == "Dict response from model"


@pytest.mark.asyncio
async def test_openai_server_edge_cases(websocket_server):
    """Test edge cases for OpenAI server including null max_tokens."""

    # Model that generates long content to test max_tokens handling
    async def long_content_model(request: dict):
        # Generate content longer than typical max_tokens
        for i in range(100):
            yield f"word{i} "

    model_registry = {"long-model": long_content_model}
    app = create_openai_chat_server(model_registry)

    server = await connect_to_server({"server_url": WS_SERVER_URL})
    workspace = server.config["workspace"]
    service_id = "openai-edge-test"

    await register_asgi_service(server, service_id, app)

    async with httpx.AsyncClient() as client:
        base_url = f"{WS_SERVER_URL}/{workspace}/apps/{service_id}"

        # Test with null max_tokens (should not cause errors)
        response = await client.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": "long-model",
                "messages": [{"role": "user", "content": "Generate long text"}],
                "max_tokens": None,  # This was causing bugs before the fix
                "stream": False,
            },
        )
        assert response.status_code == 200
        data = response.json()
        # Should not be truncated when max_tokens is None
        content = data["choices"][0]["message"]["content"]
        assert len(content.split()) >= 50  # Should be much longer

        # Test with specific max_tokens limit
        response = await client.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": "long-model",
                "messages": [{"role": "user", "content": "Generate long text"}],
                "max_tokens": 5,
                "stream": False,
            },
        )
        assert response.status_code == 200
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        # Should be truncated to max_tokens
        word_count = len([w for w in content.split() if w.strip()])
        assert word_count <= 5


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
