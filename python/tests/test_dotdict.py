import pytest
import copy
from hypha_rpc.utils import dotdict  # Adjust the import based on your file structure


def test_getattr():
    d = dotdict({"a": 1, "b": {"c": 2}})
    assert d.a == 1
    assert d.b.c == 2


def test_getattr_keyerror():
    d = dotdict({"a": 1})
    assert d.nonexistent is None


def test_setattr():
    d = dotdict()
    d.a = 1
    assert d["a"] == 1


def test_setattr_nested():
    d = dotdict()
    d.b = {"c": 2}
    assert isinstance(d.b, dotdict)
    assert d.b.c == 2


def test_delattr():
    d = dotdict({"a": 1, "b": 2})
    del d.a
    assert "a" not in d


def test_delattr_keyerror():
    d = dotdict({"a": 1})
    with pytest.raises(KeyError):
        del d.nonexistent


def test_deepcopy():
    d = dotdict({"a": 1, "b": {"c": 2}})
    d_copy = copy.deepcopy(d)
    assert d == d_copy
    assert d is not d_copy
    assert d.b == d_copy.b
    assert d.b is not d_copy.b


def test_nested_dotdict():
    d = dotdict({"a": {"b": {"c": 2}}})
    assert isinstance(d.a, dotdict)
    assert isinstance(d.a.b, dotdict)
    assert d.a.b.c == 2


def test_regular_dict_methods():
    d = dotdict({"a": 1, "b": 2})
    assert d.get("a") == 1
    assert d.get("nonexistent", "default") == "default"
    assert list(d.keys()) == ["a", "b"]
    assert list(d.values()) == [1, 2]
    assert list(d.items()) == [("a", 1), ("b", 2)]


def test_update():
    d = dotdict({"a": 1, "b": 2})
    d.update({"b": 3, "c": 4})
    assert d.b == 3
    assert d.c == 4


def test_update_nested():
    d = dotdict({"a": {"b": 1}})
    d.update({"a": {"c": 2}})
    assert d.a.b == 1
    assert d.a.c == 2


def test_key_collision():
    d = dotdict({"get": "custom_value", "update": "another_value", "keys": 99})
    assert d["get"] == "custom_value"
    assert d["update"] == "another_value"
    assert d["keys"] == 99
    assert d.keys == 99


def test_recursive_initialization():
    d = dotdict.from_dict({"a": {"b": {"c": 2}}})
    assert isinstance(d.a, dotdict)
    assert isinstance(d.a.b, dotdict)
    assert d.a.b.c == 2


def test_recursive_setitem():
    d = dotdict.from_dict({"a": {"b": {"c": 2}}})
    assert isinstance(d.a, dotdict)
    assert isinstance(d.a.b, dotdict)
    assert d.a.b.c == 2


if __name__ == "__main__":
    pytest.main()
