from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from collections.abc import Callable
from typing import Any, NoReturn, TypeVar

from pydantic import BaseModel, ValidationError

from .models import (
    AIProvider,
    AIUsage,
    ExtractedDocument,
    GeneratedSearchAnswer,
    GeneratedSummary,
    RepositoryConfig,
    SearchResult,
)
from .prompts import (
    FOLDER_SUMMARY_PROMPT,
    JSON_REPAIR_PROMPT,
    LEAF_SUMMARY_PROMPT,
    PROMPT_VERSION,
    SEARCH_COMPOSE_PROMPT,
    SEARCH_RERANK_PROMPT,
)


class ProviderError(RuntimeError):
    """Base class for sanitized provider failures."""


class ProviderAuthError(ProviderError):
    pass


class ProviderQuotaError(ProviderError):
    pass


class ProviderRateLimitError(ProviderError):
    pass


class ProviderTransientError(ProviderError):
    pass


class ProviderOutputError(ProviderError):
    pass


class ProviderBudgetError(ProviderQuotaError):
    pass


OutputModel = TypeVar("OutputModel", bound=BaseModel)


def classify_provider_error(error: Exception) -> ProviderError:
    status_code = getattr(error, "status_code", None)
    if status_code == 401:
        return ProviderAuthError("DeepSeek authentication failed (HTTP 401)")
    if status_code == 402:
        return ProviderQuotaError("DeepSeek account balance is insufficient (HTTP 402)")
    if status_code == 429:
        return ProviderRateLimitError("DeepSeek rate limit reached (HTTP 429)")
    if isinstance(status_code, int) and status_code >= 500:
        return ProviderTransientError(f"DeepSeek service failed (HTTP {status_code})")
    name = type(error).__name__
    if any(token in name for token in ("Timeout", "Connection", "InternalServer")):
        return ProviderTransientError(f"DeepSeek transport failed ({name})")
    return ProviderError(f"DeepSeek request failed ({name})")


def _keywords(text: str, limit: int = 8) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,6}", text.casefold())
    ignored = {"this", "that", "with", "from", "文件", "内容", "以及", "可以", "一个"}
    return [
        token
        for token, _ in Counter(token for token in tokens if token not in ignored).most_common(
            limit
        )
    ]


class HeuristicProvider:
    """Deterministic fallback used when no API key is configured."""

    def __init__(self) -> None:
        self.usage = AIUsage()

    def generate_leaf(self, document: ExtractedDocument) -> GeneratedSummary:
        lines = [line.strip() for line in document.text.splitlines() if line.strip()]
        first = next((line for line in lines if not line.startswith("[")), "")
        sentence = (
            first[:180] if first else f"{document.name} 的 {document.document_type} 文件索引。"
        )
        description = " ".join(lines[:5])[:600] or (
            f"该文件为 {document.document_type} 类型；当前索引主要包含元数据和结构信号。"
        )
        keywords = _keywords(document.name + " " + document.text[:20_000])
        return GeneratedSummary(
            one_sentence_summary=sentence,
            description=description,
            tag_rough=[document.document_type],
            topic_keywords=keywords,
            recommended_reading=document.structure[:5],
        )

    def summarize_folder(
        self, name: str, children: list[dict[str, Any]], previous: str = ""
    ) -> GeneratedSummary:
        summaries = [str(item.get("one_sentence_summary", "")) for item in children if item]
        sentence = f"{name or 'Raw Repository'} 包含 {len(children)} 个直接下级索引节点。"
        description = " ".join(item for item in summaries[:6] if item)[:800] or sentence
        keywords = _keywords(" ".join([name, description]))
        notable = [str(item.get("name", "")) for item in children[:5] if item.get("name")]
        return GeneratedSummary(
            one_sentence_summary=sentence,
            description=description,
            tag_rough=["文件夹索引"],
            topic_keywords=keywords,
            recommended_reading=notable,
        )

    def rerank_search(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
        return results

    def compose_search(self, query: str, results: list[SearchResult]) -> GeneratedSearchAnswer:
        if not results:
            return GeneratedSearchAnswer(summary="未找到匹配的索引。")
        names = "、".join(result.name for result in results[:5])
        return GeneratedSearchAnswer(
            summary=f"与“{query}”最相关的索引包括：{names}。",
            recommended_node_ids=[result.node_id for result in results],
            cited_node_ids=[result.node_id for result in results[:5]],
            warnings=["当前结果使用本地确定性摘要，未调用联网模型。"],
        )


class DeepSeekProvider(HeuristicProvider):
    def __init__(
        self,
        config: RepositoryConfig,
        api_key: str,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__()
        try:
            from openai import OpenAI
        except ImportError as error:
            raise RuntimeError("The openai package is required for DeepSeek") from error
        policy = config.ai_policy
        if policy.prompt_version != PROMPT_VERSION:
            raise ValueError(
                f"Unsupported prompt version {policy.prompt_version!r}; "
                f"available version is {PROMPT_VERSION!r}"
            )
        self.client = OpenAI(
            api_key=api_key,
            base_url=policy.base_url,
            max_retries=0,
            timeout=180.0,
        )
        self.policy = policy
        self.model = policy.model
        self.remaining_calls = policy.max_calls_per_run
        self.sleeper = sleeper
        self.fatal_error: ProviderError | None = None

    def _stop_for_budget(self, message: str) -> NoReturn:
        error = ProviderBudgetError(message)
        self.usage.errors["ProviderBudgetError"] = (
            self.usage.errors.get("ProviderBudgetError", 0) + 1
        )
        self.fatal_error = error
        raise error

    def _preflight_budget(self, system: str, payload_text: str) -> int:
        if self.remaining_calls <= 0:
            self._stop_for_budget("AI call budget exhausted for this run")
        estimated_input_tokens = max(1, (len(system) + len(payload_text) + 3) // 4)
        input_limit = self.policy.max_input_tokens_per_run
        if (
            input_limit is not None
            and self.usage.input_tokens + estimated_input_tokens > input_limit
        ):
            self._stop_for_budget("AI input token budget would be exceeded")
        output_limit = self.policy.max_output_tokens_per_request
        if self.policy.max_output_tokens_per_run is not None:
            remaining_output = self.policy.max_output_tokens_per_run - self.usage.output_tokens
            if remaining_output <= 0:
                self._stop_for_budget("AI output token budget exhausted for this run")
            output_limit = min(output_limit, remaining_output)
        cost_limit = self.policy.max_estimated_cost_per_run
        if cost_limit is not None:
            input_price = self.policy.input_cost_per_million
            output_price = self.policy.output_cost_per_million
            if input_price is None or output_price is None:
                self._stop_for_budget("AI cost cap requires configured input and output prices")
            estimated_request_cost = (
                estimated_input_tokens * input_price + output_limit * output_price
            ) / 1_000_000
            if (self.usage.estimated_cost or 0.0) + estimated_request_cost > cost_limit:
                self._stop_for_budget("AI estimated cost cap would be exceeded")
        return max(1, output_limit)

    def _record_cost(self, input_tokens: int, output_tokens: int) -> None:
        if (
            self.policy.input_cost_per_million is None
            or self.policy.output_cost_per_million is None
        ):
            return
        cost = (
            input_tokens * self.policy.input_cost_per_million
            + output_tokens * self.policy.output_cost_per_million
        ) / 1_000_000
        self.usage.estimated_cost = (self.usage.estimated_cost or 0.0) + cost

    def _request(self, purpose: str, system: str, payload: dict[str, Any]) -> str:
        if self.fatal_error is not None:
            raise self.fatal_error
        attempt = 0
        while True:
            payload_text = json.dumps(payload, ensure_ascii=False)
            output_limit = self._preflight_budget(system, payload_text)
            self.remaining_calls -= 1
            self.usage.calls += 1
            self.usage.models[self.model] = self.usage.models.get(self.model, 0) + 1
            prompt_version = self.policy.prompt_version
            self.usage.prompt_versions[prompt_version] = (
                self.usage.prompt_versions.get(prompt_version, 0) + 1
            )
            self.usage.purposes[purpose] = self.usage.purposes.get(purpose, 0) + 1
            started = time.perf_counter()
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": payload_text},
                    ],
                    response_format={"type": "json_object"},
                    max_tokens=output_limit,
                    stream=False,
                )
                elapsed = int((time.perf_counter() - started) * 1000)
                self.usage.duration_ms += elapsed
                response_usage = response.usage
                input_tokens = int(response_usage.prompt_tokens if response_usage else 0)
                output_tokens = int(response_usage.completion_tokens if response_usage else 0)
                self.usage.input_tokens += input_tokens
                self.usage.output_tokens += output_tokens
                self.usage.total_tokens += input_tokens + output_tokens
                self._record_cost(input_tokens, output_tokens)
                return response.choices[0].message.content or "{}"
            except Exception as error:
                self.usage.duration_ms += int((time.perf_counter() - started) * 1000)
                mapped = classify_provider_error(error)
                code = type(mapped).__name__
                self.usage.errors[code] = self.usage.errors.get(code, 0) + 1
                if isinstance(mapped, (ProviderAuthError, ProviderQuotaError)):
                    self.fatal_error = mapped
                    raise mapped from error
                if (
                    isinstance(mapped, (ProviderRateLimitError, ProviderTransientError))
                    and attempt < self.policy.max_transport_retries
                ):
                    self.sleeper(min(2**attempt, 8))
                    attempt += 1
                    continue
                raise mapped from error

    def _json_call(
        self,
        purpose: str,
        system: str,
        payload: dict[str, Any],
        repair_remaining: int | None = None,
    ) -> dict[str, Any]:
        remaining = (
            self.policy.json_repair_attempts if repair_remaining is None else repair_remaining
        )
        content = self._request(purpose, system, payload)
        try:
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ProviderOutputError("AI response must be a JSON object")
            return parsed
        except (json.JSONDecodeError, ProviderOutputError) as error:
            if remaining <= 0:
                self.usage.errors["ProviderOutputError"] = (
                    self.usage.errors.get("ProviderOutputError", 0) + 1
                )
                raise ProviderOutputError("DeepSeek returned invalid JSON") from error
            return self._json_call(
                "json_repair",
                JSON_REPAIR_PROMPT,
                {"invalid_json": content},
                repair_remaining=remaining - 1,
            )

    def _validate_output(self, model: type[OutputModel], output: dict[str, Any]) -> OutputModel:
        try:
            return model.model_validate(output)
        except ValidationError as error:
            self.usage.errors["ProviderOutputError"] = (
                self.usage.errors.get("ProviderOutputError", 0) + 1
            )
            raise ProviderOutputError("DeepSeek returned invalid structured output") from error

    def generate_leaf(self, document: ExtractedDocument) -> GeneratedSummary:
        output = self._json_call(
            "leaf_summary",
            LEAF_SUMMARY_PROMPT,
            {
                "name": document.name,
                "document_type": document.document_type,
                "metadata": document.metadata,
                "structure": document.structure[:200],
                "content_excerpt": document.text[: self.policy.max_input_characters_per_request],
                "quality_flags": document.quality_flags,
                "evidence": [
                    item.model_dump(mode="json", exclude_none=True)
                    for item in document.evidence[:100]
                ],
            },
        )
        return self._validate_output(GeneratedSummary, output)

    def summarize_folder(
        self, name: str, children: list[dict[str, Any]], previous: str = ""
    ) -> GeneratedSummary:
        output = self._json_call(
            "folder_summary",
            FOLDER_SUMMARY_PROMPT,
            {
                "folder": name,
                "children": children[: self.policy.max_folder_children_per_request],
                "previous_summary": previous,
            },
        )
        return self._validate_output(GeneratedSummary, output)

    def rerank_search(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
        output = self._json_call(
            "search_rerank",
            SEARCH_RERANK_PROMPT,
            {
                "query": query,
                "candidates": [
                    {
                        "node_id": item.node_id,
                        "type": item.index_type,
                        "name": item.name,
                        "summary": item.summary,
                        "description": item.description,
                    }
                    for item in results[: self.policy.max_search_candidates]
                ],
            },
        )
        order = output.get("ordered_node_ids", [])
        positions = {str(node_id): index for index, node_id in enumerate(order)}
        return sorted(results, key=lambda item: positions.get(item.node_id, len(positions)))

    def compose_search(self, query: str, results: list[SearchResult]) -> GeneratedSearchAnswer:
        output = self._json_call(
            "search_compose",
            SEARCH_COMPOSE_PROMPT,
            {
                "query": query,
                "candidates": [
                    {
                        "citation_id": f"S{index}",
                        "node_id": result.node_id,
                        "name": result.name,
                        "summary": result.summary,
                        "description": result.description,
                        "status": result.status,
                    }
                    for index, result in enumerate(
                        results[: self.policy.max_search_candidates], start=1
                    )
                ],
            },
        )
        return self._validate_output(GeneratedSearchAnswer, output)


def create_provider(config: RepositoryConfig, require_network: bool = False) -> AIProvider:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if config.ai_policy.enabled and key:
        return DeepSeekProvider(config, key)
    if require_network:
        raise RuntimeError("DEEPSEEK_API_KEY is required for this operation")
    return HeuristicProvider()
