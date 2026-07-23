from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from agent.config_models import Config, ModelRuntimeConfig
from agent.model_runtime.errors import (
    AuthenticationError,
    ContextWindowError,
    QuotaError,
    RateLimitError,
    RetryableTransportError,
    TransportError,
)
from agent.model_runtime.provider_profiles import (
    TERRA_RESPONSES_MODEL,
    TERRA_RESPONSES_PROVIDER,
)
from agent.model_runtime.transports.responses import (
    OpenAICompatibleResponsesTransport,
)
from agent.model_runtime.types import ModelRequest
from agent.provider import LLMProvider
from agent.tool_runtime import append_assistant_tool_calls, append_tool_result
from bootstrap.proactive import _build_proactive_provider


def _runtime(*, base_url: str = "http://127.0.0.1:8317/v1") -> ModelRuntimeConfig:
    return ModelRuntimeConfig(
        runtime_id="terra_main",
        provider=TERRA_RESPONSES_PROVIDER,
        model=TERRA_RESPONSES_MODEL,
        api_key="unit-credential",
        base_url=base_url,
        reasoning_effort="high",
        context_window=272_000,
        max_output_tokens=8192,
        input_modalities=("text", "image"),
        reasoning_summary="auto",
    )


def _request(**values: object) -> ModelRequest:
    fields: dict[str, object] = {
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [],
        "model": TERRA_RESPONSES_MODEL,
        "max_output_tokens": 512,
        "system_prompt": "system contract",
        "reasoning_effort": "high",
    }
    fields.update(values)
    return ModelRequest(**fields)  # type: ignore[arg-type]


class _FakeClient:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.responses = SimpleNamespace(create=self._create)
        self.closed = False

    async def _create(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        self.closed = True


def _reasoning_and_call() -> SimpleNamespace:
    return SimpleNamespace(
        status="completed",
        model=TERRA_RESPONSES_MODEL,
        output=[
            {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "check"}],
                "content": [],
                "encrypted_content": "opaque",
            },
            {
                "type": "function_call",
                "call_id": "call-1",
                "name": "search",
                "arguments": '{"query":"terra"}',
            },
        ],
        usage={
            "input_tokens": 41,
            "input_tokens_details": {"cached_tokens": 17},
            "output_tokens": 9,
            "output_tokens_details": {"reasoning_tokens": 4},
        },
    )


def _message_response(text: str = "READY") -> SimpleNamespace:
    return SimpleNamespace(
        status="completed",
        model=TERRA_RESPONSES_MODEL,
        output=[
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
        usage=None,
    )


def test_terra_profile_rejects_wrong_model_reasoning_and_modality() -> None:
    values = {
        "runtime_id": "terra_main",
        "provider": TERRA_RESPONSES_PROVIDER,
        "model": TERRA_RESPONSES_MODEL,
        "api_key": "unit-credential",
        "base_url": "http://127.0.0.1:8317/v1",
        "reasoning_effort": "high",
        "context_window": 272_000,
    }
    with pytest.raises(ValueError, match="仅支持 model"):
        ModelRuntimeConfig(**{**values, "model": "gpt-other"})
    with pytest.raises(ValueError, match="要求 reasoning_effort"):
        ModelRuntimeConfig(**{**values, "reasoning_effort": "medium"})
    with pytest.raises(ValueError, match="仅支持 input_modalities"):
        ModelRuntimeConfig(**{**values, "input_modalities": ("text", "audio")})


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "https://user:password@proxy.example/v1",
        "http://proxy.example/v1",
        "https://proxy.example/v1/responses",
        "https://proxy.example/v1/chat/completions",
    ],
)
def test_terra_transport_rejects_unsafe_or_method_base_url(base_url: str) -> None:
    with pytest.raises(TransportError):
        OpenAICompatibleResponsesTransport(
            "unit-credential",
            runtime_id="terra_main",
            base_url=base_url,
        )


def test_terra_transport_rejects_unresolved_credential() -> None:
    with pytest.raises(AuthenticationError, match="credential is unavailable"):
        OpenAICompatibleResponsesTransport(
            "${TERRA_API_KEY}",
            runtime_id="terra_main",
            base_url="http://127.0.0.1:8317/v1",
        )


@pytest.mark.asyncio
async def test_terra_payload_tools_usage_and_no_chat_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient([_reasoning_and_call()])
    monkeypatch.setattr(
        "agent.model_runtime.transports.responses.AsyncOpenAI", lambda **_: fake
    )
    provider = LLMProvider.from_runtime(_runtime(), system_prompt="system contract")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Search",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                "strict": True,
            },
        }
    ]

    result = await provider.chat(
        [{"role": "user", "content": "find terra"}],
        tools,
        TERRA_RESPONSES_MODEL,
        512,
        tool_choice={"type": "function", "function": {"name": "search"}},
        cache_namespace="conversation-1",
    )

    payload = fake.calls[0]
    assert payload["model"] == TERRA_RESPONSES_MODEL
    assert payload["reasoning"] == {"effort": "high", "summary": "auto"}
    assert payload["stream"] is False
    assert payload["store"] is False
    assert payload["include"] == ["reasoning.encrypted_content"]
    assert payload["max_output_tokens"] == 512
    assert payload["tool_choice"] == {"type": "function", "name": "search"}
    assert payload["parallel_tool_calls"] is True
    assert payload["tools"][0]["name"] == "search"
    assert payload["instructions"] == "system contract"
    assert payload["prompt_cache_key"]
    assert result.tool_calls[0].arguments == {"query": "terra"}
    assert result.thinking == "check"
    assert result.cache_prompt_tokens == 41
    assert result.cache_hit_tokens == 17
    assert result.usage is not None
    assert result.usage.reasoning_output_tokens == 4
    assert result.provider_fields["model_state"]["items"] == [
        {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "check"}],
            "content": [],
            "encrypted_content": "opaque",
        }
    ]
    assert fake.closed is True
    assert not hasattr(fake, "chat")


@pytest.mark.asyncio
async def test_terra_keeps_required_reasoning_when_caller_disables_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient([_message_response()])
    monkeypatch.setattr(
        "agent.model_runtime.transports.responses.AsyncOpenAI", lambda **_: fake
    )
    provider = LLMProvider.from_runtime(
        _runtime(),
        system_prompt="",
        force_disable_thinking=True,
    )

    await provider.chat(
        [],
        [],
        TERRA_RESPONSES_MODEL,
        64,
        disable_thinking=True,
    )

    assert fake.calls[0]["reasoning"] == {"effort": "high", "summary": "auto"}


def test_proactive_reuses_the_strict_terra_provider() -> None:
    provider = LLMProvider.from_runtime(_runtime(), system_prompt="")
    config = Config(
        provider=TERRA_RESPONSES_PROVIDER,
        model=TERRA_RESPONSES_MODEL,
        api_key="unit-credential",
        system_prompt="",
    )

    assert _build_proactive_provider(config, provider) is provider


@pytest.mark.asyncio
async def test_terra_replays_reasoning_call_and_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient(
        [
            _reasoning_and_call(),
            SimpleNamespace(
                status="completed",
                model=TERRA_RESPONSES_MODEL,
                output=[
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": "TERRA_TOOL_READY"}
                        ],
                    }
                ],
                usage=None,
            ),
        ]
    )
    monkeypatch.setattr(
        "agent.model_runtime.transports.responses.AsyncOpenAI", lambda **_: fake
    )
    provider = LLMProvider.from_runtime(_runtime(), system_prompt="system contract")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Search",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    messages: list[dict] = [{"role": "user", "content": "use search"}]

    first = await provider.chat(messages, tools, TERRA_RESPONSES_MODEL, 256)
    append_assistant_tool_calls(
        messages,
        content=first.content,
        tool_calls=first.tool_calls,
        provider_fields=first.provider_fields,
    )
    append_tool_result(messages, tool_call_id="call-1", content="ready")
    second = await provider.chat(messages, tools, TERRA_RESPONSES_MODEL, 256)

    replay = fake.calls[1]["input"]
    assert [item.get("type", "message") for item in replay] == [
        "message",
        "reasoning",
        "function_call",
        "function_call_output",
    ]
    assert replay[1]["encrypted_content"] == "opaque"
    assert replay[2]["call_id"] == "call-1"
    assert replay[3] == {
        "type": "function_call_output",
        "call_id": "call-1",
        "output": "ready",
    }
    assert second.content == "TERRA_TOOL_READY"


@pytest.mark.asyncio
async def test_terra_calls_exact_responses_http_path() -> None:
    captured: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            captured.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": json.loads(self.rfile.read(length)),
                }
            )
            body = json.dumps(
                {
                    "id": "resp_local",
                    "object": "response",
                    "created_at": 1,
                    "status": "completed",
                    "model": TERRA_RESPONSES_MODEL,
                    "output": [
                        {
                            "id": "msg_local",
                            "type": "message",
                            "status": "completed",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "annotations": [],
                                    "logprobs": [],
                                    "text": "HTTP_READY",
                                }
                            ],
                        }
                    ],
                    "usage": {
                        "input_tokens": 3,
                        "input_tokens_details": {"cached_tokens": 0},
                        "output_tokens": 2,
                        "output_tokens_details": {"reasoning_tokens": 0},
                        "total_tokens": 5,
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        provider = LLMProvider.from_runtime(
            _runtime(base_url=f"http://{host}:{port}/v1"),
            system_prompt="system contract",
        )
        result = await provider.chat(
            [{"role": "user", "content": "ping"}],
            [],
            TERRA_RESPONSES_MODEL,
            64,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert result.content == "HTTP_READY"
    assert captured[0]["path"] == "/v1/responses"
    assert captured[0]["authorization"] == "Bearer unit-credential"
    assert captured[0]["body"]["model"] == TERRA_RESPONSES_MODEL
    assert captured[0]["body"]["stream"] is False


@pytest.mark.asyncio
async def test_terra_timeout_is_typed_and_never_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeout = openai.APITimeoutError(
        request=httpx.Request("POST", "http://127.0.0.1:8317/v1/responses")
    )
    fake = _FakeClient([timeout])
    monkeypatch.setattr(
        "agent.model_runtime.transports.responses.AsyncOpenAI", lambda **_: fake
    )
    provider = LLMProvider.from_runtime(_runtime(), system_prompt="")

    with pytest.raises(RetryableTransportError, match="connection failed"):
        await provider.chat([], [], TERRA_RESPONSES_MODEL, 64)

    assert len(fake.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "message", "expected"),
    [
        (401, "unauthorized", AuthenticationError),
        (429, "rate limit", RateLimitError),
        (429, "insufficient_quota", QuotaError),
        (400, "context_length_exceeded", ContextWindowError),
        (408, "stream disconnected before completion", RetryableTransportError),
        (500, "server error", RetryableTransportError),
        (404, "not found", TransportError),
    ],
)
async def test_terra_http_status_errors_are_classified(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    message: str,
    expected: type[Exception],
) -> None:
    request = httpx.Request("POST", "http://127.0.0.1:8317/v1/responses")
    response = httpx.Response(status, request=request, json={"error": message})
    error = openai.APIStatusError(message, response=response, body=response.json())
    fake = _FakeClient([error])
    monkeypatch.setattr(
        "agent.model_runtime.transports.responses.AsyncOpenAI", lambda **_: fake
    )
    transport = OpenAICompatibleResponsesTransport(
        "unit-credential",
        runtime_id="terra_main",
        base_url="http://127.0.0.1:8317/v1",
    )

    with pytest.raises(expected):
        await transport.send(_request())

    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    ("status", "error", "expected"),
    [
        ("failed", {"code": "context_length_exceeded"}, ContextWindowError),
        ("failed", {"code": "rate_limit_exceeded"}, RateLimitError),
        ("failed", {"code": "insufficient_quota"}, QuotaError),
        ("incomplete", {"reason": "max_output_tokens"}, TransportError),
    ],
)
def test_terra_terminal_errors_are_classified(
    status: str,
    error: dict[str, str],
    expected: type[Exception],
) -> None:
    transport = OpenAICompatibleResponsesTransport(
        "unit-credential",
        runtime_id="terra_main",
        base_url="http://127.0.0.1:8317/v1",
    )
    response = {
        "status": status,
        "model": TERRA_RESPONSES_MODEL,
        "output": [],
        "error": error if status == "failed" else None,
        "incomplete_details": error if status == "incomplete" else None,
    }
    with pytest.raises(expected):
        transport._consume_response(response, _request())


def test_terra_request_rejects_wrong_model_and_extra_body() -> None:
    transport = OpenAICompatibleResponsesTransport(
        "unit-credential",
        runtime_id="terra_main",
        base_url="http://127.0.0.1:8317/v1",
    )
    with pytest.raises(TransportError, match="requires model"):
        transport._build_payload(_request(model="gpt-other"))
    with pytest.raises(TransportError, match="extra_body"):
        transport._build_payload(_request(extra_body={"temperature": 0.0}))
    with pytest.raises(ValueError, match="incompatible runtime options"):
        LLMProvider(
            api_key="unit-credential",
            base_url="http://127.0.0.1:8317/v1",
            provider_name=TERRA_RESPONSES_PROVIDER,
            extra_body={"reasoning_effort": "high", "temperature": 0.0},
        )
