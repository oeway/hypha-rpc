# Python RPC Remote Method Call Path Analysis

## Overview
The Python RPC library implements transparent remote method invocation by encoding remote methods into callable proxy objects. When called, these proxies create futures with resolve/reject callbacks and transmit the request through a two-segment msgpack protocol.

## Key Components

### 1. Service Retrieval: `get_remote_service()` (Lines 1727-1770)
**Location:** `/Users/weio/workspace/hypha-rpc/python/hypha_rpc/rpc.py:1727-1770`

**Purpose:** Retrieves a service from a remote client and returns a RemoteService proxy.

**Key Metadata Available in Proxy:**
- Service ID (stored in `svc["id"]`)
- Service dict containing all methods with `_rtype` = "method"
- Provider/target ID
- Service configuration

**Example Code:**
```python
async def get_remote_service(self, service_uri=None, config=None, **kwargs):
    provider, service_id = service_uri.split(":")
    method = self._generate_remote_method({
        "_rtarget": provider,
        "_rmethod": "services.built-in.get_service",
        "_rpromise": True,
        ...
    })
    svc = await asyncio.wait_for(method(service_id), timeout=timeout)
    svc["id"] = service_uri  # Store the URI for later access
    return RemoteService.fromDict(svc)
```

### 2. Remote Function Wrapper: `RemoteFunction` Class (Lines 396-638)

**Location:** `/Users/weio/workspace/hypha-rpc/python/hypha_rpc/rpc.py:396-638`

**Structure:**
- `__init__()` (Lines 397-427): Stores reference to RPC instance, encoded method dict, workspace info
- `__call__()` (Lines 429-632): The actual method invocation logic
- `__repr__()` and `__str__()`: String representations

**Key Instance Variables:**
```python
self._rpc = rpc_instance
self._encoded_method = encoded_method  # Contains _rtarget, _rmethod, _rname, etc.
self._remote_parent = remote_parent
self._local_parent = local_parent
self._remote_workspace = remote_workspace
self._local_workspace = local_workspace
self._description = description
self._with_promise = with_promise  # Whether to expect a response
```

### 3. Remote Method Generation: `_generate_remote_method()` (Lines 2566-2593)

**Location:** `/Users/weio/workspace/hypha-rpc/python/hypha_rpc/rpc.py:2566-2593`

**Purpose:** Creates a RemoteFunction instance from an encoded method dictionary.

**Metadata Available:**
- `encoded_method["_rtarget"]`: Target client ID
- `encoded_method["_rmethod"]`: Full method path (e.g., "service-id.method-name")
- `encoded_method["_rpromise"]`: Whether a response is expected (True/False/"*")
- `encoded_method["_rname"]`: Method name
- `encoded_method["_rdoc"]`: Documentation
- `encoded_method["_rschema"]`: Parameter schema
- `encoded_method["_renc_pub"]`: Target's encryption public key (if enabled)

**Code:**
```python
def _generate_remote_method(self, encoded_method, remote_parent=None, ...):
    target_id = encoded_method["_rtarget"]
    method_id = encoded_method["_rmethod"]
    with_promise = encoded_method.get("_rpromise", False)
    description = f"method: {method_id}, docs: {encoded_method.get('_rdoc')}"
    return RemoteFunction(
        rpc_instance=self,
        encoded_method=encoded_method,
        remote_parent=remote_parent,
        local_parent=local_parent,
        remote_workspace=remote_workspace,
        local_workspace=local_workspace,
        description=description,
        with_promise=with_promise,
    )
```

### 4. Method Invocation: `RemoteFunction.__call__()` (Lines 429-632)

**Location:** `/Users/weio/workspace/hypha-rpc/python/hypha_rpc/rpc.py:429-632`

**Execution Steps:**

#### Step 1: Create Future and Promise (Lines 429-446)
```python
def __call__(self, *arguments, **kwargs):
    arguments = list(arguments)
    if kwargs:
        arguments = arguments + [kwargs]
    
    fut = safe_create_future()
    
    def resolve(result):
        if fut.done(): return
        fut.set_result(result)
    
    def reject(error):
        if fut.done(): return
        fut.set_exception(error)
```

#### Step 2: Create Session and Store (Lines 446-461)
```python
local_session_id = shortuuid.uuid()
if self._local_parent:
    local_session_id = self._local_parent + "." + local_session_id
store = self._rpc._get_session_store(local_session_id, create=True)
store["target_id"] = self._encoded_method["_rtarget"]

# Update target_id index for fast session cleanup
top_key = local_session_id.split(".")[0]
target_id = self._encoded_method["_rtarget"]
if target_id not in self._rpc._target_id_index:
    self._rpc._target_id_index[target_id] = set()
self._rpc._target_id_index[target_id].add(top_key)
```

**Session ID Format:** `shortuuid.uuid()` (e.g., "abc123def456...")
**Parent Session ID Format:** `parent_session.child_id`

#### Step 3: Encode Arguments (Lines 457-474)
```python
args = self._rpc._encode(
    arguments,
    session_id=local_session_id,
    local_workspace=self._local_workspace,
)

# Build main message and extra_data (two-segment msgpack)
main_message = {
    "type": "method",
    "from": from_client,  # e.g., "workspace/client-id"
    "to": self._encoded_method["_rtarget"],
    "method": self._encoded_method["_rmethod"],
}
extra_data = {}
if args:
    extra_data["args"] = args
if kwargs:
    extra_data["with_kwargs"] = bool(kwargs)
```

#### Step 4: Create Promise and Timeout (Lines 482-544)
```python
if self._with_promise:
    main_message["session"] = local_session_id
    method_name = f"{self._encoded_method['_rtarget']}:{self._encoded_method['_rmethod']}"
    
    # Create timeout callback
    async def timeout_callback(error_msg):
        error = TimeoutError(error_msg)
        try:
            if asyncio.iscoroutinefunction(reject):
                await reject(error)
            else:
                reject(error)
        except Exception as e:
            logger.debug("Error rejecting timed-out call: %s", e)
        # Clean up session resources
        session = self._rpc._object_store.get(local_session_id)
        if isinstance(session, dict):
            heartbeat = session.get("heartbeat_task")
            if heartbeat and not getattr(heartbeat, "done", lambda: True)():
                heartbeat.cancel()
            t = session.get("timer")
            if t and getattr(t, "started", False):
                try:
                    t.clear()
                except Exception:
                    pass
            self._rpc._remove_from_target_id_index(local_session_id)
            del self._rpc._object_store[local_session_id]
    
    timer = Timer(
        self._rpc._method_timeout,
        timeout_callback,
        f"Method call timed out: {method_name}, context: {self._description}",
        label=method_name,
    )
```

#### Step 5: Encode Promise with Resolve/Reject Callbacks (Lines 532-545)
```python
promise_data = self._rpc._encode_promise(
    resolve=resolve,
    reject=reject,
    session_id=local_session_id,
    clear_after_called=clear_after_called,
    timer=timer,
    local_workspace=self._local_workspace,
    description=self._description,
)
# promise_data contains encoded resolve/reject methods
extra_data["promise"] = promise_data
```

#### Step 6: Encryption (if enabled) (Lines 547-572)
```python
if extra_data and self._rpc._encryption_enabled and self._target_encryption_pub:
    plaintext = msgpack.packb(extra_data)
    nonce, ciphertext = encrypt_payload(
        self._rpc._encryption_private_key,
        bytes(self._target_encryption_pub),
        plaintext,
    )
    extra_data = {
        "_enc": {"v": 2, "pub": self._rpc._encryption_public_key, "nonce": nonce},
        "data": ciphertext,
    }
```

#### Step 7: Encode Message (Lines 573-589)
```python
# TWO-SEGMENT MSGPACK PROTOCOL
message_package = msgpack.packb(main_message)
if extra_data:
    # Concatenate: [msgpack(main)][msgpack(extra)]
    message_package = message_package + msgpack.packb(extra_data)

total_size = len(message_package)
if total_size <= self._rpc._long_message_chunk_size + 1024 or self.__no_chunk__:
    emit_task = asyncio.create_task(self._rpc._emit_message(message_package))
else:
    emit_task = asyncio.create_task(
        self._rpc._send_chunks(message_package, ...)
    )
```

#### Step 8: Handle Emission Result (Lines 591-629)
```python
def handle_result(emit_fut):
    self._rpc._background_tasks.discard(emit_fut)
    if emit_fut.exception():
        error_msg = f"Failed to send the request: {emit_fut.exception()}"
        if reject:
            reject(Exception(error_msg))
        if timer and timer.started:
            timer.clear()
    else:
        if timer:
            timer.start()  # Start timeout timer after message sent
        if not self._with_promise:
            # Fire-and-forget: resolve immediately
            resolve(None)
            # Clean up session if no response expected
            if not self._local_parent:
                session = self._rpc._object_store.get(local_session_id)
                if isinstance(session, dict):
                    has_live_entries = any(
                        callable(v) or isinstance(v, dict)
                        for k, v in session.items()
                        if not k.startswith("_") and k != "target_id"
                    )
                    if not has_live_entries:
                        self._rpc._remove_from_target_id_index(local_session_id)
                        del self._rpc._object_store[local_session_id]

emit_task.add_done_callback(handle_result)
return fut  # Return the future to the caller
```

### 5. Message Reception: `_on_message()` (Lines 1184-1231)

**Location:** `/Users/weio/workspace/hypha-rpc/python/hypha_rpc/rpc.py:1184-1231`

**Two-Segment Msgpack Protocol:**
```python
def _on_message(self, message):
    if isinstance(message, bytes):
        # TWO-SEGMENT MSGPACK PROTOCOL
        unpacker = msgpack.Unpacker(io.BytesIO(message), ...)
        
        # First segment: Main message (routing info)
        main = unpacker.unpack()
        main = self._add_context_to_message(main)
        
        # Second segment: Extra data (args, kwargs, promise) - optional
        try:
            extra = unpacker.unpack()
            main.update(extra)  # Merge extra data
        except msgpack.exceptions.OutOfData:
            pass  # No extra segment
        
        self._fire(main["type"], main)
```

### 6. Promise Resolution: `_encode_promise()` (Lines 2399-2453)

**Location:** `/Users/weio/workspace/hypha-rpc/python/hypha_rpc/rpc.py:2399-2453`

**How Promise Callbacks Are Encoded:**
```python
def _encode_promise(self, resolve, reject, session_id, clear_after_called=False, ...):
    store = self._get_session_store(session_id, create=True)
    store["_promise_manager"] = self._create_promise_manager()
    encoded = {}
    
    # Heartbeat keeps timer alive while method runs
    if timer and reject and self._method_timeout:
        encoded["heartbeat"], store["heartbeat"] = self._encode_callback(
            "heartbeat",
            timer.reset,  # Reset timer on each heartbeat
            session_id,
            ...
        )
        store["timer"] = timer
        encoded["interval"] = self._method_timeout / 2
    
    # Resolve callback
    encoded["resolve"], store["resolve"] = self._encode_callback(
        "resolve",
        resolve,
        session_id,
        clear_after_called=clear_after_called,
        ...
    )
    
    # Reject callback
    encoded["reject"], store["reject"] = self._encode_callback(
        "reject",
        reject,
        session_id,
        clear_after_called=clear_after_called,
        ...
    )
    
    return encoded
```

The callbacks become remote methods that the server can call back:
- `resolve` method path: `{session_id}.resolve`
- `reject` method path: `{session_id}.reject`
- `heartbeat` method path: `{session_id}.heartbeat`

When the remote method completes, it calls:
```javascript
// In remote service execution
await promise.resolve(result)  // or
await promise.reject(error)
```

This triggers an RPC callback to `{session_id}.resolve()` or `{session_id}.reject()` on the caller's client.

## Key Data Flow for Retry Implementation

### Service Information Available
1. **From `get_remote_service()` result:**
   - Service ID: `svc["id"]` (e.g., "workspace/client-id:service-name")
   - Provider/target: Derived from service ID split on ":"
   - Service configuration: `svc.get("config", {})`
   - All methods with metadata

2. **From `RemoteFunction` instance:**
   - `self._rpc`: RPC instance for re-transmission
   - `self._encoded_method["_rtarget"]`: Target client ID
   - `self._encoded_method["_rmethod"]`: Full method path
   - `self._remote_workspace`: Remote workspace ID
   - `self._description`: Human-readable description

### Error Interception Points for Retry

1. **During Message Emission (Lines 591-629):**
   - `emit_fut.exception()`: Network/transmission errors
   - Can retry message transmission here

2. **From Timeout Callback (Lines 492-521):**
   - `TimeoutError`: Method didn't respond within `_method_timeout`
   - Session cleanup occurs here before rejection

3. **From Remote Error Response:**
   - When remote `promise.reject()` is called
   - Error is propagated through the stored reject callback
   - Could be intercepted in `_encode_callback()` wrapper

4. **From Missing Target:**
   - `_handle_peer_not_found()` (Lines 2618-2636): Server notifies peer is disconnected
   - Can re-fetch service and retry

## Session Management for Retry

**Session ID Structure:**
- Top-level: `shortuuid.uuid()` (e.g., "abc123def456")
- Child sessions: `parent_id.child_id`

**Session Store Location:**
```python
store = self._rpc._get_session_store(local_session_id, create=True)
```

**Stored Per-Session:**
- `target_id`: Target client ID
- `resolve`, `reject`: Callbacks
- `timer`: Timeout timer
- `heartbeat_task`: Heartbeat coroutine
- `heartbeat`: Heartbeat callback
- `_promise_manager`: Promise lifecycle tracker
- `_created_at`, `_last_activity_at`: For session GC

**Index for Fast Access:**
```python
self._rpc._target_id_index[target_id].add(top_session_key)
```

## Critical Code Paths for Implementing Retry

### 1. Service Re-fetch Path
```python
# Location: Lines 1727-1770 (get_remote_service)
# Can be called again with same service_uri to get fresh method proxies
provider, service_id = service_uri.split(":")
method = self._generate_remote_method({
    "_rtarget": provider,
    "_rmethod": "services.built-in.get_service",
    "_rpromise": True,
    ...
})
svc = await asyncio.wait_for(method(service_id), timeout=timeout)
```

### 2. Retry-Aware RemoteFunction Wrapper
Could wrap the __call__ logic with retry decorator:
- Intercept at line 591 (emission failure)
- Or wrap at entry to __call__ (whole method level)
- Re-create future, session, and re-encode message

### 3. Metadata Available for Retry Decision
```python
# From RemoteFunction instance
method._rpc  # RPC instance
method._encoded_method  # Full method definition
method._remote_workspace  # For service re-fetch
method._description  # For logging

# From last_error
error_type  # TimeoutError, ConnectionError, RemoteException, etc.
error_message  # Exception message
```

## Execution Order Summary

1. Caller gets service via `get_remote_service()` → RemoteService proxy
2. Caller accesses method attribute → RemoteFunction instance created via `_generate_remote_method()`
3. Caller invokes method → `RemoteFunction.__call__()` executed
4. `__call__()` creates future + session + resolve/reject callbacks
5. `__call__()` encodes method call and promise data
6. `__call__()` creates timer and emits message via RPC
7. Remote service receives message via `_on_message()` → `_handle_method()`
8. Remote service calls `promise.resolve(result)` or `promise.reject(error)`
9. This triggers RPC callback to `{session_id}.resolve()` on caller
10. Resolve callback sets result on original future
11. Caller awaits future and gets result

## Error Propagation

- **Network errors:** Caught in emit_task callback (line 599-606)
- **Timeout errors:** Timer callback triggered (line 492-521)
- **Remote errors:** Received as response, passed to reject callback
- **Session errors:** Session not found, peer not found (line 2618-2636)

All errors eventually call the `reject()` function which does `fut.set_exception(error)`.
