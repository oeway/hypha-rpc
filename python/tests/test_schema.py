"""Test the schema generation."""

import pytest
from hypha_rpc import (
    connect_to_server,
)
from pydantic import BaseModel, Field
from hypha_rpc.utils.schema import (
    schema_service,
    schema_function,
    schema_method,
    Field as NativeField,
)
from typing import Optional, Union

from . import WS_SERVER_URL


@schema_function(schema_type="native:strict")
def register_user_native(
    user_info: dict = NativeField(
        ..., description="Information of the user to register"
    ),
    receive_newsletter: bool = Field(
        False, description="Whether the user wants to receive newsletters"
    ),
) -> str:
    """Register a new user."""
    assert isinstance(user_info, dict)
    assert isinstance(receive_newsletter, bool)
    return f"User {user_info['name']} registered{' with newsletter subscription' if receive_newsletter else ''}"


@pytest.mark.asyncio
async def test_schema_function_native():
    # If we pass a dictionary, it should do model_validate with the pydantic type
    assert (
        register_user_native({"name": "Alice", "email": "alice@example.com"})
        == "User Alice registered"
    )
    assert (
        register_user_native(dict(name="Alice", email="alice@example.com"))
        == "User Alice registered"
    )
    assert (
        register_user_native(
            dict(name="Alice", email="alice@example.com"), receive_newsletter=True
        )
        == "User Alice registered with newsletter subscription"
    )


class OrderManagerNative:

    @schema_method(schema_type="native:auto")
    def place_order(
        self,
        product_id: str,
        quantity: Optional[int] = Field(
            1, description="Quantity of the product to order"
        ),
    ) -> dict:
        """Place an order for a product."""
        assert isinstance(quantity, int)
        return dict(product_id=product_id, quantity=quantity)


@pytest.mark.asyncio
async def test_schema_function_on_class_method_native():
    """Test using @schema_function on a class method."""
    manager = OrderManagerNative()

    place_order_with_schema = manager.place_order

    # If we don't pass any parameter
    # Instead of the Field which was set to the default value, it should use the default value of the Field
    assert place_order_with_schema("12345") == dict(product_id="12345", quantity=1)


class UserInfo(BaseModel):
    """User information."""

    name: str = Field(..., description="Name of the user")
    email: str = Field(..., description="Email of the user")


class UserInfoDetailed(BaseModel):
    """User information."""

    name: str = Field(..., description="Name of the user")
    email: str = Field(..., description="Email of the user")
    age: int = Field(..., description="Age of the user")
    address: str = Field(..., description="Address of the user")


@schema_function(schema_type="pydantic:strict")
def register_user(
    user_info: Union[UserInfo, UserInfoDetailed] = Field(
        ..., description="Information of the user to register"
    ),
    receive_newsletter: bool = NativeField(
        False, description="Whether the user wants to receive newsletters"
    ),
) -> str:
    """Register a new user."""
    assert isinstance(user_info, (UserInfo, UserInfoDetailed))
    assert isinstance(receive_newsletter, bool)
    if isinstance(user_info, UserInfoDetailed):
        return f"User {user_info.name} registered with detailed information{' with newsletter subscription' if receive_newsletter else ''}"
    return f"User {user_info.name} registered{' with newsletter subscription' if receive_newsletter else ''}"


@pytest.mark.asyncio
async def test_schema_function():
    # If we pass a dictionary, it should do model_validate with the pydantic type
    assert (
        register_user({"name": "Alice", "email": "alice@example.com"})
        == "User Alice registered"
    )

    assert (
        register_user(
            {
                "name": "Alice",
                "email": "alice@example.com",
                "age": 20,
                "address": "1234 Main St",
            }
        )
        == "User Alice registered with detailed information"
    )
    assert (
        register_user(UserInfo(name="Alice", email="alice@example.com"))
        == "User Alice registered"
    )
    assert (
        register_user(
            UserInfo(name="Alice", email="alice@example.com"), receive_newsletter=True
        )
        == "User Alice registered with newsletter subscription"
    )


class OrderManager:

    @schema_function(skip_self=True, schema_type="pydantic:auto")
    def place_order(
        self,
        product_id: str,
        quantity: Optional[Union[int, str]] = Field(
            1, description="Quantity of the product to order"
        ),
    ) -> dict:
        """Place an order for a product."""
        assert isinstance(quantity, (int, str))
        return dict(product_id=product_id, quantity=quantity)


@pytest.mark.asyncio
async def test_schema_function_on_class_method():
    """Test using @schema_function on a class method."""
    manager = OrderManager()

    place_order_with_schema = manager.place_order
    assert place_order_with_schema.__schema__ == {
        "name": "place_order",
        "description": "Place an order for a product.",
        "parameters": {
            "properties": {
                "product_id": {"description": "product_id", "type": "string"},
                "quantity": {
                    "anyOf": [
                        {"type": "integer"},
                        {"type": "string"},
                        {"type": "null"},
                    ],
                    "default": 1,
                    "description": "Quantity of the product to order",
                },
            },
            "required": ["product_id"],
            "type": "object",
        },
    }

    # If we don't pass any parameter
    # Instead of the Field which was set to the default value, it should use the default value of the Field
    assert place_order_with_schema("12345") == dict(product_id="12345", quantity=1)


def place_order(
    product_id: str = Field(..., description="ID of the product to order"),
    quantity: int = NativeField(1, description="Quantity of the product to order"),
) -> dict:
    """Place an order for a product."""
    assert isinstance(quantity, int)
    assert isinstance(product_id, str)
    return dict(product_id=product_id, quantity=quantity)


def place_order_native(
    product_id: str = NativeField(..., description="ID of the product to order"),
    quantity: int = Field(1, description="Quantity of the product to order"),
) -> dict:
    """Place an order for a product."""
    assert isinstance(quantity, int)
    assert isinstance(product_id, str)
    return dict(product_id=product_id, quantity=quantity)


@pytest.mark.asyncio
async def test_schema_function(websocket_server):
    """Test extract schema from functions."""
    place_order_with_schema = schema_function(place_order)

    expected = "User John registered"
    assert register_user({"name": "John", "email": "john@example.com"}) == expected
    assert register_user(UserInfo(name="John", email="john@example.com")) == expected

    assert place_order_with_schema(product_id="12345", quantity=2) == dict(
        product_id="12345", quantity=2
    )

    # test workspace is an exception, so it can pass directly
    ws = await connect_to_server({"name": "my app", "server_url": WS_SERVER_URL})

    await ws.register_service(
        {
            "name": "Order Service",
            "id": "order-service",
            "description": "Service for placing orders",
            "config": {
                "visibility": "protected",
                "run_in_executor": True,
            },
            "register_user": register_user,
            "place_order": place_order_with_schema,
        }
    )
    svc = await ws.get_service("order-service")

    assert svc.register_user.__schema__
    assert svc.place_order.__schema__
    # check some fields for the json schema
    assert svc.register_user.__schema__["name"] == "register_user"
    assert svc.register_user.__schema__ == {
        "name": "register_user",
        "description": "Register a new user.",
        "parameters": {
            "$defs": {
                "UserInfo": {
                    "description": "User information.",
                    "properties": {
                        "name": {"description": "Name of the user", "type": "string"},
                        "email": {"description": "Email of the user", "type": "string"},
                    },
                    "required": ["name", "email"],
                    "type": "object",
                },
                "UserInfoDetailed": {
                    "description": "User information.",
                    "properties": {
                        "name": {"description": "Name of the user", "type": "string"},
                        "email": {"description": "Email of the user", "type": "string"},
                        "age": {"description": "Age of the user", "type": "integer"},
                        "address": {
                            "description": "Address of the user",
                            "type": "string",
                        },
                    },
                    "required": ["name", "email", "age", "address"],
                    "type": "object",
                },
            },
            "properties": {
                "user_info": {
                    "anyOf": [
                        {"$ref": "#/$defs/UserInfo"},
                        {"$ref": "#/$defs/UserInfoDetailed"},
                    ],
                    "description": "Information of the user to register",
                },
                "receive_newsletter": {
                    "default": False,
                    "description": "Whether the user wants to receive newsletters",
                    "type": "boolean",
                },
            },
            "required": ["user_info"],
            "type": "object",
        },
    }
    assert (
        await svc.register_user({"name": "John", "email": "john@example.com"})
        == expected
    )
    assert await svc.place_order(product_id="12345", quantity=2) == dict(
        product_id="12345", quantity=2
    )


@pytest.mark.asyncio
async def test_schema_service(websocket_server):
    """Test creating schema service."""
    ws = await connect_to_server({"name": "my app", "server_url": WS_SERVER_URL})

    def place_order_with_context(
        product_id: str = Field(..., description="ID of the product to order"),
        quantity: int = Field(1, description="Quantity of the product to order"),
        context=None,
    ) -> dict:
        """Place an order for a product."""
        return dict(product_id=product_id, quantity=quantity)

    svc = await ws.register_service(
        schema_service(
            schema_type="pydantic",
            id="test",
            name="Test Service",
            description="Services for testing.",
            config={
                "require_context": True,
                "visibility": "public",
            },
            place_order=place_order_with_context,
            inner_service={
                "place_order": place_order_with_context,
            },
        )
    )
    assert svc.service_schema.place_order["type"] == "function"
    assert svc.service_schema.place_order["function"] == {
        "name": "place_order",
        "description": "Place an order for a product.",
        "parameters": {
            "properties": {
                "product_id": {
                    "description": "ID of the product to order",
                    "type": "string",
                },
                "quantity": {
                    "default": 1,
                    "description": "Quantity of the product to order",
                    "type": "integer",
                },
            },
            "required": ["product_id"],
            "type": "object",
        },
    }

    test_service = await ws.get_service(svc.id)

    assert test_service.place_order.__schema__


@pytest.mark.asyncio
async def test_schema_service_modes(websocket_server):
    """Test creating schema service with different modes."""
    ws = await connect_to_server({"name": "my app", "server_url": WS_SERVER_URL})

    # Pydantic mode
    svc_pydantic = await ws.register_service(
        schema_service(
            schema_type="pydantic",
            id="test_pydantic",
            name="Test Service Pydantic",
            description="Services for testing pydantic mode.",
            config={
                "require_context": True,
                "visibility": "public",
            },
            place_order=place_order,
            inner_service={
                "place_order": place_order,
            },
        )
    )
    assert svc_pydantic.service_schema.place_order["type"] == "function"
    assert svc_pydantic.service_schema.place_order["function"]["name"] == "place_order"
    assert "parameters" in svc_pydantic.service_schema.place_order["function"]

    # native mode
    svc_native = await ws.register_service(
        schema_service(
            schema_type="native",
            id="test_native",
            name="Test Service native",
            description="Services for testing native mode.",
            config={
                "require_context": True,
                "visibility": "public",
            },
            place_order=place_order_native,
            inner_service={
                "place_order": place_order_native,
            },
        )
    )
    assert svc_native.service_schema.place_order["type"] == "function"
    assert svc_native.service_schema.place_order["function"]["name"] == "place_order"
    assert "parameters" in svc_native.service_schema.place_order["function"]

    test_service_pydantic = await ws.get_service(svc_pydantic.id)
    assert test_service_pydantic.place_order.__schema__

    test_service_native = await ws.get_service(svc_native.id)
    assert test_service_native.place_order.__schema__ == {
        "name": "place_order",
        "description": "Place an order for a product.",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {
                    "type": "str",
                    "description": "ID of the product to order",
                },
                "quantity": {
                    "type": "int",
                    "default": 1,
                    "description": "Quantity of the product to order",
                },
            },
            "required": ["product_id"],
        },
    }
    assert "name" in test_service_native.place_order.__schema__
    assert "description" in test_service_native.place_order.__schema__
    assert "parameters" in test_service_native.place_order.__schema__
