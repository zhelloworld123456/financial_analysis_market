from __future__ import annotations

import json
import os
import socket
import time
import uuid
from dataclasses import dataclass
from http.client import RemoteDisconnected
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .errors import ModelError


@dataclass(frozen=True, slots=True)
class KimiResult:
    content: dict[str, Any]
    usage: dict[str, int]
    model: str
    request_id: str


class KimiClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.moonshot.cn/v1",
        model: str = "kimi-k3.1-pro",
        reasoning_effort: str = "low",
        timeout: float = 90.0,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Kimi API Key 不能为空")
        if reasoning_effort not in ("low", "high", "max"):
            raise ValueError("KIMI_REASONING_EFFORT 必须是 low、high 或 max")
        if not base_url.startswith(("https://", "http://")):
            raise ValueError("KIMI_BASE_URL 必须是 HTTP(S) 地址")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "KimiClient | None":
        api_key = os.environ.get("KIMI_API_KEY") or os.environ.get("MOONSHOT_API_KEY")
        if not api_key:
            return None
        is_kimi_code = api_key.startswith("sk-kimi-")
        default_base_url = "https://api.kimi.com/coding/v1" if is_kimi_code else "https://api.moonshot.cn/v1"
        default_model = "k3.1-pro" if is_kimi_code else "kimi-k3.1-pro"
        return cls(
            api_key=api_key,
            base_url=os.environ.get("KIMI_BASE_URL", default_base_url),
            model=os.environ.get("KIMI_MODEL", default_model),
            reasoning_effort=os.environ.get("KIMI_REASONING_EFFORT", "low"),
            timeout=float(os.environ.get("KIMI_TIMEOUT", "90")),
        )

    def public_config(self) -> dict[str, Any]:
        return {"available": True, "model": self.model, "reasoning_effort": self.reasoning_effort}

    def complete(
        self,
        role: str,
        instructions: str,
        evidence: dict[str, Any],
        schema: dict[str, Any],
        max_completion_tokens: int = 900,
    ) -> KimiResult:
        body = {
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "max_completion_tokens": max_completion_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"你是金融研究团队的{role}。{instructions}\n"
                        "只能使用输入中的事实，不得编造数据、新闻、公告全文或未来业绩。"
                        "不得给出买卖指令、仓位建议或确定性收益承诺，不得复述内部推理过程。"
                        "区分已披露事实、规则模型结果和统计预测；数据不足必须明确说明。"
                        "公告只能依据所给标题评论，并提醒阅读全文。所有输出使用简体中文。"
                    ),
                },
                {
                    "role": "user",
                    "content": "以下是本次任务唯一可使用的事实快照：\n"
                    + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "financial_research", "strict": True, "schema": schema},
            },
        }
        payload = self._post(body)
        choices = payload.get("choices") or []
        if not choices:
            raise ModelError("Kimi 返回空结果")
        choice = choices[0]
        if choice.get("finish_reason") == "length":
            raise ModelError("Kimi 输出达到长度上限，未采用不完整结果")
        message = choice.get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ModelError("Kimi 未返回结构化研究内容")
        try:
            structured = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ModelError("Kimi 返回的研究内容不是有效 JSON") from exc
        if not isinstance(structured, dict):
            raise ModelError("Kimi 研究结果必须是 JSON 对象")
        return KimiResult(
            content=structured,
            usage=_usage(payload.get("usage")),
            model=str(payload.get("model") or self.model),
            request_id=str(payload.get("id") or ""),
        )

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        request_body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        for attempt in range(2):
            request = Request(
                f"{self.base_url}/chat/completions",
                data=request_body,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "financial-analysis-market/0.1",
                    "X-Msh-Request-Nonce": str(uuid.uuid4()),
                },
            )
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.load(response)
                if not isinstance(payload, dict):
                    raise ModelError("Kimi 响应格式异常")
                return payload
            except HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt == 0:
                    time.sleep(1.0)
                    continue
                raise ModelError(_http_error_message(exc)) from exc
            except (URLError, RemoteDisconnected, ConnectionError, TimeoutError, socket.timeout) as exc:
                if attempt == 0:
                    time.sleep(0.6)
                    continue
                raise ModelError("Kimi 服务连接失败，请检查网络后重试") from exc
            except json.JSONDecodeError as exc:
                raise ModelError("Kimi 响应不是有效 JSON") from exc
        raise ModelError("Kimi 请求未完成")


def _http_error_message(error: HTTPError) -> str:
    if error.code == 401:
        return "Kimi API Key 无效或已过期，请更新 KIMI_API_KEY"
    if error.code == 403:
        return "Kimi 账户没有该模型的访问权限，请检查账户权限和余额"
    if error.code == 429:
        return "Kimi 请求触发速率限制，请稍后重试"
    try:
        payload = json.loads(error.read())
        message = str((payload.get("error") or {}).get("message") or "")[:240]
    except (ValueError, AttributeError):
        message = ""
    return f"Kimi 请求失败（HTTP {error.code}）{('：' + message) if message else ''}"


def _usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return {
        key: int(value.get(key) or 0)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }
