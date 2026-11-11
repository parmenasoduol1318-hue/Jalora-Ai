from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.services.tool_preflight import validate_tool_arguments
from app.tools.base import PermissionLevel, ToolSpec


class _Arguments(BaseModel):
    count: int


class _ArgumentsWithDefault(_Arguments):
    label: str = ""


def _tool_spec(args_schema: type[BaseModel] | None) -> ToolSpec:
    def run(count: int = 0) -> int:
        return count

    tool = StructuredTool.from_function(
        run,
        name="preflight_test",
        description="Test argument validation.",
        args_schema=args_schema,
        infer_schema=args_schema is not None,
    )
    return ToolSpec(
        name="preflight_test",
        tool=tool,
        permission=PermissionLevel.AUTO,
        category="test",
    )


def test_validate_tool_arguments_normalizes_schema_values():
    normalized, errors = validate_tool_arguments(
        _tool_spec(_Arguments),
        {"count": "4"},
    )

    assert normalized == {"count": 4}
    assert errors is None


def test_validate_tool_arguments_does_not_add_omitted_defaults():
    normalized, errors = validate_tool_arguments(
        _tool_spec(_ArgumentsWithDefault),
        {"count": "4"},
    )

    assert normalized == {"count": 4}
    assert errors is None


def test_graph_tool_preflight_returns_normalized_arguments():
    from app.agents.graph import _validate_tool_arguments

    normalized, error = _validate_tool_arguments(
        _tool_spec(_Arguments),
        {"count": "4"},
    )

    assert error is None
    assert normalized == {"count": 4}


def test_validate_tool_arguments_returns_schema_errors_without_raising():
    original = {"count": "not-an-integer"}
    normalized, errors = validate_tool_arguments(_tool_spec(_Arguments), original)

    assert normalized is original
    assert errors


def test_validate_tool_arguments_preserves_tools_without_schema():
    original = {"value": "unchanged"}
    normalized, errors = validate_tool_arguments(_tool_spec(None), original)

    assert normalized is original
    assert errors is None
