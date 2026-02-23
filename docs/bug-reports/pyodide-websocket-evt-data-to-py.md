# Bug report: `AttributeError: 'str' object has no attribute 'to_py'` in Pyodide websocket handler

## Summary
In `python/hypha_rpc/pyodide_websocket.py`, the runtime message handler for an open websocket assumes `evt.data` always has a `.to_py()` method:

```py
def on_message(evt):
    data = evt.data.to_py()
```

When websocket text frames are delivered as a Python `str` (which is valid in Pyodide/browser interop), this raises:

```text
AttributeError: 'str' object has no attribute 'to_py'
```

## Observed stack trace

```text
File "/lib/python3.13/site-packages/hypha_rpc/pyodide_websocket.py", line 312, in on_message
    data = evt.data.to_py()
AttributeError: 'str' object has no attribute 'to_py'
```

## Why this is valid runtime behavior
WebSocket message events can carry text payloads as strings. In Pyodide interop, primitives can already be converted to native Python objects; therefore `evt.data` may be a Python `str` and not a JS proxy object.

## Impact
- Reconnection/runtime messages can fail in browser/Pyodide environments.
- Client may repeatedly retry and/or time out despite server responses.
- Tool/function-calling workflows become unstable because message handling crashes on incoming text payloads.

## Scope and evidence
- Affected code path: `PyodideWebsocketRPCConnection.open()` message callback (`on_message`) in `python/hypha_rpc/pyodide_websocket.py`.
- Current branch examined: `main` at commit `722980161ecd3a4b874637d6b836bae1811471be`.
- This line is still present in current `main` and package version `0.21.15`.

## Reproduction (minimal)

```py
class DummyEvent:
    data = "{\"type\": \"reconnection_token\", \"reconnection_token\": \"abc\"}"

# mirrors current conversion line
_ = DummyEvent().data.to_py()  # raises AttributeError
```

## Suggested fix direction
Use a type-safe conversion path that accepts both JS proxy values and native Python values:

1. Read `raw = evt.data`.
2. If `raw` has `.to_py()`, call it.
3. If resulting `data` is `str`, JSON-decode and process as control message.
4. If binary-like, convert to bytes and pass to message handler.

This bug report PR intentionally demonstrates the issue and adds a regression test marked `xfail`.
