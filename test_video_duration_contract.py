import unittest

import provider_catalog


class VideoDurationContractTests(unittest.TestCase):
    def test_ark_default_seedance_duration_is_a_continuous_range(self):
        model = next(
            item for item in provider_catalog.DEFAULT_MODELS
            if item["id"] == "doubao-seedance-2-0"
        )
        self.assertEqual(model["params"]["duration"], {
            "type": "range",
            "min": 4,
            "max": 15,
            "step": 1,
        })

    def test_documented_150_second_model_accepts_each_integer(self):
        contract = provider_catalog._documented_video_contract(
            "高质量sd2.0/(全参933原生过真人速度快720P)"
        )
        self.assertEqual(contract["durations"], list(range(4, 16)))
        self.assertIn(11, contract["durations"])
        self.assertIn(12, contract["durations"])

    def test_exact_single_duration_model_only_accepts_15(self):
        contract = provider_catalog._documented_video_contract(
            "002系列/sd2.0(9图3音频15秒不卡脸480P)"
        )
        self.assertEqual(contract["durations"], [15])


if __name__ == "__main__":
    unittest.main()
