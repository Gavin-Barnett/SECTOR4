from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

from sector4_core.config import get_settings
from sector4_sec_ingestion.types import (
    FilingMetadata,
    ParsedInsider,
    ParsedIssuer,
    ParsedOwnershipDocument,
    ParsedTransaction,
)

ROUTINE_KEYWORDS = {
    "10b5-1": "trading_plan_language",
    "award": "compensation_language",
    "gift": "gift_language",
    "withhold": "tax_withholding_language",
    "vesting": "vesting_language",
    "option": "option_mechanics_language",
}


def parse_ownership_xml(
    xml_text: str,
    metadata: FilingMetadata,
    micro_transaction_usd: Decimal | int | str | None = None,
) -> ParsedOwnershipDocument:
    settings = get_settings()
    threshold = Decimal(str(micro_transaction_usd or settings.routine_micro_transaction_usd))
    root = ET.fromstring(xml_text)
    footnotes = {
        element.attrib["id"]: _clean_text(element.text)
        for element in root.findall(".//footnote")
        if element.attrib.get("id")
    }

    issuer = ParsedIssuer(
        cik=_text(root, "issuer/issuerCik") or "",
        name=_text(root, "issuer/issuerName") or "Unknown issuer",
        ticker=_text(root, "issuer/issuerTradingSymbol"),
    )
    insiders = [_parse_insider(node) for node in root.findall("reportingOwner")]
    if not insiders:
        insiders = [ParsedInsider(name="Unknown reporting owner")]

    transactions = [
        *_parse_transactions(
            root.findall(".//nonDerivativeTransaction"), False, footnotes, threshold
        ),
        *_parse_transactions(root.findall(".//derivativeTransaction"), True, footnotes, threshold),
    ]
    _promote_indirect_candidate_buys(transactions)

    return ParsedOwnershipDocument(
        metadata=metadata,
        document_type=_text(root, "documentType") or metadata.form_type,
        period_of_report=_date_value(_text(root, "periodOfReport")),
        issuer=issuer,
        insiders=insiders,
        transactions=transactions,
        footnotes=footnotes,
        remarks=_text(root, "remarks"),
        extra_data={
            "schema_version": _text(root, "schemaVersion"),
            "date_of_original_submission": _text(root, "dateOfOriginalSubmission"),
        },
    )


def _parse_insider(node: ET.Element) -> ParsedInsider:
    return ParsedInsider(
        reporting_owner_cik=_text(node, "reportingOwnerId/rptOwnerCik"),
        name=_text(node, "reportingOwnerId/rptOwnerName") or "Unknown reporting owner",
        is_director=_bool_text(_text(node, "reportingOwnerRelationship/isDirector")),
        is_officer=_bool_text(_text(node, "reportingOwnerRelationship/isOfficer")),
        is_ten_percent_owner=_bool_text(
            _text(node, "reportingOwnerRelationship/isTenPercentOwner")
        ),
        officer_title=_text(node, "reportingOwnerRelationship/officerTitle"),
    )


def _parse_transactions(
    nodes: Iterable[ET.Element],
    is_derivative: bool,
    footnotes: dict[str, str],
    micro_threshold_usd: Decimal,
) -> list[ParsedTransaction]:
    transactions: list[ParsedTransaction] = []
    for node in nodes:
        shares = _decimal_value(_text(node, "transactionAmounts/transactionShares/value"))
        price_per_share = _decimal_value(
            _text(node, "transactionAmounts/transactionPricePerShare/value")
        )
        value_usd = (
            shares * price_per_share if shares is not None and price_per_share is not None else None
        )
        footnote_ids = [
            item.attrib["id"] for item in node.findall(".//footnoteId") if item.attrib.get("id")
        ]
        footnote_text = (
            " ".join(dict.fromkeys(filter(None, (footnotes.get(fid) for fid in footnote_ids))))
            or None
        )
        transaction_code = _text(node, "transactionCoding/transactionCode")
        acquired_disposed = _text(node, "transactionAmounts/transactionAcquiredDisposedCode/value")
        ownership_type = _text(node, "ownershipNature/directOrIndirectOwnership/value")
        security_title = _text(node, "securityTitle/value")
        is_likely_routine, routine_reason = _classify_routine(
            transaction_code=transaction_code,
            is_derivative=is_derivative,
            ownership_type=ownership_type,
            value_usd=value_usd,
            security_title=security_title,
            footnote_text=footnote_text,
            micro_threshold_usd=micro_threshold_usd,
        )
        transactions.append(
            ParsedTransaction(
                security_title=security_title,
                transaction_date=_date_value(_text(node, "transactionDate/value")),
                deemed_execution_date=_date_value(_text(node, "deemedExecutionDate/value")),
                transaction_code=transaction_code,
                acquired_disposed=acquired_disposed,
                shares=shares,
                price_per_share=price_per_share,
                value_usd=value_usd,
                shares_after=_decimal_value(
                    _text(node, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")
                ),
                ownership_type=ownership_type,
                is_derivative=is_derivative,
                footnote_text=footnote_text,
                is_candidate_buy=(
                    not is_derivative
                    and transaction_code == "P"
                    and acquired_disposed == "A"
                    and not is_likely_routine
                ),
                is_likely_routine=is_likely_routine,
                routine_reason=routine_reason,
                raw_payload={
                    "footnote_ids": footnote_ids,
                    "xml": ET.tostring(node, encoding="unicode"),
                },
            )
        )
    return transactions


def _classify_routine(
    *,
    transaction_code: str | None,
    is_derivative: bool,
    ownership_type: str | None,
    value_usd: Decimal | None,
    security_title: str | None,
    footnote_text: str | None,
    micro_threshold_usd: Decimal,
) -> tuple[bool, str | None]:
    if transaction_code != "P":
        return True, "non_purchase_code"
    if is_derivative:
        return True, "derivative_security"

    text_blob = f"{security_title or ''} {footnote_text or ''}".lower()
    for keyword, reason in ROUTINE_KEYWORDS.items():
        if keyword in text_blob:
            return True, reason

    if value_usd is not None and value_usd < micro_threshold_usd:
        return True, "micro_transaction"
    if ownership_type and ownership_type.upper() != "D":
        return True, "indirect_only"
    return False, None


def _promote_indirect_candidate_buys(transactions: list[ParsedTransaction]) -> None:
    has_direct_open_market_buy = any(
        not transaction.is_derivative
        and transaction.transaction_code == "P"
        and transaction.acquired_disposed == "A"
        and (transaction.ownership_type or "D").upper() == "D"
        and not transaction.is_likely_routine
        for transaction in transactions
    )
    if not has_direct_open_market_buy:
        return

    for transaction in transactions:
        if transaction.is_derivative:
            continue
        if transaction.transaction_code != "P" or transaction.acquired_disposed != "A":
            continue
        if (transaction.ownership_type or "D").upper() == "D":
            continue
        if transaction.routine_reason != "indirect_only":
            continue
        transaction.is_likely_routine = False
        transaction.routine_reason = None
        transaction.is_candidate_buy = True


def _text(node: ET.Element, path: str) -> str | None:
    child = node.find(path)
    if child is None:
        return None
    return _clean_text(child.text)


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = " ".join(value.split())
    return stripped or None


def _bool_text(value: str | None) -> bool:
    return value in {"1", "true", "True", "TRUE"}


def _date_value(value: str | None):
    if not value:
        return None
    return __import__("datetime").date.fromisoformat(value)


def _decimal_value(value: str | None) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None
