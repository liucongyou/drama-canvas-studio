# -*- coding: utf-8 -*-
"""Server-side generation cost estimation and conservative budget checks."""
from __future__ import annotations

from typing import Any


def estimate_generation_cost(
    kind: str,
    params: dict[str, Any] | None,
    model: dict[str, Any] | None,
    *,
    has_video_input: bool = False,
) -> dict[str, Any]:
    params = params if isinstance(params, dict) else {}
    model = model if isinstance(model, dict) else {}
    pricing = model.get("pricing") if isinstance(model.get("pricing"), dict) else {}
    rule = pricing.get(kind) if isinstance(pricing.get(kind), dict) else {}
    currency = str(pricing.get("currency") or "CNY").upper()
    count = max(1, int(params.get("count") or 1))
    fixed = _number(rule.get("fixed"))
    amount = None

    if kind == "video":
        if rule.get("unit") == "second":
            rates = (
                rule.get("ratesWithVideo")
                if has_video_input and isinstance(rule.get("ratesWithVideo"), dict)
                else rule.get("rates")
            )
            rates = rates if isinstance(rates, dict) else {}
            resolution = str(params.get("resolution") or "")
            rate = _number(rates.get(resolution))
            if rate is None:
                rate = _number(rates.get("default"))
            duration = _number(params.get("duration")) or 0
            if rate is not None and rate >= 0 and duration > 0:
                amount = rate * duration * count
        elif rule.get("unit") in {"video", "request"} and fixed is not None and fixed >= 0:
            amount = fixed * count
    elif kind == "image":
        if rule.get("unit") in {"image", "request"}:
            rates = rule.get("rates") if isinstance(rule.get("rates"), dict) else {}
            rate = _number(rates.get(str(params.get("clarity") or "")))
            if rate is None:
                rate = _number(rates.get(str(params.get("ratio") or "")))
            if rate is None:
                rate = _number(rates.get("default"))
            if rate is None:
                rate = fixed
            if rate is not None and rate >= 0:
                amount = rate * count
    elif kind == "audio":
        if rule.get("unit") == "second":
            rates = rule.get("rates") if isinstance(rule.get("rates"), dict) else {}
            rate = _number(rates.get("default"))
            if rate is None:
                rate = fixed
            duration = _number(params.get("previewDuration") or params.get("duration")) or 0
            if rate is not None and rate >= 0 and duration > 0:
                amount = rate * duration
        elif rule.get("unit") == "request" and fixed is not None and fixed >= 0:
            amount = fixed
    elif kind == "text":
        if rule.get("unit") in {"request", "text"} and fixed is not None and fixed >= 0:
            amount = fixed

    known = amount is not None and amount >= 0
    return {
        "amount": round(float(amount), 6) if known else None,
        "currency": currency,
        "known": known,
    }


def check_budget(
    *,
    budget: dict[str, Any],
    estimate: dict[str, Any],
    daily_spend: float,
    monthly_spend: float,
) -> dict[str, Any] | None:
    if not budget.get("enabled"):
        return None
    amount = estimate.get("amount")
    currency = str(estimate.get("currency") or "CNY").upper()
    budget_currency = str(budget.get("currency") or "CNY").upper()
    if amount is None:
        if budget.get("allowUnknownCost"):
            return None
        return {
            "code": "BUDGET_PRICE_UNKNOWN",
            "message": "已启用生成预算保护，但当前模型没有可核实的计费规则。请先配置模型 pricing，或明确允许未知费用。",
        }
    if currency != budget_currency:
        return {
            "code": "BUDGET_CURRENCY_MISMATCH",
            "message": f"模型计费币种为 {currency}，预算币种为 {budget_currency}，无法安全比较。",
        }
    amount = float(amount)
    max_request = float(budget.get("maxRequestCost") or 0)
    if max_request > 0 and amount > max_request:
        return {
            "code": "BUDGET_REQUEST_LIMIT",
            "message": f"本次预计 {currency} {amount:.2f}，超过单次上限 {currency} {max_request:.2f}。",
        }
    daily_limit = float(budget.get("dailyLimit") or 0)
    if daily_limit > 0 and float(daily_spend) + amount > daily_limit:
        return {
            "code": "BUDGET_DAILY_LIMIT",
            "message": f"本次预计 {currency} {amount:.2f}；今日已占用 {currency} {daily_spend:.2f}，将超过日预算 {currency} {daily_limit:.2f}。",
        }
    monthly_limit = float(budget.get("monthlyLimit") or 0)
    if monthly_limit > 0 and float(monthly_spend) + amount > monthly_limit:
        return {
            "code": "BUDGET_MONTHLY_LIMIT",
            "message": f"本次预计 {currency} {amount:.2f}；本月已占用 {currency} {monthly_spend:.2f}，将超过月预算 {currency} {monthly_limit:.2f}。",
        }
    return None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None
