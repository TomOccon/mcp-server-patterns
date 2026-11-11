from mcp_patterns.capabilities import CapabilityProbe
from mcp_patterns.errors import FeatureUnavailable
from mcp_patterns.registry import ToolRegistry
from mcp_patterns.workspaces import WorkspaceRouter
from tests.conftest import CONFIG


def test_supported_feature_works(call):
    r = call("vector_search", index="docs", query="churn retention", top_k=2)
    assert r["status"] == "ok"
    assert len(r["data"]["items"]) == 2
    assert "churn" in r["data"]["items"][0]["text"]


def test_missing_feature_returns_structured_unavailable(call):
    for tool, args in [("vector_search", {"index": "docs", "query": "x"}),
                       ("list_models", {}), ("invoke_model", {"model": "m", "inputs": ["a"]})]:
        r = call(tool, workspace="legacy", **args)
        assert r["status"] == "unavailable"
        assert r["workspace"] == "legacy"
        assert r["feature"] in ("vector_search", "model_serving")
        assert "reason" in r and "hint" in r


def test_other_features_on_same_workspace_still_work(call):
    assert call("list_clusters", workspace="legacy")["status"] == "ok"
    assert call("list_tables", workspace="legacy")["status"] == "ok"


def test_in_function_gate_degrades_too(router):
    r = ToolRegistry()

    @r.tool(read_only=True)
    def sometimes(ctx):
        ctx.require("vector_search")
        return "reached"

    assert r.call("sometimes", {"workspace": "dev"}, router)["data"] == "reached"
    out = r.call("sometimes", {"workspace": "legacy"}, router)
    assert out["status"] == "unavailable" and out["feature"] == "vector_search"


def test_probe_is_cached_per_workspace_and_feature(router):
    probe = router.probe
    ctx = router.resolve("legacy")
    for _ in range(5):
        probe.check("legacy", ctx.backend, "vector_search")
    assert probe.probe_count == 1
    probe.check("legacy", ctx.backend, "jobs")
    assert probe.probe_count == 2
    probe.invalidate("legacy")
    probe.check("legacy", ctx.backend, "jobs")
    assert probe.probe_count == 3


def test_failing_probe_means_unavailable_not_crash():
    class Broken:
        def supports(self, feature):
            raise RuntimeError("boom with secret-ish text")

    probe = CapabilityProbe()
    ok, reason = probe.check("w", Broken(), "jobs")
    assert not ok and "boom" not in reason
    try:
        probe.require("w", Broken(), "jobs")
    except FeatureUnavailable as fu:
        assert fu.feature == "jobs"
