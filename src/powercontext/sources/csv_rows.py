"""Captured, row-level evidence from offline CSV documents."""

from __future__ import annotations

import csv
import io
import json
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class CSVDocumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(min_length=1)
    primary_key: str = Field(min_length=1)
    content: bytes


class CSVRowInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    if not document_id or document_id != document_id.strip():
        raise ValueError("document_id must be non-empty and have no surrounding whitespace")
    identity = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError("CSV row source identity exceeds the source ID limit")
    return identity


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    if not value.document_id or value.document_id != value.document_id.strip():
        raise ValueError("document_id must be non-empty and have no surrounding whitespace")
    if not value.primary_key:
        raise ValueError("primary_key must be non-empty")
    try:
        text = value.content.decode("utf-8-sig", errors="strict")
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=",", quotechar='"', doublequote=True, strict=True)
        records = iter(reader)
        header = next(records)
        if not header or any(not col or not col.strip() for col in header) or len(set(header)) != len(header):
            raise ValueError("invalid CSV header")
        try:
            key_index = header.index(value.primary_key)
        except ValueError:
            raise ValueError("primary_key must exactly match a header") from None
        rows: list[CSVRowInput] = []
        seen: set[str] = set()
        for record in records:
            if not record:
                continue
            if len(record) != len(header):
                raise ValueError("CSV row width does not match header")
            key = record[key_index]
            if not key.strip() or key in seen:
                raise ValueError("CSV primary key is blank or duplicated")
            seen.add(key)
            csv_row_source_id(value.document_id, key)
            rows.append(CSVRowInput(document_id=value.document_id, primary_key=value.primary_key,
                                    primary_key_value=key, columns=tuple(header), values=tuple(record)))
        return tuple(rows)
    except (UnicodeDecodeError, csv.Error, StopIteration) as exc:
        raise ValueError("invalid CSV document") from exc


class _CSVRowAdapter:
    input_class = CSVRowInput
    name = "csv-row"
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        identity = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(name=identity, source_id=identity, document_id=value.document_id,
            primary_key=value.primary_key, primary_key_value=value.primary_key_value,
            columns=value.columns, values=value.values, materialization=SourceMaterialization.CAPTURED)

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(document_id=source.document_id, primary_key=source.primary_key,
            primary_key_value=source.primary_key_value, columns=source.columns, values=source.values)


class _CSVRowProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(source_type="csv-row", source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={"document_id": source.document_id, "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value, "columns": list(source.columns), "row": row})


CSV_ROW_SOURCE_DEFINITION = AdapterSourceDefinition(_CSVRowAdapter(), projections=(_CSVRowProjection(),))
