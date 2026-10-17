import sys

import pytest

from live2d_automation.editor_ui import EditorUI
from live2d_automation.environment import installation_info
from live2d_automation.jab import Bridge
from live2d_automation.models import AutomationError


def test_reads_version_contents_instead_of_directory_name(tmp_path):
    root = tmp_path / "Cubism 5.4 alpha2"
    root.mkdir()
    assert installation_info(root)["version"] is None
    (root / "CubismEditor5.bat").write_text("rem Live2D Cubism ver 5.3.04\n", encoding="utf-8")
    assert installation_info(root)["version"] == "5.3.04"


def test_non_windows_native_ui_stops_before_process_or_focus_access(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(AutomationError, match="requires Windows"):
        EditorUI(-1)
    with pytest.raises(RuntimeError, match="requires Windows"):
        Bridge(-1)
    with pytest.raises(AutomationError, match="requires Windows"):
        EditorUI.__new__(EditorUI).focus(0)
    with pytest.raises(RuntimeError, match="requires Windows"):
        Bridge.__new__(Bridge).windows()
