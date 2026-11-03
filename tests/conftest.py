import pytest

from incident_lab.scenarios import REGISTRY
from incident_lab.workspace import build_workspace


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    """Every scenario workspace, built once per test session."""
    out = tmp_path_factory.mktemp("build")
    return {sid: build_workspace(sid, out) for sid in REGISTRY}
