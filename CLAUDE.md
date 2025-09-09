# Hypha RPC Client Library

## Project Overview

Hypha RPC is a client library that provides Python and JavaScript implementations for connecting to the Hypha server, enabling Remote Procedure Call (RPC) based distributed computing. The library allows applications and distributed components to connect, communicate, and share services through a seamless function-calling interface that mimics local function invocation.

## Core Concepts

### Remote Procedure Call (RPC) Framework

The Hypha RPC system is designed to make remote function calls appear as local function calls. When you register a service on one client, other clients connected to the same workspace can call those service functions as if they were defined locally. All remote calls are automatically converted to asynchronous functions to handle network latency.

### Workspace-Based Isolation

Each client connects to a **workspace** - the fundamental unit of isolation in the Hypha ecosystem. Workspaces provide:
- User and project isolation
- Service namespace management
- Access control boundaries
- Resource scoping

Clients within the same workspace can freely discover and use each other's services. Services can optionally be made public to allow cross-workspace access.

## Architecture

### Connection Flow

1. **WebSocket Connection**: Clients establish a WebSocket connection to the Hypha server
2. **RPC Connection**: The WebSocket connection provides the transport layer for the RPC protocol
3. **Workspace Manager**: Upon connection, clients automatically fetch the workspace manager interface
4. **Service Registration/Discovery**: Clients can then register their own services or discover and use services from other clients

### Service Registration

Services are collections of functions that a client exposes to the workspace. Key features:
- Functions can be synchronous or asynchronous
- Service configuration allows fine-grained control
- Services are registered with the workspace manager
- Each service gets a unique identifier within the workspace

### Service Discovery and Invocation

Clients can discover available services through the workspace manager and invoke them:
- Service discovery returns proxy objects
- All remote functions become async (even if originally sync)
- Function calls are transparently serialized and transmitted
- Results are deserialized and returned to the caller

## Authentication and Authorization

### Context Injection

Services can enable the `require_context` flag in their configuration. When enabled:
- The Hypha server automatically injects an authentication context into each service function call
- Service functions receive information about the caller (user ID, email, etc.)
- This enables fine-grained, function-level authorization
- Services can implement custom access control based on caller identity

### User Authentication

The system integrates with the Hypha server's authentication mechanisms:
- External authentication via Auth0
- Internal JWT-based authentication
- Anonymous user support for public services

## Python Implementation

### Core Features

- **Async/Await Support**: Native support for Python's asyncio
- **Synchronous Wrapper**: Provides sync wrappers for async functions for compatibility
- **Type Annotations**: Support for function annotations and validation

### Special Implementations

#### Pyodide Support
- Enables Python execution in web browsers
- WebSocket transport layer adapted for browser environment
- Maintains full RPC functionality in browser-based Python

### Usage Pattern

```python
# Connect to server
client = await connect_to_server({"server_url": "wss://hypha.example.com"})

# Get workspace manager
workspace = client.workspace

# Register a service
await workspace.register_service({
    "name": "my-service",
    "config": {
        "require_context": True
    },
    "functions": {
        "calculate": async_calculate_function,
        "process": sync_process_function
    }
})

# Get and use a remote service
remote_service = await workspace.get_service("other-service")
result = await remote_service.calculate(param1, param2)
```

## JavaScript Implementation

### Core Features

- **Pure Async**: All operations are asynchronous (no sync wrappers)
- **Browser & Node.js**: Works in both environments
- **Modern JavaScript**: Uses async/await patterns

### Transport Options

- **WebSocket**: Primary transport for bidirectional communication
- **WebRTC**: Peer-to-peer connections (still requires WebSocket for signaling)

### Usage Pattern

```javascript
// Connect to server
const client = await connectToServer({server_url: "wss://hypha.example.com"});

// Get workspace manager
const workspace = client.workspace;

// Register a service
await workspace.registerService({
    name: "my-service",
    config: {
        require_context: true
    },
    functions: {
        calculate: asyncCalculateFunction,
        process: processFunction
    }
});

// Get and use a remote service
const remoteService = await workspace.getService("other-service");
const result = await remoteService.calculate(param1, param2);
```

## Transport Layers

### WebSocket (Primary)

- Bidirectional real-time communication
- Supports callbacks and streaming
- Low latency for interactive applications
- Handles connection management and reconnection

### WebRTC (Peer-to-Peer)

- Direct peer connections for reduced latency
- Requires initial WebSocket connection for signaling
- Useful for high-bandwidth data transfer
- Falls back to WebSocket relay if P2P fails

### HTTP Message Transmission

- Alternative transport for environments where WebSocket is not available
- Request-response pattern
- Higher latency but broader compatibility

## Advanced Features

### Service Visibility

- **Protected** (default): Only accessible within the same workspace
- **Public**: Accessible from other workspaces with proper permissions

### Callback Support

Remote functions can accept callback functions as parameters, enabling:
- Progress reporting
- Streaming results
- Event notifications
- Bidirectional communication patterns

### Error Handling

- Automatic error serialization and propagation
- Stack traces preserved across RPC boundaries
- Timeout handling for long-running operations
- Automatic retry logic for transient failures

## Hypha Server Integration

The Hypha RPC client library is designed to work with the Hypha server, which provides:

### Server Architecture
- **FastAPI-based server** with WebSocket and HTTP transport
- **Redis-backed event bus** for horizontal scaling and message routing
- **Multi-tenant workspace** system for isolation
- **Service registry** for discovery and management

### Key Server Components
- **WebSocket Server** (`hypha/websocket.py`): Handles client connections and message routing
- **Workspace Manager** (`hypha/core/workspace.py`): Manages workspace lifecycle and service registration
- **Event Bus** (`hypha/core/__init__.py`): Redis-based system for distributed message passing
- **Authentication** (`hypha/core/auth.py`): JWT-based auth with Auth0 integration

### Scaling and Performance
- Horizontal scaling through Redis event bus
- Multiple Hypha server instances can share workspaces
- Prefix-based routing for efficient message distribution
- Activity-based cleanup for resource management

## Use Cases

### Distributed Computing
- Distribute computational tasks across multiple clients
- Load balancing and parallel processing
- Resource sharing across heterogeneous systems

### Microservices Architecture
- Build applications as collections of services
- Language-agnostic service communication
- Dynamic service discovery and binding

### AI Model Serving
- Expose AI models as services
- Share computational resources
- Enable model chaining and pipelines

### Real-time Collaboration
- Share live data and computations
- Collaborative editing and processing
- Event-driven architectures

## Development Guidelines

### Best Practices

1. **Service Design**
   - Keep services focused and cohesive
   - Use clear, descriptive function names
   - Document service interfaces
   - Handle errors gracefully

2. **Security**
   - Enable `require_context` for sensitive services
   - Validate inputs on service functions
   - Implement proper authorization checks
   - Never expose credentials in service responses

3. **Performance**
   - Use async functions for I/O operations
   - Batch operations when possible
   - Consider data serialization costs
   - Monitor service response times

4. **Testing**
   - Test services in isolation
   - Mock external dependencies
   - Test error conditions
   - Verify context injection works correctly

## Dependencies

### Python
- `websocket-client` or `aiohttp` for WebSocket support
- `asyncio` for async operations
- Optional: `pyodide` for browser execution

### JavaScript
- WebSocket API (native in browsers and Node.js)
- Optional: WebRTC support for peer-to-peer

## Configuration

### Connection Options
```python
{
    "server_url": "wss://hypha.example.com",
    "workspace": "my-workspace",
    "token": "authentication-token",
    "client_id": "unique-client-id",
    "reconnect": True,
    "reconnect_interval": 1000
}
```

### Service Configuration
```python
{
    "name": "service-name",
    "id": "service-id",  # optional, auto-generated if not provided
    "config": {
        "visibility": "protected",  # or "public"
        "require_context": False,
        "timeout": 30,  # seconds
        "description": "Service description"
    }
}
```

## Error Handling

The library provides comprehensive error handling:
- Connection errors with automatic reconnection
- RPC errors with remote stack traces
- Timeout errors for long-running operations
- Validation errors for schema mismatches
- Authentication and authorization errors

## Monitoring and Debugging

- Connection status monitoring
- Service call metrics
- Error logging and reporting
- Debug mode for detailed logging
- Performance profiling support

## Future Enhancements

- GraphQL support for complex queries
- Binary data optimization
- Enhanced caching mechanisms
- Service versioning
- Advanced load balancing strategies