from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import openpyxl
import pytest

from app.services import tabular_analysis as analysis


def _xlsx(sheets: dict[str, list[list[object]]]) -> bytes:
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(row)
    content = BytesIO()
    workbook.save(content)
    workbook.close()
    return content.getvalue()


def _column(result: dict, sheet_index: int, column_index: int) -> dict:
    return result["sheets"][sheet_index]["columns"][column_index]


def _lie_about_first_sheet_dimension(content: bytes, old: bytes) -> bytes:
    source = ZipFile(BytesIO(content))
    result = BytesIO()
    with source, ZipFile(result, "w", ZIP_DEFLATED) as target:
        for entry in source.infolist():
            data = source.read(entry.filename)
            if entry.filename == "xl/worksheets/sheet1.xml":
                data = data.replace(old, b'ref="A1"')
            target.writestr(entry, data)
    return result.getvalue()


def test_csv_returns_finite_descriptive_stats_without_cell_values() -> None:
    content = (
        b"amount,note,formula\r\n"
        b"1,ok,=__import__('os').system('echo unsafe')\r\n"
        b"3,,not-a-number\r\n"
        b"NaN,last\r\n"
    )

    result = analysis.analyze_tabular_data(content, "sample.csv")

    assert result["format"] == "csv"
    assert result["sheets"][0]["rows"] == 3
    amount = _column(result, 0, 0)
    assert amount == {
        "index": 0,
        "name": "amount",
        "numeric_count": 2,
        "missing_count": 0,
        "non_numeric_count": 1,
        "min": 1.0,
        "max": 3.0,
        "mean": 2.0,
        "population_stddev": pytest.approx(1.0),
    }
    assert _column(result, 0, 1)["missing_count"] == 1
    assert _column(result, 0, 2)["non_numeric_count"] == 2
    assert "=__import__" not in repr(result)


def test_csv_short_rows_count_trailing_columns_as_missing() -> None:
    result = analysis.analyze_tabular_data(b"a,b\n1\n", "sample.csv")

    assert _column(result, 0, 1)["missing_count"] == 1


def test_csv_statistics_remain_finite_for_extreme_finite_values() -> None:
    result = analysis.analyze_tabular_data(
        b"value\n1.7e308\n-1.7e308\n", "extreme.csv"
    )

    column = _column(result, 0, 0)
    assert column["mean"] == 0.0
    assert column["population_stddev"] == pytest.approx(1.7e308)
    assert all(
        value is None or (isinstance(value, (int, float)) and abs(value) != float("inf"))
        for value in (column["min"], column["max"], column["mean"], column["population_stddev"])
    )


def test_xlsx_uses_cached_values_and_analyzes_each_sheet() -> None:
    content = _xlsx(
        {
            "Sales": [["amount", "memo"], [1, "one"], [3, "=1+2"]],
            "Other": [["score"], [4], [6]],
        }
    )

    result = analysis.analyze_tabular_data(content, "sample.xlsx")

    assert result["format"] == "xlsx"
    assert [sheet["name"] for sheet in result["sheets"]] == ["Sales", "Other"]
    assert _column(result, 0, 0)["mean"] == 2.0
    assert _column(result, 0, 1)["missing_count"] == 1
    assert _column(result, 1, 0)["mean"] == 5.0
    assert "=1+2" not in repr(result)


@pytest.mark.parametrize(
    ("filename", "content", "code"),
    [
        ("bad.json", b"{}", "UNSUPPORTED_FORMAT"),
        ("bad.csv", b'a\n"unterminated', "INVALID_CSV"),
        ("bad.xlsx", b"not a workbook", "INVALID_WORKBOOK"),
    ],
)
def test_format_errors_are_stable(filename: str, content: bytes, code: str) -> None:
    with pytest.raises(analysis.TabularAnalysisError) as error:
        analysis.analyze_tabular_data(content, filename)

    assert error.value.code == code


def test_empty_and_oversized_inputs_have_stable_errors(monkeypatch) -> None:
    with pytest.raises(analysis.TabularAnalysisError) as empty_error:
        analysis.analyze_tabular_data(b"", "empty.csv")
    assert empty_error.value.code == "EMPTY_FILE"

    monkeypatch.setattr(analysis, "MAX_INPUT_BYTES", 2)
    with pytest.raises(analysis.TabularAnalysisError) as large_error:
        analysis.analyze_tabular_data(b"a\n1", "large.csv")
    assert large_error.value.code == "FILE_TOO_LARGE"


def test_csv_row_and_column_limits_are_enforced(monkeypatch) -> None:
    monkeypatch.setattr(analysis, "MAX_ROWS_PER_SHEET", 1)
    with pytest.raises(analysis.TabularAnalysisError) as row_error:
        analysis.analyze_tabular_data(b"a\n1\n2\n", "rows.csv")
    assert row_error.value.code == "ROW_LIMIT"

    monkeypatch.setattr(analysis, "MAX_ROWS_PER_SHEET", 10)
    monkeypatch.setattr(analysis, "MAX_COLUMNS", 1)
    with pytest.raises(analysis.TabularAnalysisError) as column_error:
        analysis.analyze_tabular_data(b"a,b\n", "columns.csv")
    assert column_error.value.code == "COLUMN_LIMIT"


def test_xlsx_sheet_and_expanded_size_limits_are_enforced(monkeypatch) -> None:
    workbook = _xlsx({"One": [["a"], [1]], "Two": [["b"], [2]]})
    monkeypatch.setattr(analysis, "MAX_SHEETS", 1)
    with pytest.raises(analysis.TabularAnalysisError) as sheet_error:
        analysis.analyze_tabular_data(workbook, "sheets.xlsx")
    assert sheet_error.value.code == "SHEET_LIMIT"

    monkeypatch.setattr(analysis, "MAX_SHEETS", 20)
    monkeypatch.setattr(analysis, "MAX_WORKBOOK_UNCOMPRESSED_BYTES", 1)
    with pytest.raises(analysis.TabularAnalysisError) as size_error:
        analysis.analyze_tabular_data(workbook, "expanded.xlsx")
    assert size_error.value.code == "WORKBOOK_SIZE_LIMIT"


def test_xlsx_row_and_column_limits_are_enforced(monkeypatch) -> None:
    workbook = _xlsx({"Data": [["a"], [1], [2]]})
    monkeypatch.setattr(analysis, "MAX_ROWS_PER_SHEET", 1)
    with pytest.raises(analysis.TabularAnalysisError) as row_error:
        analysis.analyze_tabular_data(workbook, "rows.xlsx")
    assert row_error.value.code == "ROW_LIMIT"

    workbook = _xlsx({"Data": [["a", "b"], [1, 2]]})
    monkeypatch.setattr(analysis, "MAX_ROWS_PER_SHEET", 10)
    monkeypatch.setattr(analysis, "MAX_COLUMNS", 1)
    with pytest.raises(analysis.TabularAnalysisError) as column_error:
        analysis.analyze_tabular_data(workbook, "columns.xlsx")
    assert column_error.value.code == "COLUMN_LIMIT"


def test_xlsx_actual_cells_are_bounded_despite_underreported_dimensions() -> None:
    row_workbook = _xlsx({"Data": [["a"], [1], [2]]})
    row_workbook = _lie_about_first_sheet_dimension(
        row_workbook, b'ref="A1:A3"'
    )
    row_result = analysis.analyze_tabular_data(
        row_workbook, "underreported-rows.xlsx"
    )
    assert row_result["sheets"][0]["rows"] == 2

    column_workbook = _xlsx({"Data": [["header"]]})
    source = ZipFile(BytesIO(column_workbook))
    result = BytesIO()
    with source, ZipFile(result, "w", ZIP_DEFLATED) as target:
        for entry in source.infolist():
            data = source.read(entry.filename)
            if entry.filename == "xl/worksheets/sheet1.xml":
                data = data.replace(b'ref="A1"', b'ref="A1:JA1"')
                data = data.replace(b'<c r="A1"', b'<c r="JA1"')
                data = data.replace(b'<v>header</v>', b'<v>too-far</v>')
            target.writestr(entry, data)
    with pytest.raises(analysis.TabularAnalysisError) as column_error:
        analysis.analyze_tabular_data(result.getvalue(), "underreported-columns.xlsx")
    assert column_error.value.code == "COLUMN_LIMIT"


def test_xlsx_cell_limit_includes_header_cells(monkeypatch) -> None:
    workbook = _xlsx({"Data": [["a"], [1]]})
    monkeypatch.setattr(analysis, "MAX_TOTAL_CELLS", 1)

    with pytest.raises(analysis.TabularAnalysisError) as error:
        analysis.analyze_tabular_data(workbook, "cells.xlsx")

    assert error.value.code == "CELL_LIMIT"


def test_processing_deadline_is_enforced(monkeypatch) -> None:
    monkeypatch.setattr(analysis, "MAX_PROCESSING_SECONDS", -1)
    with pytest.raises(analysis.TabularAnalysisError) as error:
        analysis.analyze_tabular_data(b"a\n1\n", "slow.csv")

    assert error.value.code == "TIME_LIMIT"
