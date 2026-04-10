import json

import httpx

from sector4_ai_summary import (
    OpenAISignalSummaryGenerator,
    get_signal_summary_generator,
    summarize_fact_payload,
)
from sector4_core.config import Settings


def test_get_signal_summary_generator_returns_disabled_without_api_key() -> None:
    settings = Settings(openai_api_key=None)

    generator = get_signal_summary_generator(settings)
    request = summarize_fact_payload(1, {"ticker": "ACME", "unique_buyers": 2})
    result = generator.generate(request)

    assert result.status == "disabled"
    assert result.summary_text is None
    assert result.warnings == ["AI summary unavailable because OPENAI_API_KEY is not configured."]


def test_openai_signal_summary_generator_parses_output_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://api.openai.com/v1/responses")
        body = json.loads(request.content.decode("utf-8"))
        assert body["model"] == "gpt-5.4-mini"
        assert body["input"][1]["role"] == "user"
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "summary_text": (
                                            "Public filings show coordinated insider buying."
                                        ),
                                        "highlights": ["2 insiders bought shares"],
                                        "warnings": ["Not investment advice."],
                                    }
                                ),
                            }
                        ],
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    settings = Settings(openai_api_key="test-key", ai_summary_model="gpt-5.4-mini")
    generator = OpenAISignalSummaryGenerator(settings, client=client)

    request = summarize_fact_payload(
        7,
        {
            "ticker": "ACME",
            "issuer_name": "Acme Robotics, Inc.",
            "unique_buyers": 2,
            "window_start": "2024-02-14",
            "window_end": "2024-02-18",
        },
    )
    result = generator.generate(request)

    assert result.status == "generated"
    assert result.summary_text == "Public filings show coordinated insider buying."
    assert result.highlights == ["2 insiders bought shares"]
    assert result.warnings == ["Not investment advice."]
    assert result.provider == "openai"
    assert result.model == "gpt-5.4-mini"
