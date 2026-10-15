import os

import pytest

from live2d_automation.cli import settings_from_env
from live2d_automation.engine import Engine


@pytest.mark.real_editor
@pytest.mark.skipif(
    os.environ.get("LIVE2D_REAL_EDITOR") != "1",
    reason="Real Cubism integration is opt-in; contract fixtures do not satisfy acceptance",
)
async def test_real_editor_read_snapshot():
    engine = Engine(settings_from_env())
    try:
        result = await engine.run("snapshot", expected_uid=os.environ.get("LIVE2D_TEST_MODEL_UID"))
        assert result.status == "verified", result.model_dump()
        assert result.model_uid and result.data["snapshot_sha256"]
    finally:
        await engine.close()
