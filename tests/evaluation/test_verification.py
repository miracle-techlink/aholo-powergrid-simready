import pytest

from simready.core.artifacts import digest, write_json
from simready.evaluation.verification import verify_candidate
from tests.fixtures.scene_candidate import make_scene_candidate


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
