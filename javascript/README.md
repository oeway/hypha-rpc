# Hypha RPC

## Usage

### Connect to Hypha

```javascript
import { hyphaWebsocketClient } from "hypha-rpc";

hyphaWebsocketClient.connectToServer({
  server_url: 'https://ai.imjoy.io',
}).then(async (api)=>{
  await api.register_service(
      {
          "id": "echo-service",
          "config":{
              "visibility": "public"
          },
          "type": "echo",
          echo( data ){
              console.log("Echo: ", data)
              return data
          }
      }
  )
})
```

### Keyword Arguments (kwargs)

Hypha RPC supports Python-style keyword arguments when calling JavaScript services. This works seamlessly across Python→JS and JS→JS calls.

#### Calling from Python to JavaScript

When a Python client calls a JS service using keyword arguments, they are automatically unpacked into the matching positional parameters:

```python
# Python caller
svc = await server.get_service("my-js-service")
result = await svc.greet(name="Alice", greeting="Hello")
```

```javascript
// JavaScript service — params are matched by name
await api.registerService({
  id: "my-js-service",
  greet(name, greeting) {
    return `${greeting}, ${name}!`;  // "Hello, Alice!"
  }
});
```

#### Calling from JavaScript to JavaScript

Use the `_rkwargs: true` flag to send keyword arguments from JS:

```javascript
const svc = await api.getService("my-js-service");

// Positional (traditional)
await svc.greet("Alice", "Hello");

// Keyword arguments — params matched by name, order doesn't matter
await svc.greet({ greeting: "Hello", name: "Alice", _rkwargs: true });
```

#### With `require_context`

When a service uses `require_context: true`, the `context` parameter is automatically injected by the server and cannot be overridden via kwargs:

```javascript
await api.registerService({
  id: "secure-svc",
  config: { require_context: true },
  greet(name, greeting, context) {
    console.log("Called by:", context.user);
    return `${greeting}, ${name}!`;
  }
});

// Caller — context is injected automatically, not passed by the caller
await svc.greet({ name: "Alice", greeting: "Hello", _rkwargs: true });
```

#### With `kwargs_expansion`

For convenience when calling server APIs, connect with `kwargs_expansion: true` to automatically convert the last object argument to kwargs:

```javascript
const api = await connectToServer({
  server_url: "https://ai.imjoy.io",
  kwargs_expansion: true,
});
// The last object arg is automatically sent as kwargs
const token = await api.generateToken({ config: { workspace: "my-ws" } });
```

> **Note:** Kwargs unpacking relies on parsing function parameter names from source code. This works with regular functions, arrow functions, and async functions, but will not work with minified/bundled code where parameter names are mangled.