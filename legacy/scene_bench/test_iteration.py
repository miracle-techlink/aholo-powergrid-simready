import copy
import unittest

from legacy.scene_bench.bench import candidate_digest
from legacy.scene_bench.evaluate_iteration import verify_pair


class EvidenceBindingTests(unittest.TestCase):
    def setUp(self):
        self.assets = [{"path": "/source.usdc", "sha256": "original"}]
        self.repeat = {
            "assets": self.assets,
            "candidate_digest": candidate_digest({"assets": self.assets}),
            "renderer_script_sha256": "same",
            "raw_capture_index": 2,
        }
        self.candidate = {
            **self.repeat,
            "assets": self.assets + [{"path": "/patch.usdc", "sha256": "patch"}],
        }

    def test_legitimate_patch_keeps_baseline(self):
        verify_pair(self.candidate, self.repeat, self.assets)

    def test_candidate_cannot_be_its_own_repeat_control(self):
        fake = copy.deepcopy(self.candidate)
        fake["candidate_digest"] = candidate_digest(fake)
        with self.assertRaisesRegex(ValueError, "original baseline"):
            verify_pair(self.candidate, fake, self.assets)

    def test_changed_source_cannot_hide_under_new_patch(self):
        altered = copy.deepcopy(self.candidate)
        altered["assets"][0]["sha256"] = "replaced"
        with self.assertRaisesRegex(ValueError, "source layers"):
            verify_pair(altered, self.repeat, self.assets)

    def test_renderer_change_rejected(self):
        altered = {**self.candidate, "renderer_script_sha256": "other"}
        with self.assertRaisesRegex(ValueError, "renderer code"):
            verify_pair(altered, self.repeat, self.assets)


if __name__ == "__main__":
    unittest.main()
