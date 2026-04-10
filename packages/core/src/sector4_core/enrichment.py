from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from sector4_core.config import Settings


@dataclass(slots=True)
class IssuerEnrichmentRequest:
    cik: str
    ticker: str | None
    name: str
    market_cap: Decimal | None
    latest_price: Decimal | None
    market_cap_price_hint: Decimal | None = None
    event_anchor_date: date | None = None
    upcoming_earnings_date: date | None = None
    earnings_date_source: str | None = None


@dataclass(slots=True)
class PriceContextSnapshot:
    status: str
    score: Decimal | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class HealthSnapshot:
    status: str
    score: Decimal | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EventContextSnapshot:
    status: str
    score: Decimal | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class IssuerEnrichmentSnapshot:
    market_cap: Decimal | None = None
    latest_price: Decimal | None = None
    exchange: str | None = None
    sic: str | None = None
    state_of_incorp: str | None = None
    upcoming_earnings_date: date | None = None
    earnings_date_source: str | None = None
    price_context: PriceContextSnapshot = field(
        default_factory=lambda: PriceContextSnapshot(
            status="unavailable",
            details={"reason": "no_market_data_provider_configured"},
        )
    )
    health: HealthSnapshot = field(
        default_factory=lambda: HealthSnapshot(
            status="unknown",
            details={"reason": "fundamental_enrichment_not_implemented"},
        )
    )
    event_context: EventContextSnapshot = field(
        default_factory=lambda: EventContextSnapshot(
            status="not_implemented",
            details={"reason": "later_milestone"},
        )
    )


class IssuerEnrichmentProvider(Protocol):
    def enrich(self, request: IssuerEnrichmentRequest) -> IssuerEnrichmentSnapshot: ...

    def close(self) -> None: ...


class NullIssuerEnrichmentProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def enrich(self, request: IssuerEnrichmentRequest) -> IssuerEnrichmentSnapshot:
        price_reason = (
            "no_market_data_provider_configured"
            if not self.settings.market_data_provider
            else "market_data_provider_not_implemented"
        )
        return IssuerEnrichmentSnapshot(
            market_cap=request.market_cap,
            latest_price=request.latest_price,
            upcoming_earnings_date=request.upcoming_earnings_date,
            earnings_date_source=request.earnings_date_source,
            price_context=PriceContextSnapshot(
                status="unavailable",
                details={"reason": price_reason},
            ),
            health=HealthSnapshot(
                status="unknown",
                details={"reason": "health_provider_not_configured"},
            ),
            event_context=EventContextSnapshot(
                status="unavailable",
                details={"reason": "event_context_provider_not_configured"},
            ),
        )

    def close(self) -> None:
        return None


class StaticIssuerEnrichmentProvider:
    def __init__(
        self,
        snapshots: dict[str, IssuerEnrichmentSnapshot],
        fallback: IssuerEnrichmentProvider | None = None,
    ) -> None:
        self.snapshots = snapshots
        self.fallback = fallback

    def enrich(self, request: IssuerEnrichmentRequest) -> IssuerEnrichmentSnapshot:
        snapshot = self.snapshots.get(request.cik)
        if snapshot is not None:
            return snapshot
        if self.fallback is not None:
            return self.fallback.enrich(request)
        return IssuerEnrichmentSnapshot(
            market_cap=request.market_cap,
            latest_price=request.latest_price,
            upcoming_earnings_date=request.upcoming_earnings_date,
            earnings_date_source=request.earnings_date_source,
        )

    def close(self) -> None:
        if self.fallback is not None:
            self.fallback.close()


class CompositeIssuerEnrichmentProvider:
    def __init__(self, providers: list[IssuerEnrichmentProvider]) -> None:
        self.providers = providers

    def enrich(self, request: IssuerEnrichmentRequest) -> IssuerEnrichmentSnapshot:
        merged = IssuerEnrichmentSnapshot(
            market_cap=request.market_cap,
            latest_price=request.latest_price,
            upcoming_earnings_date=request.upcoming_earnings_date,
            earnings_date_source=request.earnings_date_source,
        )
        current_request = request
        for provider in self.providers:
            merged = _merge_snapshots(merged, provider.enrich(current_request))
            current_request = IssuerEnrichmentRequest(
                cik=request.cik,
                ticker=request.ticker,
                name=request.name,
                market_cap=merged.market_cap,
                latest_price=merged.latest_price,
                market_cap_price_hint=request.market_cap_price_hint,
                event_anchor_date=request.event_anchor_date,
                upcoming_earnings_date=merged.upcoming_earnings_date,
                earnings_date_source=merged.earnings_date_source,
            )
        return merged

    def close(self) -> None:
        for provider in self.providers:
            provider.close()


def _merge_snapshots(
    base: IssuerEnrichmentSnapshot, update: IssuerEnrichmentSnapshot
) -> IssuerEnrichmentSnapshot:
    return IssuerEnrichmentSnapshot(
        market_cap=update.market_cap if update.market_cap is not None else base.market_cap,
        latest_price=update.latest_price if update.latest_price is not None else base.latest_price,
        exchange=update.exchange if update.exchange is not None else base.exchange,
        sic=update.sic if update.sic is not None else base.sic,
        state_of_incorp=(
            update.state_of_incorp if update.state_of_incorp is not None else base.state_of_incorp
        ),
        upcoming_earnings_date=(
            update.upcoming_earnings_date
            if update.upcoming_earnings_date is not None
            else base.upcoming_earnings_date
        ),
        earnings_date_source=(
            update.earnings_date_source
            if update.earnings_date_source is not None
            else base.earnings_date_source
        ),
        price_context=(
            update.price_context
            if _should_override_price_context(base.price_context, update.price_context)
            else base.price_context
        ),
        health=(
            update.health if _should_override_health(base.health, update.health) else base.health
        ),
        event_context=(
            update.event_context
            if _should_override_event_context(base.event_context, update.event_context)
            else base.event_context
        ),
    )


def _should_override_price_context(
    current: PriceContextSnapshot, update: PriceContextSnapshot
) -> bool:
    if update.score is not None:
        return True
    if current.score is not None:
        return False
    return update.status != "unavailable"


def _should_override_health(current: HealthSnapshot, update: HealthSnapshot) -> bool:
    if update.score is not None:
        return True
    if current.score is not None:
        return False
    return update.status != "unknown"


def _should_override_event_context(
    current: EventContextSnapshot, update: EventContextSnapshot
) -> bool:
    if update.score is not None:
        return True
    if current.score is not None:
        return False
    status_rank = {
        "not_implemented": 0,
        "unavailable": 1,
        "available": 2,
    }
    return status_rank.get(update.status, 0) > status_rank.get(current.status, 0)


def get_issuer_enrichment_provider(settings: Settings) -> IssuerEnrichmentProvider:
    provider_name = (settings.market_data_provider or "").strip().lower()
    if provider_name in {"", "null"}:
        return NullIssuerEnrichmentProvider(settings)
    if provider_name == "sec_companyfacts":
        from sector4_sec_ingestion.enrichment import SecCompanyfactsEnrichmentProvider

        return SecCompanyfactsEnrichmentProvider(settings)
    if provider_name == "alpha_vantage":
        from sector4_core.market_data import AlphaVantagePriceEnrichmentProvider
        from sector4_sec_ingestion.enrichment import SecCompanyfactsEnrichmentProvider

        return CompositeIssuerEnrichmentProvider(
            [
                AlphaVantagePriceEnrichmentProvider(settings),
                SecCompanyfactsEnrichmentProvider(settings),
            ]
        )
    return NullIssuerEnrichmentProvider(settings)
