from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Protocol


DEFAULT_LLM_CONFIG_PATH = Path(".mka/llm_config.json")
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


class LLMError(ValueError):
    """Raised when LLM configuration or execution cannot proceed safely."""


class LLMPolicyError(LLMError):
    """Raised when a policy key prevents an external provider from starting."""


class LLMProvider(Protocol):
    name: str

    def generate(self, prompt: str) -> str:
        ...


Transport = Callable[[str, Dict[str, str], dict], dict]


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "mock"
    model: Optional[str] = None
    data_policy_confirmed: bool = False
    allow_internal_data_to_llm: bool = False


class MockProvider:
    name = "mock"

    def __init__(self, response: str = ""):
        self.response = response

    def generate(self, prompt: str) -> str:
        return self.response


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        config: LLMConfig,
        api_key: Optional[str] = None,
        transport: Optional[Transport] = None,
    ):
        validate_provider_policy(config, self.name)
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise LLMError("ANTHROPIC_API_KEY 未設定，外部 LLM 未啟用。")
        self.model = config.model or ""
        self._api_key = resolved_key
        self._transport = transport or _urllib_transport

    def generate(self, prompt: str) -> str:
        response = self._transport(
            ANTHROPIC_MESSAGES_URL,
            {
                "x-api-key": self._api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            {
                "model": self.model,
                "max_tokens": 1200,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        try:
            content = response["content"]
            text = next(item["text"] for item in content if item.get("type") == "text")
        except (KeyError, StopIteration, TypeError) as exc:
            raise LLMError("Anthropic 回應格式無法解析。") from exc
        if not isinstance(text, str) or not text.strip():
            raise LLMError("Anthropic 回應未包含文字內容。")
        return text.strip()


def load_llm_config(path: Path = DEFAULT_LLM_CONFIG_PATH) -> LLMConfig:
    path = Path(path)
    if not path.is_file():
        return LLMConfig()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LLMError(f"LLM 設定檔無法解析：{path}") from exc
    if not isinstance(payload, dict):
        raise LLMError("LLM 設定檔必須是 JSON object。")

    provider = str(payload.get("provider", "mock")).strip().lower()
    if provider not in {"mock", "anthropic"}:
        raise LLMError(f"不支援的 LLM provider：{provider}")
    model = payload.get("model")
    if model is not None and not isinstance(model, str):
        raise LLMError("LLM model 必須是字串或 null。")
    for key in ("data_policy_confirmed", "allow_internal_data_to_llm"):
        if key in payload and not isinstance(payload[key], bool):
            raise LLMError(f"{key} 必須是 boolean。")
    return LLMConfig(
        provider=provider,
        model=model.strip() if isinstance(model, str) and model.strip() else None,
        data_policy_confirmed=payload.get("data_policy_confirmed", False),
        allow_internal_data_to_llm=payload.get("allow_internal_data_to_llm", False),
    )


def validate_provider_policy(config: LLMConfig, provider_name: str) -> None:
    provider_name = provider_name.strip().lower()
    if provider_name == "mock":
        return
    if not config.data_policy_confirmed:
        raise LLMPolicyError("公司 AI 資料政策未確認，外部 LLM 停用。")
    if config.provider != provider_name:
        raise LLMPolicyError("LLM provider 與人工設定檔不一致，外部 LLM 停用。")
    if not config.model:
        raise LLMPolicyError("外部 LLM model 尚未由人工設定，外部 LLM 停用。")


def build_provider(provider_name: str, config: LLMConfig) -> LLMProvider:
    provider_name = provider_name.strip().lower()
    if provider_name == "mock":
        return MockProvider()
    if provider_name == "anthropic":
        return AnthropicProvider(config=config)
    raise LLMError(f"不支援的 LLM provider：{provider_name}")


def _urllib_transport(url: str, headers: Dict[str, str], body: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise LLMError("Anthropic API 呼叫失敗；未記錄憑證或 payload。") from exc
