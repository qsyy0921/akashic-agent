from __future__ import annotations

from dataclasses import dataclass

OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
TERRA_RESPONSES_PROVIDER = "terra-responses"
TERRA_RESPONSES_MODEL = "gpt-5.6-terra"
TERRA_RESPONSES_REASONING_EFFORT = "high"


@dataclass(frozen=True)
class ProviderProfile:
    provider_id: str
    default_base_url: str
    messages_model_prefixes: tuple[str, ...]
    input_modalities: tuple[str, ...] = ("text",)
    wire_protocol: str = "chat_completions"
    fixed_models: tuple[str, ...] = ()
    required_reasoning_effort: str = ""

    def classify_model(self, model: str) -> str:
        """排除已知 Messages 家族，其余模型默认走 Chat Completions。"""
        normalized = model.strip().lower()
        if not normalized:
            return "unknown"
        if normalized.startswith(self.messages_model_prefixes):
            return "messages"
        return "chat_completions"


OPENCODE_GO_PROFILE = ProviderProfile(
    provider_id="opencode-go",
    default_base_url=OPENCODE_GO_BASE_URL,
    messages_model_prefixes=("minimax-", "qwen"),
)

TERRA_RESPONSES_PROFILE = ProviderProfile(
    provider_id=TERRA_RESPONSES_PROVIDER,
    default_base_url="",
    messages_model_prefixes=(),
    input_modalities=("text", "image"),
    wire_protocol="responses",
    fixed_models=(TERRA_RESPONSES_MODEL,),
    required_reasoning_effort=TERRA_RESPONSES_REASONING_EFFORT,
)

_PROFILES = {
    OPENCODE_GO_PROFILE.provider_id: OPENCODE_GO_PROFILE,
    TERRA_RESPONSES_PROFILE.provider_id: TERRA_RESPONSES_PROFILE,
}


def get_provider_profile(provider: str) -> ProviderProfile | None:
    return _PROFILES.get(provider.strip().lower())


def validate_profile_runtime(
    *,
    provider: str,
    model: str,
    input_modalities: tuple[str, ...],
    reasoning_effort: str = "",
) -> None:
    """在配置边界拒绝 profile 不支持的协议和输入模态。"""
    profile = get_provider_profile(provider)
    if profile is None:
        return

    if profile.fixed_models and model not in profile.fixed_models:
        supported = ", ".join(profile.fixed_models)
        raise ValueError(f"provider {profile.provider_id} 仅支持 model = {supported}")
    if (
        profile.required_reasoning_effort
        and reasoning_effort != profile.required_reasoning_effort
    ):
        raise ValueError(
            f"provider {profile.provider_id} 要求 reasoning_effort = "
            f"{profile.required_reasoning_effort}"
        )

    # 1. 已知 Messages 家族不得误发到 Chat；新家族由真实请求继续验证。
    if profile.wire_protocol == "chat_completions":
        protocol = profile.classify_model(model)
        if protocol == "messages":
            raise ValueError(
                f"provider {profile.provider_id} 的模型 {model} 使用 Messages API，"
                "当前仅支持 Chat Completions 模型"
            )
        if protocol == "unknown":
            raise ValueError(f"provider {profile.provider_id} 的模型 ID 不能为空")

    # 2. runtime 声明的输入模态必须是 profile 能力的子集。
    if not set(input_modalities).issubset(profile.input_modalities):
        raise ValueError(
            f"provider {profile.provider_id} 仅支持 input_modalities = "
            f"{list(profile.input_modalities)}"
        )


def uses_responses_transport(provider: str) -> bool:
    profile = get_provider_profile(provider)
    return profile is not None and profile.wire_protocol == "responses"
