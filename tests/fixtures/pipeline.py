"""Shared synthetic inputs; no test collection or real device initialization."""

import shutil

import pytest

from examples.pipeline.create_fixture import create_fixture
from simready.workflows.pipelines import video_pipeline


@pytest.fixture
def configured(tmp_path, monkeypatch):
    pytest.importorskip("pxr")
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg required for real observation extraction")
    path = create_fixture(tmp_path / "inputs")
    # Individual contract tests can coexist with other agents' source edits.
    monkeypatch.setattr(
        video_pipeline, "implementation_identity", lambda: "test-engine"
    )
    return path, tmp_path / "run"
