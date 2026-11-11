"""Customer support tools and templates (spec §6: personal/business assistance).

Provides ticket categorization, policy-based response generation,
template management, and FAQ retrieval.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.database.session import SessionLocal
from app.models.support import (
    SupportTicket,
    FAQ,
    KnowledgeBaseArticle,
    SupportTemplate
)

logger = logging.getLogger("jalora.support")


# ============================================================================
# Customer Support Assistant
# ============================================================================

@dataclass
class TicketCategory:
    """Category for a support ticket."""
    name: str  # billing, technical, feature, general
    confidence: float  # 0.0-1.0
    subcategories: list[str]


@dataclass
class TemplateVariables:
    """Variables that can be used in templates."""
    ticket_id: str = ""
    customer_name: str = ""
    customer_email: str = ""
    subject: str = ""
    priority: str = "normal"
    category: str = "general"


class SupportAssistant:
    """Assists with customer support tasks."""
    
    # Category keywords mapping
    CATEGORY_KEYWORDS = {
        "billing": ["billing", "invoice", "charge", "payment", "refund", "cost", "price"],
        "technical": ["error", "bug", "crash", "broken", "not working", "fail", "issue", "problem"],
        "feature": ["feature", "request", "suggestion", "enhancement", "new", "add", "want"],
        "account": ["account", "login", "password", "signin", "sign out", "access"],
        "general": []  # Default category
    }
    
    # Priority keywords
    PRIORITY_KEYWORDS = {
        "p1": ["urgent", "asap", "immediately", "emergency", "critical", "down"],
        "p2": ["soon", "today", "important", "high"],
        "p3": ["normal", "standard", "next"],
        "low": ["low", "later", "whenever"]
    }
    
    def categorize_ticket(self, subject: str, description: str = "") -> tuple[SupportTicketCategory, str]:
        """Categorize a support ticket based on subject and description."""
        text = (subject + " " + description).lower()
        
        best_category = "general"
        best_confidence = 0.0
        best_subcats = []
        
        for category, keywords in self.CATEGORY_KEYWORDS.items():
            matches = sum(1 for kw in keywords if kw in text)
            confidence = matches / max(len(keywords), 1) if keywords else 0
            
            if confidence > best_confidence:
                best_confidence = confidence
                best_category = category
                best_subcats = []
        
        return TicketCategory(
            name=best_category,
            confidence=best_confidence,
            subcategories=best_subcats
        ), "Automated categorization"
    
    def determine_priority(self, subject: str, customer_tier: str = "standard") -> tuple[str, str]:
        """Determine priority level based on content and customer tier."""
        text = (subject or "").lower()
        
        # Check for urgency keywords
        for priority, keywords in self.PRIORITY_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                # Adjust for customer tier
                if customer_tier == "premium":
                    # Premium customers get higher priority benefit
                    if priority == "p3":
                        priority = "p2"
                return priority, f"Detected keywords: {[k for k in keywords if k in text]}"
        
        return "normal", "No urgency keywords detected"
    
    def generate_response(self, ticket_id: str, category: str, 
                          customer_info: dict, tone: str = "professional") -> dict:
        """Generate a response based on ticket category and customer info."""
        templates = self._get_appropriate_templates(category, tone)
        
        variables = TemplateVariables(
            ticket_id=ticket_id,
            customer_name=customer_info.get("name", "there"),
            customer_email=customer_info.get("email", ""),
            subject=customer_info.get("subject", ""),
            priority=customer_info.get("priority", "normal"),
            category=category
        )
        
        if templates:
            # Use template substitution
            template = templates[0]
            body = self._substitute_variables(template["body_template"], variables)
            subject = self._substitute_variables(
                template.get("subject_template", f"Re: {ticket_id}"), 
                variables
            )
        else:
            # Generate basic response
            body = self._generate_basic_response(category, variables)
            subject = f"Re: {ticket_id}"
        
        return {
            "subject": subject,
            "body": body,
            "tone": tone,
            "variables_used": list(variables.__dict__.keys())
        }
    
    def _get_appropriate_templates(self, category: str, tone: str = "professional") -> list[dict]:
        """Get response templates for a category."""
        db = SessionLocal()
        try:
            templates = db.scalars(
                select(SupportTemplate)
                .where(
                    SupportTemplate.category == category,
                    SupportTemplate.is_active == True
                )
            ).all()
            
            return [
                {
                    "category": t.category,
                    "name": t.name,
                    "subject_template": t.subject_template,
                    "body_template": t.body_template,
                    "variables": t.variables
                }
                for t in templates
            ]
        finally:
            db.close()
    
    def _substitute_variables(self, template: str, variables: TemplateVariables) -> str:
        """Substitute variables in template."""
        result = template
        
        # Simple variable substitution
        var_dict = {
            "{{ticket_id}}": variables.ticket_id,
            "{{customer_name}}": variables.customer_name,
            "{{customer_email}}": variables.customer_email,
            "{{subject}}": variables.subject,
            "{{priority}}": variables.priority,
            "{{category}}": variables.category,
        }
        
        for placeholder, value in var_dict.items():
            result = result.replace(placeholder, str(value))
        
        return result
    
    def _generate_basic_response(self, category: str, variables: TemplateVariables) -> str:
        """Generate a basic response when no template exists."""
        responses = {
            "billing": f"Thank you for contacting us about billing. We're looking into your inquiry (Ticket: {variables.ticket_id}) and will respond within 24 hours.",
            "technical": f"We've received your technical issue report. Our team is investigating (Ticket: {variables.ticket_id}).",
            "feature": f"Thanks for your feature suggestion. We've logged it as a request (Ticket: {variables.ticket_id}).",
            "account": f"Regarding your account issue, we're looking into it (Ticket: {variables.ticket_id}).",
            "general": f"Thank you for reaching out (Ticket: {variables.ticket_id}). We'll get back to you shortly."
        }
        
        return responses.get(category, f"Thank you for your message (Ticket: {variables.ticket_id}).")
    
    def search_faq(self, query: str, limit: int = 5) -> list[dict]:
        """Search FAQs for relevant answers."""
        db = SessionLocal()
        try:
            # Simple keyword search
            faqs = db.scalars(
                select(FAQ)
                .where(
                    FAQ.keywords.op("like")(f"%{query}%"),
                    FAQ.is_active == True
                )
                .limit(limit)
            ).all()
            
            if not faqs:
                # Try searching in question/answer
                faqs = db.scalars(
                    select(FAQ)
                    .where(FAQ.is_active == True)
                    .limit(limit)
                ).all()
            
            return [
                {
                    "id": faq.id,
                    "question": faq.question,
                    "answer": faq.answer,
                    "category": faq.category
                }
                for faq in faqs
            ]
        finally:
            db.close()
    
    def search_kb(self, query: str, limit: int = 5) -> list[dict]:
        """Search knowledge base articles."""
        db = SessionLocal()
        try:
            articles = db.scalars(
                select(KnowledgeBaseArticle)
                .where(
                    KnowledgeBaseArticle.keywords.op("like")(f"%{query}%"),
                    KnowledgeBaseArticle.is_published == True
                )
                .limit(limit)
            ).all()
            
            return [
                {
                    "id": article.id,
                    "title": article.title,
                    "summary": article.summary,
                    "category": article.category,
                    "url": f"/kb/{article.slug}"
                }
                for article in articles
            ]
        finally:
            db.close()
    
    def calculate_sla_status(self, ticket_id: str, sla_hours: float) -> dict:
        """Calculate SLA status for a ticket."""
        db = SessionLocal()
        try:
            ticket = db.scalar(
                select(SupportTicket).where(SupportTicket.id == ticket_id)
            )
            
            if not ticket:
                return {"status": "unknown", "message": "Ticket not found"}
            
            created = ticket.created_at or datetime.now(timezone.utc)
            elapsed = (datetime.now(timezone.utc) - created).total_seconds() / 3600
            
            if elapsed > sla_hours:
                return {
                    "status": "breached",
                    "elapsed_hours": elapsed,
                    "sla_hours": sla_hours,
                    "message": f"SLA breached by {elapsed - sla_hours:.1f} hours"
                }
            else:
                remaining = sla_hours - elapsed
                return {
                    "status": "on_track",
                    "elapsed_hours": elapsed,
                    "sla_hours": sla_hours,
                    "remaining_hours": remaining,
                    "message": f"SLA met. {remaining:.1f} hours remaining"
                }
        finally:
            db.close()


# Singleton instance
_support_assistant: SupportAssistant | None = None

def get_support_assistant() -> SupportAssistant:
    """Get or create the support assistant singleton."""
    global _support_assistant
    if _support_assistant is None:
        _support_assistant = SupportAssistant()
    return _support_assistant