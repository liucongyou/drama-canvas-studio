from __future__ import annotations

import unittest

import provider_client
from server import _generation_error_status, is_provider_rate_limit_error


class GenerationErrorStatusTests(unittest.TestCase):
    def test_reference_limit_is_a_client_input_error(self):
        error = RuntimeError("当前模型最多接受 5 个参考图片，实际 6 个")
        self.assertEqual(_generation_error_status(error), 400)

    def test_provider_rate_limit_preserves_429(self):
        error = provider_client.ProviderHTTPError(429, "rate limited")
        self.assertEqual(_generation_error_status(error), 429)
        self.assertTrue(is_provider_rate_limit_error(error))

    def test_upstream_500_becomes_bad_gateway(self):
        error = provider_client.ProviderHTTPError(500, "internal error")
        self.assertEqual(_generation_error_status(error), 502)


if __name__ == "__main__":
    unittest.main()
