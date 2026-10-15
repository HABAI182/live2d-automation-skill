from live2d_automation.environment import installation_info


def test_reads_version_contents_instead_of_directory_name(tmp_path):
    root = tmp_path / "Cubism 5.4 alpha2"
    root.mkdir()
    assert installation_info(root)["version"] is None
    (root / "CubismEditor5.bat").write_text("rem Live2D Cubism ver 5.3.04\n", encoding="utf-8")
    assert installation_info(root)["version"] == "5.3.04"
