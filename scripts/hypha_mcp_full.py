"""
This script registers a Python interpreter service with tools, resources and prompts.
The service is registered as an MCP server and can be used in any MCP client.
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


# Define resource read functions
@schema_function
def read_numpy_guide() -> str:
    """Returns a guide on how to use NumPy for numerical computing."""
    return """# NumPy Quick Guide

NumPy is a powerful library for numerical computing in Python. Here are some common operations:

1. Creating Arrays:
```python
import numpy as np

# Create array from list
arr = np.array([1, 2, 3, 4, 5])

# Create array of zeros
zeros = np.zeros((3, 3))

# Create array of ones
ones = np.ones((2, 4))

# Create array with range
range_arr = np.arange(0, 10, 2)  # [0, 2, 4, 6, 8]
```

2. Basic Operations:
```python
# Element-wise operations
arr1 = np.array([1, 2, 3])
arr2 = np.array([4, 5, 6])
sum_arr = arr1 + arr2  # [5, 7, 9]
mult_arr = arr1 * arr2  # [4, 10, 18]

# Matrix multiplication
mat1 = np.array([[1, 2], [3, 4]])
mat2 = np.array([[5, 6], [7, 8]])
result = np.dot(mat1, mat2)
```

3. Array Manipulation:
```python
# Reshape array
arr = np.array([1, 2, 3, 4, 5, 6])
reshaped = arr.reshape(2, 3)

# Transpose
transposed = reshaped.T

# Concatenate arrays
combined = np.concatenate([arr1, arr2])
```

Try these examples in the interpreter!
"""


@schema_function
def read_matplotlib_guide() -> str:
    """Returns a guide on how to use Matplotlib for data visualization."""
    return """# Matplotlib Quick Guide

Matplotlib is a plotting library for Python. Here are some common plot types:

1. Line Plot:
```python
import matplotlib.pyplot as plt
import numpy as np

x = np.linspace(0, 10, 100)
y = np.sin(x)
plt.plot(x, y)
plt.title('Sine Wave')
plt.xlabel('x')
plt.ylabel('sin(x)')
plt.show()
```

2. Scatter Plot:
```python
x = np.random.rand(50)
y = np.random.rand(50)
plt.scatter(x, y)
plt.title('Random Scatter Plot')
plt.show()
```

3. Bar Plot:
```python
categories = ['A', 'B', 'C', 'D']
values = [4, 3, 2, 1]
plt.bar(categories, values)
plt.title('Bar Chart')
plt.show()
```

4. Histogram:
```python
data = np.random.randn(1000)
plt.hist(data, bins=30)
plt.title('Histogram')
plt.show()
```

Remember to save plots to a file when using the interpreter!
"""


# Define prompt read functions
@schema_function
def read_prompt(name: str = Field(..., description="Name of the prompt")) -> str:
    """Returns the content of a specific prompt template."""
    prompts = {
        "numpy_starter": """Here's a starter template for NumPy calculations:

```python
import numpy as np

# Create your arrays
arr1 = np.array([1, 2, 3, 4, 5])
arr2 = np.array([6, 7, 8, 9, 10])

# Perform operations
result = arr1 + arr2

# Store the result
__result__ = result.tolist()  # Convert to list for display
```""",
        "matplotlib_starter": """Here's a starter template for creating plots:

```python
import matplotlib.pyplot as plt
import numpy as np

# Generate data
x = np.linspace(0, 10, 100)
y = np.sin(x)

# Create the plot
plt.figure(figsize=(8, 6))
plt.plot(x, y, 'b-', label='sin(x)')
plt.title('My Plot')
plt.xlabel('x')
plt.ylabel('y')
plt.legend()

# Save the plot (don't use plt.show() in the interpreter)
plt.savefig('/tmp/plot.png')
__result__ = '/tmp/plot.png'
```""",
        "data_analysis": """Here's a template for basic data analysis:

```python
import numpy as np

# Generate sample data
data = np.random.randn(100)

# Calculate statistics
mean = np.mean(data)
std = np.std(data)
median = np.median(data)
min_val = np.min(data)
max_val = np.max(data)

# Format results
__result__ = f'''
Data Statistics:
- Mean: {mean:.2f}
- Standard Deviation: {std:.2f}
- Median: {median:.2f}
- Min: {min_val:.2f}
- Max: {max_val:.2f}
'''
```""",
    }
    return prompts.get(name, f"Prompt '{name}' not found")


async def main(server_url):
    user_info = await login({"server_url": server_url, "profile": True})
    print(f"Logged in as: {user_info}")
    server = await connect_to_server(
        {"server_url": server_url, "token": user_info.token}
    )

    # Register the Python interpreter service with resources and prompts
    interpreter_info = await server.register_service(
        {
            "id": "interpreter",
            "name": "Python Interpreter",
            "description": "A service to execute Python code with helpful resources and prompts",
            "type": "mcp",  # type=mcp allows providing additional mcp resources and prompts
            "config": {"visibility": "public", "run_in_executor": True},
            "tools": [execute_python],
            "resources": [
                {
                    "uri": "resource://numpy-guide",
                    "name": "NumPy Guide",
                    "description": "A quick guide to NumPy operations",
                    "tags": ["numpy", "guide", "numerical-computing"],
                    "mime_type": "text/markdown",
                    "read": read_numpy_guide,
                },
                {
                    "uri": "resource://matplotlib-guide",
                    "name": "Matplotlib Guide",
                    "description": "A quick guide to Matplotlib plotting",
                    "tags": ["matplotlib", "guide", "visualization"],
                    "mime_type": "text/markdown",
                    "read": read_matplotlib_guide,
                },
            ],
            "prompts": [
                {
                    "name": "NumPy Starter",
                    "description": "Template for NumPy calculations",
                    "tags": ["numpy", "template"],
                    "read": lambda: read_prompt("numpy_starter"),
                },
                {
                    "name": "Matplotlib Starter",
                    "description": "Template for creating plots",
                    "tags": ["matplotlib", "template"],
                    "read": lambda: read_prompt("matplotlib_starter"),
                },
                {
                    "name": "Data Analysis",
                    "description": "Template for basic data analysis",
                    "tags": ["analysis", "template"],
                    "read": lambda: read_prompt("data_analysis"),
                },
            ],
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
