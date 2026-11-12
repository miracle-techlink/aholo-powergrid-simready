"""Environment failures must stop the one-command run before costly workers."""

import json
import sys

import pytest

from simready.simulation import environment_check


@pytest.mark.parametrize(
    "missing", ["ffmpeg", "isaacsim", "numpy", "PIL", "pxr", "scipy"]
)
def test_required_dependencies_affect_passed_without_starting_kit(
    monkeypatch, capsys, missing
):
    monkeypatch.setattr(sys, "argv", ["doctor", "--require-lab"])
    monkeypatch.setattr(
        environment_check.importlib.util,
        "find_spec",
        lambda name: None if name == missing else object(),
    )
    monkeypatch.setattr(
        environment_check.shutil,
        "which",
        lambda name: None if missing == "ffmpeg" else "/usr/bin/ffmpeg",
    )
    assert environment_check.main() == 2
    result = json.loads(capsys.readouterr().out)
    assert result["passed"] is False and result["kit_launched"] is False
    assert missing == "ffmpeg" or missing in result["missing_modules"]
