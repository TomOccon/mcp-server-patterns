from mcp_patterns.workspaces import WorkspaceRouter, token_env_var
from tests.conftest import CONFIG, SECRET_DEV, SECRET_LEGACY


def test_env_var_naming():
    assert token_env_var("dev") == "WORKSPACE_DEV_TOKEN"
    assert token_env_var("my-ws.1") == "WORKSPACE_MY_WS_1_TOKEN"


def test_default_and_explicit_routing(call):
    assert call("describe_table", table="orders")["workspace"] == "dev"
    assert call("describe_table", table="orders", workspace="legacy")["workspace"] == "legacy"


def test_unknown_workspace_lists_available(call):
    r = call("list_tables", workspace="prod")
    assert r["code"] == "unknown_workspace"
    assert "dev" in r["message"] and "legacy" in r["message"]


def test_missing_token_names_env_var_only(router):
    router.env = {"WORKSPACE_DEV_TOKEN": SECRET_DEV}
    from mcp_patterns.tools import registry

    r = registry.call("list_tables", {"workspace": "legacy"}, router)
    assert r["code"] == "auth_missing"
    assert "WORKSPACE_LEGACY_TOKEN" in r["message"]


def test_workspaces_hold_independent_state(call):
    assert call("drop_table", table="events", confirm=True)["status"] == "ok"
    assert call("describe_table", table="events")["code"] == "not_found"
    assert call("describe_table", table="events", workspace="legacy")["status"] == "ok"


def test_list_workspaces_reports_presence_not_values(call):
    r = call("list_workspaces")
    text = str(r)
    assert SECRET_DEV not in text and SECRET_LEGACY not in text
    assert all(w["token_configured"] for w in r["data"]["workspaces"])


def test_token_rotation_is_picked_up(env):
    router = WorkspaceRouter.from_dict(CONFIG, env=env)
    ctx = router.resolve("dev")
    env["WORKSPACE_DEV_TOKEN"] = "rotated-token-value-xyz"
    assert router.resolve("dev").backend._token == "rotated-token-value-xyz"
    assert ctx.backend is router.resolve("dev").backend  # same state, new credentials
