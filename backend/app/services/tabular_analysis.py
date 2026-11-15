"""Bounded, deterministic descriptive analysis for CSV and XLSX uploads."""
from __future__ import annotations

import csv
import io
import math
import time
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import ParseError

MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_WORKBOOK_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_ZIP_ENTRIES = 4096
MAX_SHEETS = 20
MAX_ROWS_PER_SHEET = 10_000
MAX_TOTAL_ROWS = 20_000
MAX_COLUMNS = 256
MAX_TOTAL_CELLS = 1_000_000
MAX_CELL_CHARS = 32_767
MAX_PROCESSING_SECONDS = 3.0


class TabularAnalysisError(ValueError):
    """A stable, user-safe analysis error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def analyze_tabular_data(content: bytes, filename: str) -> dict[str, Any]:
    """Return bounded descriptive statistics without retaining cell contents."""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in {".csv", ".xlsx"}:
        raise TabularAnalysisError(
            "UNSUPPORTED_FORMAT", "Only CSV and XLSX files can be analyzed."
        )
    if not isinstance(content, bytes) or not content:
        raise TabularAnalysisError("EMPTY_FILE", "The analysis input is empty.")
    if len(content) > MAX_INPUT_BYTES:
        raise TabularAnalysisError(
            "FILE_TOO_LARGE", "The analysis input exceeds the 10 MiB limit."
        )

    deadline = time.monotonic() + MAX_PROCESSING_SECONDS
    if suffix == ".csv":
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise TabularAnalysisError(
                "INVALID_CSV", "CSV data must be valid UTF-8."
            ) from None
        try:
            sheet = _analyze_rows(
                "CSV",
                csv.reader(io.StringIO(text, newline=""), strict=True),
                deadline,
            )
        except csv.Error:
            raise TabularAnalysisError(
                "INVALID_CSV", "CSV data is malformed or exceeds parser limits."
            ) from None
        return {"format": "csv", "sheets": [sheet]}

    sheets: dict[str, _ColumnStats] = {}
    sheet_rows: dict[str, int] = {}
    try:
        for sheet_name, row_number, row in iter_bounded_xlsx_rows(content, deadline):
            _check_deadline(deadline)
            if row_number == 1:
                sheet = _new_sheet(sheet_name, row)
                sheets[sheet_name] = sheet
                sheet_rows[sheet_name] = 0
                continue
            sheet_rows[sheet_name] += 1
            _accumulate_row(sheets[sheet_name], row, sheet_name)
        if not sheets:
            raise TabularAnalysisError(
                "EMPTY_DATA", "The workbook contains no worksheets."
            )
    except TabularAnalysisError:
        raise

    result = []
    for name, stats in sheets.items():
        result.append(_finish_sheet(name, sheet_rows[name], stats))
    return {"format": "xlsx", "sheets": result}


class _ColumnStats:
    def __init__(self, headers: list[str]) -> None:
        self.headers = headers
        self.numeric_count = [0] * len(headers)
        self.missing_count = [0] * len(headers)
        self.non_numeric_count = [0] * len(headers)
        self.minimum: list[float | None] = [None] * len(headers)
        self.maximum: list[float | None] = [None] * len(headers)
        self.scale = [0.0] * len(headers)
        self.scaled_sum = [0.0] * len(headers)
        self.scaled_squares = [0.0] * len(headers)


def _analyze_rows(
    name: str, rows: Iterable[list[Any]], deadline: float
) -> dict[str, Any]:
    iterator = iter(rows)
    try:
        header = next(iterator)
    except StopIteration:
        raise TabularAnalysisError(
            "EMPTY_DATA", "The tabular file has no header row."
        ) from None
    stats = _new_sheet(name, header)
    count = 0
    total_cells = 0
    for row in iterator:
        _check_deadline(deadline)
        count += 1
        if count > MAX_ROWS_PER_SHEET or count > MAX_TOTAL_ROWS:
            raise TabularAnalysisError(
                "ROW_LIMIT", "The tabular file exceeds the row limit."
            )
        total_cells += len(row)
        if total_cells > MAX_TOTAL_CELLS:
            raise TabularAnalysisError(
                "CELL_LIMIT", "The tabular file exceeds the total cell limit."
            )
        _accumulate_row(stats, row, name)
    _check_deadline(deadline)
    return _finish_sheet(name, count, stats)


def _new_sheet(name: str, header: Iterable[Any]) -> _ColumnStats:
    cells = list(header)
    if len(cells) > MAX_COLUMNS:
        raise TabularAnalysisError(
            "COLUMN_LIMIT", "A worksheet exceeds the column limit."
        )
    if not cells:
        raise TabularAnalysisError("EMPTY_DATA", "A worksheet has no header row.")
    labels: list[str] = []
    for index, value in enumerate(cells, start=1):
        label = "" if value is None else str(value).strip()
        if len(label) > MAX_CELL_CHARS:
            raise TabularAnalysisError(
                "CELL_LIMIT", "A worksheet cell exceeds the cell size limit."
            )
        labels.append(label or f"column_{index}")
    return _ColumnStats(labels)


def _accumulate_row(stats: _ColumnStats, row: Iterable[Any], sheet_name: str) -> None:
    values = list(row)
    if len(values) > len(stats.headers):
        raise TabularAnalysisError(
            "INVALID_DATA", f"Worksheet '{sheet_name}' has more cells than its header."
        )
    for index, value in enumerate(values):
        if isinstance(value, str) and len(value) > MAX_CELL_CHARS:
            raise TabularAnalysisError(
                "CELL_LIMIT", "A worksheet cell exceeds the cell size limit."
            )
        if value is None or (isinstance(value, str) and not value.strip()):
            stats.missing_count[index] += 1
            continue
        number = _finite_number(value)
        if number is None:
            stats.non_numeric_count[index] += 1
            continue

        old_scale = stats.scale[index]
        new_scale = max(old_scale, abs(number))
        if new_scale != old_scale:
            factor = old_scale / new_scale if new_scale else 0.0
            stats.scaled_sum[index] *= factor
            stats.scaled_squares[index] *= factor * factor
            stats.scale[index] = new_scale
        scaled = number / new_scale if new_scale else 0.0
        stats.scaled_sum[index] += scaled
        stats.scaled_squares[index] += scaled * scaled
        stats.numeric_count[index] += 1
        current_min = stats.minimum[index]
        current_max = stats.maximum[index]
        stats.minimum[index] = number if current_min is None else min(current_min, number)
        stats.maximum[index] = number if current_max is None else max(current_max, number)
    for index in range(len(values), len(stats.headers)):
        stats.missing_count[index] += 1


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except OverflowError:
            return None
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _finish_sheet(name: str, rows: int, stats: _ColumnStats) -> dict[str, Any]:
    columns = []
    for index, label in enumerate(stats.headers):
        count = stats.numeric_count[index]
        scale = stats.scale[index]
        if not count:
            mean = standard_deviation = None
        elif not scale:
            mean = standard_deviation = 0.0
        else:
            normalized_mean = max(-1.0, min(1.0, stats.scaled_sum[index] / count))
            variance = max(
                0.0,
                stats.scaled_squares[index] / count - normalized_mean * normalized_mean,
            )
            normalized_std = min(1.0, math.sqrt(variance))
            mean = _finite_or_none(normalized_mean * scale)
            standard_deviation = _finite_or_none(normalized_std * scale)
        columns.append(
            {
                "index": index,
                "name": label,
                "numeric_count": count,
                "missing_count": stats.missing_count[index],
                "non_numeric_count": stats.non_numeric_count[index],
                "min": stats.minimum[index],
                "max": stats.maximum[index],
                "mean": mean,
                "population_stddev": standard_deviation,
            }
        )
    return {"name": name, "rows": rows, "columns": columns}


def _finite_or_none(value: float) -> float | None:
    return value if math.isfinite(value) else None


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise TabularAnalysisError(
            "TIME_LIMIT", "Tabular analysis exceeded the 3 second time limit."
        )


def iter_bounded_xlsx_rows(
    content: bytes,
    deadline: float,
    *,
    max_input_bytes: int = MAX_INPUT_BYTES,
) -> Iterator[tuple[str, int, tuple[Any, ...]]]:
    if len(content) > max_input_bytes:
        raise TabularAnalysisError(
            "FILE_TOO_LARGE", "The workbook exceeds the input size limit."
        )
    _validate_xlsx_container(content, deadline)
    try:
        import openpyxl
    except ImportError:
        raise TabularAnalysisError(
            "DEPENDENCY_MISSING", "XLSX analysis requires the openpyxl package."
        ) from None
    parse_errors = _xlsx_parse_errors()

    try:
        workbook = openpyxl.load_workbook(
            io.BytesIO(content), read_only=True, data_only=True, keep_links=False
        )
    except TabularAnalysisError:
        raise
    except parse_errors:
        raise TabularAnalysisError(
            "INVALID_WORKBOOK", "XLSX data is invalid or exceeds parser limits."
        ) from None
    try:
        if len(workbook.worksheets) > MAX_SHEETS:
            raise TabularAnalysisError(
                "SHEET_LIMIT", "The workbook exceeds the worksheet limit."
            )
        total_rows = 0
        total_cells = 0
        for worksheet in workbook.worksheets:
            _check_deadline(deadline)
            if (
                worksheet.max_row is None
                or worksheet.max_column is None
                or worksheet.max_row > MAX_ROWS_PER_SHEET + 1
                or worksheet.max_column > MAX_COLUMNS
            ):
                code = (
                    "ROW_LIMIT"
                    if worksheet.max_row is not None
                    and worksheet.max_row > MAX_ROWS_PER_SHEET + 1
                    else "COLUMN_LIMIT"
                )
                raise TabularAnalysisError(
                    code,
                    "A worksheet exceeds the row or column limit.",
                )
            worksheet.reset_dimensions()
            for row_number, row in enumerate(
                worksheet.iter_rows(values_only=True), start=1
            ):
                _check_deadline(deadline)
                if row_number > MAX_ROWS_PER_SHEET + 1:
                    raise TabularAnalysisError(
                        "ROW_LIMIT", "A worksheet exceeds the row limit."
                    )
                if row_number > 1:
                    total_rows += 1
                if total_rows > MAX_TOTAL_ROWS:
                    raise TabularAnalysisError(
                        "ROW_LIMIT", "The workbook exceeds the total row limit."
                    )
                if len(row) > MAX_COLUMNS:
                    raise TabularAnalysisError(
                        "COLUMN_LIMIT", "A worksheet exceeds the column limit."
                    )
                total_cells += len(row)
                if total_cells > MAX_TOTAL_CELLS:
                    raise TabularAnalysisError(
                        "CELL_LIMIT", "The workbook exceeds the total cell limit."
                    )
                yield worksheet.title, row_number, row
    except TabularAnalysisError:
        raise
    except parse_errors:
        raise TabularAnalysisError(
            "INVALID_WORKBOOK", "XLSX data is invalid or exceeds parser limits."
        ) from None
    finally:
        workbook.close()


def _validate_xlsx_container(content: bytes, deadline: float) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ZIP_ENTRIES:
                raise TabularAnalysisError(
                    "WORKBOOK_SIZE_LIMIT", "The workbook exceeds the archive entry limit."
                )
            total_size = 0
            for entry in entries:
                _check_deadline(deadline)
                if entry.flag_bits & 0x1:
                    raise TabularAnalysisError(
                        "INVALID_WORKBOOK", "Encrypted workbooks are not supported."
                    )
                total_size += entry.file_size
                if total_size > MAX_WORKBOOK_UNCOMPRESSED_BYTES:
                    raise TabularAnalysisError(
                        "WORKBOOK_SIZE_LIMIT",
                        "The workbook exceeds the uncompressed size limit.",
                    )
    except TabularAnalysisError:
        raise
    except (OSError, EOFError, zipfile.BadZipFile, zipfile.LargeZipFile):
        raise TabularAnalysisError(
            "INVALID_WORKBOOK", "XLSX data is invalid or exceeds parser limits."
        ) from None


def _xlsx_parse_errors() -> tuple[type[BaseException], ...]:
    errors: list[type[BaseException]] = [
        OSError,
        EOFError,
        KeyError,
        ValueError,
        TypeError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        ParseError,
    ]
    from openpyxl.utils.exceptions import InvalidFileException

    errors.append(InvalidFileException)
    try:
        from lxml.etree import XMLSyntaxError
    except ImportError:
        pass
    else:
        errors.append(XMLSyntaxError)
    return tuple(errors)
