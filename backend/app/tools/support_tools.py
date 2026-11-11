"""Customer support tools (spec §6).

Tools for ticket categorization, response generation, FAQ/KB search, and SLA tracking.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.context import get_tool_context
from app.errors import ToolResult
from app.tools.base import PermissionLevel, registry
from app.services.support import get_support_assistant


def _user_id() -> str:
    return get_tool_context().user_id


# ============================================================================
# Ticket Categorization Tools
# ============================================================================

async def categorize_ticket(subject: str, description: str = "") -> ToolResult:
    """Categorize a support ticket based on content.
    
    Args:
        subject: Ticket subject line
        description: Ticket description
        
    Returns:
        ToolResult with category and confidence
    """
    assistant = get_support_assistant()
    
    category, reason = assistant.categorize_ticket(subject, description)
    
    return ToolResult.ok(
        f"Ticket categorized as: {category.name}",
        {
            "category": category.name,
            "confidence": category.confidence,
            "subcategories": category.subcategories,
            "reason": reason
        }
    )


async def determine_priority(subject: str, customer_tier: str = "standard") -> ToolResult:
    """Determine priority level for a support ticket.
    
    Args:
        subject: Ticket subject line
        customer_tier: Customer tier (standard, premium, enterprise)
        
    Returns:
        ToolResult with priority level
    """
    assistant = get_support_assistant()
    
    priority, reason = assistant.determine_priority(subject, customer_tier)
    
    # Map priority to urgency level
    urgency_map = {
        "p1": "critical",
        "p2": "high", 
        "p3": "medium",
        "low": "low"
    }
    
    return ToolResult.ok(
        f"Priority set to: {priority}",
        {
            "priority": priority,
            "urgency": urgency_map.get(priority, "medium"),
            "reason": reason,
            "customer_tier": customer_tier
        }
    )


# ============================================================================
# Response Generation Tools
# ============================================================================

async def generate_response(ticket_id: str, category: str, 
                           customer_name: str = "Customer",
                           customer_email: str = "") -> ToolResult:
    """Generate a response for a support ticket.
    
    Args:
        ticket_id: Ticket identifier
        category: Ticket category
        customer_name: Customer name for personalization
        customer_email: Customer email
        
    Returns:
        ToolResult with generated response
    """
    assistant = get_support_assistant()
    
    customer_info = {
        "name": customer_name,
        "email": customer_email,
        "ticket_id": ticket_id
    }
    
    response = assistant.generate_response(ticket_id, category, customer_info)
    
    return ToolResult.ok(
        f"Generated response for ticket {ticket_id}",
        response
    )


async def generate_apology_response(ticket_id: str, 
                                   issue_type: str = "generic") -> ToolResult:
    """Generate an apology response for customer issues.
    
    Args:
        ticket_id: Ticket identifier
        issue_type: Type of issue (technical, billing, service, etc.)
        
    Returns:
        ToolResult with apology response
    """
    assistant = get_support_assistant()
    
    customer_info = {
        "name": "Customer",
        "email": "",
        "ticket_id": ticket_id,
        "priority": "high"
    }
    
    response = assistant.generate_response(
        ticket_id, 
        "technical" if issue_type == "technical" else "general",
        customer_info,
        tone="apologetic"
    )
    
    return ToolResult.ok(
        f"Generated apology response for ticket {ticket_id}",
        response
    )


# ============================================================================
# Knowledge Base Tools
# ============================================================================

async def search_faqs(query: str, limit: int = 5) -> ToolResult:
    """Search FAQ database for answers.
    
    Args:
        query: Search query
        limit: Maximum results to return
        
    Returns:
        ToolResult with matching FAQs
    """
    assistant = get_support_assistant()
    
    results = assistant.search_faq(query, limit)
    
    return ToolResult.ok(
        f"Found {len(results)} relevant FAQ(s)",
        {"faqs": results, "total": len(results)}
    )


async def search_kb_articles(query: str, limit: int = 5) -> ToolResult:
    """Search knowledge base articles.
    
    Args:
        query: Search query
        limit: Maximum results to return
        
    Returns:
        ToolResult with matching KB articles
    """
    assistant = get_support_assistant()
    
    results = assistant.search_kb(query, limit)
    
    return ToolResult.ok(
        f"Found {len(results)} knowledge base article(s)",
        {"articles": results, "total": len(results)}
    )


async def get_article_by_slug(slug: str) -> ToolResult:
    """Get a specific KB article by its slug.
    
    Args:
        slug: Article slug
        
    Returns:
        ToolResult with article content
    """
    from app.database.session import SessionLocal
    from app.models.support import KnowledgeBaseArticle
    
    db = SessionLocal()
    try:
        article = db.scalar(
            select(KnowledgeBaseArticle).where(
                KnowledgeBaseArticle.slug == slug,
                KnowledgeBaseArticle.is_published == True
            )
        )
        
        if not article:
            return ToolResult.fail("NOT_FOUND", f"Article not found: {slug}")
        
        return ToolResult.ok(
            f"Retrieved article: {article.title}",
            {
                "id": article.id,
                "title": article.title,
                "content": article.content,
                "summary": article.summary,
                "category": article.category,
                "published_at": article.published_at.isoformat() if article.published_at else None
            }
        )
    finally:
        db.close()


# ============================================================================
# SLA Tracking Tools
# ============================================================================

async def check_sla_status(ticket_id: str, sla_hours: float = 24.0) -> ToolResult:
    """Check SLA status for a ticket.
    
    Args:
        ticket_id: Ticket identifier
        sla_hours: SLA hours (default 24)
        
    Returns:
        ToolResult with SLA status
    """
    assistant = get_support_assistant()
    
    status = assistant.calculate_sla_status(ticket_id, sla_hours)
    
    return ToolResult.ok(
        f"SLA status: {status['status']}",
        status
    )


# ============================================================================
# Ticket Resolution Tools
# ============================================================================

async def generate_resolution_summary(ticket_id: str, 
                                    resolution: str,
                                    next_steps: list[str] | None = None) -> ToolResult:
    """Generate a resolution summary for a ticket.
    
    Args:
        ticket_id: Ticket identifier
        resolution: Resolution details
        next_steps: Optional list of next steps
        
    Returns:
        ToolResult with resolution summary
    """
    summary_parts = [f"Resolution for ticket {ticket_id}:", f"**{resolution}**"]
    
    if next_steps:
        summary_parts.append("\n\nNext steps:")
        for i, step in enumerate(next_steps, 1):
            summary_parts.append(f"{i}. {step}")
    
    return ToolResult.ok(
        "Generated resolution summary",
        {
            "ticket_id": ticket_id,
            "resolution_summary": "\n".join(summary_parts),
            "next_steps": next_steps or []
        }
    )


# ============================================================================
# Tool Registration
# ============================================================================

registry.register(categorize_ticket, permission=PermissionLevel.AUTO,
                  category="support", tags=["ticket", "categorization"])
registry.register(determine_priority, permission=PermissionLevel.AUTO,
                  category="support", tags=["ticket", "priority"])
registry.register(generate_response, permission=PermissionLevel.AUTO,
                  category="support", tags=["response", "email"])
registry.register(generate_apology_response, permission=PermissionLevel.AUTO,
                  category="support", tags=["response", "apology"])
registry.register(search_faqs, permission=PermissionLevel.AUTO,
                  category="support", tags=["faq", "search"])
registry.register(search_kb_articles, permission=PermissionLevel.AUTO,
                  category="support", tags=["knowledge", "search"])
registry.register(get_article_by_slug, permission=PermissionLevel.AUTO,
                  category="support", tags=["knowledge", "article"])
registry.register(check_sla_status, permission=PermissionLevel.AUTO,
                  category="support", tags=["sla", "tracking"])
registry.register(generate_resolution_summary, permission=PermissionLevel.AUTO,
                  category="support", tags=["resolution", "summary"])