from __future__ import annotations

import re
import time
from datetime import date, datetime
from urllib.parse import urljoin

import httpx

from sector4_core.config import Settings, get_settings
from sector4_sec_ingestion.types import FilingMetadata, SecIndexEntry

FORM_TYPES = {"4", "4/A"}


class SecClient:
    def __init__(
        self, settings: Settings | None = None, client: httpx.Client | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client or httpx.Client(
            headers={"User-Agent": self.settings.sec_user_agent},
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
        )
        self._last_request_at = 0.0

    def close(self) -> None:
        self.client.close()

    def _throttle(self) -> None:
        min_interval = 1 / max(self.settings.sec_max_rps, 1)
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_request_at = time.monotonic()

    def _get_text(self, url: str) -> str:
        self._throttle()
        response = self.client.get(url)
        response.raise_for_status()
        return response.text

    def _get_json(self, url: str) -> dict:
        self._throttle()
        response = self.client.get(url)
        response.raise_for_status()
        return response.json()

    def fetch_text(self, url: str) -> str:
        return self._get_text(url)

    def fetch_daily_index(self, for_date: date) -> list[SecIndexEntry]:
        quarter = ((for_date.month - 1) // 3) + 1
        url = (
            f"{self.settings.sec_base_url}/Archives/edgar/daily-index/"
            f"{for_date.year}/QTR{quarter}/master.{for_date:%Y%m%d}.idx"
        )
        return parse_daily_index(self._get_text(url))

    def fetch_filing_directory(self, entry: SecIndexEntry) -> dict:
        directory_url = f"{self.settings.sec_base_url}/Archives/{entry.directory_path}/index.json"
        return self._get_json(directory_url)

    def fetch_filing_metadata(self, entry: SecIndexEntry) -> tuple[FilingMetadata, str]:
        directory = self.fetch_filing_directory(entry)
        items = directory.get("directory", {}).get("item", [])
        xml_item = next(
            (
                item
                for item in items
                if item.get("name", "").lower() == "ownership.xml"
                or item.get("name", "").lower().endswith(".xml")
            ),
            None,
        )
        if xml_item is None:
            raise ValueError(f"No ownership XML found for {entry.accession_number}")
        xml_url = urljoin(
            f"{self.settings.sec_base_url}/Archives/{entry.directory_path}/", xml_item["name"]
        )
        metadata = FilingMetadata(
            accession_number=entry.accession_number,
            form_type=entry.form_type,
            filed_at=datetime.combine(entry.filed_date, datetime.min.time()),
            source_url=f"{self.settings.sec_base_url}/Archives/{entry.filename}",
            xml_url=xml_url,
        )
        return metadata, self._get_text(xml_url)

    def fetch_submissions(self, cik: str) -> dict:
        normalized_cik = normalize_cik(cik)
        url = f"{self.settings.sec_data_base_url}/submissions/CIK{normalized_cik}.json"
        return self._get_json(url)

    def fetch_companyfacts(self, cik: str) -> dict:
        normalized_cik = normalize_cik(cik)
        url = f"{self.settings.sec_data_base_url}/api/xbrl/companyfacts/CIK{normalized_cik}.json"
        return self._get_json(url)

    def build_archive_document_url(
        self, cik: str, accession_number: str, primary_document: str
    ) -> str:
        normalized_cik = str(int(normalize_cik(cik)))
        accession_path = accession_number.replace("-", "")
        return (
            f"{self.settings.sec_base_url}/Archives/edgar/data/"
            f"{normalized_cik}/{accession_path}/{primary_document}"
        )


def normalize_cik(value: str) -> str:
    digits = "".join(character for character in value if character.isdigit())
    if not digits:
        raise ValueError("CIK must contain at least one digit")
    return digits.zfill(10)


def parse_daily_index(raw_text: str) -> list[SecIndexEntry]:
    entries: list[SecIndexEntry] = []
    seen_data = False
    for line in raw_text.splitlines():
        if line.startswith("-----"):
            seen_data = True
            continue
        if not seen_data or not line.strip():
            continue

        form_type: str
        company_name: str
        cik: str
        filed_date: str
        filename: str

        if "|" in line:
            parts = [part.strip() for part in line.split("|")]
            if len(parts) < 5 or not parts[0].isdigit():
                continue
            cik, company_name, form_type, filed_date, filename = parts[:5]
        else:
            parts = re.split(r"\s{2,}", line.strip())
            if len(parts) < 5:
                continue
            form_type, company_name, cik, filed_date, filename = parts[:5]

        if form_type not in FORM_TYPES:
            continue
        entries.append(
            SecIndexEntry(
                form_type=form_type,
                company_name=company_name,
                cik=cik,
                filed_date=date.fromisoformat(filed_date),
                filename=filename,
            )
        )
    return entries

