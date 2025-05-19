"""
This script registers a Python interpreter service as an MCP server.
The service is registered with a single tool, the Python code interpreter.
"""

import asyncio
import json
from hypha_rpc import login, connect_to_server
from hypha_rpc.utils.schema import schema_function
from hypha_rpc.utils.mcp import serve_mcp
from pydantic import Field


# Define the Python code interpreter function
@schema_function
def execute_python(
    code: str = Field(
        ...,
        description="The Python code to execute. Use `__result__ = value` to return a value.",
    )
) -> str:
    """Executes a snippet of Python code and returns the result as a string.
    The code should assign to a variable named __result__ to return a value.
    """
    local_vars = {}
    try:
        exec(code, globals(), local_vars)
        if "__result__" in local_vars:
            return str(local_vars["__result__"])
        return "Code executed successfully"
    except Exception as e:
        return f"Error: {str(e)}"


async def main(server_url):
    user_info = await login({"server_url": server_url, "profile": True})
    print(f"Logged in as: {user_info}")
    server = await connect_to_server(
        {"server_url": server_url, "token": user_info.token}
    )
    server.on("connected", lambda info: print("Connected to server: ", info))

    # Register the Python interpreter service
    interpreter_info = await server.register_service(
        {
            "id": "interpreter",
            "name": "Python Interpreter",
            "description": "A service to execute Python code",
            "config": {"visibility": "public", "run_in_executor": True},
            "execute": execute_python,
        }
    )
    print(f"Interpreter service registered at workspace: {server.config.workspace}")

    # Serve the interpreter service as an MCP
    mcp_info = await serve_mcp(server, interpreter_info.id)
    # print mcp info in pretty json format
    print(
        f"Please use the following mcp server info in any MCP client: \n{json.dumps(mcp_info, indent=4)}"
    )

    # Wait forever (serve)
    await server.serve()


if __name__ == "__main__":
    server_url = "https://hypha.aicell.io"
    asyncio.run(main(server_url))
