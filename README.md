# Hypha RPC

Hypha RPC is a simple RPC library for Hypha, a framework for building distributed data management and AI model serving systems.

## Usage

`hypha-rpc` is the Python client library for the [Hypha server](https://docs.amun.ai), which allows you to connect to a Hypha server and interact with its services. You can use the library to call remote functions, register services, and exchange data with the server.

### Installation

```bash
pip install -U hypha-rpc
```

### Connecting to a Hypha server

```python
from hypha_rpc import connect_to_server
server = await connect_to_server({"server_url": server_url})
```

You can also obtain a login token from the server and use it to connect to the server:

```python
from hypha_rpc import login, connect_to_server
token = await login({"server_url": server_url})
server = await connect_to_server({"server_url": server_url, "token": token})
```

## Data type representation

ImJoy RPC is built on top of two-way transport layer. Currently, we use `websocket` to implement the transport layer between different peers. Data with different types are encoded into a unified representation and sent over the transport layer. It will then be decoded into the same or corresponding data type on the other side.

The data representation is a JSON object (but can contain binary data, e.g. `ArrayBuffer` in JS or `bytes` in Python). The goal is to represent more complex data types with primitive types that are commonly supported by many programming language, including strings, numbers, boolean, bytes, list/array and dictionary/object.


| Javascript | Python | hypha-rpc representation |
|------------|--------- | ---- |
| String   | str        | v |
| Number   | int/float | v |
| Boolean  |  bool     | v |
| null/undefined  | None    | v |
| Uint8Array | bytes  | v |
| ArrayBuffer | memoryview  | {_rtype: "memoryview", _rvalue: v} |
| Array([])   | list/tuple |[_encode(v)] |
| Object({})  | dict  | {_encode(v)} |
| Set | Set | {_rtype: "set", _rvalue: _encode(Array.from(v))} |
| Map | OrderedDict  |{_rtype: "orderedmap", _rvalue: _encode(Array.from(v))} |
| Error | Exception | { _rtype: "error", _rvalue: v.toString() } |
| Blob/File | BytesIO/StringIO etc.  | { _rtype: "iostream", name: v, type: v.type, read: v.read, seek: v.seek, ...} |
| DataView | memoryview  |  { _rtype: "memoryview", _rvalue: v.buffer }|
| TypedArray | 1-D numpy array*  |{_rtype: "typedarray", _rvalue: v.buffer, _rdtype: dtype} |
| tf.Tensor/nj.array | numpy array  |{_rtype: "ndarray", _rvalue: v.buffer, _rshape: shape, _rdtype: _dtype} |
| Function* | function/callable* | {_rtype: "method", _rtarget: _rid, _rmethod: name, _rpromise: true } |
| Class | class/dotdict()* | {...} |
| custom | custom | encoder(v) (default `_rtype` = encoder name) |

Notes:
 - `_encode(...)` in the hypha-rpc representation means the type will be recursively encoded (decoded).
 - When sending functions to be used remotely in a remote function call (e.g. passing an object with member functions when calling a remote function), the functions will only be available during the call and will be removed after the call. If you want to keep the function available for later calls, you can either mark the function as a "interface" function by setting any of the containing objects' `_rintf` to true, or you can register the function as a service, then call the service instead.
 - For n-D numpy array, there is no established n-D array library in javascript, the current behavior is, if there is `tf`(Tensorflow.js) detected, then it will be decoded into `tf.Tensor`. If `nj`(numjs) is detected, then it will be decoded into `nj.array`.
 - Typed array will be represented as numpy array if available, otherwise it will be converted to raw bytes.    
    Type Conversion
    | Javascript | Numpy  | _rdtype |
    | -- | -- | -- |
    | Int8Array | int8 | int8 |
    | Int16Array| int16 |int16 |
    |  Int32Array| int32 | int32 |
    |  Uint8Array| uint8 | uint8 |
    |  Uint16Array| uint16 | uint16 |
    |  Uint32Array| uint32 | uint32 |
    |  Float32Array| float32 | float32 |
    |  Float64Array| float64 | float64 |
    |  Array| array | array |
    |note: 64-bit integers (signed or unsigned) are not supported|

 - `dotdict` in Python is a simple wrapper over `dict` that support using the dot notation to get item, similar to what you can do with Javascript object.
 - In Python, file instances (inherit from `io.IOBase`) will be automatically encoded.

## Encoding and decoding custom objects

 For the data or object types that are not in the table above, for example, a custom class, you can support them by register your own `codec`(i.e. encoder and decoder) with `api.register_codec()`.

 You need to provide a `name`, a `type`, `encoder` and `decoder` function. For example: in javascript, you can call `api.register_codec({"name": "my_custom_codec", "type": MyClass, "encoder": (obj)=>{ ... return encoded;}, "decoder": (obj)=>{... return decoded;})`, or in Python you can do `api.register_codec(name="my_custom_codec", type=MyClass, encoder=my_encoder_func, decoder=my_decoder_func)`.
 

 The basic idea of using a custom codec is to use the `encoder` to represent your custom data type into array/dictionary of primitive types (string, number etc.) such that they can be send via the transport layer of hypha-rpc. Then use the `decoder` to reconstruct the object remotely based on the representation.

For the `name`, it will be assigned as `_rtype` for the data representation, therefore please be aware that you should not use a name that already used internally (see the table above), unless you want to overried the default encoding. Also note that you cannot overried the encoding of primitive types and functions.

The `encoder` function take an object as input and you need to return the represented object/dictionary. You can only use primitive types plus array/list and object/dict in the represented object. By default, if your returned object does not contain a key `_rtype`, the codec `name` will be used as `_rtype`. You can also assign a different `_rtype` name, that allows the conversion between different types.

The `decoder` function converts the encoded object into the actual object. It will only be called when the `_rtype` of an object matches the `name` of the codec.

### Example 1: Encode and Decode xarray

Here you can find an example for encoding and decoding [xarray](https://xarray.dev/):
```python
import asyncio
from hypha_rpc import connect_to_server
import xarray as xr
import numpy as np

def encode_xarray(obj):
    """Encode the zarr store."""
    assert isinstance(obj, xr.DataArray)
    return {
        "_rintf": True,
        "_rtype": "xarray",
        "data": obj.data,
        "dims": obj.dims,
        "attrs": obj.attrs,
        "name": obj.name,
    }

def decode_xarray(obj):
    assert obj["_rtype"] == "xarray"
    return xr.DataArray(
                data=obj["data"],
                dims=obj["dims"],
                attrs=obj.get("attrs", {}),
                name=obj.get("name", None),
        )


async def start_server(server_url):
    server = await connect_to_server({"server_url": server_url})

    # Register the codecs
    server.register_codec(
        {"name": "xarray", "type": xr.DataArray, "encoder": encode_xarray, "decoder": decode_xarray}
    )
    
    z = xr.DataArray(data=np.arange(100), dims=["x"], attrs={"test": "test"}, name="mydata")

    # Use the echo function to do a round-trip with the xarray object
    # It will first encode z and send it to the server, then the server return the encoded object and decoded it back to a xarray
    z2 = await server.echo(z)

    assert isinstance(z2, xr.DataArray)
    assert z2.attrs["test"] == "test"
    assert z2.dims == ("x",)
    assert z2.data[0] == 0
    assert z2.data[99] == 99
    assert z2.name == "mydata"
    print("Success!")

if __name__ == "__main__":
    server_url = "https://hypha.aicell.io"
    loop = asyncio.get_event_loop()
    loop.create_task(start_server(server_url))
    loop.run_forever()

```


### Example 2: Encode zarr store

Since we can include functions in the encoded object, this allows us sending an interface to the remote location and use it as a lazy object.

```python
import asyncio
from hypha_rpc import connect_to_server

import zarr
import numpy as np

def encode_zarr_store(zobj):
    """Encode the zarr store."""
    import zarr

    path_prefix = f"{zobj.path}/" if zobj.path else ""

    def getItem(key, options=None):
        return zobj.store[path_prefix + key]

    def setItem(key, value):
        zobj.store[path_prefix + key] = value

    def containsItem(key, options=None):
        if path_prefix + key in zobj.store:
            return True

    return {
        "_rintf": True,
        "_rtype": "zarr-array" if isinstance(zobj, zarr.Array) else "zarr-group",
        "getItem": getItem,
        "setItem": setItem,
        "containsItem": containsItem,
    }


async def start_server(server_url):
    server = await connect_to_server({"server_url": server_url})

    # Register the codecs
    server.register_codec(
        {"name": "zarr-group", "type": zarr.Group, "encoder": encode_zarr_store}
    )

    z = zarr.array(np.arange(100))
  
    # Use the echo function to do a round-trip with the zarr object
    # Note: Since we didn't create a decoder, so we won't get the zarr object, but a zarr store interface
    z2 = await server.echo(z)
    print(z2)

if __name__ == "__main__":
    server_url = "https://hypha.aicell.io"
    loop = asyncio.get_event_loop()
    loop.create_task(start_server(server_url))
    loop.run_forever()
```


### Remote function calls and arguments

Remote function call is almost the same as calling a local function. The arguments are mapped directly, for example, you can call a Python function `foo(a, b, c)` from javascript or vise versa. However, since Javascript does not support named arguments as Python does, ImJoy does the following conversion:

 * For functions defined in Javascript, there is no difference when calling from Python
 * For functions defined in Python, when calling from Javascript, if the last argument is an object and its `_rkwargs` is set to true, then it will be converted into keyword arguments when calling the Python function. For example, if you have a Python function defined as `def foo(a, b, c=None):`, in Javascript, you should call it as `foo(9, 10, {c: 33, _rkwargs: true})`.

### Generators Support

Hypha RPC supports both synchronous and asynchronous generators across Python and JavaScript. This allows you to stream data between services efficiently.

#### Python Generators

You can define both regular and async generators in your Python services:

```python
# Regular generator
def counter(start=0, end=5):
    """Return a generator that counts from start to end."""
    for i in range(start, end):
        yield i

# Async generator
async def async_counter(start=0, end=5):
    """Return an async generator that counts from start to end."""
    for i in range(start, end):
        yield i
        await asyncio.sleep(0.01)  # Small delay to simulate async work

# Register service with generators
await server.register_service({
    "id": "generator-service",
    "config": {"visibility": "public"},
    "get_counter": counter,
    "get_async_counter": async_counter,
})
```

##### Using Generators with Async API

When using the async API, all generators (both regular and async) are consumed using async iteration:

```python
# Connect to the service
gen_service = await client.get_service("generator-service")

# Using regular generator (becomes async over RPC)
gen = await gen_service.get_counter(0, 5)
async for item in gen:
    print(item)  # Prints: 0, 1, 2, 3, 4

# Using async generator
async_gen = await gen_service.get_async_counter(0, 5)
async for item in async_gen:
    print(item)  # Prints: 0, 1, 2, 3, 4
```

##### Using Generators with Sync API

The synchronous API allows you to use generators with regular for loops:

```python
# Connect using sync API
client = connect_to_server_sync({
    "server_url": "https://hypha.aicell.io",
})
gen_service = client.get_service("generator-service")

# Both regular and async generators can be used with for loops
for item in gen_service.get_counter(0, 5):
    print(item)  # Prints: 0, 1, 2, 3, 4

for item in gen_service.get_async_counter(0, 5):
    print(item)  # Prints: 0, 1, 2, 3, 4
```

#### JavaScript Generators

In JavaScript, you can define and consume generators in a similar way:

```javascript
// Define a generator service
const generatorService = {
    *counter(start = 0, end = 5) {
        for (let i = start; i < end; i++) {
            yield i;
        }
    },
    
    async *asyncCounter(start = 0, end = 5) {
        for (let i = start; i < end; i++) {
            yield i;
            await new Promise(resolve => setTimeout(resolve, 10));
        }
    }
};

// Register the service
await server.registerService({
    id: "js-generator-service",
    config: { visibility: "public" },
    ...generatorService
});

// Consume generators
const service = await client.getService("js-generator-service");

// Using regular generator
const gen = await service.counter(0, 5);
for await (const item of gen) {
    console.log(item); // Prints: 0, 1, 2, 3, 4
}

// Using async generator
const asyncGen = await service.asyncCounter(0, 5);
for await (const item of asyncGen) {
    console.log(item); // Prints: 0, 1, 2, 3, 4
}
```

Note: When using generators across RPC:
 * All generators become async generators when accessed remotely
 * The sync API in Python automatically handles the async-to-sync conversion
 * Values are streamed one at a time, making it memory efficient for large datasets
 * Generators are great for implementing progress updates or streaming data

## Type Annotations for LLM Function Calling

Hypha RPC supports generating standardized function schemas based on type annotations, which is particularly useful for integrating with Large Language Models (LLMs) that support function calling (like OpenAI's models).

### Python

In Python, you can use standard type hints, docstrings, and Pydantic models along with the `@schema_function` decorator (from `hypha_rpc.utils.schema`) to automatically generate a JSON schema compatible with LLM function calling standards.

**Example with basic types:**

```python
from hypha_rpc.utils.schema import schema_function

@schema_function
def get_current_weather(location: str, unit: str = "fahrenheit") -> str:
    """Get the current weather in a given location.

    Args:
        location: The city and state, e.g. San Francisco, CA.
        unit: The temperature unit, either "celsius" or "fahrenheit".

    Returns:
        A JSON string with the weather information.
    """
    # (Implementation details omitted for brevity)
    import json
    if "tokyo" in location.lower():
        return json.dumps({"location": "Tokyo", "temperature": "10", "unit": unit})
    # ... other locations ...
    else:
        return json.dumps({"location": location, "temperature": "unknown"})
```

**Example with Pydantic:**

```python
from pydantic import BaseModel, Field
from hypha_rpc.utils.schema import schema_function

class UserInfo(BaseModel):
    """User information."""
    name: str = Field(..., description="Name of the user")
    email: str = Field(..., description="Email of the user")
    age: int = Field(..., description="Age of the user")
    address: str = Field(..., description="Address of the user")

@schema_function
def register_user(user_info: UserInfo) -> str:
    """Register a new user."""
    return f"User {user_info.name} registered"
```

The decorator attaches the generated schema to the function's `__schema__` attribute. When you register a service containing these decorated functions, the schema information is included in the service registration details, making it available for clients (or LLMs) to understand how to call the functions.

```python
# Example service registration
await server.register_service({
    "name": "User Service",
    "id": "user-service",
    "description": "Service for registering users",
    "register_user": register_user # Decorated function
})
```

### JavaScript

JavaScript utilizes the `schemaFunction` utility (imported from `hypha-rpc/utils/schema.js` or re-exported by `hypha-rpc`) to achieve similar results.

You provide the function implementation and a separate schema object detailing the function's name, description, and parameters (following JSON Schema conventions).

**Example:**

```javascript
import { schemaFunction } from "./hypha-rpc.js"; // Adjust import path as needed

// Define the function implementation
const multiply = (a, b) => a * b;

// Define the schema
const multiplySchema = {
    name: "multiply",
    description: "Multiplies two numbers.",
    parameters: {
        type: "object",
        properties: {
            a: { type: "number", description: "First number" },
            b: { type: "number", description: "Second number" },
        },
        required: ["a", "b"],
        // Note: Return value schema is not explicitly part of this standard schema,
        // but can be included in the description or a custom field if needed.
    },
};

// Create the annotated function
const annotatedMultiply = schemaFunction(multiply, multiplySchema);

// Register the service
await server.registerService({
    id: "calculator-service",
    config: { visibility: "public" },
    multiply: annotatedMultiply, // Use the annotated function
});
```

The `schemaFunction` utility attaches the provided schema to the `__schema__` property of the returned function object (`annotatedMultiply` in the example). When the service is registered, this schema is included, similar to the Python version.

## Peer-to-peer connection via WebRTC

The current implementation requires all the traffic going through the websocket server. This is not ideal for large data transmission. Therefore, we implemented webRTC support in addition to the websocket connection. You can use the following two functions for enabling peer-to-peer communication between clients:

Here is an example for setting up a webrtc service on the python side:

```python
from hypha_rpc import connect_to_server, register_rtc_service, get_rtc_service
server = await connect_to_server({"server_url": "https://hypha.aicell.io"})
await register_rtc_service(server, "webrtc-service")
```

You can also use the synchronous version:

```python
from hypha_rpc.sync import register_rtc_service, get_rtc_service
```

Now, in the browser, you can connect to the server and get the webrtc service:

```html
<script src="https://cdn.jsdelivr.net/npm/hypha-rpc@0.5.30/dist/hypha-rpc-websocket.min.js"></script>
<script>
const server = await hyphaWebsocketClient.connectToServer({"server_url": "https://hypha.aicell.io"})
const pc = await hyphaWebsocketClient.getRTCService(server, "webrtc-service");
const svc = await pc.get_service("hello"); // now you can get service via webrtc
// ...
</script>
```

It works by using hypha server as a signaling server, after establishing the connection, the rest goes through webrtc in a peer-to-peer manner. 

Both `register_rtc_service` and `get_rtc_service` take an optional `config` object as the last argument. The `config` object can contain a `on_init(peer_connection)` callback function that will be called when the webrtc connection is established.

You can setup streaming services inside the `on_init` callback. This is ideally suited for applications such as microscope control. As an example, we generate a random video stream on the python side, and provide a microscope control service (e.g. move stage and snap image): https://github.com/oeway/webrtc-hypha-demo

### Enable WebRTC automatically

You can also enable webrtc for the `connect_to_server` function, by setting the `webrtc` option to `True` or `auto` in the config object. For example:

```python
server = await connect_to_server({"server_url": "https://hypha.aicell.io", "webrtc": True})
```

Or javascript:

```javascript
const server = await hyphaWebsocketClient.connectToServer({"server_url": "https://hypha.aicell.io", "webrtc": true})
```

This will automatically register a webrtc service (named as `<client_id>-rtc`) so that other clients can connect to it.

Now if you register a hypha service, it will be automatically made available through the webrtc connection.

To get the service via webrtc, you can pass `webrtc=True` and `webrtc_config` to `server.get_service()`:

```python
svc = await server.get_service("my-service", webrtc=True, webrtc_config={})
```

In the above example, we only show how to enable it in Python, but it also works in Javascript. However, please not that the webrtc won't work directly in pyodide-based environment (e.g. in JupyterLite).

## Synchronous Wrapper

To make it easier to work with synchronous python code, we provide a synchronous wrapper, which allows for synchronous usage of the asynchronous `hypha_rpc` API.

To use the synchronous wrapper, you can import the following functions from the `hypha_rpc.sync` module:

```python
from hypha_rpc.sync import login, connect_to_server, get_rtc_service, register_rtc_service
```
**connect_to_server**

The `connect_to_server` function creates a synchronous Hypha server instance and establishes a connection to the server. It takes a configuration object as an argument and returns the server instance.

```python
server = connect_to_server(config)
```

**Example:**

```python
server_url = "https://hypha.aicell.io"
server = connect_to_server({"server_url": server_url})
```


**login**

The `login` function is used to log in to a Hypha server. It takes a configuration object as an argument and returns the token for connecting to the server.

```python
token = login(config)
```

**Example:**

```python
server_url = "https://hypha.aicell.io"

def login_callback(context):
    print("Please open the following URL in your browser to log in:")
    print(context["login_url"])

config = {
    "server_url": server_url,
    "login_callback": login_callback,
}

token = login(config)
server = connect_to_server({"server_url": server_url, "token": token})
```

The `config` object should contain the following properties:

- `server_url`: The URL of the Hypha server.
- `login_service_id`: The service ID for the login service (default: "public/*:hypha-login").
- `login_timeout`: The timeout duration for the login process (default: 60 seconds).
- `login_callback`: An optional callback function to handle the login process.

The `login` function connects to the Hypha server, starts the login service, and initiates the login process. If a `login_callback` function is provided, it will be called with the login context. Otherwise, the login URL will be printed to the console, and the user needs to open their browser and complete the login process.

The function returns the result of the login process, which is obtained by checking the login key within the specified timeout duration.


**get_rtc_service**

The `get_rtc_service` function retrieves a synchronous Real-Time Communication (RTC) service from the Hypha server. It takes the server instance and a service ID as arguments and returns the synchronous RTC service.

```python
rtc_service = get_rtc_service(server, service_id, config=None)
```

**Example:**

```python
rtc_service = get_rtc_service(server, "webrtc-service")
```

**register_rtc_service**

The `register_rtc_service` function registers a synchronous RTC service with the Hypha server. It takes the server instance, service ID, and an optional configuration object as arguments.

```python
register_rtc_service(server, service_id, config=None)
```

**Example:**

```python
register_rtc_service(
    server,
    service_id="webrtc-service",
    config={
        "visibility": "public",
        # "ice_servers": ice_servers,
    },
)
```

Please note that the synchronous wrapper is designed to provide a convenient synchronous interface for the asynchronous `hypha-rpc` API. It utilizes asyncio and threading under the hood to achieve synchronous behavior.
