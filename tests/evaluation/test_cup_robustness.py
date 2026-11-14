import pytest

from simready.evaluation.cup_robustness import execution_status, summarize_campaign


def passing_audits():
    parameters = {
        "composition_complete": True,
        "frozen_sample_matches": True,
        "asset_hashes_match": True,
        "plan_unchanged": True,
        "dependencies_unchanged": True,
        "mass_matches": True,
        "mass_inertia_consistent": True,
    }
    for group in ("cup", "gripper", "table"):
        for field in (
            "coefficients",
            "combine",
            "coverage",
            "binding",
            "collisions_enabled",
        ):
            parameters[f"{group}.{field}"] = True
    task = dict.fromkeys(
        [
            "evidence_hashes_match",
            "complete_phase_sequence",
            "finite_measured_arrays",
            "minimum_2s_hold",
            "lifted_clear_of_table",
            "bilateral_hold_and_transfer",
            "upright_during_transfer",
            "destination_center_error_under_15mm",
            "destination_surface_contact",
            "upright_after_release",
            "released_from_gripper",
            "supported_by_destination_table",
            "robot_did_not_hit_monitored_environment",
            "no_object_attachment_joint",
            "object_is_dynamic",
        ],
        True,
    )
    return {
        "parameter_audit": {"status": "PASS", "checks": parameters},
        "task_audit": {"status": "PASS", "checks": task},
    }


def test_shell_wrapped_task_failure_is_complete_but_not_successful():
    worker = {"status": "FAILED", "frames": 5400, "checks": {"placed": False}}
    audit = {
        "status": "FAILED",
        "checks": {
            "complete_phase_sequence": True,
            "finite_measured_arrays": True,
            "evidence_hashes_match": True,
        },
    }
    assert execution_status(1, worker, audit) == "COMPLETE"
    assert execution_status(2, worker, audit) == "COMPLETE"
    assert execution_status(1, {**worker, "status": "PASS"}, audit) == "WORKER_FAILED"
    assert execution_status(1, {**worker, "error": "crash"}, audit) == "WORKER_FAILED"
    audit["checks"]["evidence_hashes_match"] = False
    assert execution_status(1, worker, audit) == "WORKER_FAILED"


def test_missing_and_worker_failed_episodes_remain_in_denominator():
    manifest = {
        "samples": [
            {"id": "a", "split": "development"},
            {"id": "b", "split": "heldout"},
            {"id": "c", "split": "heldout"},
        ]
    }
    passed = {
        "id": "a",
        "status": "COMPLETE",
        "worker_exit_code": 0,
        "worker_status": "PASS",
        "task_audit_exit_code": 0,
        "runtime_parameters_match": True,
        **passing_audits(),
    }
    report = summarize_campaign(
        manifest, [passed, {**passed, "id": "b", "worker_exit_code": 1}]
    )
    assert report["all"]["episodes"] == 3
    assert report["all"]["successes"] == 1
    assert report["by_split"]["heldout"]["successes"] == 0
    assert report["status"] == "INCOMPLETE"
    assert report["all"]["wilson_95"] == pytest.approx([0.06149, 0.79234], abs=1e-5)


@pytest.mark.parametrize("key", ["parameter_audit", "task_audit"])
def test_execution_alone_does_not_count_as_success(key):
    row = {
        "id": "a",
        "status": "COMPLETE",
        "worker_exit_code": 0,
        "worker_status": "PASS",
        "task_audit_exit_code": 0,
        "runtime_parameters_match": True,
        **passing_audits(),
    }
    row[key]["status"] = "FAILED"
    report = summarize_campaign({"samples": [{"id": "a", "split": "heldout"}]}, [row])
    assert report["all"]["success_rate"] == 0
    assert report["policy_training_performed"] is False


def test_duplicate_or_unrequested_result_is_rejected():
    manifest = {"samples": [{"id": "a", "split": "heldout"}]}
    with pytest.raises(ValueError, match="Unexpected or duplicate"):
        summarize_campaign(manifest, [{"id": "a"}, {"id": "a"}])
    with pytest.raises(ValueError, match="Unexpected or duplicate"):
        summarize_campaign(manifest, [{"id": "b"}])


@pytest.mark.parametrize(
    "corruption",
    [
        "audit_exit",
        "worker_failed",
        "empty_checks",
        "false_check",
        "missing_checks",
        "missing_criterion",
    ],
)
def test_labels_without_accepted_execution_and_checks_are_not_success(corruption):
    row = {
        "id": "a",
        "status": "COMPLETE",
        "worker_exit_code": 0,
        "worker_status": "PASS",
        "task_audit_exit_code": 0,
        "runtime_parameters_match": True,
        **passing_audits(),
    }
    if corruption == "audit_exit":
        row["task_audit_exit_code"] = 1
    elif corruption == "worker_failed":
        row["worker_status"] = "FAILED"
    elif corruption == "empty_checks":
        row["task_audit"]["checks"] = {}
    elif corruption == "false_check":
        row["task_audit"]["checks"]["placed"] = False
    elif corruption == "missing_criterion":
        del row["task_audit"]["checks"]["upright_after_release"]
    else:
        del row["parameter_audit"]["checks"]
    report = summarize_campaign({"samples": [{"id": "a", "split": "heldout"}]}, [row])
    assert report["all"]["successes"] == 0
