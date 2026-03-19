# _rintf_ Service Leak - Complete Analysis

## Problem Statement
240+ `_rintf_` (callback) services appear on the Redis server, despite being created locally-only. These should NEVER be registered with the server.

## Root Cause (Simple)
1. `_rintf_` services created via `add_service()` are stored in `self._services` / `this._services`
2. On reconnection, `on_connected()` loops over ALL services in `self._services` and registers them with the server
3. No filtering exists to exclude `_rintf_` services from server registration

## Exact Leak Points

### Python (hypha_rpc/rpc.py:860-900)
In `on_connected()` function:
```python
for service in list(self._services.values()):  # <-- INCLUDES _rintf_
    service_info = self._extract_service_info(service)
    await manager.register_service(service_info)  # <-- SENDS TO SERVER
```

### JavaScript (javascript/src/rpc.js:600-640)
In connection handler:
```javascript
const services = Object.values(this._services);  // <-- INCLUDES _rintf_
for (let service of services) {
    const serviceInfo = this._extract_service_info(service);
    await manager.registerService(serviceInfo);  // <-- SENDS TO SERVER
}
```

## Why It Happens
- `_rintf_` services created in `_encode()` method (line 3482+ Python, line 3373+ JS)
- Called `add_service(serviceApi, true)` which stores in `self._services` / `this._services`
- On each reconnection, ALL services are re-registered
- 240 leaked = ~240 reconnections where _rintf_ services persisted

## Why This Is Wrong
- `_rintf_` = temporary callback objects for peer-to-peer RPC
- Should be restricted to single allowed caller (stored in `_rintf_allowed_caller` config)
- NEVER should be exposed to server/workspace
- Both Python and JS already track _rintf_ separately in `_rintf_caller_index` / `_rintfCallerIndex`

## The Fix
**Cleanest**: Separate storage for _rintf_ services
1. Create `self._rintf_services` / `this._rintf_services`
2. In `_encode()`, add _rintf_ to separate dict instead of `_services`
3. In `on_connected()`, loop only over `self._services` (excludes _rintf_)
4. Update cleanup methods to handle both dicts

**Alternative**: Add filter in loop
- `if not service.get("id", "").startswith("_rintf_"): register_service(...)`

## Files to Modify
1. `python/hypha_rpc/rpc.py` - Initialize `_rintf_services`, separate storage in `_encode()`, filter in `on_connected()`
2. `javascript/src/rpc.js` - Same changes in JS

## Key Line Numbers
- Python creation: 3482
- Python leak: 860-900 (on_connected)
- Python storage init: 785
- JS creation: 3373
- JS leak: 600-640 (connection handler)
