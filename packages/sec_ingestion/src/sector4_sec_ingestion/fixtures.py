from __future__ import annotations

import json
from pathlib import Path

from sector4_sec_ingestion.types import FilingMetadata, ProxyFilingMetadata


def load_fixture_manifest(path: str) -> list[tuple[FilingMetadata, Path]]:
    manifest_path = Path(path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    fixtures: list[tuple[FilingMetadata, Path]] = []
    for item in payload:
        fixtures.append(
            (
                FilingMetadata(
                    accession_number=item["accession_number"],
                    form_type=item["form_type"],
                    filed_at=__import__("datetime").datetime.fromisoformat(item["filed_at"]),
                    source_url=item["source_url"],
                    xml_url=item["xml_url"],
                ),
                (manifest_path.parent / item["fixture_path"]).resolve(),
            )
        )
    return fixtures


def load_proxy_fixture_manifest(path: str) -> list[tuple[ProxyFilingMetadata, Path]]:
    manifest_path = Path(path)
    if not manifest_path.exists():
        return []
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    fixtures: list[tuple[ProxyFilingMetadata, Path]] = []
    for item in payload:
        fixtures.append(
            (
                ProxyFilingMetadata(
                    accession_number=item["accession_number"],
                    form_type=item["form_type"],
                    filed_at=__import__("datetime").datetime.fromisoformat(item["filed_at"]),
                    source_url=item["source_url"],
                    document_url=item["document_url"],
                    issuer_cik=item["issuer_cik"],
                    issuer_name=item["issuer_name"],
                    fiscal_year=item.get("fiscal_year"),
                ),
                (manifest_path.parent / item["fixture_path"]).resolve(),
            )
        )
    return fixtures
