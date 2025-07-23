"""Test the Hypha RPC module."""

import uuid

WS_PORT = 3828
WS_SERVER_URL = f"http://127.0.0.1:{WS_PORT}"

MINIO_PORT = 38583
MINIO_SERVER_URL = f"http://127.0.0.1:{MINIO_PORT}"
MINIO_SERVER_URL_PUBLIC = f"http://localhost:{MINIO_PORT}"
MINIO_ROOT_USER = "minio"
MINIO_ROOT_PASSWORD = str(uuid.uuid4())


def find_item(items, key, value):
    """Find an item with key or attributes in an object list."""
    filtered = [
        item
        for item in items
        if (item[key] if isinstance(item, dict) else getattr(item, key)) == value
    ]
    if len(filtered) == 0:
        return None

    return filtered[0]
