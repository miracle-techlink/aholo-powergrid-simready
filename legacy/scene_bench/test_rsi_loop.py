import unittest

from legacy.scene_bench.rsi_loop import decide


class AcceptanceTests(unittest.TestCase):
    gates = {
        "minimum_mean_psnr_gain_db": 0.1,
        "baseline_repeat_noise_multiplier": 2,
        "minimum_mean_mae_reduction_fraction": 0.005,
        "maximum_any_roi_mae_increase": 0.001,
    }

    def row(self, gain=0.2, mae=0.098, noise=0.01):
        return {
            "psnr_gain_vs_repeat": gain,
            "repeat_psnr_drift_abs": noise,
            "repeat": {"rgb_mae": 0.1},
            "candidate": {"rgb_mae": mae},
            "repeat_mae_drift_abs": 0.0001,
        }

    def test_improvement_never_promoted_without_holdout(self):
        result = decide([self.row()], self.gates)
        self.assertEqual(result["status"], "DEV_IMPROVEMENT_PENDING_HOLDOUT")
        self.assertFalse(result["promoted"])

    def test_mean_gain_cannot_hide_roi_regression(self):
        result = decide([self.row(mae=0.08), self.row(mae=0.102)], self.gates)
        self.assertEqual(result["status"], "REJECT_KEEP_BASELINE")

    def test_repeat_drift_blocks_small_improvement(self):
        self.assertEqual(
            decide([self.row(noise=0.15)], self.gates)["status"], "REJECT_KEEP_BASELINE"
        )


if __name__ == "__main__":
    unittest.main()
