import pytest


class DummyEvent:
    def __init__(self, data):
        self.data = data


@pytest.mark.xfail(
    strict=True,
    raises=AttributeError,
    reason=(
        "Current pyodide websocket handler assumes evt.data has .to_py(); "
        "text websocket payloads can already be Python str in Pyodide interop."
    ),
)
def test_text_event_payload_can_trigger_str_to_py_attribute_error():
    evt = DummyEvent('{"type":"reconnection_token","reconnection_token":"abc"}')

    # Mirrors current line in python/hypha_rpc/pyodide_websocket.py:on_message
    _ = evt.data.to_py()
