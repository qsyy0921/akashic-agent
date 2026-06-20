import asyncio

from eval.longmemeval.metrics import judge_answer, score_results


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeProvider:
    def __init__(self, contents):
        self._contents = list(contents)
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(self._contents.pop(0))


def test_judge_disables_thinking_and_uses_configured_token_budget():
    provider = _FakeProvider(["yes"])

    result = asyncio.run(
        judge_answer(
            provider,
            "judge-model",
            question="q",
            gold="a",
            predicted="a",
            max_tokens=512,
        )
    )

    assert result is True
    assert provider.calls[0]["max_tokens"] == 512
    assert provider.calls[0]["tool_choice"] == "none"
    assert provider.calls[0]["disable_thinking"] is True


def test_judge_retries_empty_content_before_returning_result():
    provider = _FakeProvider(["", "no"])

    result = asyncio.run(
        judge_answer(
            provider,
            "judge-model",
            question="q",
            gold="a",
            predicted="b",
            max_tokens=16,
        )
    )

    assert result is False
    assert len(provider.calls) == 2
    assert provider.calls[1]["max_tokens"] >= 1024


def test_score_results_ignores_unjudged_items_for_judge_acc():
    scores = score_results(
        [
            {
                "question_type": "x",
                "predicted_answer": "a",
                "gold_answer": "a",
                "judge_correct": True,
            },
            {
                "question_type": "x",
                "predicted_answer": "b",
                "gold_answer": "c",
                "judge_correct": None,
            },
        ]
    )

    assert scores["overall"]["judge_acc"] == 1.0
