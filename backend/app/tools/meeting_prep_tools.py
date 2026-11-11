"""Meeting preparation tools (spec §6).

Tools for creating meeting agendas, gathering documents, and 
preparing for meetings.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.context import get_tool_context
from app.errors import ToolResult
from app.tools.base import PermissionLevel, registry
from app.services.meeting_prep import get_meeting_assistant, AgendaItem, DocumentInfo


def _user_id() -> str:
    return get_tool_context().user_id


# ============================================================================
# Meeting Preparation Tools
# ============================================================================

async def create_meeting_prep(title: str, participants: list[str],
                             meeting_date: str, duration_minutes: int = 60) -> ToolResult:
    """Create a meeting preparation plan.
    
    Args:
        title: Meeting title
        participants: List of participant names or emails
        meeting_date: ISO format meeting date/time
        duration_minutes: Meeting duration in minutes
        
    Returns:
        ToolResult with meeting prep plan
    """
    assistant = get_meeting_assistant()
    
    try:
        meeting_date_dt = datetime.fromisoformat(meeting_date.replace("Z", "+00:00"))
    except ValueError:
        meeting_date_dt = datetime.now(timezone.utc) + timedelta(days=1)
    
    plan = assistant.create_prep_plan(
        title=title,
        participants=participants,
        meeting_date=meeting_date_dt,
        duration_minutes=duration_minutes
    )
    
    return ToolResult.ok(
        f"Created meeting prep: {title}",
        {
            "title": plan.title,
            "meeting_date": plan.meeting_date.isoformat(),
            "duration_minutes": plan.duration_minutes,
            "participants_count": len(plan.participants),
            "agenda_items": [{"title": a.title, "time": a.time_allocated} for a in plan.agenda],
            "documents_count": len(plan.documents)
        }
    )


async def gather_meeting_documents(topics: list[str], 
                                  lookback_days: int = 30) -> ToolResult:
    """Gather documents relevant to meeting topics.
    
    Args:
        topics: List of topics to find documents for
        lookback_days: How far back to look for documents
        
    Returns:
        ToolResult with gathered documents
    """
    assistant = get_meeting_assistant()
    
    documents = assistant.gather_documents(topics, lookback_days)
    
    return ToolResult.ok(
        f"Found {len(documents)} relevant documents",
        {"documents": [
            {
                "name": d.name,
                "url": d.url,
                "summary": d.summary,
                "relevance_score": d.relevance_score
            }
            for d in documents
        ]}
    )


async def create_meeting_agenda(title: str, topics: list[str],
                               duration_minutes: int = 60,
                               owner: str = "Meeting Host") -> ToolResult:
    """Create a meeting agenda from topics.
    
    Args:
        title: Meeting title
        topics: List of discussion topics
        duration_minutes: Meeting duration
        owner: Default agenda owner
        
    Returns:
        ToolResult with agenda items
    """
    assistant = get_meeting_assistant()
    
    agenda = assistant.create_agenda(title, topics, duration_minutes, owner)
    
    return ToolResult.ok(
        f"Created agenda with {len(agenda)} items",
        {
            "agenda": [
                {
                    "title": item.title,
                    "time_allocated": item.time_allocated,
                    "owner": item.owner,
                    "importance": item.importance
                }
                for item in agenda
            ],
            "total_duration": sum(a.time_allocated for a in agenda)
        }
    )


async def get_preparation_status(meeting_id: str) -> ToolResult:
    """Check preparation status for a meeting.
    
    Args:
        meeting_id: Meeting preparation ID
        
    Returns:
        ToolResult with preparation status
    """
    assistant = get_meeting_assistant()
    
    status = assistant.check_preparation_status(_user_id(), meeting_id)
    
    return ToolResult.ok(
        f"Preparation status: {status.get('status', 'unknown')}",
        status
    )


async def generate_summary_template(attendees: list[str],
                                   key_decisions: list[str] | None = None) -> ToolResult:
    """Generate a meeting summary template.
    
    Args:
        attendees: List of attendee names
        key_decisions: Optional key decisions to include
        
    Returns:
        ToolResult with summary template
    """
    assistant = get_meeting_assistant()
    
    template = assistant.generate_summary_template(attendees, key_decisions)
    
    return ToolResult.ok(
        "Generated meeting summary template",
        template
    )


async def suggest_meeting_reading(meeting_topic: str,
                                 participants: list[str]) -> ToolResult:
    """Suggest reading materials for a meeting.
    
    Args:
        meeting_topic: Main meeting topic
        participants: List of participants (for personalized suggestions)
        
    Returns:
        ToolResult with reading suggestions
    """
    assistant = get_meeting_assistant()
    
    suggestions = assistant.suggest_reading(meeting_topic, participants)
    
    return ToolResult.ok(
        f"Suggested {sum(len(s['items']) for s in suggestions)} reading items",
        {"suggestions": suggestions}
    )


# ============================================================================
# Helper Functions
# ============================================================================

def _parse_iso_date(date_str: str) -> datetime:
    """Parse ISO date string to datetime."""
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


# ============================================================================
# Tool Registration
# ============================================================================

registry.register(create_meeting_prep, permission=PermissionLevel.AUTO,
                  category="meeting_prep", tags=["planning", "agenda"])
registry.register(gather_meeting_documents, permission=PermissionLevel.AUTO,
                  category="meeting_prep", tags=["documents", "research"])
registry.register(create_meeting_agenda, permission=PermissionLevel.AUTO,
                  category="meeting_prep", tags=["agenda", "planning"])
registry.register(get_preparation_status, permission=PermissionLevel.AUTO,
                  category="meeting_prep", tags=["status", "tracking"])
registry.register(generate_summary_template, permission=PermissionLevel.AUTO,
                  category="meeting_prep", tags=["summary", "minutes"])
registry.register(suggest_meeting_reading, permission=PermissionLevel.AUTO,
                  category="meeting_prep", tags=["reading", "preparation"])