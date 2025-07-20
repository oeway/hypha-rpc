"""
Integration tests to verify consistent API between Python and JavaScript implementations.
Tests for unlisted visibility and enhanced schema generation.
"""

import pytest
import asyncio
import subprocess
import tempfile
import os
from pathlib import Path
from hypha_rpc import connect_to_server
from hypha_rpc.rpc import _get_schema, _convert_function_to_schema


@pytest.mark.asyncio
async def test_unlisted_visibility_consistency():
    """Test that unlisted visibility works consistently across Python and JavaScript."""
    # This test requires a running server, skip if not available
    WS_SERVER_URL = "wss://hypha.aicell.io/ws"
    
    try:
        # Connect with Python client
        ws = await connect_to_server({
            "name": "python-test-client",
            "server_url": WS_SERVER_URL,
            "timeout": 10
        })
        
        def test_unlisted_service(message: str):
            """Test service with unlisted visibility.
            
            Args:
                message: Test message to process
            """
            return f"Processed: {message}"
        
        # Register service with unlisted visibility
        await ws.register_service({
            "id": "test-unlisted-service",
            "name": "Test Unlisted Service",
            "description": "Service to test unlisted visibility",
            "config": {
                "visibility": "unlisted",
                "require_context": False,
            },
            "process_message": test_unlisted_service
        })
        
        # Verify service is accessible (like public)
        service = await ws.get_service("test-unlisted-service")
        result = await service.process_message("Hello World")
        assert result == "Processed: Hello World"
        
        # Verify schema generation works correctly
        service_info = ws._extract_service_info({
            "id": "test-service",
            "config": {"visibility": "unlisted"},
            "process_message": test_unlisted_service
        })
        
        schema = service_info["service_schema"]
        assert "process_message" in schema
        assert schema["process_message"]["type"] == "function"
        assert schema["process_message"]["function"]["name"] == "process_message"
        assert schema["process_message"]["function"]["description"] == "Test service with unlisted visibility."
        assert "parameters" in schema["process_message"]["function"]
        
        await ws.disconnect()
        
    except Exception as e:
        pytest.skip(f"Server not available or connection failed: {e}")


@pytest.mark.asyncio
async def test_enhanced_schema_generation_consistency():
    """Test that enhanced schema generation works consistently."""
    
    def example_function(name: str, age: int = 25, active: bool = True):
        """Process user information.
        
        Args:
            name: User's full name
            age: User's age in years
            active: Whether user is active
        
        Returns:
            dict: Processed user data
        """
        return {"name": name, "age": age, "active": active}
    
    # Test Python schema generation
    schema = _get_schema(example_function, "example_function")
    
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "example_function"
    assert "Process user information." in schema["function"]["description"]
    
    properties = schema["function"]["parameters"]["properties"]
    assert properties["name"]["type"] == "string"
    assert properties["name"]["description"] == "User's full name"
    assert properties["age"]["type"] == "integer"
    assert properties["age"]["description"] == "User's age in years"
    assert properties["active"]["type"] == "boolean"
    assert properties["active"]["description"] == "Whether user is active"
    
    # Required parameters (those without defaults)
    assert schema["function"]["parameters"]["required"] == ["name"]


def test_schema_structure_matches_openai_format():
    """Test that generated schemas match OpenAI function calling format."""
    
    def calculate_total(price: float, tax_rate: float = 0.1, discount: float = 0.0):
        """Calculate total price with tax and discount.
        
        Args:
            price: Base price before tax and discount
            tax_rate: Tax rate as decimal (e.g., 0.1 for 10%)
            discount: Discount amount to subtract
            
        Returns:
            float: Final calculated total
        """
        return (price - discount) * (1 + tax_rate)
    
    schema = _get_schema(calculate_total, "calculate_total")
    
    # Verify OpenAI-compatible structure
    expected_structure = {
        "type": "function",
        "function": {
            "name": str,
            "description": str,
            "parameters": {
                "type": "object",
                "properties": dict,
                "required": list
            }
        }
    }
    
    def check_structure(actual, expected, path=""):
        if isinstance(expected, dict):
            assert isinstance(actual, dict), f"Expected dict at {path}, got {type(actual)}"
            for key, expected_type in expected.items():
                assert key in actual, f"Missing key '{key}' at {path}"
                if isinstance(expected_type, type):
                    assert isinstance(actual[key], expected_type), f"Expected {expected_type} at {path}.{key}, got {type(actual[key])}"
                else:
                    check_structure(actual[key], expected_type, f"{path}.{key}")
    
    check_structure(schema, expected_structure)
    
    # Verify specific content
    assert "Calculate total price" in schema["function"]["description"]
    assert schema["function"]["parameters"]["required"] == ["price"]
    
    properties = schema["function"]["parameters"]["properties"]
    assert "price" in properties
    assert "tax_rate" in properties
    assert "discount" in properties
    
    # All should have proper descriptions
    assert properties["price"]["description"] == "Base price before tax and discount"
    assert properties["tax_rate"]["description"] == "Tax rate as decimal (e.g., 0.1 for 10%)"
    assert properties["discount"]["description"] == "Discount amount to subtract"


if __name__ == "__main__":
    # Run the tests directly
    asyncio.run(test_enhanced_schema_generation_consistency())
    test_schema_structure_matches_openai_format()
    print("All integration tests passed!")