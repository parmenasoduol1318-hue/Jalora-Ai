"""Form completion tools (spec §6).

Tools for form validation, auto-fill, and submission assistance.
"""
from __future__ import annotations

from app.context import get_tool_context
from app.errors import ToolResult
from app.tools.base import PermissionLevel, registry
from app.services.form_filling import get_form_service, FormField, FormValidationResult, AutoFillResult


def _user_id() -> str:
    return get_tool_context().user_id


# ============================================================================
# Form Validation Tools
# ============================================================================

async def validate_form(fields: list[dict], values: dict) -> ToolResult:
    """Validate a form's field values.
    
    Args:
        fields: List of field definitions {name, type, required, ...}
        values: Dictionary of field values
        
    Returns:
        ToolResult with validation result
    """
    from app.services.form_filling import FormCompletionService
    
    service = get_form_service()
    form_fields = [
        FormField(
            name=f["name"],
            field_type=f.get("type", "text"),
            required=f.get("required", False),
            default=f.get("default"),
            options=f.get("options", []),
            validation_rules=f.get("validation", []),
            description=f.get("description", "")
        )
        for f in fields
    ]
    
    result = service.validate_form(form_fields, values)
    
    return ToolResult.ok(
        f"Form validation: {'PASSED' if result.is_valid else 'FAILED'}",
        {
            "is_valid": result.is_valid,
            "errors": result.field_errors,
            "missing_required": result.missing_required,
            "warnings": result.warnings
        }
    )


async def auto_fill_form(fields: list[dict], user_data: dict) -> ToolResult:
    """Auto-fill a form from user data.
    
    Args:
        fields: List of field definitions
        user_data: User data to fill from
        
    Returns:
        ToolResult with auto-filled values
    """
    from app.services.form_filling import FormCompletionService
    
    service = get_form_service()
    form_fields = [
        FormField(
            name=f["name"],
            field_type=f.get("type", "text"),
            required=f.get("required", False),
            default=f.get("default"),
            options=f.get("options", []),
            validation_rules=f.get("validation", []),
            description=f.get("description", "")
        )
        for f in fields
    ]
    
    result = service.auto_fill_form(form_fields, user_data)
    
    return ToolResult.ok(
        f"Auto-filled {len(result.filled_fields)} fields",
        {
            "filled_values": result.filled_fields,
            "confidence": result.confidence_scores,
            "missing_fields": result.missing_fields,
            "suggestions": result.suggestions
        }
    )


async def validate_field_type(field_name: str, value: str, field_type: str) -> ToolResult:
    """Validate that a value matches the expected field type.
    
    Args:
        field_name: Name of the field
        value: Value to validate
        field_type: Expected type (email, phone, number, url, etc.)
        
    Returns:
        ToolResult with validation result
    """
    import re
    
    validation_rules = {
        "email": r'^[^@]+@[^@]+\.[^@]+$',
        "phone": r'^[\d\s\-\+\(\)]+$',
        "url": r'^https?://',
        "number": r'^-?\d+\.?\d*$'
    }
    
    if field_type not in validation_rules:
        return ToolResult.fail("INVALID_TYPE", f"Unknown field type: {field_type}")
    
    pattern = validation_rules[field_type]
    is_valid = bool(re.match(pattern, str(value)))
    
    return ToolResult.ok(
        f"Field '{field_name}' type validation: {'PASSED' if is_valid else 'FAILED'}",
        {"is_valid": is_valid, "field_type": field_type}
    )


# ============================================================================
# Tool Registration
# ============================================================================

registry.register(validate_form, permission=PermissionLevel.AUTO,
                  category="forms", tags=["validation", "forms"])
registry.register(auto_fill_form, permission=PermissionLevel.AUTO,
                  category="forms", tags=["auto-fill", "forms"])
registry.register(validate_field_type, permission=PermissionLevel.AUTO,
                  category="forms", tags=["validation", "type-checking"])