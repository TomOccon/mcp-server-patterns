import pytest

from mcp_patterns.tools import registry
from mcp_patterns.workspaces import WorkspaceRouter

SECRET_DEV = "dev-secret-value-9f8e7d6c"
SECRET_LEGACY = "legacy-secret-value-1a2b3c4d"

CONFIG = {
    "default_workspace": "dev",
    "workspaces": {
        "dev": {"url": "https://dev.example.invalid"},
        "legacy": {"url": "https://legacy.example.invalid", "features": ["catalog", "jobs", "compute"]},
    },
}


@pytest.fixture
def env():
    return {"WORKSPACE_DEV_TOKEN": SECRET_DEV, "WORKSPACE_LEGACY_TOKEN": SECRET_LEGACY}


@pytest.fixture
def router(env):
    return WorkspaceRouter.from_dict(CONFIG, env=env)


@pytest.fixture
def call(router):
    def _call(tool_name, **args):
        return registry.call(tool_name, args, router)

    return _call


@pytest.fixture(autouse=True)
def default_cap():
    old = registry.max_output_chars
    yield
    registry.max_output_chars = old
