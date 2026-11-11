"""Tests for form completion tools.

Run with: python -m pytest backend/tests/test_form_completion.py -v
"""
import pytest

from app.services.form_filling import (
    FormCompletionService,
    get_form_service,
    FormField,
    FormValidationResult,
    AutoFillResult,
)


# ============================================================================
# Form Completion Service Tests
# ============================================================================

def test_form_service_creation():
    """Test form service can be created."""
    service = FormCompletionService()
    assert service is not None


def test_get_form_service():
    """Test singleton pattern."""
    service1 = get_form_service()
    service2 = get_form_service()
    assert service1 is service2


# ============================================================================
# Field Validation Tests
# ============================================================================

def test_validate_email_field():
    """Test email field validation."""
    service = get_form_service()
    
    field = FormField(name="email", field_type="email", required=True)
    
    # Valid email
    is_valid, error = service.validate_field(field, "test@example.com")
    assert is_valid == True
    assert error is None
    
    # Invalid email
    is_valid, error = service.validate_field(field, "invalid-email")
    assert is_valid == False
    assert "email" in error.lower()


def test_validate_phone_field():
    """Test phone field validation."""
    service = get_form_service()
    
    field = FormField(name="phone", field_type="phone")
    
    # Valid phone
    is_valid, error = service.validate_field(field, "+1-555-123-4567")
    assert is_valid == True
    
    # Invalid phone
    is_valid, error = service.validate_field(field, "abc123")
    assert is_valid == False


def test_validate_required_field():
    """Test required field validation."""
    service = get_form_service()
    
    field = FormField(name="name", required=True)
    
    # Empty required field
    is_valid, error = service.validate_field(field, "")
    assert is_valid == False
    assert "required" in error.lower()


# ============================================================================
# Form Validation Tests
# ============================================================================

def test_validate_complete_form():
    """Test validating a complete valid form."""
    service = get_form_service()
    
    fields = [
        FormField(name="name", required=True),
        FormField(name="email", field_type="email", required=True),
    ]
    values = {
        "name": "John Doe",
        "email": "john@example.com"
    }
    
    result = service.validate_form(fields, values)
    
    assert result.is_valid == True
    assert len(result.field_errors) == 0


def test_validate_incomplete_form():
    """Test validating an incomplete form."""
    service = get_form_service()
    
    fields = [
        FormField(name="name", required=True),
        FormField(name="email", field_type="email", required=True),
    ]
    values = {
        "name": "",  # Missing required
        "email": "invalid"  # Invalid format
    }
    
    result = service.validate_form(fields, values)
    
    assert result.is_valid == False
    assert len(result.missing_required) > 0


# ============================================================================
# Auto-Fill Tests
# ====================

def test_auto_fill_basic():
    """Test basic auto-fill functionality."""
    service = get_form_service()
    
    fields = [
        FormField(name="email"),
        FormField(name="name"),
    ]
    user_data = {
        "email": "alice@example.com",
        "name": "Alice"
    }
    
    result = service.auto_fill_form(fields, user_data)
    
    assert len(result.filled_fields) == 2
    assert result.filled_fields["email"] == "alice@example.com"
    assert result.filled_fields["name"] == "Alice"


def test_auto_fill_missing_fields():
    """Test auto-fill identifies missing fields."""
    service = get_form_service()
    
    fields = [
        FormField(name="email"),
        FormField(name="phone"),
    ]
    user_data = {
        "email": "bob@example.com"
        # phone is missing
    }
    
    result = service.auto_fill_form(fields, user_data)
    
    assert result.filled_fields["email"] == "bob@example.com"
    assert "phone" in result.missing_fields


def test_auto_fill_confidence():
    """Test auto-fill confidence scores."""
    service = get_form_service()
    
    fields = [FormField(name="email")]
    user_data = {"email": "test@example.com"}
    
    result = service.auto_fill_form(fields, user_data)
    
    assert "email" in result.confidence_scores
    assert result.confidence_scores["email"] > 0


# ============================================================================
# Dataclass Tests
# ====================

def test_form_field_defaults():
    """Test FormField default values."""
    field = FormField(name="test")
    
    assert field.name == "test"
    assert field.field_type == "text"
    assert field.required == False
    assert field.default is None


def test_validation_result_defaults():
    """Test FormValidationResult default values."""
    result = FormValidationResult(is_valid=True)
    
    assert result.is_valid == True
    assert result.field_errors == {}
    assert result.missing_required == []


def test_autofill_result_defaults():
    """Test AutoFillResult default values."""
    result = AutoFillResult(
        filled_fields={},
        confidence_scores={},
        missing_fields=["test"],
        suggestions={}
    )
    
    assert result.missing_fields == ["test"]


# ============================================================================
# Edge Cases
# ====================

def test_validate_empty_form():
    """Test validating empty form."""
    service = get_form_service()
    
    result = service.validate_form([], {})
    
    assert result.is_valid == True


def test_auto_fill_empty_data():
    """Test auto-fill with empty user data."""
    service = get_form_service()
    
    fields = [FormField(name="email", required=True)]
    result = service.auto_fill_form(fields, {})
    
    assert len(result.missing_fields) == 1
    assert "email" in result.missing_fields


def test_optional_field_not_required():
    """Test that optional fields don't fail validation when empty."""
    service = get_form_service()
    
    field = FormField(name="optional", required=False)
    
    is_valid, error = service.validate_field(field, "")
    assert is_valid == True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])