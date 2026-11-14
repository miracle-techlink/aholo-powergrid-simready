import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from legacy.scene_evaluation.bench import (
    candidate_digest,
    evaluate,
    evaluate_pair,
    sha256,
)
from legacy.scene_evaluation.cost_guard import CostGuard


class EvidenceTests(unittest.TestCase):
    def test_render_is_bound_to_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new("RGB", (4, 4), "white").save(root / "ref.png")
            (root / "camera.json").write_text('{"synthetic_test_only":true}')

            def item(name):
                return {"path": name, "sha256": sha256(root / name)}

            reference = item("ref.png")
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "frames": [
                            {
                                "filename": "ref.png",
                                "sha256": reference["sha256"],
                                "split": "dev",
                            }
                        ]
                    }
                )
            )
            candidate = {
                "id": "synthetic",
                "role": "test",
                "assets": [item("camera.json")],
            }
            provenance = {
                "candidate_digest": candidate_digest(candidate),
                "render_sha256": reference["sha256"],
                "camera_evidence_sha256": item("camera.json")["sha256"],
            }
            (root / "provenance.json").write_text(json.dumps(provenance))
            pair = {
                "id": "synthetic",
                "split": "dev",
                "reference": reference,
                "render": reference,
                "camera": {
                    "status": "verified",
                    "reviewer": "test_only",
                    "evidence": item("camera.json"),
                },
                "render_provenance": item("provenance.json"),
            }
            candidate["matched_views"] = [pair]
            config = {
                "reference_manifest": "manifest.json",
                "reference_manifest_sha256": sha256(root / "manifest.json"),
                "candidates": [candidate],
            }
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config))
            self.assertEqual(len(evaluate(config_path)["candidates"][0]["pairs"]), 1)
            candidate["assets"].append(reference)
            config_path.write_text(json.dumps(config))
            result = evaluate(config_path)["candidates"][0]
            self.assertEqual(result["pairs"], [])
            self.assertIn("provenance", result["errors"][0])

    def test_frozen_split_cannot_be_relabelled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new("RGB", (4, 4), "white").save(root / "ref.png")
            item = {"path": "ref.png", "sha256": sha256(root / "ref.png")}
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "frames": [
                            {
                                "filename": "ref.png",
                                "sha256": item["sha256"],
                                "split": "heldout",
                            }
                        ]
                    }
                )
            )
            config = {
                "reference_manifest": "manifest.json",
                "reference_manifest_sha256": sha256(root / "manifest.json"),
                "candidates": [
                    {
                        "id": "test",
                        "role": "test",
                        "assets": [],
                        "matched_views": [
                            {"id": "bad", "reference": item, "split": "dev"}
                        ],
                    }
                ],
            }
            (root / "config.json").write_text(json.dumps(config))
            report = evaluate(root / "config.json")
            self.assertIn("frozen reference", report["candidates"][0]["errors"][0])
            self.assertEqual(report["candidates"][0]["pairs"], [])
            (root / "manifest.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "changed"):
                evaluate(root / "config.json")

    def test_unverified_camera_cannot_score(self):
        with self.assertRaisesRegex(ValueError, "independently reviewed"):
            evaluate_pair({"split": "dev"}, Path("."))

    def test_identical_and_changed_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new("RGB", (12, 8), (100, 120, 90)).save(root / "ref.png")
            (root / "camera.json").write_text('{"synthetic_test_only":true}')
            item = {"path": "ref.png", "sha256": sha256(root / "ref.png")}
            pair = {
                "id": "synthetic",
                "split": "dev",
                "reference": item,
                "render": item,
                "camera": {
                    "status": "verified",
                    "reviewer": "unit_test_only",
                    "evidence": {
                        "path": "camera.json",
                        "sha256": sha256(root / "camera.json"),
                    },
                },
            }
            result = evaluate_pair(pair, root)
            self.assertEqual(result["rgb_mae"], 0)
            self.assertTrue(result["identical_pixels"])
            review = {
                "status": "CONDITIONAL_SINGLE_VIEW_APPEARANCE_BASELINE",
                "reviewer": "unit_test_only",
                "reference": item,
                "render": item,
                "approved_regions": {"full_image": {"mask_sha256": None}},
            }
            (root / "review.json").write_text(json.dumps(review))
            pair["camera"].update(
                status="conditional_estimated",
                review={"path": "review.json", "sha256": sha256(root / "review.json")},
            )
            self.assertEqual(
                evaluate_pair(pair, root)["camera_status"], "conditional_estimated"
            )
            pair["region"] = "unreviewed_region"
            with self.assertRaisesRegex(ValueError, "region/mask"):
                evaluate_pair(pair, root)
            pair.pop("region")
            pair["camera"]["reviewer"] = "unapproved_reviewer"
            with self.assertRaisesRegex(ValueError, "does not approve"):
                evaluate_pair(pair, root)
            pair["camera"]["reviewer"] = "unit_test_only"
            Image.new("RGB", (12, 8), (1, 2, 3)).save(root / "ref.png")
            with self.assertRaisesRegex(ValueError, "changed evidence"):
                evaluate_pair(pair, root)


class CostTests(unittest.TestCase):
    request = {
        "provider": "test",
        "endpoint": "reconstruction",
        "provider_version": "unknown",
        "input_sha256": ["a" * 64],
        "parameters": {"quality": "high"},
    }

    def test_run_metadata_cannot_bypass_dedup(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = CostGuard(Path(directory) / "cost.sqlite")
            guard.reserve(self.request, 2)
            with self.assertRaisesRegex(ValueError, "exactly"):
                guard.reserve({**self.request, "run_id": "new-run"}, 2)
            self.assertEqual(guard.summary()["reserved_submissions"], 1)
            guard.db.close()

    def test_zero_budget_and_unknown_outcome_never_resubmits(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = CostGuard(Path(directory) / "cost.sqlite")
            self.assertEqual(guard.reserve(self.request)["action"], "BUDGET_BLOCKED")
            reservation = guard.reserve(self.request, 1)
            guard.record(reservation["fingerprint"], "unknown")
            self.assertEqual(
                guard.reserve(self.request, 10)["action"], "RECONCILE_EXISTING"
            )
            self.assertIsNone(guard.summary()["actual_credits"])
            guard.db.close()

    def test_concurrent_budget_and_cache_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cost.sqlite"
            initial = CostGuard(path)
            initial.db.close()

            def reserve(_):
                guard = CostGuard(path)
                result = guard.reserve(self.request, 1)
                guard.db.close()
                return result

            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(reserve, range(8)))
            self.assertEqual(sum(r["action"] == "RESERVED" for r in results), 1)
            guard = CostGuard(path)
            artifact = Path(directory) / "asset.usda"
            artifact.write_text("test fixture, not USD")
            key = results[0]["fingerprint"]
            guard.record(key, "succeeded", "test-job", artifact, 2)
            self.assertEqual(guard.reserve(self.request, 1)["action"], "REUSE_ARTIFACT")
            artifact.write_text("corrupted")
            self.assertEqual(
                guard.reserve(self.request, 1)["action"], "RECONCILE_EXISTING"
            )
            self.assertEqual(
                guard.reserve({**self.request, "parameters": {"quality": "normal"}}, 1)[
                    "action"
                ],
                "BUDGET_BLOCKED",
            )
            guard.db.close()


if __name__ == "__main__":
    unittest.main()
