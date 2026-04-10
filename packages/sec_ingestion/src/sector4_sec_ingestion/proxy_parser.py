from __future__ import annotations

import html
import re
from decimal import Decimal

from sector4_sec_ingestion.types import (
    ParsedCompensationRecord,
    ParsedProxyCompensationDocument,
    ProxyFilingMetadata,
)

_TABLE_RE = re.compile(r"<table\b.*?>.*?</table>", re.IGNORECASE | re.DOTALL)
_ROW_RE = re.compile(r"<tr\b.*?>.*?</tr>", re.IGNORECASE | re.DOTALL)
_CELL_RE = re.compile(r"<t[hd]\b.*?>(.*?)</t[hd]>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"\s+")
_NUMBER_RE = re.compile(r"\(?\$?([\d,]+(?:\.\d+)?)\)?")
_YEAR_RE = re.compile(r"\b(20\d{2})\b")


class ProxyCompensationParseError(ValueError):
    pass


def parse_proxy_compensation_html(
    html_text: str,
    metadata: ProxyFilingMetadata,
) -> ParsedProxyCompensationDocument:
    tables = [_extract_table_rows(table_html) for table_html in _TABLE_RE.findall(html_text)]
    candidate_tables = [table for table in tables if table]
    table = _select_summary_comp_table(candidate_tables)
    if table is None:
        raise ProxyCompensationParseError("Summary compensation table not found")

    header_row_index = _find_header_row_index(table)
    if header_row_index is None:
        raise ProxyCompensationParseError("Summary compensation headers not found")
    headers = [_normalize_header(cell) for cell in table[header_row_index]]
    columns = _resolve_columns(headers)
    fiscal_year = metadata.fiscal_year or _extract_fiscal_year(headers)

    records: list[ParsedCompensationRecord] = []
    for row in table[header_row_index + 1 :]:
        if not any(cell.strip() for cell in row):
            continue
        name_cell = _cell_value(row, columns["name"])
        insider_name, title = _split_name_and_title(name_cell)
        if not insider_name or _looks_like_header(insider_name):
            continue
        total = _parse_currency(_cell_value(row, columns["total"]))
        salary = _parse_currency(_cell_value(row, columns.get("salary")))
        bonus = _parse_currency(_cell_value(row, columns.get("bonus")))
        stock_awards = _parse_currency(_cell_value(row, columns.get("stock_awards")))
        option_awards = _parse_currency(_cell_value(row, columns.get("option_awards")))
        non_equity = _parse_currency(_cell_value(row, columns.get("non_equity_incentive")))
        all_other = _parse_currency(_cell_value(row, columns.get("all_other")))
        if total is None and not any(
            value is not None
            for value in [salary, bonus, stock_awards, option_awards, non_equity, all_other]
        ):
            continue
        records.append(
            ParsedCompensationRecord(
                insider_name=insider_name,
                title=title,
                fiscal_year=fiscal_year,
                salary_usd=salary,
                bonus_usd=bonus,
                stock_awards_usd=stock_awards,
                option_awards_usd=option_awards,
                non_equity_incentive_usd=non_equity,
                all_other_comp_usd=all_other,
                total_compensation_usd=total,
                raw_payload={
                    "name_cell": name_cell,
                    "row": row,
                    "fiscal_year": fiscal_year,
                },
            )
        )

    if not records:
        raise ProxyCompensationParseError("No compensation rows parsed from summary table")

    return ParsedProxyCompensationDocument(
        metadata=metadata,
        records=records,
        extra_data={
            "table_rows": len(table),
            "parsed_record_count": len(records),
            "headers": headers,
        },
    )


def _select_summary_comp_table(tables: list[list[list[str]]]) -> list[list[str]] | None:
    best_table: list[list[str]] | None = None
    best_score = -1
    for table in tables:
        flattened_headers = " ".join(_normalize_header(cell) for row in table[:3] for cell in row)
        score = 0
        if "name and principal position" in flattened_headers or (
            "name" in flattened_headers and "principal position" in flattened_headers
        ):
            score += 2
        if "total" in flattened_headers:
            score += 1
        if "salary" in flattened_headers:
            score += 1
        if score > best_score:
            best_score = score
            best_table = table
    return best_table if best_score >= 3 else None


def _find_header_row_index(table: list[list[str]]) -> int | None:
    for index, row in enumerate(table[:4]):
        normalized = [_normalize_header(cell) for cell in row]
        if any("name" in cell for cell in normalized) and any(
            "total" in cell for cell in normalized
        ):
            return index
    return None


def _resolve_columns(headers: list[str]) -> dict[str, int]:
    columns: dict[str, int] = {}
    for index, header in enumerate(headers):
        if (
            "name and principal position" in header
            or header == "name"
            or ("name" in header and "principal position" in header)
        ):
            columns.setdefault("name", index)
        elif "salary" in header:
            columns.setdefault("salary", index)
        elif "bonus" in header:
            columns.setdefault("bonus", index)
        elif "stock awards" in header:
            columns.setdefault("stock_awards", index)
        elif "option awards" in header:
            columns.setdefault("option_awards", index)
        elif "non-equity incentive" in header or "non equity incentive" in header:
            columns.setdefault("non_equity_incentive", index)
        elif "all other" in header:
            columns.setdefault("all_other", index)
        elif header == "total" or header.startswith("total ") or " total" in header:
            columns.setdefault("total", index)
    if "name" not in columns or "total" not in columns:
        raise ProxyCompensationParseError("Required summary compensation columns are missing")
    return columns


def _extract_table_rows(table_html: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for row_html in _ROW_RE.findall(table_html):
        cells = [_html_to_text(cell_html) for cell_html in _CELL_RE.findall(row_html)]
        if cells:
            rows.append(cells)
    return rows


def _html_to_text(cell_html: str) -> str:
    with_breaks = re.sub(r"<br\s*/?>", "|", cell_html, flags=re.IGNORECASE)
    stripped = _TAG_RE.sub(" ", with_breaks)
    unescaped = html.unescape(stripped).replace("\xa0", " ")
    return _WS_RE.sub(" ", unescaped).strip()


def _normalize_header(value: str) -> str:
    return _WS_RE.sub(" ", value.strip().lower())


def _extract_fiscal_year(headers: list[str]) -> int | None:
    for header in headers:
        match = _YEAR_RE.search(header)
        if match:
            return int(match.group(1))
    return None


def _split_name_and_title(name_cell: str) -> tuple[str | None, str | None]:
    if not name_cell:
        return None, None
    normalized = name_cell.replace("|", "\n")
    parts = [segment.strip(" ,-;") for segment in normalized.split("\n") if segment.strip()]
    if not parts:
        return None, None
    name = parts[0]
    title = parts[1] if len(parts) > 1 else None
    return name, title


def _looks_like_header(value: str) -> bool:
    normalized = _normalize_header(value)
    return "name and principal position" in normalized or normalized == "name"


def _cell_value(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return row[index]


def _parse_currency(value: str) -> Decimal | None:
    if not value or value in {"-", "--", "n/a", "N/A"}:
        return None
    match = _NUMBER_RE.search(value)
    if match is None:
        return None
    parsed = Decimal(match.group(1).replace(",", ""))
    if value.strip().startswith("(") and value.strip().endswith(")"):
        parsed = -parsed
    return parsed.quantize(Decimal("0.01"))
