"""Test the MCP server utilities."""

import pytest
import pytest_asyncio
from fastmcp import FastMCP, Client
from hypha_rpc import connect_to_server, connect_to_server_sync
from hypha_rpc.utils.schema import schema_function
from hypha_rpc.utils.mcp import (
    create_mcp_from_service,
    create_mcp_from_workspace,
    serve_mcp,
)
from pydantic import Field
from typing import Dict, Any, List

from . import WS_SERVER_URL


@schema_function
def add(
    a: int = Field(..., description="First integer to add"),
    b: int = Field(..., description="Second integer to add"),
) -> int:
    """Adds two integers together."""
    return a + b


@schema_function
def subtract(
    a: int = Field(..., description="Integer to subtract from"),
    b: int = Field(..., description="Integer to subtract"),
) -> int:
    """Subtracts the second integer from the first."""
    return a - b


@schema_function
def multiply(
    a: int = Field(..., description="First integer to multiply"),
    b: int = Field(..., description="Second integer to multiply"),
) -> int:
    """Multiplies two integers together."""
    return a * b


@schema_function
def divide(
    a: int = Field(..., description="Numerator"),
    b: int = Field(..., description="Denominator (cannot be zero)"),
) -> float:
    """Divides the first integer by the second."""
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b


@schema_function
def execute_python(
    code: str = Field(
        ...,
        description="The Python code to execute. Use `__result__ = value` to return a value.",
    )
) -> str:
    """Executes a snippet of Python code and returns the result as a string.

    The code should assign to a variable named __result__ to return a value,
    otherwise a success message will be returned.

    Example:
        __result__ = 2 + 2  # Will return "4"
    """
    # Create a dictionary to store local variables after execution
    local_vars = {}

    # Execute the code and capture output
    try:
        # We use exec with a locals dictionary to capture variable assignments
        exec(code, globals(), local_vars)

        # Check if there's a result in local_vars
        if "__result__" in local_vars:
            return str(local_vars["__result__"])

        # If no explicit result, return success message
        return "Code executed successfully"
    except Exception as e:
        return f"Error: {str(e)}"


@pytest_asyncio.fixture
async def hypha_server_with_services(websocket_server):
    """Fixture that creates a Hypha server with calculator and python interpreter services."""
    # Connect to the Hypha server
    server = await connect_to_server({"name": "mcp-test", "server_url": WS_SERVER_URL})

    # Register the calculator service
    calc_info = await server.register_service(
        {
            "id": "calculator",
            "name": "Calculator Service",
            "description": "A simple calculator service",
            "config": {
                "visibility": "public",
                "run_in_executor": True,
            },
            "add": add,
            "subtract": subtract,
            "multiply": multiply,
            "divide": divide,
        }
    )

    # Register the Python interpreter service
    interpreter_info = await server.register_service(
        {
            "id": "interpreter",
            "name": "Python Interpreter",
            "description": "A service to execute Python code",
            "config": {
                "visibility": "public",
                "run_in_executor": True,
            },
            "execute": execute_python,
        }
    )

    try:
        yield server, calc_info, interpreter_info
    finally:
        # Clean up
        await server.disconnect()


@pytest_asyncio.fixture
async def mcp_service_with_all_features(websocket_server):
    """Fixture that creates a Hypha server with an MCP service that has tools, resources, and prompts."""
    # Connect to the Hypha server
    server = await connect_to_server(
        {"name": "mcp-features-test", "server_url": WS_SERVER_URL}
    )

    # Define a simple resource read function
    @schema_function
    def resource_read():
        return "This is a test resource content"

    # Define a simple prompt read function
    @schema_function
    def prompt_read(name: str = Field(..., description="Name of the prompt")):
        return f"This is a test prompt template, name: {name}"

    # Create a simple tool function
    @schema_function
    def simple_tool(text: str = Field(..., description="Text to process")) -> str:
        """A simple tool that processes text."""
        return f"Processed: {text}"

    # Register the MCP service with tools, resources, and prompts
    mcp_service_info = await server.register_service(
        {
            "id": "mcp-features",
            "name": "MCP Features Service",
            "description": "A service with MCP features",
            "type": "mcp",
            "config": {
                "visibility": "public",
                "run_in_executor": True,
            },
            "tools": [simple_tool],
            "resources": [
                {
                    "uri": "resource://test",
                    "name": "Test Resource",
                    "description": "A test resource",
                    "tags": ["test", "resource"],
                    "mime_type": "text/plain",
                    "read": resource_read,
                }
            ],
            "prompts": [
                {
                    "name": "Test Prompt",
                    "description": "A test prompt template",
                    "tags": ["test", "prompt"],
                    "read": prompt_read,
                }
            ],
        }
    )

    try:
        yield server, mcp_service_info
    finally:
        # Clean up
        await server.disconnect()


@pytest_asyncio.fixture
async def calculator_service(hypha_server_with_services):
    """Fixture that returns the calculator service."""
    server, calc_info, interpreter_info = hypha_server_with_services
    return await server.get_service(calc_info.id)


@pytest_asyncio.fixture
async def interpreter_service(hypha_server_with_services):
    """Fixture that returns the interpreter service."""
    server, calc_info, interpreter_info = hypha_server_with_services
    return await server.get_service(interpreter_info.id)


@pytest_asyncio.fixture
async def service_mcp(calculator_service):
    """Fixture that creates an MCP server from a single service."""
    # Create MCP server from calculator service
    mcp = await create_mcp_from_service(calculator_service)
    yield mcp


@pytest_asyncio.fixture
async def workspace_mcp(hypha_server_with_services):
    """Fixture that creates an MCP server from all services in the workspace."""
    # Create MCP server from all services in the workspace
    server, _, _ = hypha_server_with_services

    # Use the create_mcp_from_workspace function directly
    mcp = await create_mcp_from_workspace(server)

    # Make sure we've properly set up the MCP server by checking available tools
    # The new fastmcp version doesn't expose _mounted_servers, so we check tools instead
    async with Client(mcp) as client:
        tools = await client.list_tools()
        tool_names = [tool.name for tool in tools]
        # Check that we have calculator and interpreter tools available
        has_calculator = any(
            "add" in name
            or "subtract" in name
            or "multiply" in name
            or "divide" in name
            for name in tool_names
        )
        has_interpreter = any("execute" in name for name in tool_names)
        assert has_calculator, f"Calculator tools not found in {tool_names}"
        assert has_interpreter, f"Interpreter tools not found in {tool_names}"

    yield mcp


@pytest.mark.asyncio
async def test_service_mcp_creation(service_mcp):
    """Test that an MCP server can be created from a single service."""
    # Assert that the MCP server has been created
    assert isinstance(service_mcp, FastMCP)

    # Verify that the MCP server has the expected tools
    tools = await service_mcp.get_tools()
    # Extract tool names - Tool objects have a name attribute
    tool_names = [tool.name for tool in tools.values()]
    assert "add" in tool_names
    assert "subtract" in tool_names
    assert "multiply" in tool_names
    assert "divide" in tool_names
    # Interpreter tools should not be present
    assert "execute" not in tool_names


@pytest.mark.asyncio
async def test_service_mcp_client(service_mcp):
    """Test that a client can connect to a service MCP and use its tools."""
    # Define test values
    a_value = 5
    b_value = 3

    # Connect a client to the MCP server
    async with Client(service_mcp) as client:
        # Verify client is connected
        assert client.is_connected()

        # List available tools - handle different FastMCP versions
        tools = await client.list_tools()

        # The tools may be returned as a list of Tool objects, a dict of Tool objects,
        # or a dict with schema information
        if isinstance(tools, list):
            # Get tool names from Tool objects
            tool_names = [
                (
                    tool.name
                    if hasattr(tool, "name")
                    else tool.get("function", {}).get("name")
                )
                for tool in tools
            ]
        elif isinstance(tools, dict):
            # Dict could contain Tool objects or schema dicts
            if tools and hasattr(next(iter(tools.values())), "name"):
                # Dict of Tool objects
                tool_names = [tool.name for tool in tools.values()]
            else:
                # Dict of schema objects
                tool_names = list(tools.keys())
        else:
            tool_names = []

        assert "add" in tool_names
        assert "subtract" in tool_names
        assert "multiply" in tool_names
        assert "divide" in tool_names

        # Test add tool
        add_result = await client.call_tool("add", {"a": a_value, "b": b_value})

        # Handle different result formats - new fastmcp returns CallToolResult
        if hasattr(add_result, "content") and add_result.content:
            # New fastmcp format: CallToolResult with content array
            result_text = add_result.content[0].text
        elif hasattr(add_result, "text"):
            # Single result object with text attribute
            result_text = add_result.text
        elif isinstance(add_result, list) and len(add_result) > 0:
            # List of results - get first element's text or value
            first_result = add_result[0]
            if hasattr(first_result, "text"):
                result_text = first_result.text
            elif hasattr(first_result, "value"):
                result_text = str(first_result.value)
            else:
                result_text = str(first_result)
        else:
            # Direct value
            result_text = str(add_result)

        # Verify the result
        assert result_text == str(a_value + b_value)


@pytest.mark.asyncio
async def test_workspace_mcp_creation(workspace_mcp):
    """Test that an MCP server can be created from all services in a workspace."""
    # Assert that the MCP server has been created
    assert isinstance(workspace_mcp, FastMCP)

    # Check that the workspace MCP has both calculator and interpreter tools
    # The new fastmcp version doesn't expose _mounted_servers, so we check tools instead
    async with Client(workspace_mcp) as client:
        tools = await client.list_tools()
        tool_names = [tool.name for tool in tools]
        # Check that we have calculator and interpreter tools available
        has_calculator = any(
            "add" in name
            or "subtract" in name
            or "multiply" in name
            or "divide" in name
            for name in tool_names
        )
        has_interpreter = any("execute" in name for name in tool_names)
        assert has_calculator, f"Calculator tools not found in {tool_names}"
        assert has_interpreter, f"Interpreter tools not found in {tool_names}"


@pytest.mark.asyncio
async def test_workspace_mcp_calculator_client(workspace_mcp):
    """Test that a client can access calculator tools from the workspace MCP."""
    # Connect a client to the MCP server
    async with Client(workspace_mcp) as client:
        # Verify client is connected
        assert client.is_connected()

        # List available tools (should include both calculator and interpreter tools)
        tools = await client.list_tools()
        # make sure there are 4 tools
        assert len(tools) == 5

        # and calculator_add exists
        assert "calculator_add" in [tool.name for tool in tools]

        # Test calculator tools
        add_result = await client.call_tool("calculator_add", {"a": 5, "b": 3})

        # Extract result text - handle new fastmcp CallToolResult format
        if hasattr(add_result, "content") and add_result.content:
            # New fastmcp format: CallToolResult with content array
            result_text = add_result.content[0].text
        elif hasattr(add_result, "text"):
            result_text = add_result.text
        elif isinstance(add_result, list) and len(add_result) > 0:
            first_result = add_result[0]
            if hasattr(first_result, "text"):
                result_text = first_result.text
            elif hasattr(first_result, "value"):
                result_text = str(first_result.value)
            else:
                result_text = str(first_result)
        else:
            result_text = str(add_result)

        assert result_text == "8"

        subtract_result = await client.call_tool(
            "calculator_subtract", {"a": 10, "b": 4}
        )

        # Extract result text - handle new fastmcp CallToolResult format
        if hasattr(subtract_result, "content") and subtract_result.content:
            # New fastmcp format: CallToolResult with content array
            result_text = subtract_result.content[0].text
        elif hasattr(subtract_result, "text"):
            result_text = subtract_result.text
        elif isinstance(subtract_result, list) and len(subtract_result) > 0:
            first_result = subtract_result[0]
            if hasattr(first_result, "text"):
                result_text = first_result.text
            elif hasattr(first_result, "value"):
                result_text = str(first_result.value)
            else:
                result_text = str(first_result)
        else:
            result_text = str(subtract_result)

        assert result_text == "6"


@pytest.mark.asyncio
async def test_workspace_mcp_interpreter_client(workspace_mcp):
    """Test that a client can access interpreter tools from the workspace MCP."""
    # Connect a client to the MCP server
    async with Client(workspace_mcp) as client:
        # Verify client is connected
        assert client.is_connected()

        # Test interpreter tool
        code = "__result__ = 2 + 2"
        result = await client.call_tool("interpreter_execute", {"code": code})

        # Extract result text - handle new fastmcp CallToolResult format
        if hasattr(result, "content") and result.content:
            # New fastmcp format: CallToolResult with content array
            result_text = result.content[0].text
        elif hasattr(result, "text"):
            result_text = result.text
        elif isinstance(result, list) and len(result) > 0:
            first_result = result[0]
            if hasattr(first_result, "text"):
                result_text = first_result.text
            elif hasattr(first_result, "value"):
                result_text = str(first_result.value)
            else:
                result_text = str(first_result)
        else:
            result_text = str(result)

        assert result_text == "4"

        # Test more complex code
        code = """
def factorial(n):
    result = 1
    for i in range(1, n+1):
        result *= i
    return result
__result__ = factorial(5)
"""
        result = await client.call_tool("interpreter_execute", {"code": code})

        # Extract result text - handle new fastmcp CallToolResult format
        if hasattr(result, "content") and result.content:
            # New fastmcp format: CallToolResult with content array
            result_text = result.content[0].text
        elif hasattr(result, "text"):
            result_text = result.text
        elif isinstance(result, list) and len(result) > 0:
            first_result = result[0]
            if hasattr(first_result, "text"):
                result_text = first_result.text
            elif hasattr(first_result, "value"):
                result_text = str(first_result.value)
            else:
                result_text = str(first_result)
        else:
            result_text = str(result)

        assert result_text == "120"


@pytest.mark.asyncio
async def test_json_schema_conversion(service_mcp):
    """Test that JSON schema from Hypha services is properly converted to MCP schema."""
    # Connect a client to the MCP server
    async with Client(service_mcp) as client:
        # Verify client is connected
        assert client.is_connected()

        # Get the tools
        tools = await client.list_tools()
        add_tool = [tool for tool in tools if tool.name == "add"][0]
        add_schema = add_tool.inputSchema

        # Verify the schema structure
        assert "type" in add_schema
        assert add_schema["type"] == "object"
        assert "properties" in add_schema
        assert "a" in add_schema["properties"]
        assert "b" in add_schema["properties"]
        assert add_schema["properties"]["a"]["type"] == "integer"
        assert add_schema["properties"]["b"]["type"] == "integer"

        # Check that parameter descriptions from Field are correctly passed to the schema
        assert add_schema["properties"]["a"]["description"] == "First integer to add"
        assert add_schema["properties"]["b"]["description"] == "Second integer to add"

        # Check that both parameters are required
        assert "required" in add_schema
        required_params = set(add_schema["required"])
        assert "a" in required_params
        assert "b" in required_params


@pytest.mark.asyncio
async def test_workspace_mcp_interpreter_tool_schema(workspace_mcp):
    """Test that the interpreter tool schema correctly includes parameter descriptions."""
    # Connect a client to the MCP server
    async with Client(workspace_mcp) as client:
        # Verify client is connected
        assert client.is_connected()

        # Get the tools
        tools = await client.list_tools()
        interpreter_tool = [
            tool for tool in tools if tool.name == "interpreter_execute"
        ][0]
        interpreter_schema = interpreter_tool.inputSchema

        # Verify the schema structure includes our enhanced description
        assert "properties" in interpreter_schema
        assert "code" in interpreter_schema["properties"]
        assert "description" in interpreter_schema["properties"]["code"]
        assert "__result__" in interpreter_schema["properties"]["code"]["description"]

        # Test that the tool works with __result__
        code = "__result__ = 'Hello from the test!'"
        result = await client.call_tool("interpreter_execute", {"code": code})

        # Extract result text - handle new fastmcp CallToolResult format
        if hasattr(result, "content") and result.content:
            # New fastmcp format: CallToolResult with content array
            result_text = result.content[0].text
        elif hasattr(result, "text"):
            result_text = result.text
        elif isinstance(result, list) and len(result) > 0:
            first_result = result[0]
            if hasattr(first_result, "text"):
                result_text = first_result.text
            elif hasattr(first_result, "value"):
                result_text = str(first_result.value)
            else:
                result_text = str(first_result)
        else:
            result_text = str(result)

        assert result_text == "Hello from the test!"


@pytest.mark.asyncio
async def test_serve_mcp_calculator(hypha_server_with_services):
    """Test that an MCP service can be created and served using serve_mcp."""
    from hypha_rpc.utils.mcp import serve_mcp
    import requests

    # Get the server and calculator service info
    server, calc_info, _ = hypha_server_with_services

    # Serve the calculator service as an MCP
    mcp_info = await serve_mcp(server, calc_info.id)
    # await server.serve()

    # Verify that the URL is returned
    assert isinstance(mcp_info, dict)
    assert "mcpServers" in mcp_info

    # Create a client to connect to the served MCP

    # Test the client connection and tool usage
    async with Client(mcp_info) as client:
        # Verify client is connected
        assert client.is_connected()

        # List available tools
        tools = await client.list_tools()

        # Extract tool names
        if isinstance(tools, list):
            tool_names = [
                (
                    tool.name
                    if hasattr(tool, "name")
                    else tool.get("function", {}).get("name")
                )
                for tool in tools
            ]
        elif isinstance(tools, dict):
            if tools and hasattr(next(iter(tools.values())), "name"):
                tool_names = [tool.name for tool in tools.values()]
            else:
                tool_names = list(tools.keys())
        else:
            tool_names = []

        # Check that calculator tools are available
        assert "add" in tool_names
        assert "subtract" in tool_names
        assert "multiply" in tool_names
        assert "divide" in tool_names

        # Test add tool
        add_result = await client.call_tool("add", {"a": 7, "b": 3})

        # Extract result text - handle new fastmcp CallToolResult format
        if hasattr(add_result, "content") and add_result.content:
            # New fastmcp format: CallToolResult with content array
            result_text = add_result.content[0].text
        elif hasattr(add_result, "text"):
            result_text = add_result.text
        elif isinstance(add_result, list) and len(add_result) > 0:
            first_result = add_result[0]
            if hasattr(first_result, "text"):
                result_text = first_result.text
            elif hasattr(first_result, "value"):
                result_text = str(first_result.value)
            else:
                result_text = str(first_result)
        else:
            result_text = str(add_result)

        # Verify the result
        assert result_text == "10"


@pytest.mark.asyncio
async def test_mcp_service_with_all_features(mcp_service_with_all_features):
    """Test that a service with type=mcp can handle tools, resources, and prompts."""
    server, service_info = mcp_service_with_all_features

    # Get the service
    service = await server.get_service(service_info.id)

    # Create MCP from service
    mcp = await create_mcp_from_service(service)

    # Verify MCP was created properly
    assert isinstance(mcp, FastMCP)

    # Connect a client to test the features
    async with Client(mcp) as client:
        # Verify client is connected
        assert client.is_connected()

        # Test tools
        tools = await client.list_tools()
        tool_names = [tool.name for tool in tools]
        assert "simple_tool" in tool_names

        # Test the tool
        result = await client.call_tool("simple_tool", {"text": "Hello world"})
        # Handle different result formats - new fastmcp returns CallToolResult
        if hasattr(result, "content") and result.content:
            # New fastmcp format: CallToolResult with content array
            result_text = result.content[0].text
        elif hasattr(result, "text"):
            result_text = result.text
        elif isinstance(result, list) and len(result) > 0:
            first_result = result[0]
            result_text = str(
                first_result.text if hasattr(first_result, "text") else first_result
            )
        else:
            result_text = str(result)
        assert result_text == "Processed: Hello world"

        # Test resources
        resources = await client.list_resources()
        assert len(resources) > 0

        # Find the test resource
        test_resource = None
        for resource in resources:
            if resource.name == "Test Resource":
                test_resource = resource
                break

        assert test_resource is not None
        assert str(test_resource.uri) == "resource://test"
        assert test_resource.description == "A test resource"

        # Test reading resource content
        resource_content = await client.read_resource(test_resource.uri)
        assert resource_content[0].text == "This is a test resource content"

        # Test prompts
        prompts = await client.list_prompts()
        assert len(prompts) > 0

        # Find the test prompt
        test_prompt = None
        for prompt in prompts:
            if prompt.name == "Test Prompt":
                test_prompt = prompt
                break

        assert test_prompt is not None
        assert test_prompt.description == "A test prompt template"

        # Test reading prompt content
        prompt_content = await client.get_prompt(test_prompt.name, {"name": "test"})

        # Test prompt content with parameter substitution
        assert (
            prompt_content.messages[0].content.text
            == "This is a test prompt template, name: test"
        )
