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
from fastmcp.tools import Tool
import inspect
from fastmcp.prompts.prompt import (
    Prompt,
    PromptArgument,
    compress_schema,
    validate_call,
)


logger = logging.getLogger("hypha_rpc.utils.mcp")


def create_fn(read: Callable):
    async def fn():
        return await read()

    return fn


def create_prompt(prompt_data: dict):
    """
    Create a prompt from prompt data.

    Args:
        prompt_data: Dictionary containing prompt information including read function

    Returns:
        Prompt instance
    """
    # extract the original read function and its schema
    orig_fn = prompt_data.get("read")
    schema = getattr(orig_fn, "__schema__", {}) or {}
    parameters = schema.get("parameters", {})
    # convert to JSON schema for arguments
    parameters = compress_schema(parameters, prune_params=None)

    # build PromptArgument list
    arguments: list[PromptArgument] = []
    if "properties" in parameters:
        for param_name, param in parameters["properties"].items():
            arguments.append(
                PromptArgument(
                    name=param_name,
                    description=param.get("description"),
                    required=param_name in parameters.get("required", []),
                )
            )

    # wrap the read function to ensure it is awaited if needed
    async def fn(**kwargs: Any):
        result = orig_fn(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result
        # ensure the arguments are properly cast

    fn = validate_call(fn)
    # create the Prompt with wrapped function
    return Prompt(
        name=prompt_data.get("name", "unnamed_prompt"),
        description=prompt_data.get("description"),
        tags=prompt_data.get("tags", set()),
        arguments=arguments,
        fn=fn,
    )


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

    tools_list = extract_tools(tools)
    for tool in tools_list:
        try:
            mcp._tool_manager.add_tool(tool)
            logger.debug(f"Added tool {tool.name} from service {name}")
        except Exception as e:
            logger.error(f"Failed to add tool {tool.name} from service {name}: {e}")
            logger.exception(e)
    return mcp


def extract_tools(obj: Any, prefix: str = "") -> List[Tool]:
    """
    Recursively convert functions from an object into MCP tools.

    Handles nested dictionaries and lists.

    Args:
        obj: Object containing tool functions, can be nested dict/list
        prefix: Prefix for nested tool names

    Returns:
        List of Tool objects
    """
    tool_list = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            new_prefix = f"{prefix}_{key}" if prefix else key
            if callable(value):
                tool_list.append(create_tool(value, new_prefix))
            elif isinstance(value, (dict, list)):
                tool_list.extend(extract_tools(value, new_prefix))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):

            if callable(value):
                new_prefix = (
                    f"{prefix}_{value.__name__}" if prefix else str(value.__name__)
                )
                tool_list.append(create_tool(value, new_prefix))
            elif isinstance(value, (dict, list)):
                new_prefix = f"{prefix}_{i}" if prefix else str(i)
                tool_list.extend(extract_tools(value, new_prefix))
    else:
        for member_name in dir(obj):
            if member_name.startswith("_"):
                continue
            member = getattr(obj, member_name)
            new_prefix = f"{prefix}_{member_name}" if prefix else member_name
            if callable(member):
                tool_list.append(create_tool(member, new_prefix))
            elif isinstance(member, (dict, list)):
                tool_list.extend(extract_tools(member, new_prefix))
    return tool_list


def create_tool(fn: Callable, name: str) -> Tool:
    """
    Create a Tool object from a function.

    Args:
        fn: Function to convert to tool
        name: Name for the tool

    Returns:
        Tool object
    """
    description = getattr(fn, "__doc__", "") or ""
    schema = getattr(fn, "__schema__", {}) or {}
    if not description and "description" in schema:
        description = schema["description"]

    return Tool(
        fn=fn,
        name=name,
        description=description.strip(),
        parameters=schema.get("parameters", {}),
        annotations=ToolAnnotations(title=name, readOnlyHint=True),
    )


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

    return {"mcpServers": {"url": mcp_url}}
