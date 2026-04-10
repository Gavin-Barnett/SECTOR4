from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.entities import Filing, Insider, Issuer, SignalWindow, Transaction
from app.schemas.browse import (
    FilingDetail,
    InsiderDetail,
    InsiderReference,
    IssuerDetail,
    IssuerReference,
    TransactionRecord,
)


class BrowseService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_filing(self, accession_number: str) -> FilingDetail | None:
        filing = self.session.scalar(
            select(Filing)
            .options(
                selectinload(Filing.issuer),
                selectinload(Filing.transactions).selectinload(Transaction.insider),
            )
            .where(Filing.accession_number == accession_number)
        )
        if filing is None:
            return None
        issuer_ref = _issuer_reference(filing.issuer)
        transactions = sorted(
            filing.transactions,
            key=lambda item: (item.transaction_date or filing.filed_at.date(), item.id),
            reverse=True,
        )
        return FilingDetail(
            accession_number=filing.accession_number,
            form_type=filing.form_type,
            filed_at=filing.filed_at,
            source_url=filing.source_url,
            xml_url=filing.xml_url,
            is_amendment=filing.is_amendment,
            raw_xml_path=filing.raw_xml_path,
            fingerprint=filing.fingerprint,
            issuer=issuer_ref,
            extra_data=filing.extra_data,
            transactions=[
                _transaction_record(transaction, filing, filing.issuer, transaction.insider)
                for transaction in transactions
            ],
        )

    def get_issuer(self, ticker_or_cik: str) -> IssuerDetail | None:
        issuer = self._resolve_issuer(ticker_or_cik)
        if issuer is None:
            return None
        filing_count = (
            self.session.scalar(
                select(func.count()).select_from(Filing).where(Filing.issuer_id == issuer.id)
            )
            or 0
        )
        transaction_count = (
            self.session.scalar(
                select(func.count())
                .select_from(Transaction)
                .join(Transaction.filing)
                .where(Filing.issuer_id == issuer.id)
            )
            or 0
        )
        latest_signal = self.session.scalar(
            select(SignalWindow)
            .where(SignalWindow.issuer_id == issuer.id)
            .where(SignalWindow.is_active.is_(True))
            .order_by(SignalWindow.window_end.desc(), SignalWindow.signal_score.desc())
            .limit(1)
        )
        rationale = latest_signal.rationale_json if latest_signal is not None else {}
        return IssuerDetail(
            id=issuer.id,
            cik=issuer.cik,
            ticker=issuer.ticker,
            name=issuer.name,
            exchange=issuer.exchange,
            sic=issuer.sic,
            state_of_incorp=issuer.state_of_incorp,
            market_cap=issuer.market_cap,
            latest_price=issuer.latest_price,
            filing_count=int(filing_count),
            transaction_count=int(transaction_count),
            latest_signal_id=latest_signal.id if latest_signal is not None else None,
            latest_signal_score=latest_signal.signal_score if latest_signal is not None else None,
            latest_signal_window_end=latest_signal.window_end
            if latest_signal is not None
            else None,
            latest_signal_health_status=(
                str(rationale.get("health_status", "unknown"))
                if latest_signal is not None
                else None
            ),
            latest_signal_price_context_status=(
                str(rationale.get("price_context_status", "unavailable"))
                if latest_signal is not None
                else None
            ),
        )

    def get_issuer_transactions(
        self,
        ticker_or_cik: str,
        *,
        limit: int = 100,
        include_derivative: bool = True,
        include_routine: bool = True,
        candidate_only: bool = False,
    ) -> list[TransactionRecord] | None:
        issuer = self._resolve_issuer(ticker_or_cik)
        if issuer is None:
            return None
        statement = (
            select(Transaction, Filing, Insider)
            .join(Transaction.filing)
            .join(Transaction.insider)
            .where(Filing.issuer_id == issuer.id)
            .order_by(Filing.filed_at.desc(), Transaction.id.desc())
            .limit(limit)
        )
        if not include_derivative:
            statement = statement.where(Transaction.is_derivative.is_(False))
        if not include_routine:
            statement = statement.where(Transaction.is_likely_routine.is_(False))
        if candidate_only:
            statement = statement.where(Transaction.is_candidate_buy.is_(True))

        rows = self.session.execute(statement).all()
        return [
            _transaction_record(transaction, filing, issuer, insider)
            for transaction, filing, insider in rows
        ]

    def get_insider(self, insider_id: int, *, limit: int = 50) -> InsiderDetail | None:
        insider = self.session.scalar(select(Insider).where(Insider.id == insider_id))
        if insider is None:
            return None
        transaction_count = (
            self.session.scalar(
                select(func.count())
                .select_from(Transaction)
                .where(Transaction.insider_id == insider.id)
            )
            or 0
        )
        rows = self.session.execute(
            select(Transaction, Filing, Issuer)
            .join(Transaction.filing)
            .join(Filing.issuer)
            .where(Transaction.insider_id == insider.id)
            .order_by(Filing.filed_at.desc(), Transaction.id.desc())
            .limit(limit)
        ).all()
        return InsiderDetail(
            id=insider.id,
            reporting_owner_cik=insider.reporting_owner_cik,
            name=insider.name,
            is_director=insider.is_director,
            is_officer=insider.is_officer,
            is_ten_percent_owner=insider.is_ten_percent_owner,
            officer_title=insider.officer_title,
            transaction_count=int(transaction_count),
            recent_transactions=[
                _transaction_record(transaction, filing, issuer, insider)
                for transaction, filing, issuer in rows
            ],
        )

    def _resolve_issuer(self, ticker_or_cik: str) -> Issuer | None:
        normalized = ticker_or_cik.strip()
        return self.session.scalar(
            select(Issuer).where(or_(Issuer.cik == normalized, Issuer.ticker == normalized.upper()))
        )


def _issuer_reference(issuer: Issuer) -> IssuerReference:
    return IssuerReference(
        id=issuer.id,
        cik=issuer.cik,
        ticker=issuer.ticker,
        name=issuer.name,
    )


def _insider_reference(insider: Insider) -> InsiderReference:
    return InsiderReference(
        id=insider.id,
        reporting_owner_cik=insider.reporting_owner_cik,
        name=insider.name,
        is_director=insider.is_director,
        is_officer=insider.is_officer,
        is_ten_percent_owner=insider.is_ten_percent_owner,
        officer_title=insider.officer_title,
    )


def _transaction_record(
    transaction: Transaction,
    filing: Filing,
    issuer: Issuer,
    insider: Insider,
) -> TransactionRecord:
    return TransactionRecord(
        id=transaction.id,
        filing_accession_number=filing.accession_number,
        form_type=filing.form_type,
        filed_at=filing.filed_at,
        source_url=filing.source_url,
        xml_url=filing.xml_url,
        issuer=_issuer_reference(issuer),
        insider=_insider_reference(insider),
        transaction_date=transaction.transaction_date,
        security_title=transaction.security_title,
        is_derivative=transaction.is_derivative,
        transaction_code=transaction.transaction_code,
        acquired_disposed=transaction.acquired_disposed,
        shares=transaction.shares,
        price_per_share=transaction.price_per_share,
        value_usd=transaction.value_usd,
        shares_after=transaction.shares_after,
        ownership_type=transaction.ownership_type,
        deemed_execution_date=transaction.deemed_execution_date,
        footnote_text=transaction.footnote_text,
        is_candidate_buy=transaction.is_candidate_buy,
        is_likely_routine=transaction.is_likely_routine,
        routine_reason=transaction.routine_reason,
    )
