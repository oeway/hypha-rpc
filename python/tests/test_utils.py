"""Tests for the utils module."""

from functools import partial
from hypha_rpc.utils import callable_sig, callable_doc, parse_service_url

import pytest


def test_parse_service_url():
    # Test case 1: Basic service URL
    assert parse_service_url("https://hypha.aicell.io/public/services/hypha-login") == (
        "https://hypha.aicell.io",
        "public",
        "*",
        "hypha-login",
        "*",
    )

    # Test case 2: Service URL with client_id
    assert parse_service_url(
        "https://hypha.aicell.io/public/services/client:hypha-login"
    ) == ("https://hypha.aicell.io", "public", "client", "hypha-login", "*")

    # Test case 3: Service URL with app_id
    assert parse_service_url(
        "https://hypha.aicell.io/public/services/hypha-login@app"
    ) == ("https://hypha.aicell.io", "public", "*", "hypha-login", "app")

    # Test case 4: Service URL with both client_id and app_id
    assert parse_service_url(
        "https://hypha.aicell.io/public/services/client:hypha-login@app"
    ) == ("https://hypha.aicell.io", "public", "client", "hypha-login", "app")

    # Test case 5: Service URL with trailing slash
    assert parse_service_url(
        "https://hypha.aicell.io/public/services/hypha-login/"
    ) == ("https://hypha.aicell.io", "public", "*", "hypha-login", "*")

    # Test case 6: Invalid service URL (should raise ValueError)
    with pytest.raises(ValueError):
        parse_service_url("https://hypha.aicell.io/public/hypha-login")


def test_callable_sig():
    """Test callable_sig."""

    # Function
    def func(a, b, context=None):
        return a + b

    assert callable_sig(func) == "func(a, b, context=None)"
    assert callable_sig(func, skip_context=True) == "func(a, b)"

    # Lambda function
    def lambda_func(a, b, context=None):
        return a + b

    assert callable_sig(lambda_func) == "lambda_func(a, b, context=None)"
    assert callable_sig(lambda_func, skip_context=True) == "lambda_func(a, b)"

    # Class with a __call__ method
    class CallableClass:
        def __call__(self, a, b, context=None):
            return a + b

    assert callable_sig(CallableClass) == "CallableClass(self, a, b, context=None)"
    assert callable_sig(CallableClass, skip_context=True) == "CallableClass(self, a, b)"

    # Instance of a class with a __call__ method
    callable_instance = CallableClass()
    assert callable_sig(callable_instance) == "CallableClass(a, b, context=None)"
    assert callable_sig(callable_instance, skip_context=True) == "CallableClass(a, b)"

    # Built-in function
    assert callable_sig(print) in [
        "print(*args, **kwargs)",
        "print(*args, sep=' ', end='\\n', file=None, flush=False)",
    ]
    assert callable_sig(print, skip_context=True) in [
        "print(*args, **kwargs)",
        "print(*args, sep=' ', end='\\n', file=None, flush=False)",
    ]

    # Partial function
    partial_func = partial(func, b=3)
    assert callable_sig(partial_func) == "func(a, context=None)"
    assert callable_sig(partial_func, skip_context=True) == "func(a)"


def test_callable_doc():
    """Test callable_doc."""

    # Function with docstring
    def func_with_doc(a, b):
        """This is a function with a docstring."""
        return a + b

    assert callable_doc(func_with_doc) == "This is a function with a docstring."

    # Function without docstring
    def func_without_doc(a, b):
        return a + b

    assert callable_doc(func_without_doc) is None

    # Partial function with docstring
    def partial_func_with_doc(a, b=3):
        """This is a partial function with a docstring"""
        return a + b

    partial_func = partial(partial_func_with_doc, b=3)
    assert callable_doc(partial_func) == "This is a partial function with a docstring"
