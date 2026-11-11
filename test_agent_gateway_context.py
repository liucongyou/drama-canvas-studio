from __future__ import annotations

import unittest
from unittest.mock import patch

import agent_gateway


class _Handler:
    def __init__(self, headers, client=("127.0.0.1", 50000)):
        self.headers = headers
        self.client_address = client


class AgentGatewayContextTests(unittest.TestCase):
    def test_unsigned_headers_carry_tenant_and_account(self):
        headers = agent_gateway.unsigned_context_headers(
            tenant_id="owner",
            account_id="admin",
            request_id="req-1",
        )
        context = agent_gateway.verify_unsigned_context_headers(headers)

        self.assertEqual(context.tenant_id, "owner")
        self.assertEqual(context.account_id, "admin")
        self.assertEqual(context.request_id, "req-1")

    def test_unsigned_headers_reject_missing_identity(self):
        with self.assertRaisesRegex(ValueError, "上下文不完整"):
            agent_gateway.unsigned_context_headers(
                tenant_id="",
                account_id="admin",
            )

    def test_only_loopback_unsigned_requests_are_eligible(self):
        self.assertTrue(
            agent_gateway.loopback_client(
                _Handler({}, client=("127.0.0.1", 50000)),
            )
        )
        self.assertFalse(
            agent_gateway.loopback_client(
                _Handler({}, client=("203.0.113.10", 50000)),
            )
        )

    def test_unsigned_mode_requires_no_secret_and_loopback_sidecar(self):
        with (
            patch.dict("os.environ", {"AGENT_INTERNAL_SECRET": ""}),
            patch.object(
                agent_gateway,
                "_sidecar_url",
                return_value="http://127.0.0.1:8895",
            ),
        ):
            self.assertTrue(agent_gateway.allow_unsigned_local_agent())

        with (
            patch.dict("os.environ", {"AGENT_INTERNAL_SECRET": "secret"}),
            patch.object(
                agent_gateway,
                "_sidecar_url",
                return_value="http://127.0.0.1:8895",
            ),
        ):
            self.assertFalse(agent_gateway.allow_unsigned_local_agent())


if __name__ == "__main__":
    unittest.main()
