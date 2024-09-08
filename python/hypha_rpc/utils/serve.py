import argparse
import importlib
import asyncio
from hypha_rpc import connect_to_server, login
import time
import random
from typing import List, Literal, Union, Optional, Callable, Dict
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

# Define the model registry type for better type hints
ModelRegistry = Dict[str, callable]


# Base API Models
class ModelCard(BaseModel):
    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "owner"
    root: Optional[str] = None
    parent: Optional[str] = None
    permission: Optional[list] = None


class ModelList(BaseModel):
    object: str = "list"
    data: List[ModelCard] = []


class ChatMessageInput(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: Union[str, List[str]]
    name: Optional[str] = None


class ChatMessageResponse(BaseModel):
    role: Literal["assistant"]
    content: str = None
    name: Optional[str] = None


class DeltaMessage(BaseModel):
    role: Optional[Literal["user", "assistant", "system"]] = None
    content: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessageInput]
    temperature: Optional[float] = 0.8
    top_p: Optional[float] = 0.8
    max_tokens: Optional[int] = 50
    stream: Optional[bool] = False


class ChatCompletionResponseChoice(BaseModel):
    index: int
    message: ChatMessageResponse


class ChatCompletionResponseStreamChoice(BaseModel):
    index: int
    delta: DeltaMessage


class ChatCompletionResponse(BaseModel):
    model: str
    object: Literal["chat.completion", "chat.completion.chunk"]
    choices: List[
        Union[ChatCompletionResponseChoice, ChatCompletionResponseStreamChoice]
    ]
    created: Optional[int] = Field(default_factory=lambda: int(time.time()))


# Function to create FastAPI app based on the model registry
def create_openai_chat_server(model_registry: ModelRegistry) -> FastAPI:
    app = FastAPI()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/v1/models", response_model=ModelList)
    async def list_models():
        model_cards = [ModelCard(id=model_name) for model_name in model_registry.keys()]
        return ModelList(data=model_cards)

    @app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
    async def create_chat_completion(request: ChatCompletionRequest):
        if len(request.messages) < 1 or request.messages[-1].role == "assistant":
            raise HTTPException(status_code=400, detail="Invalid request")

        model_id = request.model
        if model_id not in model_registry:
            raise HTTPException(status_code=404, detail="Model not found")

        # Get the model's text generator function
        text_generator = model_registry[model_id]

        # Streaming mode
        if request.stream:
            generate = text_generator(request.dict())
            return EventSourceResponse(
                stream_chunks(generate), media_type="text/event-stream"
            )

        # Non-streaming mode
        response_text = ""
        async for chunk in text_generator(request.dict()):
            response_text += chunk
            if len(response_text) >= request.max_tokens:
                break
        response_text = response_text[: request.max_tokens]

        message = ChatMessageResponse(role="assistant", content=response_text)
        choice_data = ChatCompletionResponseChoice(index=0, message=message)
        return ChatCompletionResponse(
            model=request.model, choices=[choice_data], object="chat.completion"
        )

    return app


# Streaming helper to package text chunks in the response structure
async def stream_chunks(generator: Callable) -> dict:
    async for chunk in generator:
        if isinstance(chunk, str):
            delta = DeltaMessage(content=chunk, role="assistant")
            choice_data = ChatCompletionResponseStreamChoice(index=0, delta=delta)
            chunk_response = ChatCompletionResponse(
                model="test-chat-model",
                choices=[choice_data],
                object="chat.completion.chunk",
            )
            yield chunk_response.model_dump_json(exclude_unset=True)
        elif isinstance(chunk, dict):
            chunk_response = ChatCompletionResponse.model_validate(chunk)
            yield chunk_response.model_dump_json(exclude_unset=True)
        else:
            raise ValueError("Invalid chunk type")


async def serve_app(
    app: FastAPI,
    server_url: str,
    service_id: str,
    workspace: str = None,
    token: str = None,
    disable_ssl: bool = False,
    service_name: str = None,
):
    # Connection options
    connection_options = {
        "server_url": server_url,
        "workspace": workspace,
        "token": token,
    }
    if disable_ssl:
        connection_options["ssl"] = False

    # Connect to the Hypha server
    server = await connect_to_server(connection_options)
    svc_info = await register_asgi_service(server, service_id, app)
    print(
        f"Access your app at: {server_url}/{server.config.workspace}/apps/{svc_info['id'].split(':')[1]}"
    )
    # Keep the server running
    await server.serve()


async def register_asgi_service(server, service_id, app, **kwargs):
    async def serve_fastapi(args, context=None):
        await app(args["scope"], args["receive"], args["send"])

    svc = {
        "id": service_id,
        "name": service_id,
        "type": "asgi",
        "serve": serve_fastapi,
        "config": {"visibility": "public"},
    }
    svc.update(kwargs)
    svc_info = await server.register_service(svc)
    return svc_info


async def main(args):
    if args.login:
        if args.token:
            raise ValueError("Cannot use --token when --login is enabled.")
        login_options = {"server_url": args.server_url}
        if args.disable_ssl:
            login_options["ssl"] = False
        # Perform login to get the token
        token = await login(login_options)
    else:
        if not args.token:
            raise ValueError("Either --token or --login must be provided.")
        token = args.token

    # Import the app dynamically
    module_name, app_name = args.app.split(":")
    module = importlib.import_module(module_name)
    app = getattr(module, app_name)

    if not isinstance(app, FastAPI):
        raise TypeError("The specified app is not a FastAPI instance")

    # Start serving the app asynchronously
    await serve_app(
        app,
        args.server_url,
        args.id,
        args.workspace,
        token,
        args.disable_ssl,
        args.name,
    )


def main_entry():
    parser = argparse.ArgumentParser(description="Serve FastAPI app using Hypha")
    parser.add_argument(
        "app", type=str, help="The FastAPI app to serve (e.g., myapp:app)"
    )
    parser.add_argument("--id", type=str, required=True, help="The service ID")
    parser.add_argument(
        "--name", type=str, default=None, required=False, help="The service name"
    )
    parser.add_argument(
        "--server-url", type=str, required=True, help="The Hypha server URL"
    )
    parser.add_argument(
        "--workspace", type=str, default=None, help="The workspace to connect to"
    )
    parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="The token for authentication (not needed if --login is used)",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Enable login to get the token (overrides --token)",
    )
    parser.add_argument(
        "--disable-ssl", action="store_true", help="Disable SSL verification"
    )

    args = parser.parse_args()

    # Run the main coroutine indefinitely
    asyncio.run(main(args))


if __name__ == "__main__":
    main_entry()
