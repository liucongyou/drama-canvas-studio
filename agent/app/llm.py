from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.backend_models import BackendConfigError, resolve_text_model
from app.config import get_settings

TICK3 = chr(96) * 3


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolRound:
    content: str
    tool_calls: list[dict[str, Any]]
    native: bool


def _demo_feed() -> dict[str, Any]:
    path = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "canvas-feed-valid" / "generic.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "title": "Agent Demo · 第01集",
        "canvas_contract": {"schema": "drama-canvas-feed/v2", "video_model": "doubao-seedance-2-0", "video_aspect": "9:16"},
        "asset_prompts": [{"code": "图1", "name": "主角", "raw": "一位克制的中年男人，站在旧楼道里。"}],
        "voice_prompts": [{
            "code": "图1", "name": "主角", "format": "audio-prompt-v1",
            "prompt": "calm male voice, 40 years old, standard Mandarin, clean short drama dubbing reference clip.",
            "label": "calm male", "description": "calm male voice", "age": "40", "gender": "male", "dialect": "standard",
        }],
        "segments": [{
            "no": 1, "name": "段1",
            "raw": "【核心人物】@角色｜主角（图1）\n【站位声明】@角色｜主角（图1）（画面左）\n台词：主角（calm）：\"这次，我不走了。\"",
        }],
    }


def extract_json(text: str) -> dict[str, Any]:
    pattern = TICK3 + r"(?:json)?\s*(\{.*?\})\s*" + TICK3
    fence = re.search(pattern, text, re.S)
    candidate = fence.group(1) if fence else text
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end <= start:
        raise LLMError("模型没有返回 JSON 对象")
    try:
        data = json.loads(candidate[start:end + 1])
    except ValueError as exc:
        raise LLMError(f"模型返回的 JSON 无法解析：{exc}") from exc
    if not isinstance(data, dict):
        raise LLMError("模型返回的不是 JSON 对象")
    return data


def _mock() -> str:
    return TICK3 + "json\n" + json.dumps(_demo_feed(), ensure_ascii=False, indent=2) + "\n" + TICK3


def chat(
    system: str,
    user: str,
    model_id: str = "",
    tenant_id: str = "",
) -> str:
    settings = get_settings()
    if settings.llm_mode == "mock":
        return _mock()

    resolved = None
    try:
        resolved = resolve_text_model(model_id, tenant_id=tenant_id)
    except BackendConfigError:
        resolved = None

    if resolved:
        base_url = str(resolved["baseUrl"])
        api_key = str(resolved["apiKey"])
        model_name = str(resolved["model"])
    else:
        base_url = settings.llm_base_url.rstrip("/")
        api_key = settings.llm_api_key
        model_name = settings.llm_model
    if not (base_url and api_key and model_name):
        return _mock()

    payload = {
        "model": model_name,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    last_error = None
    for attempt in range(2):
        try:
            with httpx.Client(timeout=httpx.Timeout(connect=20, read=settings.request_timeout_seconds, write=30, pool=20), trust_env=False) as client:
                response = client.post(
                    base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                )
                if response.status_code >= 400:
                    raise LLMError(f"模型接口 HTTP {response.status_code}: {response.text[:300]}")
                data = response.json()
            break
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.8)
                continue
            raise LLMError(f"模型连接失败：{exc}") from exc
    else:
        raise LLMError(f"模型连接失败：{last_error}")
    try:
        return str(data["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("模型接口返回格式不符合 OpenAI 兼容格式") from exc


def chat_tool_round(
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    model_id: str = "",
    tenant_id: str = "",
) -> ToolRound:
    settings = get_settings()
    if settings.llm_mode == "mock":
        return ToolRound(content=_mock(), tool_calls=[], native=True)
    base_url, api_key, model_name = _target(model_id, tenant_id=tenant_id)
    if not (base_url and api_key and model_name):
        return ToolRound(content=_mock(), tool_calls=[], native=True)
    payload: dict[str, Any] = {
        "model": model_name,
        "temperature": 0.2,
        "messages": [{"role": "system", "content": system}, *messages],
        "tools": tools,
        "tool_choice": "auto",
    }
    try:
        with httpx.Client(
            timeout=httpx.Timeout(
                connect=20,
                read=settings.request_timeout_seconds,
                write=30,
                pool=20,
            ),
            trust_env=False,
        ) as client:
            response = client.post(
                base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
    except (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.RemoteProtocolError,
    ) as exc:
        raise LLMError(f"模型连接失败：{exc}") from exc
    if response.status_code >= 400:
        text = response.text[:500]
        if response.status_code in {400, 404, 422} and "tool" in text.lower():
            fallback_user = "\n\n".join(
                _message_text(item)
                for item in messages
                if item.get("role") in {"user", "tool", "assistant"}
            )
            return ToolRound(
                content=chat(
                    system,
                    fallback_user,
                    model_id=model_id,
                    tenant_id=tenant_id,
                ),
                tool_calls=[],
                native=False,
            )
        raise LLMError(f"模型接口 HTTP {response.status_code}: {text}")
    try:
        message = response.json()["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise LLMError("模型接口返回格式不符合 OpenAI 兼容格式") from exc
    return ToolRound(
        content=str(message.get("content") or ""),
        tool_calls=[
            item
            for item in message.get("tool_calls") or []
            if isinstance(item, dict)
        ],
        native=True,
    )


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False) if content is not None else ""


def _target(
    model_id: str = "",
    tenant_id: str = "",
) -> tuple[str, str, str]:
    settings = get_settings()
    resolved = None
    try:
        resolved = resolve_text_model(model_id, tenant_id=tenant_id)
    except BackendConfigError:
        resolved = None
    if resolved:
        return str(resolved["baseUrl"]), str(resolved["apiKey"]), str(resolved["model"])
    return settings.llm_base_url.rstrip("/"), settings.llm_api_key, settings.llm_model


def chat_stream(
    system: str,
    user: str,
    model_id: str = "",
    tenant_id: str = "",
):
    settings = get_settings()
    if settings.llm_mode == "mock":
        text = _mock()
        for index in range(0, len(text), 48):
            yield text[index:index + 48]
        return
    base_url, api_key, model_name = _target(model_id, tenant_id=tenant_id)
    if not (base_url and api_key and model_name):
        text = _mock()
        for index in range(0, len(text), 48):
            yield text[index:index + 48]
        return
    payload = {
        "model": model_name,
        "temperature": 0.2,
        "stream": True,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    last_error = None
    for attempt in range(2):
        yielded = False
        try:
            with httpx.Client(timeout=httpx.Timeout(connect=20, read=settings.request_timeout_seconds, write=30, pool=20), trust_env=False) as client:
                with client.stream(
                    "POST",
                    base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                ) as response:
                    if response.status_code >= 400:
                        raise LLMError(f"模型接口 HTTP {response.status_code}: {response.read()[:300]}")
                    for line in response.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            item = json.loads(data)
                            delta = item["choices"][0].get("delta") or {}
                            content = delta.get("content")
                        except (ValueError, KeyError, IndexError, TypeError):
                            continue
                        if content:
                            yielded = True
                            yield str(content)
            return
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            last_error = exc
            if yielded:
                raise LLMError(f"模型流式请求中断：{exc}") from exc
            if attempt == 1:
                break
            time.sleep(0.8)
    # Streaming is an optimization; never lose a generation because the streaming
    # connection failed before the first byte. Fall back to the non-stream call.
    try:
        text = chat(
            system,
            user,
            model_id=model_id,
            tenant_id=tenant_id,
        )
    except LLMError:
        raise LLMError(f"模型流式请求失败：{last_error}") from last_error
    for index in range(0, len(text), 120):
        yield text[index:index + 120]
