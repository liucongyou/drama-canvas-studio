# -*- coding: utf-8 -*-
import unittest

from generation_budget import check_budget, estimate_generation_cost


class GenerationBudgetTests(unittest.TestCase):
    def test_video_cost_uses_model_resolution_duration_and_video_input(self):
        model = {
            "pricing": {
                "currency": "CNY",
                "video": {
                    "unit": "second",
                    "rates": {"480P": 0.462, "720P": 0.994},
                    "ratesWithVideo": {"480P": 0.506},
                },
            },
        }
        normal = estimate_generation_cost(
            "video",
            {"resolution": "480P", "duration": 15},
            model,
        )
        with_video = estimate_generation_cost(
            "video",
            {"resolution": "480P", "duration": 15},
            model,
            has_video_input=True,
        )
        self.assertEqual(normal["amount"], 6.93)
        self.assertEqual(with_video["amount"], 7.59)

    def test_unknown_cost_is_blocked_only_when_budget_protection_is_on(self):
        estimate = estimate_generation_cost("image", {}, {})
        self.assertIsNone(check_budget(
            budget={"enabled": False},
            estimate=estimate,
            daily_spend=0,
            monthly_spend=0,
        ))
        issue = check_budget(
            budget={
                "enabled": True,
                "currency": "CNY",
                "allowUnknownCost": False,
            },
            estimate=estimate,
            daily_spend=0,
            monthly_spend=0,
        )
        self.assertEqual(issue["code"], "BUDGET_PRICE_UNKNOWN")

    def test_request_daily_and_monthly_limits_are_enforced(self):
        estimate = {"amount": 5.0, "currency": "CNY", "known": True}
        request_issue = check_budget(
            budget={
                "enabled": True,
                "currency": "CNY",
                "maxRequestCost": 4,
            },
            estimate=estimate,
            daily_spend=0,
            monthly_spend=0,
        )
        self.assertEqual(request_issue["code"], "BUDGET_REQUEST_LIMIT")
        daily_issue = check_budget(
            budget={
                "enabled": True,
                "currency": "CNY",
                "dailyLimit": 10,
            },
            estimate=estimate,
            daily_spend=6,
            monthly_spend=0,
        )
        self.assertEqual(daily_issue["code"], "BUDGET_DAILY_LIMIT")
        monthly_issue = check_budget(
            budget={
                "enabled": True,
                "currency": "CNY",
                "monthlyLimit": 12,
            },
            estimate=estimate,
            daily_spend=0,
            monthly_spend=8,
        )
        self.assertEqual(monthly_issue["code"], "BUDGET_MONTHLY_LIMIT")


if __name__ == "__main__":
    unittest.main()
