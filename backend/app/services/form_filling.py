"""Form filling services (spec §6).

Provides form validation, auto-fill, and submission assistance.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from datetime import datetime, timezone

logger = logging.getLogger("jalora.forms")


# ============================================================================
# Form Field Types
# ============================================================================

FIELD_TYPES = ["text", "email", "phone", "number", "date", "textarea", 
               "select", "radio", "checkbox", "url", "password"]


@dataclass
class FormField:
    """Definition of a form field."""
    name: str
    field_type: str = "text"
    required: bool = False
    default: Any = None
    options: list[str] = field(default_factory=list)
    validation_rules: list[str] = field(default_factory=list)
    description: str = ""
    confidence: float = 1.0


@dataclass
class FormValidationResult:
    """Result of form validation."""
    is_valid: bool
    field_errors: dict[str, str] = field(default_factory=dict)
    missing_required: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class AutoFillResult:
    """Result of auto-fill operation."""
    filled_fields: dict[str, Any]
    confidence_scores: dict[str, float]
    missing_fields: list[str]
    suggestions: dict[str, list[Any]]


# ============================================================================
# Form Completion Service
# ============================================================================

class FormCompletionService:
    """Service for form completion and validation."""
    
    def __init__(self):
        self.confidence_threshold = 0.8
        self.validation_enabled = True
    
    def validate_field(self, field: FormField, value: Any) -> tuple[bool, str | None]:
        """Validate a single field value."""
        # Check if required and empty
        if field.required:
            if value is None or (isinstance(value, str) and not value.strip()):
                return False, f"Field '{field.name}' is required"
        
        # If not required and empty, it's valid
        if not field.required:
            if value is None or (isinstance(value, str) and not value.strip()):
                return True, None
        
        # Type validation (skip for empty values)
        if field.field_type == "email" and value:
            if not re.match(r'^[^@]+@[^@]+\.[^@]+$', str(value)):
                return False, f"Invalid email format for '{field.name}'"
        
        elif field.field_type == "phone" and value:
            if not re.match(r'^[\d\s\-\+\(\)]+$', str(value)):
                return False, f"Invalid phone format for '{field.name}'"
        
        elif field.field_type == "number" and value:
            try:
                float(value)
            except (ValueError, TypeError):
                return False, f"Invalid number for '{field.name}'"
        
        # Custom validation rules
        for rule in field.validation_rules:
            if rule == "url" and value:
                if not str(value).startswith(("http://", "https://")):
                    return False, f"Invalid URL for '{field.name}'"
        
        return True, None
    
    def auto_fill_field(self, field: FormField, user_data: dict, 
                       context: dict | None = None) -> tuple[Any, float, str | None]:
        """Attempt to auto-fill a field from user data."""
        # Try direct match by field name
        if field.name in user_data:
            return user_data[field.name], field.confidence, None
        
        # Try common variations
        variations = {
            "email": ["email", "email_address", "mail", "contact_email"],
            "name": ["name", "full_name", "first_name", "last_name", "user_name"],
            "phone": ["phone", "phone_number", "mobile", "contact_number"],
            "company": ["company", "organization", "org", "business_name"],
            "address": ["address", "street", "street_address", "city", "state", "zip", "postal_code"]
        }
        
        for var in variations.get(field.name, []):
            if var in user_data:
                return user_data[var], 0.9, f"Matched by variable: {var}"
        
        # Use default value
        if field.default is not None:
            return field.default, 0.7, "Using default value"
        
        return None, 0.0, "No value available"
    
    def validate_form(self, fields: list[FormField], 
                     values: dict[str, Any]) -> FormValidationResult:
        """Validate all form fields."""
        errors = {}
        missing = []
        warnings = []
        
        for field in fields:
            value = values.get(field.name)
            
            # Check required fields
            is_valid, error = self.validate_field(field, value)
            
            if not is_valid:
                # Track missing required fields (empty or None)
                if field.required and (value is None or (isinstance(value, str) and not value.strip())):
                    missing.append(field.name)
                errors[field.name] = error
            
            # Add warnings for low confidence
            if field.description and "warning" in field.description.lower():
                warnings.append(f"Field '{field.name}': {field.description}")
        
        return FormValidationResult(
            is_valid=len(errors) == 0,
            field_errors=errors,
            missing_required=missing,
            warnings=warnings
        )
    
    def auto_fill_form(self, fields: list[FormField], 
                      user_data: dict,
                      context: dict | None = None) -> AutoFillResult:
        """Auto-fill a form from user data."""
        filled = {}
        confidence = {}
        missing = []
        suggestions = {}
        
        for field in fields:
            value, score, suggestion = self.auto_fill_field(field, user_data, context)
            
            if value is not None:
                filled[field.name] = value
                confidence[field.name] = score
                
                if suggestion and score < self.confidence_threshold:
                    suggestions[field.name] = [value]
            else:
                missing.append(field.name)
                confidence[field.name] = 0.0
        
        return AutoFillResult(
            filled_fields=filled,
            confidence_scores=confidence,
            missing_fields=missing,
            suggestions=suggestions
        )
    
    def get_form_template(self, template_id: str, user_id: str) -> FormTemplate | None:
        """Retrieve a form template."""
        # Lazy import database components
        from sqlalchemy import select
        from app.database.session import get_sessionmaker
        from app.models.forms import FormTemplate
        
        db = get_sessionmaker()()
        try:
            return db.scalar(
                select(FormTemplate).where(
                    FormTemplate.id == template_id,
                    FormTemplate.user_id == user_id
                )
            )
        finally:
            db.close()


# Singleton instance
_form_service: FormCompletionService | None = None

def get_form_service() -> FormCompletionService:
    """Get or create the form completion service singleton."""
    global _form_service
    if _form_service is None:
        _form_service = FormCompletionService()
    return _form_service