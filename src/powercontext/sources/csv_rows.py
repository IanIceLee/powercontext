# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Row-level CSV Sources: one captured Source per table record.

Each row of an offline CSV document becomes its own ``CAPTURED`` Source so that a
retrieval citation can be traced back to one exact record. The row identity is
derived from the document identity and the primary-key value only, so reordering
records or editing non-key columns keeps the identity stable.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from urllib.parse import quote

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

CSV_ROW_SOURCE_NAME = "csv-row"
CSV_ROW_SOURCE_VERSION = "1"
CSV_ROW_SOURCE_TYPE = "csv-row"


@dataclass(frozen=True, slots=True)
class CSVDocumentInput:
    """One complete offline CSV document awaiting row extraction."""

    document_id: str
    primary_key: str
    content: bytes


@dataclass(frozen=True, slots=True)
class CSVRowInput:
    """One parsed row, preserving original column order and raw string values."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    """Durable captured Source describing exactly one CSV record."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @property
    def source_id(self) -> str:
        """Return the row identity, which is also the Source name."""

        return self.name

    def row_mapping(self) -> dict[str, str]:
        """Return the record as an ordered column-to-value mapping."""

        return dict(zip(self.columns, self.values, strict=True))


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Build the stable row identity for one document and primary-key value.

    Both components are percent-encoded with an empty safe set so the ``:``
    separator can never collide with component content.
    """

    _require_document_id(document_id)
    if not isinstance(primary_key_value, str):
        raise ValueError("primary_key_value must be a string")
    identity = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"csv row identity must not exceed {MAX_SOURCE_ID_LENGTH} characters")
    return identity


def parse_csv_document(document: CSVDocumentInput, /) -> tuple[CSVRowInput, ...]:
    """Parse a complete offline CSV document into row inputs.

    Parsing is atomic: any encoding, CSV or data validation error raises
    ``ValueError`` and no partial result is returned.
    """

    _require_document_id(document.document_id)
    primary_key = document.primary_key
    if not isinstance(primary_key, str) or not primary_key:
        raise ValueError("primary_key must be a non-empty string")
    if not isinstance(document.content, (bytes, bytearray)):
        raise ValueError("content must be bytes")
    raw = bytes(document.content)
    if not raw:
        raise ValueError("csv document is empty")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("csv document is not valid utf-8") from error

    try:
        records = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as error:
        raise ValueError(f"malformed csv document: {error}") from error

    if not records:
        raise ValueError("csv document declares no header record")

    header = records[0]
    if not header:
        raise ValueError("csv header must declare at least one column")
    seen: set[str] = set()
    for column in header:
        if not column or not column.strip():
            raise ValueError("csv header columns must be non-empty and not blank")
        if column in seen:
            raise ValueError(f"duplicate csv header column: {column!r}")
        seen.add(column)
    if primary_key not in seen:
        raise ValueError(f"primary_key {primary_key!r} is not a declared header column")
    key_index = header.index(primary_key)
    columns = tuple(header)

    rows: list[CSVRowInput] = []
    key_values: set[str] = set()
    for record in records[1:]:
        if not record:
            continue
        if len(record) != len(columns):
            raise ValueError(
                f"csv record has {len(record)} fields, expected {len(columns)}",
            )
        key_value = record[key_index]
        if not key_value or not key_value.strip():
            raise ValueError("csv primary key value must be non-empty and not blank")
        if key_value in key_values:
            raise ValueError(f"duplicate csv primary key value: {key_value!r}")
        key_values.add(key_value)
        rows.append(
            CSVRowInput(
                document_id=document.document_id,
                primary_key=primary_key,
                primary_key_value=key_value,
                columns=columns,
                values=tuple(record),
            )
        )
    return tuple(rows)


class _CSVRowAdapter:
    """Resolve row inputs into Sources and read them back without loss."""

    input_class = CSVRowInput
    source_class = CSVRowSource
    name = CSV_ROW_SOURCE_NAME

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        return CSVRowSource(
            name=csv_row_source_id(value.document_id, value.primary_key_value),
            definition_version=CSV_ROW_SOURCE_VERSION,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class _CSVRowTextEvidenceProjection:
    """Project one row as the standard text-evidence payload."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = source.row_mapping()
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_TYPE,
            source_id=source.name,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


CSV_ROW_SOURCE_DEFINITION = AdapterSourceDefinition(
    adapter=_CSVRowAdapter(),
    version=CSV_ROW_SOURCE_VERSION,
    projections=(_CSVRowTextEvidenceProjection(),),
)


def _require_document_id(document_id: str) -> None:
    if not isinstance(document_id, str):
        raise ValueError("document_id must be a string")
    if not document_id or not document_id.strip():
        raise ValueError("document_id must be a non-empty string")
    if document_id != document_id.strip():
        raise ValueError("document_id must not contain leading or trailing whitespace")


__all__ = [
    "CSV_ROW_SOURCE_DEFINITION",
    "CSV_ROW_SOURCE_NAME",
    "CSV_ROW_SOURCE_TYPE",
    "CSV_ROW_SOURCE_VERSION",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "csv_row_source_id",
    "parse_csv_document",
]
