from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.signals import SignalRecomputeResponse
from app.services.operations import IngestOperationResult


class OpsFailureRecord(BaseModel):
    target_date: date
    stage: str
    error: str
    accession_number: str | None = None


class OpsIngestResponse(BaseModel):
    mode: str
    start_date: date
    end_date: date
    days_processed: int
    entries_discovered: int
    created_count: int
    updated_count: int
    skipped_count: int
    failure_count: int
    accession_numbers: list[str]
    failures: list[OpsFailureRecord]
    recompute: SignalRecomputeResponse | None = None

    @classmethod
    def from_result(cls, result: IngestOperationResult) -> OpsIngestResponse:
        return cls(
            mode=result.mode,
            start_date=result.start_date,
            end_date=result.end_date,
            days_processed=result.days_processed,
            entries_discovered=result.entries_discovered,
            created_count=result.created_count,
            updated_count=result.updated_count,
            skipped_count=result.skipped_count,
            failure_count=result.failure_count,
            accession_numbers=result.accession_numbers,
            failures=[
                OpsFailureRecord(
                    target_date=failure.target_date,
                    stage=failure.stage,
                    error=failure.error,
                    accession_number=failure.accession_number,
                )
                for failure in result.failures
            ],
            recompute=result.recompute_result,
        )


class OpsLiveIngestRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    target_date: date | None = None
    limit: int | None = Field(default=None, ge=1)
    recompute: bool = True


class OpsBackfillRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    start_date: date | None = None
    end_date: date | None = None
    days: int | None = Field(default=None, ge=1)
    limit_per_day: int | None = Field(default=None, ge=1)
    recompute: bool = True
