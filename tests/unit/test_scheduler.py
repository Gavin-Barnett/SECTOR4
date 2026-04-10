from __future__ import annotations

from threading import Event

from app.services.operations import IngestOperationResult
from app.services.scheduler import PollScheduler
from sector4_core.config import Settings
from sector4_core.observability import get_metrics_registry


def test_poll_scheduler_runs_iteration_and_records_metrics() -> None:
    ran = Event()
    calls: list[int] = []

    def runner() -> IngestOperationResult:
        calls.append(1)
        ran.set()
        return IngestOperationResult(
            mode="backfill",
            start_date=__import__("datetime").date(2024, 2, 15),
            end_date=__import__("datetime").date(2024, 2, 15),
            days_processed=1,
            entries_discovered=2,
        )

    scheduler = PollScheduler(
        None,
        Settings(ops_poll_interval_seconds=3600),
        runner=runner,
    )

    scheduler.start()
    assert ran.wait(1) is True
    scheduler.stop()

    assert calls == [1]
    metrics = get_metrics_registry().snapshot()
    assert metrics["scheduler.iterations.started"] >= 1
    assert metrics["scheduler.iterations.succeeded"] >= 1
    assert metrics["scheduler.entries_discovered"] >= 2
