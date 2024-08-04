"""Test the schema generation."""

import pytest
import json
from hypha_rpc import (
    connect_to_server,
)
from pydantic import BaseModel, Field
from hypha_rpc.utils.schema import schema_function, Field as NativeField

from . import WS_SERVER_URL


@schema_function(schema_type="native:strict")
def say_hello_native(
    info: dict = NativeField(
        ..., description="Information of the person to say hello to"
    ),
    say_age: bool = Field(False, description="whether the age should be exposed"),
) -> str:
    """Say hello to a person."""
    assert isinstance(info, dict)
    assert isinstance(say_age, bool)
    if info["age"] and say_age:
        return f"hello {info['name']}, you are {info['age']} years old"
    else:
        return f"hello {info['name']}"


@pytest.mark.asyncio
async def test_schema_function_native():
    # If we pass a dictionary, it should do model_validate with the pydantic type
    assert say_hello_native({"name": "Alice", "age": 30}) == "hello Alice"
    assert say_hello_native(dict(name="Alice", age=30)) == "hello Alice"
    assert (
        say_hello_native(dict(name="Alice", age=30), say_age=True)
        == "hello Alice, you are 30 years old"
    )


class ContactManagerNative:
    @schema_function(skip_self=True, schema_type="native:auto")
    def exchange_contact(
        self,
        name: str,
        with_age: bool = Field(False, description="whether the age should be exposed"),
    ) -> dict:
        """Exchange contact information."""
        assert isinstance(with_age, bool)
        return dict(name=name, age=30 if with_age else -1)


@pytest.mark.asyncio
async def test_schema_function_on_class_method_native():
    """Test using @schema_function on a class method."""
    manager = ContactManagerNative()

    exchange_contact_with_schema = manager.exchange_contact

    # If we don't pass any parameter
    # Instead of the Field which was set to the default value, it should use the default value of the Field
    assert exchange_contact_with_schema("Alice") == dict(name="Alice", age=-1)


class UserInfo(BaseModel):
    """User information."""

    name: str = Field(..., description="name of the user")
    age: int = Field(..., description="age of the user")


@schema_function(schema_type="pydantic:strict")
def say_hello(
    info: UserInfo = Field(
        ..., description="Information of the person to say hello to"
    ),
    say_age: bool = Field(False, description="whether the age should be exposed"),
) -> str:
    """Say hello to a person."""
    assert isinstance(info, UserInfo)
    assert isinstance(say_age, bool)
    if info.age and say_age:
        return f"hello {info.name}, you are {info.age} years old"
    else:
        return f"hello {info.name}"


@pytest.mark.asyncio
async def test_schema_function():
    # If we pass a dictionary, it should do model_validate with the pydantic type
    assert say_hello({"name": "Alice", "age": 30}) == "hello Alice"
    assert say_hello(UserInfo(name="Alice", age=30)) == "hello Alice"
    assert (
        say_hello(UserInfo(name="Alice", age=30), say_age=True)
        == "hello Alice, you are 30 years old"
    )


class ContactManager:

    @schema_function(skip_self=True, schema_type="pydantic:auto")
    def exchange_contact(
        self,
        name: str,
        with_age: bool = Field(False, description="whether the age should be exposed"),
    ) -> dict:
        """Exchange contact information."""
        assert isinstance(with_age, bool)
        return UserInfo(name=name, age=30 if with_age else -1).model_dump()


@pytest.mark.asyncio
async def test_schema_function_on_class_method():
    """Test using @schema_function on a class method."""
    manager = ContactManager()

    exchange_contact_with_schema = manager.exchange_contact

    # If we don't pass any parameter
    # Instead of the Field which was set to the default value, it should use the default value of the Field
    assert (
        exchange_contact_with_schema("Alice")
        == UserInfo(name="Alice", age=-1).model_dump()
    )


def exchange_contact(
    info: UserInfo = Field(
        ..., description="Information of the person to say hello to"
    ),
    with_age: bool = Field(True, description="whether the age should be exposed"),
) -> dict:
    """Exchange contact information."""
    return UserInfo(name="Alice", age=30 if with_age else -1).model_dump()


@pytest.mark.asyncio
async def test_schema_function(websocket_server):
    """Test extract schema from functions."""
    exchange_contact_with_schema = schema_function(exchange_contact)

    expected = "hello John"
    assert say_hello({"name": "John", "age": 20}) == expected
    assert say_hello(UserInfo(name="John", age=20)) == expected

    assert (
        exchange_contact_with_schema({"name": "John", "age": 20})
        == UserInfo(name="Alice", age=30).model_dump()
    )

    # test workspace is an exception, so it can pass directly
    ws = await connect_to_server({"name": "my app", "server_url": WS_SERVER_URL})

    await ws.register_service(
        {
            "name": "Hello World",
            "id": "hello-world",
            "description": "hello world service",
            "config": {
                "visibility": "protected",
                "run_in_executor": True,
            },
            "say_hello": say_hello,
            "exchange_contact": exchange_contact_with_schema,
        }
    )
    svc = await ws.get_service("hello-world")

    assert svc.say_hello.__schema__
    assert svc.exchange_contact.__schema__
    # check some fields for the json schema
    assert svc.say_hello.__schema__["name"] == "say_hello"
    assert svc.say_hello.__schema__ == {
        "name": "say_hello",
        "description": "Say hello to a person.",
        "parameters": {
            "$defs": {
                "UserInfo": {
                    "description": "User information.",
                    "properties": {
                        "name": {"description": "name of the user", "type": "string"},
                        "age": {"description": "age of the user", "type": "integer"},
                    },
                    "required": ["name", "age"],
                    "type": "object",
                }
            },
            "properties": {
                "info": {"$ref": "#/$defs/UserInfo"},
                "say_age": {"default": False, "type": "boolean"},
            },
            "required": ["info"],
            "type": "object",
        },
    }
    assert await svc.say_hello({"name": "John", "age": 20}) == expected
    assert (
        await svc.exchange_contact({"name": "John", "age": 20})
        == UserInfo(name="Alice", age=30).model_dump()
    )


@pytest.mark.asyncio
async def test_schema_service(websocket_server):
    """Test creating schema service."""
    ws = await connect_to_server({"name": "my app", "server_url": WS_SERVER_URL})

    def exchange_contact_with_context(
        info: UserInfo = Field(
            ..., description="Information of the person to say hello to"
        ),
        with_age: bool = Field(True, description="whether the age should be exposed"),
        context=None,
    ) -> dict:
        """Exchange contact information."""
        return UserInfo(name="Alice", age=30 if with_age else -1).model_dump()

    svc = await ws.register_service(
        dict(
            id="test",
            name="Test Service",
            description="Services for testing.",
            config={
                "require_context": True,
                "visibility": "public",
                "schema_type": "pydantic",
            },
            exchange_contact=exchange_contact_with_context,
            inner_service={
                "exchange_contact": exchange_contact_with_context,
            },
        )
    )
    assert svc.exchange_contact["type"] == "function"
    assert svc.exchange_contact["function"] == {
        "name": "exchange_contact",
        "description": "Exchange contact information.",
        "parameters": {
            "$defs": {
                "UserInfo": {
                    "description": "User information.",
                    "properties": {
                        "name": {"description": "name of the user", "type": "string"},
                        "age": {"description": "age of the user", "type": "integer"},
                    },
                    "required": ["name", "age"],
                    "type": "object",
                }
            },
            "properties": {
                "info": {"$ref": "#/$defs/UserInfo"},
                "with_age": {"default": True, "type": "boolean"},
            },
            "required": ["info"],
            "type": "object",
        },
    }
    test_service = await ws.get_service(svc.id)

    assert test_service.exchange_contact.__schema__


@pytest.mark.asyncio
async def test_schema_service_modes(websocket_server):
    """Test creating schema service with different modes."""
    ws = await connect_to_server({"name": "my app", "server_url": WS_SERVER_URL})

    # Pydantic mode
    svc_pydantic = await ws.register_service(
        dict(
            id="test_pydantic",
            name="Test Service Pydantic",
            description="Services for testing pydantic mode.",
            config={
                "require_context": True,
                "visibility": "public",
                "schema_type": "pydantic",
            },
            exchange_contact=exchange_contact,
            inner_service={
                "exchange_contact": exchange_contact,
            },
        )
    )
    assert svc_pydantic.exchange_contact["type"] == "function"
    assert svc_pydantic.exchange_contact["function"]["name"] == "exchange_contact"
    assert "parameters" in svc_pydantic.exchange_contact["function"]

    # native mode
    svc_native = await ws.register_service(
        dict(
            id="test_native",
            name="Test Service native",
            description="Services for testing native mode.",
            config={
                "require_context": True,
                "visibility": "public",
                "schema_type": "native",
            },
            exchange_contact=exchange_contact,
            inner_service={
                "exchange_contact": exchange_contact,
            },
        )
    )
    assert svc_native.exchange_contact["type"] == "function"
    assert svc_native.exchange_contact["function"]["name"] == "exchange_contact"
    assert "parameters" in svc_native.exchange_contact["function"]

    test_service_pydantic = await ws.get_service(svc_pydantic.id)
    assert test_service_pydantic.exchange_contact.__schema__

    test_service_native = await ws.get_service(svc_native.id)
    assert test_service_native.exchange_contact.__schema__ == {
        "name": "exchange_contact",
        "description": "Exchange contact information.",
        "parameters": {"type": "object", "properties": {"info": {}}, "required": []},
    }
    assert "name" in test_service_native.exchange_contact.__schema__
    assert "description" in test_service_native.exchange_contact.__schema__
    assert "parameters" in test_service_native.exchange_contact.__schema__
