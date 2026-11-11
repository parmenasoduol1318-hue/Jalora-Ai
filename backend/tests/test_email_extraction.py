"""Tests for email parameter extraction functionality.

Tests the email extraction helpers added to intent.py for better
natural language processing of email requests.
"""
from __future__ import annotations

import pytest

from app.agents.intent import (
    extract_email_address,
    extract_email_parameters,
    classify_intent,
    _EMAIL,
    _EMAIL_ADDR,
)


class TestEmailPatternDetection:
    """Tests for the _EMAIL pattern matching."""
    
    def test_detects_send_email(self):
        """Should detect 'send email' phrases."""
        assert _EMAIL.search("send email to swiftreply1318@gmail.com")
        assert _EMAIL.search("Send Email Now")
        assert _EMAIL.search("please email me")
    
    def test_detects_mail_keyword(self):
        """Should detect 'mail' keyword."""
        assert _EMAIL.search("send mail to test@example.com")
        assert _EMAIL.search("I want to mail you")
    
    def test_detects_mailto_uri(self):
        """Should detect mailto: URIs."""
        assert _EMAIL.search("mailto:test@example.com")
        assert _EMAIL.search("Click here: mailto:user@domain.org")
    
    def test_case_insensitive(self):
        """Pattern should be case insensitive."""
        assert _EMAIL.search("EMAIL to me")
        assert _EMAIL.search("Email swiftreply1318@gmail.com")


class TestEmailAddressExtraction:
    """Tests for email address extraction from natural language."""
    
    def test_extracts_simple_email(self):
        """Should extract a simple email address."""
        assert extract_email_address("send email to swiftreply1318@gmail.com") == \
               "swiftreply1318@gmail.com"
    
    def test_extracts_email_with_name(self):
        """Should extract email when preceded by a name."""
        assert extract_email_address("send an email to John at john@example.com") == \
               "john@example.com"
    
    def test_extracts_email_from_complex_sentence(self):
        """Should extract email from a complex sentence."""
        text = ("send an email to swiftreply1318@gmail.com to ask if he is "
                "training his ai and tell him to start if not yet")
        assert extract_email_address(text) == "swiftreply1318@gmail.com"
    
    def test_returns_none_when_no_email(self):
        """Should return None when no email is found."""
        assert extract_email_address("send email to my friend") is None
        assert extract_email_address("email john about the meeting") is None
    
    def test_extracts_multiple_emails_returns_first(self):
        """Should return the first email when multiple are present."""
        text = "send to bob@example.com and cc alice@example.com"
        assert extract_email_address(text) == "bob@example.com"


class TestEmailParameterExtraction:
    """Tests for the extract_email_parameters helper function."""
    
    def test_extracts_recipient_with_high_confidence(self):
        """Should extract recipient with high confidence when email present."""
        result = extract_email_parameters("send email to swiftreply1318@gmail.com")
        assert result["to"] == "swiftreply1318@gmail.com"
        assert result["confidence"] >= 0.7
    
    def test_extracts_recipient_from_complex_request(self):
        """Should extract recipient from complex natural language request."""
        text = ("send an email to swiftreply1318@gmail.com to ask if he is "
                "training his ai and tell him to start if not yet")
        result = extract_email_parameters(text)
        assert result["to"] == "swiftreply1318@gmail.com"
        assert "swiftreply1318@gmail.com" in result["to"]
    
    def test_has_correct_structure(self):
        """Should return dict with expected keys."""
        result = extract_email_parameters("test request")
        assert "to" in result
        assert "subject" in result
        assert "body" in result
        assert "confidence" in result


class TestIntentClassification:
    """Tests for intent classification with email patterns."""
    
    def test_email_request_classifies_as_email(self):
        """Email requests should use the dedicated email intent route."""
        result = classify_intent("send email to swiftreply1318@gmail.com")
        assert result.intent == "email"
    
    def test_email_request_extracts_email_as_signal(self):
        """Email addresses should be extracted as signals."""
        result = classify_intent("send email to swiftreply1318@gmail.com to ask about training")
        # The email address should appear in signals
        assert len(result.signals) > 0
    
    def test_scheduling_over_task(self):
        """Scheduling requests should take priority over task."""
        # When a scheduling pattern is detected, it should override task
        result = classify_intent("schedule a meeting tomorrow at 3pm and send email to john@example.com")
        assert result.intent == "scheduling"


class TestRealWorldEmailRequests:
    """Tests with actual user requests from the conversation log."""
    
    def test_extracts_swiftreply1318_email(self):
        """Test extraction from actual user request 1."""
        text = "send an email to swiftreply1318@gmail.com to ask if he is training his ai"
        result = extract_email_parameters(text)
        assert result["to"] == "swiftreply1318@gmail.com"
    
    def test_extracts_voxkenya254_email(self):
        """Test extraction from actual user request 2."""
        text = "send an email to voxkenya254@gmail.com ans send him mornig greetings and good day blessings"
        result = extract_email_parameters(text)
        assert result["to"] == "voxkenya254@gmail.com"
    
    def test_extracts_internetmoney1318_email(self):
        """Test extraction from the test case email."""
        text = "send email to internetmoney1318@gmail.com and tell him to attend meeting on friday at 8 am"
        result = extract_email_parameters(text)
        assert result["to"] == "internetmoney1318@gmail.com"


class TestEmailSummaryIntent:
    """Tests for email summary intent detection."""
    
    def test_detects_summarize_unread_emails(self):
        """Should detect 'summarize unread emails' requests."""
        from app.agents.intent import _EMAIL_SUMMARY
        assert _EMAIL_SUMMARY.search("summarize my unread emails")
        assert _EMAIL_SUMMARY.search("summarize unread emails")
        assert _EMAIL_SUMMARY.search("check my unread emails")
    
    def test_detects_inbox_summary_requests(self):
        """Should detect various inbox summary requests."""
        from app.agents.intent import _EMAIL_SUMMARY
        assert _EMAIL_SUMMARY.search("summarize my inbox")  # "summarize my inbox"
        assert _EMAIL_SUMMARY.search("check unread emails")
        assert _EMAIL_SUMMARY.search("pending emails")
        assert _EMAIL_SUMMARY.search("summary of my messages")  # "summary of my messages"


class TestEmailSummaryClassification:
    """Tests for email summary classification."""
    
    def test_summarize_unread_classifies_as_task(self):
        """Summarize unread requests should be classified as task."""
        result = classify_intent("summarize my unread emails")
        assert result.intent == "task"
    
    def test_inbox_summary_classifies_as_task(self):
        """Inbox summary requests should be classified as task."""
        result = classify_intent("check my unread inbox emails and provide a summary")
        assert result.intent == "task"
    
    def test_summarize_with_action_signals(self):
        """Summary requests with action words should have appropriate signals."""
        result = classify_intent("summarize my unread emails and tell me what needs action")
        # Should have signals from the text
        assert len(result.signals) > 0
