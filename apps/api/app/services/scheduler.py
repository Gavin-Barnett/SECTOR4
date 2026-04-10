from __future__ import annotations

import logging
from collections.abc import Callable
from threading import Event, Thread

from sqlalchemy.orm import Session

from app.services.operations import IngestOperationResult, OperationsService
from sector4_core.config import Settings, get_settings
from sector4_core.observability import get_metrics_registry

logger = logging.getLogger(__name__)


class PollScheduler:
    def __init__(
        self,
        session_factory: Callable[[], Session] | None,
        settings: Settings | None = None,
        runner: Callable[[], IngestOperationResult] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._session_factory = session_factory
        self._runner = runner or self._run_once
        self._stop_event = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = Thread(target=self._loop, name="sector4-poll-scheduler", daemon=True)
        self._thread.start()
        logger.info(
            "poll scheduler started",
            extra={
                "interval_seconds": self.settings.ops_poll_interval_seconds,
                "backfill_days": self.settings.ops_backfill_days,
                "limit_per_day": self.settings.ops_live_ingest_limit,
            },
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        logger.info("poll scheduler stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        metrics = get_metrics_registry()
        while not self._stop_event.is_set():
            metrics.increment("scheduler.iterations.started")
            try:
                result = self._runner()
                metrics.increment("scheduler.iterations.succeeded")
                metrics.increment("scheduler.entries_discovered", result.entries_discovered)
                logger.info(
                    "poll scheduler iteration completed",
                    extra={
                        "entries_discovered": result.entries_discovered,
                        "created_count": result.created_count,
                        "updated_count": result.updated_count,
                        "skipped_count": result.skipped_count,
                        "failure_count": result.failure_count,
                    },
                )
            except Exception as exc:
                metrics.increment("scheduler.iterations.failed")
                logger.exception("poll scheduler iteration failed", extra={"error": str(exc)})
            if self._stop_event.wait(self.settings.ops_poll_interval_seconds):
                break

    def _run_once(self) -> IngestOperationResult:
        if self._session_factory is None:
            raise RuntimeError("session_factory is required when no custom runner is supplied")
        session = self._session_factory()
        try:
            return OperationsService(session, self.settings).ingest_backfill(
                days=self.settings.ops_backfill_days,
                limit_per_day=self.settings.ops_live_ingest_limit,
                recompute=True,
            )
        finally:
            session.close()
