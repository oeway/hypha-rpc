"""
Utility functions for MCP (Model/Controller/Provider) integration with Hypha.
"""

import logging
from typing import Any, Callable, List
from starlette.types import Receive, Scope, Send
from starlette.middleware import Middleware
from hypha_rpc.utils.serve import register_asgi_service
from fastmcp import FastMCP
from mcp.types import ToolAnnotations
import inspect
from fastmcp.prompts.prompt import (
    Prompt,
    PromptArgument,
    compress_schema,
)
from pydantic import validate_call


logger = logging.getLogger("hypha_rpc.utils.mcp")


def create_fn(read: Callable):
    async def fn():
        return await read()

    return fn


def create_prompt(prompt_data: dict):
    """
    Create a prompt from prompt data using the new fastmcp API.

    Args:
        prompt_data: Dictionary containing prompt information including read function

    Returns:
        FunctionPrompt instance
    """
    # extract the original read function
    orig_fn = prompt_data.get("read")
    schema = getattr(orig_fn, "__schema__", {}) or {}

    # Create a typed wrapper for the prompt function
    wrapper_fn = create_typed_prompt_wrapper(
        orig_fn, prompt_data.get("name", "unnamed_prompt"), schema
    )

    # Use the new from_function API
    return Prompt.from_function(
        wrapper_fn,
        name=prompt_data.get("name", "unnamed_prompt"),
        description=prompt_data.get("description"),
        tags=prompt_data.get("tags", set()),
    )


def create_typed_prompt_wrapper(
    original_fn: Callable, name: str, schema: dict
) -> Callable:
    """
    Create a typed wrapper function for RPC remote methods used as prompts.

    Args:
        original_fn: The original RPC remote method
        name: Name of the function
        schema: Schema information from the function

    Returns:
        A wrapper function with proper typing for prompts
    """
    import inspect
    from typing import Any, Annotated
    from pydantic import Field

    # Extract parameters from schema
    parameters = schema.get("parameters", {})
    properties = parameters.get("properties", {})
    required = set(parameters.get("required", []))

    # Build the parameter list with Pydantic Field annotations
    param_list = []
    annotations = {}

    for param_name, param_info in properties.items():
        # Skip context parameter if it exists
        if param_name == "context":
            continue

        # Determine parameter type
        param_type = Any  # Default type
        if "type" in param_info:
            type_map = {
                "string": str,
                "integer": int,
                "number": float,
                "boolean": bool,
                "array": list,
                "object": dict,
            }
            param_type = type_map.get(param_info["type"], Any)

        # Create Pydantic Field with description
        param_description = param_info.get("description", "")

        if param_name in required:
            # Required parameter with Field annotation
            field_annotation = Annotated[
                param_type, Field(description=param_description)
            ]
            param = inspect.Parameter(
                param_name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=field_annotation,
            )
        else:
            # Optional parameter with Field annotation and default
            field_annotation = Annotated[
                param_type, Field(default=None, description=param_description)
            ]
            param = inspect.Parameter(
                param_name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=None,
                annotation=field_annotation,
            )

        param_list.append(param)
        annotations[param_name] = field_annotation

    # Create new signature
    new_signature = inspect.Signature(param_list)

    # Create wrapper function that calls the original and returns proper format
    async def wrapper(*args, **kwargs):
        result = await original_fn(*args, **kwargs)
        # Return the result as a string (fastmcp will convert to PromptMessage)
        return str(result)

    # Set the new signature and other attributes
    wrapper.__signature__ = new_signature
    wrapper.__name__ = name
    wrapper.__doc__ = getattr(original_fn, "__doc__", "")
    wrapper.__schema__ = schema
    wrapper.__annotations__ = annotations

    return wrapper


async def create_mcp_from_service(service: dict) -> FastMCP:
    """
    Create an MCP server from a single Hypha service.

    Args:
        service: Hypha service instance
        name: Name of the MCP server (defaults to service name if available)

    Returns:
        FastMCP server instance
    """
    name = service["id"].split(":")[-1]
    mcp = FastMCP(name)

    if service.get("type") == "mcp":
        if "resources" in service:
            resources = service.get("resources", [])
            for resource_data in resources:
                try:
                    assert "read" in resource_data and callable(resource_data["read"])
                    # Create and add a resource directly
                    mcp.add_resource_fn(
                        fn=create_fn(resource_data["read"]),
                        uri=resource_data["uri"],
                        name=resource_data.get("name"),
                        description=resource_data.get("description"),
                        mime_type=resource_data.get("mime_type"),
                        tags=resource_data.get("tags", set()),
                    )
                    resource_name = resource_data.get("name", resource_data["uri"])
                    logger.debug(f"Added resource {resource_name} to service {name}")
                except Exception as e:
                    uri = resource_data.get("uri", "unknown")
                    logger.error(f"Failed to add resource {uri} to service {name}: {e}")
                    raise e
        if "prompts" in service:
            prompts = service.get("prompts", [])
            for prompt_data in prompts:
                try:
                    assert "read" in prompt_data and callable(prompt_data["read"])
                    mcp._prompt_manager.add_prompt(create_prompt(prompt_data))
                    prompt_name = prompt_data.get("name", "unnamed")
                    logger.debug(f"Added prompt {prompt_name} to service {name}")
                except Exception as e:
                    prompt_name = prompt_data.get("name", "unnamed")
                    logger.error(
                        f"Failed to add prompt {prompt_name} to service {name}: {e}"
                    )
                    raise e
        # assuming service.tools is a list of tool functions
        tools = service.get("tools", [])
    else:
        tools = service

    register_tools(mcp, tools)
    return mcp


def register_tools(mcp: FastMCP, obj: Any, prefix: str = "") -> None:
    """
    Recursively register functions from an object as MCP tools.

    Handles nested dictionaries and lists.

    Args:
        mcp: FastMCP server instance to register tools with
        obj: Object containing tool functions, can be nested dict/list
        prefix: Prefix for nested tool names
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            new_prefix = f"{prefix}_{key}" if prefix else key
            if callable(value):
                register_single_tool(mcp, value, new_prefix)
            elif isinstance(value, (dict, list)):
                register_tools(mcp, value, new_prefix)
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            if callable(value):
                new_prefix = (
                    f"{prefix}_{value.__name__}" if prefix else str(value.__name__)
                )
                register_single_tool(mcp, value, new_prefix)
            elif isinstance(value, (dict, list)):
                new_prefix = f"{prefix}_{i}" if prefix else str(i)
                register_tools(mcp, value, new_prefix)
    else:
        for member_name in dir(obj):
            if member_name.startswith("_"):
                continue
            member = getattr(obj, member_name)
            new_prefix = f"{prefix}_{member_name}" if prefix else member_name
            if callable(member):
                register_single_tool(mcp, member, new_prefix)
            elif isinstance(member, (dict, list)):
                register_tools(mcp, member, new_prefix)


def register_single_tool(mcp: FastMCP, fn: Callable, name: str) -> None:
    """
    Register a single function as an MCP tool.

    Args:
        mcp: FastMCP server instance
        fn: Function to register
        name: Name for the tool
    """
    try:
        description = getattr(fn, "__doc__", "") or ""
        schema = getattr(fn, "__schema__", {}) or {}
        if not description and "description" in schema:
            description = schema["description"]

        # Create a wrapper function with proper signature for fastmcp compatibility
        wrapper_fn = create_typed_wrapper(fn, name, schema)

        # Extract parameters schema for fastmcp
        parameters_schema = schema.get("parameters", {})

        # Register the tool using the new API
        mcp.tool(
            wrapper_fn,
            name=name,
            description=description.strip() if description else None,
            annotations=ToolAnnotations(title=name, readOnlyHint=True),
        )

        logger.debug(f"Added tool {name}")
    except Exception as e:
        logger.error(f"Failed to add tool {name}: {e}")
        logger.exception(e)


def create_typed_wrapper(original_fn: Callable, name: str, schema: dict) -> Callable:
    """
    Create a typed wrapper function for RPC remote methods using Pydantic Field annotations.

    This is needed because fastmcp doesn't support *args, but RPC remote methods
    use *args, **kwargs. We create a wrapper with proper parameter types and descriptions.

    Args:
        original_fn: The original RPC remote method
        name: Name of the function
        schema: Schema information from the function

    Returns:
        A wrapper function with proper typing and Pydantic annotations
    """
    import inspect
    from typing import Any, Annotated
    from pydantic import Field

    # Extract parameters from schema
    parameters = schema.get("parameters", {})
    properties = parameters.get("properties", {})
    required = set(parameters.get("required", []))

    # Build the parameter list with Pydantic Field annotations
    param_list = []
    annotations = {}

    for param_name, param_info in properties.items():
        # Skip context parameter if it exists
        if param_name == "context":
            continue

        # Determine parameter type
        param_type = Any  # Default type
        if "type" in param_info:
            type_map = {
                "string": str,
                "integer": int,
                "number": float,
                "boolean": bool,
                "array": list,
                "object": dict,
            }
            param_type = type_map.get(param_info["type"], Any)

        # Create Pydantic Field with description
        param_description = param_info.get("description", "")

        if param_name in required:
            # Required parameter with Field annotation
            field_annotation = Annotated[
                param_type, Field(description=param_description)
            ]
            param = inspect.Parameter(
                param_name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=field_annotation,
            )
        else:
            # Optional parameter with Field annotation and default
            field_annotation = Annotated[
                param_type, Field(default=None, description=param_description)
            ]
            param = inspect.Parameter(
                param_name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=None,
                annotation=field_annotation,
            )

        param_list.append(param)
        annotations[param_name] = field_annotation

    # Create new signature
    new_signature = inspect.Signature(param_list)

    # Create wrapper function that calls the original
    if inspect.iscoroutinefunction(original_fn):

        async def wrapper(*args, **kwargs):
            return await original_fn(*args, **kwargs)

    else:

        def wrapper(*args, **kwargs):
            return original_fn(*args, **kwargs)

    # Set the new signature and other attributes
    wrapper.__signature__ = new_signature
    wrapper.__name__ = name
    wrapper.__doc__ = getattr(original_fn, "__doc__", "")
    wrapper.__schema__ = schema
    wrapper.__annotations__ = annotations

    return wrapper


async def create_mcp_from_workspace(server) -> FastMCP:
    """
    Create an MCP server from all services in a Hypha workspace using composition.

    Args:
        server: Hypha server instance
        name: Name of the main MCP server
        visibility: Service visibility filter ("public", "protected", or "private")

    Returns:
        FastMCP server instance with all services mounted
    """
    # Create main MCP server
    main_mcp = FastMCP(server.config.workspace)
    try:
        # Get all services in the workspace
        services = await server.list_services()
        # Process each service
        for service_info in services:
            service_id = service_info["id"]
            if service_id.endswith(":built-in"):
                continue
            # Get the service
            service = await server.get_service(service_id)
            # Create an MCP for this service
            service_mcp = await create_mcp_from_service(service)
            # Mount the service MCP with a namespace
            main_mcp.mount(service_id.split(":")[-1], service_mcp)
            logger.info(f"Mounted service {service_id} on workspace MCP")
    except Exception as e:
        logger.error(f"Error creating workspace MCP: {e}")
    return main_mcp


class SetBasePathMiddleware:
    """Middleware to patch SSE message URLs with the correct base path."""

    def __init__(self, app: Any, base_path: str = None):
        """
        Initialize the middleware.

        Args:
            app: ASGI application
            base_path: Base path to use for URL patching
        """
        self.app = app
        self.base_path = base_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process an incoming request. If HTTP request, apply base path patching."""
        if scope["type"] != "http":
            # If not HTTP, pass through unchanged
            await self.app(scope, receive, send)
            return

        async def _send(*args, **kwargs):
            # patch the args so we use the absolute url for messages
            if len(args) > 0:
                for d in args:
                    if (
                        "body" in d
                        and b"event: endpoint\r\ndata: /messages/" in d["body"]
                    ):
                        path_replace = "event: endpoint\r\ndata: /messages/"
                        path_with = (
                            f"event: endpoint\r\ndata: {self.base_path}/messages/"
                        )
                        d["body"] = (
                            d["body"].decode("utf-8").replace(path_replace, path_with)
                        )
                        d["body"] = d["body"].encode("utf-8")
            return await send(*args, **kwargs)

        await self.app(scope, receive, _send)


async def serve_mcp(server, service_id: str = None, mcp_service_id: str = None):
    """
    Serve an MCP server from a service or workspace.

    Args:
        server: The Hypha server instance
        service_id: Optional ID of the service to create MCP from. If None,
                   creates from workspace
        mcp_service_id: Optional custom ID for the MCP service.
                       Defaults to "mcp-{service_id}"

    Returns:
        Dict containing the URL of the registered MCP service
    """
    if service_id is None:
        mcp = await create_mcp_from_workspace(server)
        mcp_service_id = mcp_service_id or "mcp"
        service_name = "MCP"
    else:
        service = await server.get_service(service_id)
        mcp = await create_mcp_from_service(service)
        mcp_service_id = mcp_service_id or "mcp-" + service_id.split(":")[1]
        service_name = service.name

    base_path = f"/{server.config.workspace}/apps/{mcp_service_id}"
    middleware = [Middleware(cls=SetBasePathMiddleware, base_path=base_path)]

    mcp_app = mcp.http_app(path="/sse", transport="sse", middleware=middleware)
    await mcp_app.router.lifespan_context.__aenter__()

    svc_info = await register_asgi_service(
        server, mcp_service_id, mcp_app, service_name=service_name
    )
    svc_id = svc_info["id"].split("/")[1]

    mcp_url = (
        f"{server.config.public_base_url}/{server.config.workspace}/"
        f"apps/{svc_id}/sse"
    )
    logger.info(f"MCP server running at {mcp_url}")

    return {"mcpServers": {mcp_service_id: {"url": mcp_url, "transport": "sse"}}}
