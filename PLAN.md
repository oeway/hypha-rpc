# Implementation Plan: Fix #146 — WebSocket Reconnection Service Resilience

## Fix 1: Transparent Retry on Stale Service Errors

### Concept
When a caller's remote method call fails because the provider disconnected and re-registered
(causing "Method expired", "peer not found", etc.), automatically re-fetch the service and
retry the call **once** — transparently to the caller.

### Approach
Wrap each method on a `RemoteService` returned by `get_remote_service()` with retry logic.
This is done at the `get_remote_service` level, NOT inside `_generate_remote_method`, so:
- Only user-facing service methods are retried (not internal RPC calls)
- The retry wrapper has full context: service URI, method name, RPC instance
- No changes to the deep internals of the RPC call machinery

### Error Patterns to Retry On
- `"Method expired or not found"` — provider's service was cleaned up during disconnect
- `"Session not found"` — session was garbage collected
- `"peer_not_found"` / `"Peer ... is not connected"` — provider is currently disconnected
- `"Method not found"` — method routing changed after re-registration
- `"Connection was closed"` — WebSocket closed mid-call

### Python Implementation (`python/hypha_rpc/rpc.py`)

1. Add a helper function `_is_stale_service_error(error)` that checks error messages
2. In `get_remote_service()`, after creating the `RemoteService`, wrap each callable
   method with an async retry wrapper:
   ```python
   async def method_with_retry(*args, **kwargs):
       try:
           return await original_method(*args, **kwargs)
       except Exception as e:
           if _is_stale_service_error(e):
               logger.info("Stale service detected, refreshing: %s", service_uri)
               refreshed_svc = await self.get_remote_service(service_uri, {"_no_retry": True})
               new_method = getattr(refreshed_svc, method_name)
               return await new_method(*args, **kwargs)
           raise
   ```
3. Use a `_no_retry` config flag to prevent infinite recursion when re-fetching
4. Preserve `__rpc_object__`, `__name__`, `__doc__`, `__schema__` on the wrapper

### JavaScript Implementation (`javascript/src/rpc.js`)

Same approach — wrap methods in `get_remote_service()`:
```javascript
const retryWrapper = async function(...args) {
    try {
        return await originalMethod(...args);
    } catch (e) {
        if (isStaleServiceError(e)) {
            const refreshed = await self.get_remote_service(serviceUri, { _no_retry: true });
            return await refreshed[methodName](...args);
        }
        throw e;
    }
};
```

### Files Modified
- `python/hypha_rpc/rpc.py`: `get_remote_service()` method + helper function
- `javascript/src/rpc.js`: `get_remote_service()` method + helper function

---

## Fix 2: Application-Level Keepalive (Python)

### Problem
Python's `websockets` library sends protocol-level PING frames, but the Hypha server's
idle timeout (`asyncio.wait_for(websocket.receive(), timeout=600)`) only resets on
**data frames**, not PING/PONG. Long-running RPC calls can trigger the server's idle
timeout even though the connection is active.

JavaScript already has application-level pings (`{"type": "ping"}` every 30s), so this
fix is **Python-only**.

### Approach
Add a periodic application-level ping task to the Python WebSocket client, matching
what the JavaScript client already does.

### Implementation (`python/hypha_rpc/websocket_client.py`)

1. Add a new `_app_ping_interval` parameter (default: 30 seconds)
2. Add a `_app_ping_task` background task that sends `{"type": "ping"}` as JSON
   at `_app_ping_interval` intervals
3. Start the task after connection is established (in `open()` or the listen task)
4. Cancel/clean up the task on disconnect and close
5. The JSON ping message goes through the regular WebSocket send path, which the
   server's `receive()` WILL see as a data frame, resetting the idle timeout

### Files Modified
- `python/hypha_rpc/websocket_client.py`: Constructor, `open()`, `close()`, cleanup methods
