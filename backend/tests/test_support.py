"""Tests for customer support tools.

Run with: python -m pytest backend/tests/test_support.py -v
"""
import pytest

from app.services.support import (
    SupportAssistant,
    get_support_assistant,
    TicketCategory,
    TemplateVariables,
)


# ============================================================================
# Support Assistant Tests
# ============================================================================

def test_support_assistant_creation():
    """Test support assistant can be created."""
    assistant = SupportAssistant()
    assert assistant is not None


def test_get_support_assistant():
    """Test singleton pattern for support assistant."""
    assistant1 = get_support_assistant()
    assistant2 = get_support_assistant()
    
    assert assistant1 is assistant2


# ============================================================================
# Ticket Categorization Tests
# ============================================================================

def test_categorize_billing():
    """Test billing ticket categorization."""
    assistant = get_support_assistant()
    
    category, reason = assistant.categorize_ticket(
        "Billing issue with my invoice",
        "I was charged twice for my subscription"
    )
    
    assert category.name == "billing"


def test_categorize_technical():
    """Test technical ticket categorization."""
    assistant = get_support_assistant()
    
    category, reason = assistant.categorize_ticket(
        "App keeps crashing",
        "Error code 500 when I try to save"
    )
    
    assert category.name == "technical"


def test_categorize_feature():
    """Test feature request categorization."""
    assistant = get_support_assistant()
    
    category, reason = assistant.categorize_ticket(
        "Feature request: dark mode",
        "Would love to see a dark theme option in settings"
    )
    
    assert category.name == "feature"


def test_categorize_general():
    """Test general categorization."""
    assistant = get_support_assistant()
    
    category, reason = assistant.categorize_ticket(
        "Introduction",
        "Hello, I am introducing myself to the team"
    )
    
    assert category.name == "general"


# ============================================================================
# Priority Determination Tests
# ============================================================================

def test_priority_urgent():
    """Test urgent priority detection."""
    assistant = get_support_assistant()
    
    priority, reason = assistant.determine_priority("URGENT: System down!")
    
    assert priority == "p1"


def test_priority_normal():
    """Test normal priority detection."""
    assistant = get_support_assistant()
    
    priority, reason = assistant.determine_priority("Question about my account")
    
    assert priority == "normal"


def test_priority_premium_customer():
    """Test priority adjustment for premium customers."""
    assistant = get_support_assistant()
    
    # Standard customer gets normal for non-urgent
    p1, _ = assistant.determine_priority("General question", "premium")
    # Premium might get boost for non-urgent items
    assert p1 in ["p2", "p3", "normal"]


# ============================================================================
# Response Generation Tests
# ============================================================================

def test_generate_response():
    """Test response generation."""
    assistant = get_support_assistant()
    
    response = assistant.generate_response(
        "ticket_123",
        "technical",
        {"name": "John", "email": "john@example.com"}
    )
    
    assert "body" in response
    assert "subject" in response
    assert "ticket_123" in response["body"]


def test_apology_response():
    """Test apology response generation."""
    assistant = get_support_assistant()
    
    response = assistant.generate_response(
        "ticket_456",
        "technical",
        {"name": "Jane"},
        tone="apologetic"
    )
    
    assert "body" in response


# ============================================================================
# Search Tests
# ============================================================================

def test_search_faqs_empty():
    """Test FAQ search with empty results."""
    assistant = get_support_assistant()
    
    results = assistant.search_faq("nonexistent_query_xyz", limit=5)
    
    assert isinstance(results, list)


# ============================================================================
# Template Variables Tests
# ============================================================================

def test_template_variables_defaults():
    """Test template variable defaults."""
    vars = TemplateVariables()
    
    assert vars.ticket_id == ""
    assert vars.customer_name == ""
    assert vars.customer_email == ""
    assert vars.priority == "normal"


def test_template_variables_custom():
    """Test template variable customization."""
    vars = TemplateVariables(
        ticket_id="TKT-123",
        customer_name="Alice",
        customer_email="alice@example.com",
        subject="Test Subject",
        priority="p1"
    )
    
    assert vars.ticket_id == "TKT-123"
    assert vars.customer_name == "Alice"
    assert vars.customer_email == "alice@example.com"
    assert vars.subject == "Test Subject"
    assert vars.priority == "p1"


# ============================================================================
# Edge Cases
# ============================================================================

def test_categorize_empty_text():
    """Test categorization with empty text."""
    assistant = get_support_assistant()
    
    category, reason = assistant.categorize_ticket("", "")
    
    assert category.name == "general"


def test_priority_empty_subject():
    """Test priority with empty subject."""
    assistant = get_support_assistant()
    
    priority, reason = assistant.determine_priority("")
    
    assert priority == "normal"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])