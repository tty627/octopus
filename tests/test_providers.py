from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from octopus.models import ExtractedDocument, RepositoryConfig, SearchResult
from octopus.providers import (
    DeepSeekProvider,
    HeuristicProvider,
    ProviderAuthError,
    ProviderBudgetError,
    ProviderError,
    ProviderOutputError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderTransientError,
    classify_provider_error,
    create_provider,
)


class HTTPFailure(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


class RequestTimeout(Exception):
    pass


class FakeCompletions:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls = 0
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls += 1
        self.requests.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=response))],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
        )


def _provider(config: RepositoryConfig, responses: list[Any]) -> tuple[DeepSeekProvider, Any]:
    config.ai_policy.enabled = True
    config.ai_policy.max_calls_per_run = 20
    completions = FakeCompletions(responses)
    provider = DeepSeekProvider(config, "test-key", sleeper=lambda _: None)
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return provider, completions


def _summary() -> str:
    return json.dumps(
        {
            "one_sentence_summary": "summary",
            "description": "description",
            "tag_rough": ["tag"],
            "topic_keywords": ["keyword"],
            "recommended_reading": ["first"],
        }
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (HTTPFailure(401), ProviderAuthError),
        (HTTPFailure(402), ProviderQuotaError),
        (HTTPFailure(429), ProviderRateLimitError),
        (HTTPFailure(500), ProviderTransientError),
        (RequestTimeout(), ProviderTransientError),
        (ValueError(), ProviderError),
    ],
)
def test_provider_error_classification(error: Exception, expected: type[Exception]) -> None:
    assert isinstance(classify_provider_error(error), expected)


def test_deepseek_structured_operations_and_usage(
    repository: tuple[Path, Path, RepositoryConfig],
) -> None:
    _, _, config = repository
    config.ai_policy.input_cost_per_million = 1.0
    config.ai_policy.output_cost_per_million = 2.0
    provider, completions = _provider(
        config,
        [
            _summary(),
            _summary(),
            json.dumps({"ordered_node_ids": ["node-2", "node-1"]}),
            json.dumps(
                {
                    "summary": "answer",
                    "recommended_node_ids": ["node-2"],
                    "warnings": ["stale"],
                }
            ),
        ],
    )
    document = ExtractedDocument(name="a.pdf", document_type="pdf", text="alpha")
    results = [
        SearchResult(
            node_id=node_id,
            index_type="leaf",
            index_path=f"{node_id}.md",
            name=node_id,
            summary="summary",
            description="description",
        )
        for node_id in ("node-1", "node-2")
    ]

    assert provider.generate_leaf(document).one_sentence_summary == "summary"
    assert provider.summarize_folder("folder", []).description == "description"
    assert provider.rerank_search("query", results)[0].node_id == "node-2"
    assert provider.compose_search("query", results).summary == "answer"
    assert completions.calls == 4
    assert provider.usage.calls == 4
    assert provider.usage.input_tokens == 44
    assert provider.usage.output_tokens == 28
    assert provider.usage.models[config.ai_policy.model] == 4
    assert provider.usage.prompt_versions[config.ai_policy.prompt_version] == 4
    assert provider.usage.purposes == {
        "leaf_summary": 1,
        "folder_summary": 1,
        "search_rerank": 1,
        "search_compose": 1,
    }
    assert provider.usage.estimated_cost is not None
    assert completions.requests[0]["max_tokens"] == 2_000


def test_json_repair_budget_is_per_request(
    repository: tuple[Path, Path, RepositoryConfig],
) -> None:
    _, _, config = repository
    config.ai_policy.json_repair_attempts = 1
    provider, _ = _provider(config, ["not-json", _summary(), "also-not-json", _summary()])
    document = ExtractedDocument(name="a.pdf", document_type="pdf")
    assert provider.generate_leaf(document).description == "description"
    assert provider.generate_leaf(document).description == "description"
    assert provider.usage.purposes["json_repair"] == 2


@pytest.mark.parametrize("transient", [HTTPFailure(429), HTTPFailure(500), RequestTimeout()])
def test_retry_errors(
    repository: tuple[Path, Path, RepositoryConfig], transient: Exception
) -> None:
    _, _, config = repository
    config.ai_policy.max_transport_retries = 1
    retrying, retry_calls = _provider(config, [transient, _summary()])
    assert retrying.generate_leaf(ExtractedDocument(name="a", document_type="pdf"))
    assert retry_calls.calls == 2


@pytest.mark.parametrize("fatal_error", [HTTPFailure(401), HTTPFailure(402)])
def test_fatal_errors_stop_future_calls(
    repository: tuple[Path, Path, RepositoryConfig], fatal_error: Exception
) -> None:
    _, _, config = repository
    fatal, fatal_calls = _provider(config, [fatal_error])
    with pytest.raises((ProviderAuthError, ProviderQuotaError)):
        fatal.generate_leaf(ExtractedDocument(name="a", document_type="pdf"))
    with pytest.raises((ProviderAuthError, ProviderQuotaError)):
        fatal.generate_leaf(ExtractedDocument(name="a", document_type="pdf"))
    assert fatal_calls.calls == 1


def test_budget_and_output_validation(
    repository: tuple[Path, Path, RepositoryConfig],
) -> None:
    _, _, config = repository
    invalid, _ = _provider(config, [json.dumps({"description": "missing fields"})])
    with pytest.raises(ProviderOutputError):
        invalid.generate_leaf(ExtractedDocument(name="a", document_type="pdf"))

    exhausted, _ = _provider(config, [])
    exhausted.remaining_calls = 0
    with pytest.raises(ProviderQuotaError, match="budget exhausted"):
        exhausted.generate_leaf(ExtractedDocument(name="a", document_type="pdf"))

    malformed, _ = _provider(config, ["invalid", "still invalid"])
    with pytest.raises(ProviderOutputError, match="invalid JSON"):
        malformed.generate_leaf(ExtractedDocument(name="a", document_type="pdf"))


def test_heuristic_and_provider_factory(
    repository: tuple[Path, Path, RepositoryConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, config = repository
    heuristic = HeuristicProvider()
    document = ExtractedDocument(name="notes.txt", document_type="text", text="Python 项目需求")
    assert heuristic.generate_leaf(document).topic_keywords
    assert heuristic.summarize_folder("folder", [{"name": "child"}]).recommended_reading
    assert heuristic.compose_search("none", []).summary
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert isinstance(create_provider(config), HeuristicProvider)
    with pytest.raises(RuntimeError, match="AI API key"):
        create_provider(config, require_network=True)


def test_prompt_version_and_token_cost_budgets(
    repository: tuple[Path, Path, RepositoryConfig],
) -> None:
    _, _, config = repository
    config.ai_policy.max_input_tokens_per_run = 1
    input_limited, input_calls = _provider(config, [_summary()])
    with pytest.raises(ProviderBudgetError, match="input token budget"):
        input_limited.generate_leaf(ExtractedDocument(name="a", document_type="pdf"))
    assert input_calls.calls == 0

    config.ai_policy.max_input_tokens_per_run = None
    config.ai_policy.max_output_tokens_per_run = 5
    output_limited, output_calls = _provider(config, [_summary()])
    output_limited.generate_leaf(ExtractedDocument(name="a", document_type="pdf"))
    assert output_calls.requests[0]["max_tokens"] == 5
    with pytest.raises(ProviderBudgetError, match="output token budget"):
        output_limited.generate_leaf(ExtractedDocument(name="b", document_type="pdf"))

    config.ai_policy.max_output_tokens_per_run = None
    config.ai_policy.input_cost_per_million = 1.0
    config.ai_policy.output_cost_per_million = 1.0
    config.ai_policy.max_estimated_cost_per_run = 0.000001
    cost_limited, cost_calls = _provider(config, [_summary()])
    with pytest.raises(ProviderBudgetError, match="cost cap"):
        cost_limited.generate_leaf(ExtractedDocument(name="a", document_type="pdf"))
    assert cost_calls.calls == 0

    config.ai_policy.max_estimated_cost_per_run = None
    config.ai_policy.prompt_version = "unknown-version"
    with pytest.raises(ValueError, match="Unsupported prompt version"):
        DeepSeekProvider(config, "test-key")
