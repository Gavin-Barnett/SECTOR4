from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Protocol

import httpx

from sector4_core.config import Settings

logger = logging.getLogger(__name__)
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1/responses"


@dataclass(slots=True)
class SignalSummaryRequest:
    signal_id: int
    facts: dict[str, Any]
    input_hash: str


@dataclass(slots=True)
class SignalSummaryResult:
    status: str
    summary_text: str | None
    highlights: list[str]
    warnings: list[str]
    provider: str
    model: str | None
    input_hash: str
    generated_at: datetime | None = None
    reused: bool = False
    error: str | None = None


class SignalSummaryGenerator(Protocol):
    def generate(self, request: SignalSummaryRequest) -> SignalSummaryResult: ...

    def close(self) -> None: ...


class DisabledSignalSummaryGenerator:
    def __init__(self, reason: str = "openai_api_key_missing") -> None:
        self.reason = reason

    def generate(self, request: SignalSummaryRequest) -> SignalSummaryResult:
        return SignalSummaryResult(
            status="disabled",
            summary_text=None,
            highlights=[],
            warnings=[_warning_for_reason(self.reason)],
            provider="disabled",
            model=None,
            input_hash=request.input_hash,
        )

    def close(self) -> None:
        return None


class StaticSignalSummaryGenerator:
    def __init__(
        self,
        factory: Callable[[SignalSummaryRequest], SignalSummaryResult] | None = None,
    ) -> None:
        self.factory = factory

    def generate(self, request: SignalSummaryRequest) -> SignalSummaryResult:
        if self.factory is not None:
            return self.factory(request)
        facts = request.facts
        ticker = facts.get("ticker") or facts.get("issuer_name") or "the issuer"
        return SignalSummaryResult(
            status="generated",
            summary_text=(
                f"{facts['unique_buyers']} insiders reported public open-market "
                f"purchases in {ticker} between {facts['window_start']} "
                f"and {facts['window_end']}."
            ),
            highlights=[
                f"Signal score: {facts['signal_score']}",
                f"Total buy value: {facts['total_purchase_usd']}",
            ],
            warnings=["Uses public SEC filings only.", "Not investment advice."],
            provider="static",
            model="static-facts",
            input_hash=request.input_hash,
            generated_at=datetime.now(UTC),
        )

    def close(self) -> None:
        return None


class OpenAISignalSummaryGenerator:
    def __init__(
        self,
        settings: Settings,
        client: httpx.Client | None = None,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
    ) -> None:
        self.settings = settings
        self.base_url = base_url
        self._client = client
        self._owns_client = client is None
        if self._client is None and settings.openai_api_key:
            self._client = httpx.Client(timeout=20.0)

    def generate(self, request: SignalSummaryRequest) -> SignalSummaryResult:
        if not self.settings.openai_api_key:
            return DisabledSignalSummaryGenerator().generate(request)
        if self._client is None:
            return SignalSummaryResult(
                status="failed",
                summary_text=None,
                highlights=[],
                warnings=["AI summary client was not initialized."],
                provider="openai",
                model=self.settings.ai_summary_model,
                input_hash=request.input_hash,
                error="client_not_initialized",
            )

        payload = {
            "model": self.settings.ai_summary_model,
            "reasoning": {"effort": "low"},
            "input": [
                {
                    "role": "developer",
                    "content": (
                        "You summarize insider-buying signals using only supplied JSON facts. "
                        "Do not invent numbers, dates, names, prices, catalysts, or conclusions. "
                        "Return JSON only with keys summary_text, highlights, warnings. "
                        "Keep summary_text to 2 or 3 sentences. "
                        "Keep highlights and warnings short. Always mention that the "
                        "signal is based on public SEC filings and is not investment advice."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Summarize this signal fact payload. Unknown or unavailable "
                        "fields must be described as unknown, unavailable, or missing "
                        "rather than inferred.\n" + json.dumps(request.facts, sort_keys=True)
                    ),
                },
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = self._client.post(self.base_url, json=payload, headers=headers)
            response.raise_for_status()
            body = response.json()
            output_text = _extract_output_text(body)
            parsed = json.loads(output_text)
            result = SignalSummaryResult(
                status="generated",
                summary_text=str(parsed.get("summary_text") or "").strip() or None,
                highlights=[
                    str(item).strip() for item in parsed.get("highlights", []) if str(item).strip()
                ],
                warnings=[
                    str(item).strip() for item in parsed.get("warnings", []) if str(item).strip()
                ],
                provider="openai",
                model=self.settings.ai_summary_model,
                input_hash=request.input_hash,
                generated_at=datetime.now(UTC),
            )
            logger.info(
                "signal summary generated",
                extra={"signal_id": request.signal_id, "provider": result.provider},
            )
            return result
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning(
                "signal summary generation failed",
                extra={"signal_id": request.signal_id, "error": str(exc)},
            )
            return SignalSummaryResult(
                status="failed",
                summary_text=None,
                highlights=[],
                warnings=["AI summary generation failed; review raw SEC evidence directly."],
                provider="openai",
                model=self.settings.ai_summary_model,
                input_hash=request.input_hash,
                error=str(exc),
            )

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()


def get_signal_summary_generator(settings: Settings) -> SignalSummaryGenerator:
    if not settings.openai_api_key:
        return DisabledSignalSummaryGenerator()
    return OpenAISignalSummaryGenerator(settings)


def summarize_fact_payload(signal_id: int, facts: dict[str, Any]) -> SignalSummaryRequest:
    serialized = json.dumps(facts, sort_keys=True, separators=(",", ":"), default=str)
    input_hash = sha256(serialized.encode("utf-8")).hexdigest()
    return SignalSummaryRequest(signal_id=signal_id, facts=facts, input_hash=input_hash)


def _extract_output_text(body: dict[str, Any]) -> str:
    output = body.get("output", [])
    for item in output:
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                text = str(content.get("text") or "").strip()
                if text:
                    return text
            if content.get("type") == "refusal":
                raise ValueError(str(content.get("refusal") or "model_refused_request"))
    raise KeyError("responses.output_text_missing")


def _warning_for_reason(reason: str) -> str:
    if reason == "openai_api_key_missing":
        return "AI summary unavailable because OPENAI_API_KEY is not configured."
    return "AI summary unavailable."
