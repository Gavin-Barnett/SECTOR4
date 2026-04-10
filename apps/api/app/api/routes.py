from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.browse import FilingDetail, InsiderDetail, IssuerDetail, TransactionRecord
from app.schemas.ops import OpsBackfillRequest, OpsIngestResponse, OpsLiveIngestRequest
from app.schemas.signals import SignalDetail, SignalFilters, SignalRecomputeResponse, SignalSummary
from app.services.browse import BrowseService
from app.services.operations import OperationsService
from app.services.signals import SignalService
from sector4_core.config import get_settings
from sector4_core.observability import get_metrics_registry

router = APIRouter()
DbSession = Annotated[Session, Depends(get_db)]
OpsToken = Annotated[str | None, Header(alias="X-Ops-Token")]
TickerQuery = Annotated[str | None, Query()]
CikQuery = Annotated[str | None, Query()]
DateFromQuery = Annotated[date | None, Query()]
DateToQuery = Annotated[date | None, Query()]
MarketCapMaxQuery = Annotated[Decimal | None, Query()]
MinimumScoreQuery = Annotated[Decimal | None, Query()]
MinimumUniqueBuyersQuery = Annotated[int | None, Query()]
IncludeUnknownHealthQuery = Annotated[bool, Query()]
IncludeIndirectQuery = Annotated[bool, Query()]
IncludeAmendmentsQuery = Annotated[bool, Query()]
IncludeDerivativeQuery = Annotated[bool, Query()]
IncludeRoutineQuery = Annotated[bool, Query()]
CandidateOnlyQuery = Annotated[bool, Query()]
LimitQuery = Annotated[int, Query(ge=1, le=100)]


def require_ops_access(x_ops_token: OpsToken = None) -> None:
    settings = get_settings()
    if settings.app_env == "development":
        return
    if not settings.ops_api_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ops endpoints disabled",
        )
    if x_ops_token == settings.ops_api_token:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ops endpoint not allowed")


OpsAccess = Annotated[None, Depends(require_ops_access)]


@router.get("/")
def root() -> dict[str, object]:
    return {
        "name": "SECTOR4",
        "working_title": True,
        "status": "bootstrapping",
        "uses_public_sec_filings_only": True,
        "investment_advice": False,
        "notes": [
            "Signals are derived from public SEC ownership filings.",
            "Filings may be delayed relative to transaction dates.",
            "Review original SEC filings before acting.",
        ],
    }


@router.get("/health")
def health(request: Request) -> dict[str, object]:
    settings = get_settings()
    scheduler = getattr(request.app.state, "poll_scheduler", None)
    return {
        "status": "ok",
        "app_env": settings.app_env,
        "uses_public_sec_filings_only": True,
        "investment_advice": False,
        "scheduler_enabled": settings.ops_scheduler_enabled,
        "scheduler_running": bool(scheduler and scheduler.is_running),
    }


@router.get("/ready")
def ready(db: DbSession) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database not ready",
        ) from exc
    return {"status": "ready"}


@router.get("/metrics")
def metrics(request: Request) -> dict[str, object]:
    settings = get_settings()
    scheduler = getattr(request.app.state, "poll_scheduler", None)
    return {
        "counters": get_metrics_registry().snapshot(),
        "scheduler": {
            "enabled": settings.ops_scheduler_enabled,
            "running": bool(scheduler and scheduler.is_running),
            "interval_seconds": settings.ops_poll_interval_seconds,
            "backfill_days": settings.ops_backfill_days,
        },
    }


@router.get("/signals", response_model=list[SignalSummary])
def list_signals(
    db: DbSession,
    ticker: TickerQuery = None,
    cik: CikQuery = None,
    date_from: DateFromQuery = None,
    date_to: DateToQuery = None,
    market_cap_max: MarketCapMaxQuery = None,
    minimum_score: MinimumScoreQuery = None,
    minimum_unique_buyers: MinimumUniqueBuyersQuery = None,
    include_unknown_health: IncludeUnknownHealthQuery = True,
    include_indirect: IncludeIndirectQuery = False,
    include_amendments: IncludeAmendmentsQuery = True,
) -> list[SignalSummary]:
    return SignalService(db).list_signals(
        SignalFilters(
            ticker=ticker,
            cik=cik,
            date_from=date_from,
            date_to=date_to,
            market_cap_max=market_cap_max,
            minimum_score=minimum_score,
            minimum_unique_buyers=minimum_unique_buyers,
            include_unknown_health=include_unknown_health,
            include_indirect=include_indirect,
            include_amendments=include_amendments,
        )
    )


@router.get("/signals/latest", response_model=list[SignalSummary])
def latest_signals(db: DbSession, limit: LimitQuery = 10) -> list[SignalSummary]:
    return SignalService(db).latest_signals(limit=limit)


@router.get("/signals/{signal_id}", response_model=SignalDetail)
def get_signal(signal_id: int, db: DbSession) -> SignalDetail:
    signal = SignalService(db).get_signal(signal_id)
    if signal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="signal not found")
    return signal


@router.get("/filings/{accession_number}", response_model=FilingDetail)
def get_filing(accession_number: str, db: DbSession) -> FilingDetail:
    filing = BrowseService(db).get_filing(accession_number)
    if filing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="filing not found")
    return filing


@router.get("/issuers/{ticker_or_cik}", response_model=IssuerDetail)
def get_issuer(ticker_or_cik: str, db: DbSession) -> IssuerDetail:
    issuer = BrowseService(db).get_issuer(ticker_or_cik)
    if issuer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="issuer not found")
    return issuer


@router.get("/issuers/{ticker_or_cik}/transactions", response_model=list[TransactionRecord])
def get_issuer_transactions(
    ticker_or_cik: str,
    db: DbSession,
    limit: LimitQuery = 50,
    include_derivative: IncludeDerivativeQuery = True,
    include_routine: IncludeRoutineQuery = True,
    candidate_only: CandidateOnlyQuery = False,
) -> list[TransactionRecord]:
    transactions = BrowseService(db).get_issuer_transactions(
        ticker_or_cik,
        limit=limit,
        include_derivative=include_derivative,
        include_routine=include_routine,
        candidate_only=candidate_only,
    )
    if transactions is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="issuer not found")
    return transactions


@router.get("/insiders/{insider_id}", response_model=InsiderDetail)
def get_insider(insider_id: int, db: DbSession, limit: LimitQuery = 25) -> InsiderDetail:
    insider = BrowseService(db).get_insider(insider_id, limit=limit)
    if insider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="insider not found")
    return insider


@router.post("/ops/ingest/live", response_model=OpsIngestResponse)
def ingest_live(
    db: DbSession,
    _: OpsAccess,
    payload: Annotated[OpsLiveIngestRequest | None, Body()] = None,
) -> OpsIngestResponse:
    request = payload or OpsLiveIngestRequest()
    try:
        result = OperationsService(db).ingest_live(
            target_date=request.target_date,
            limit=request.limit,
            recompute=request.recompute,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return OpsIngestResponse.from_result(result)


@router.post("/ops/ingest/backfill", response_model=OpsIngestResponse)
def ingest_backfill(
    db: DbSession,
    _: OpsAccess,
    payload: Annotated[OpsBackfillRequest | None, Body()] = None,
) -> OpsIngestResponse:
    request = payload or OpsBackfillRequest()
    try:
        result = OperationsService(db).ingest_backfill(
            start_date=request.start_date,
            end_date=request.end_date,
            days=request.days,
            limit_per_day=request.limit_per_day,
            recompute=request.recompute,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return OpsIngestResponse.from_result(result)


@router.post("/ops/recompute-signals", response_model=SignalRecomputeResponse)
def recompute_signals(db: DbSession, _: OpsAccess) -> SignalRecomputeResponse:
    return SignalService(db).recompute()
