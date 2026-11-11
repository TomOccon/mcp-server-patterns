from typing import Annotated, Literal, Optional

import pytest

from mcp_patterns.registry import ToolRegistry, schema_for, validate
from mcp_patterns.tools import registry


def make():
    r = ToolRegistry()

    @r.tool(read_only=True)
    def t(ctx, a: str, b: int = 3, c: Optional[Literal["x", "y"]] = None,
          d: Annotated[list[str], "names"] = None):
        """Do a thing.

        More detail that must not appear in the description.
        """

    return r


def test_schema_from_type_hints():
    spec = make().get("t")
    s = spec.input_schema
    assert s["required"] == ["a"]
    assert s["properties"]["a"] == {"type": "string"}
    assert s["properties"]["b"] == {"type": "integer", "default": 3}
    assert {"enum": ["x", "y"]} in s["properties"]["c"]["anyOf"]
    assert s["properties"]["d"]["items"] == {"type": "string"}
    assert s["properties"]["d"]["description"] == "names"
    assert s["additionalProperties"] is False
    assert spec.description == "Do a thing."


def test_workspace_param_added_and_destructive_params_only_on_destructive():
    props = registry.get("list_tables").input_schema["properties"]
    assert "workspace" in props and "confirm" not in props
    dprops = registry.get("drop_table").input_schema["properties"]
    assert {"workspace", "confirm", "dry_run"} <= set(dprops)
    assert "workspace" not in registry.get("list_workspaces").input_schema["properties"]


def test_annotations():
    assert registry.get("list_tables").annotations()["readOnlyHint"] is True
    a = registry.get("drop_table").annotations()
    assert a["destructiveHint"] is True and a["readOnlyHint"] is False


def test_registration_rules():
    r = ToolRegistry()
    with pytest.raises(ValueError):
        r.tool(read_only=True, destructive=True)
    with pytest.raises(TypeError):
        @r.tool()
        def bad(ctx, confirm: bool):  # reserved name
            pass
    with pytest.raises(TypeError):
        @r.tool()
        def no_ctx(x: int):
            pass

    @r.tool()
    def ok(ctx):
        pass

    with pytest.raises(ValueError):
        r.tool(name="ok")(lambda ctx: None)


def test_enough_tools_registered():
    assert len(registry.names()) >= 12


def test_validation():
    assert validate(True, {"type": "integer"})  # bool is not an int
    assert not validate(3, {"type": "number"})
    assert validate("x", {"enum": ["a"]})
    assert validate([1], {"type": "array", "items": {"type": "string"}})
    with pytest.raises(TypeError):
        schema_for(complex)


def test_call_rejects_bad_arguments(call):
    r = call("describe_table")
    assert r["status"] == "error" and "missing required" in r["message"]
    r = call("describe_table", table="orders", nope=1)
    assert r["status"] == "error" and "unknown argument" in r["message"]
    r = call("list_tables", limit="ten")
    assert r["code"] == "invalid_arguments"
    assert call("no_such_tool")["code"] == "unknown_tool"
