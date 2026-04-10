from __future__ import annotations

import argparse
import logging
import time
from datetime import date

from app.db.session import SessionLocal
from app.services.operations import IngestOperationResult, OperationsService
from app.services.signals import SignalService
from sector4_core.config import get_settings
from sector4_sec_ingestion.fixtures import load_fixture_manifest, load_proxy_fixture_manifest
from sector4_sec_ingestion.proxy_service import ProxyCompensationService
from sector4_sec_ingestion.service import IngestionService

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SECTOR4 ingestion commands")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sample = subparsers.add_parser("ingest-sample", help="Ingest local SEC fixtures")
    sample.set_defaults(func=run_ingest_sample)

    proxy_sample = subparsers.add_parser(
        "ingest-proxy-sample",
        help="Ingest local SEC proxy compensation fixtures",
    )
    proxy_sample.set_defaults(func=run_ingest_proxy_sample)

    live = subparsers.add_parser("ingest-live", help="Run live SEC ingestion once")
    live.add_argument("--date", dest="target_date", default=None, help="Date in YYYY-MM-DD format")
    live.add_argument("--limit", type=int, default=None, help="Maximum filings to ingest")
    live.add_argument(
        "--recompute",
        action="store_true",
        help="Recompute signals after live ingestion finishes",
    )
    live.set_defaults(func=run_ingest_live)

    sync_proxy = subparsers.add_parser(
        "sync-proxy-live",
        help="Fetch and ingest the latest DEF 14A proxy compensation filing for an issuer",
    )
    sync_proxy.add_argument("--cik", required=True, help="Issuer CIK")
    sync_proxy.add_argument("--issuer-name", default=None, help="Optional issuer name override")
    sync_proxy.add_argument("--fiscal-year", type=int, default=None, help="Optional fiscal year")
    sync_proxy.set_defaults(func=run_sync_proxy_live)

    backfill = subparsers.add_parser(
        "ingest-backfill",
        help="Recover missed live filings across a recent daily-index range",
    )
    backfill.add_argument("--start-date", default=None, help="Start date in YYYY-MM-DD format")
    backfill.add_argument("--end-date", default=None, help="End date in YYYY-MM-DD format")
    backfill.add_argument("--days", type=int, default=None, help="Look back this many days")
    backfill.add_argument(
        "--limit-per-day",
        type=int,
        default=None,
        help="Maximum filings to ingest from each daily index",
    )
    backfill.add_argument(
        "--recompute",
        action="store_true",
        help="Recompute signals after the backfill finishes",
    )
    backfill.set_defaults(func=run_ingest_backfill)

    poller = subparsers.add_parser(
        "poll-live",
        help="Continuously poll recent daily indexes and recompute signals",
    )
    poller.add_argument(
        "--backfill-days",
        type=int,
        default=None,
        help="Daily-index lookback window for each poll iteration",
    )
    poller.add_argument(
        "--limit-per-day",
        type=int,
        default=None,
        help="Maximum filings to ingest from each daily index",
    )
    poller.add_argument(
        "--interval-seconds",
        type=int,
        default=None,
        help="Seconds to sleep between polling iterations",
    )
    poller.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="Number of polling iterations to run; omit to poll until interrupted",
    )
    poller.add_argument(
        "--skip-recompute",
        action="store_true",
        help="Skip signal recomputation after each polling cycle",
    )
    poller.set_defaults(func=run_poll_live)

    recompute = subparsers.add_parser("recompute-signals", help="Rebuild signal windows")
    recompute.set_defaults(func=run_recompute_signals)
    return parser


def run_ingest_sample(_: argparse.Namespace) -> int:
    settings = get_settings()
    fixtures = load_fixture_manifest(settings.fixture_manifest_path)
    proxy_fixtures = load_proxy_fixture_manifest(settings.proxy_fixture_manifest_path)
    with SessionLocal() as session:
        service = IngestionService(session, settings)
        for metadata, fixture_path in fixtures:
            logger.info("ingesting sample fixture", extra={"fixture": str(fixture_path)})
            service.ingest_xml(metadata, fixture_path.read_text(encoding="utf-8"))
        proxy_service = ProxyCompensationService(session, settings)
        try:
            for metadata, fixture_path in proxy_fixtures:
                logger.info("ingesting proxy sample fixture", extra={"fixture": str(fixture_path)})
                proxy_service.ingest_html(metadata, fixture_path.read_text(encoding="utf-8"))
        finally:
            proxy_service.close()
    return 0


def run_ingest_proxy_sample(_: argparse.Namespace) -> int:
    settings = get_settings()
    proxy_fixtures = load_proxy_fixture_manifest(settings.proxy_fixture_manifest_path)
    with SessionLocal() as session:
        proxy_service = ProxyCompensationService(session, settings)
        try:
            for metadata, fixture_path in proxy_fixtures:
                logger.info("ingesting proxy sample fixture", extra={"fixture": str(fixture_path)})
                proxy_service.ingest_html(metadata, fixture_path.read_text(encoding="utf-8"))
        finally:
            proxy_service.close()
    return 0


def run_ingest_live(args: argparse.Namespace) -> int:
    settings = get_settings()
    target_date = date.fromisoformat(args.target_date) if args.target_date else None
    with SessionLocal() as session:
        result = OperationsService(session, settings).ingest_live(
            target_date=target_date,
            limit=args.limit,
            recompute=args.recompute,
        )
    _log_ingest_result(result)
    return 0


def run_sync_proxy_live(args: argparse.Namespace) -> int:
    settings = get_settings()
    with SessionLocal() as session:
        proxy_service = ProxyCompensationService(session, settings)
        try:
            result = proxy_service.sync_latest_proxy_for_issuer(
                args.cik,
                issuer_name=args.issuer_name,
                fiscal_year=args.fiscal_year,
            )
        finally:
            proxy_service.close()
    if result is None:
        logger.info("no proxy filing available", extra={"cik": args.cik})
        return 0
    logger.info(
        "proxy sync completed",
        extra={
            "accession_number": result.accession_number,
            "status": result.status,
            "record_count": result.record_count,
            "matched_insider_count": result.matched_insider_count,
        },
    )
    return 0


def run_ingest_backfill(args: argparse.Namespace) -> int:
    settings = get_settings()
    start_date = date.fromisoformat(args.start_date) if args.start_date else None
    end_date = date.fromisoformat(args.end_date) if args.end_date else None
    with SessionLocal() as session:
        result = OperationsService(session, settings).ingest_backfill(
            start_date=start_date,
            end_date=end_date,
            days=args.days,
            limit_per_day=args.limit_per_day,
            recompute=args.recompute,
        )
    _log_ingest_result(result)
    return 0


def run_poll_live(args: argparse.Namespace) -> int:
    settings = get_settings()
    interval_seconds = (
        args.interval_seconds
        if args.interval_seconds is not None
        else settings.ops_poll_interval_seconds
    )
    iterations = args.iterations
    run_number = 0
    while iterations is None or run_number < iterations:
        run_number += 1
        with SessionLocal() as session:
            result = OperationsService(session, settings).ingest_backfill(
                days=args.backfill_days,
                limit_per_day=args.limit_per_day,
                recompute=not args.skip_recompute,
            )
        logger.info("poll iteration complete", extra={"iteration": run_number})
        _log_ingest_result(result)
        if iterations is not None and run_number >= iterations:
            break
        time.sleep(interval_seconds)
    return 0


def run_recompute_signals(_: argparse.Namespace) -> int:
    settings = get_settings()
    with SessionLocal() as session:
        result = SignalService(session, settings).recompute()
    logger.info(
        "signals recomputed",
        extra={
            "generated": result.generated,
            "alerts_created": result.alerts_created,
            "summaries_generated": result.summaries_generated,
            "summaries_reused": result.summaries_reused,
        },
    )
    return 0


def _log_ingest_result(result: IngestOperationResult) -> None:
    logger.info(
        "ingest operation completed",
        extra={
            "mode": result.mode,
            "start_date": result.start_date.isoformat(),
            "end_date": result.end_date.isoformat(),
            "days_processed": result.days_processed,
            "entries_discovered": result.entries_discovered,
            "created_count": result.created_count,
            "updated_count": result.updated_count,
            "skipped_count": result.skipped_count,
            "failure_count": result.failure_count,
            "recomputed": result.recompute_result is not None,
        },
    )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
