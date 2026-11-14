import json
import subprocess
import sys

import pytest

from simready.core.artifacts import digest, write_json
from simready.evaluation.verification import verify_candidate
from tests.fixtures.scene_candidate import make_scene_candidate


def test_evaluation_cli_accepts_synthetic_pair_without_release_authority(tmp_path):
    protocol_path, candidate_path, *_ = make_scene_candidate(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "simready.evaluation",
            "run",
            "--protocol",
            str(protocol_path),
            "--candidate",
            str(candidate_path),
            "--out",
            str(tmp_path / "result"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    score = json.loads((tmp_path / "result/scorecard.json").read_text())
    assert score["status"] == "PASS" and score["release_authorized"] is False


@pytest.mark.parametrize("phase", ["development", "sealed_release"])
def test_real_verifier_boundary_respects_feedback_phase(tmp_path, phase):
    protocol_path, candidate_path, protocol, candidate, _ = make_scene_candidate(
        tmp_path
    )
    protocol["phase"] = phase
    protocol["views"][0]["split"] = phase
    write_json(protocol_path, protocol)
    candidate["protocol_sha256"] = digest(protocol_path)
    write_json(candidate_path, candidate)
    scorecard, defects, observations = verify_candidate(
        protocol_path,
        candidate_path,
        tmp_path / "verification",
        protocol_hash=digest(protocol_path),
        timeout=30,
    )
    assert scorecard["status"] == "PASS"
    if phase == "sealed_release":
        assert defects["status"] == observations["status"] == "WITHHELD"
        assert "defects" not in defects
        assert not (tmp_path / "verification/defects.json").exists()
    else:
        assert defects["defects"] == []
        assert observations["phase"] == phase
