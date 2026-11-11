"""Meeting preparation tools (spec §6).

Provides document gathering, agenda creation, participant analysis,
and preparation checklists.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.database.session import SessionLocal
from app.models.meeting_prep import MeetingPrep, MeetingAgendaItem, MeetingMinutes

logger = logging.getLogger("jalora.meeting_prep")


# ============================================================================
# Meeting Preparation Models
# ============================================================================

@dataclass
class MeetingParticipant:
    """Participant in a meeting."""
    name: str
    email: str = ""
    role: str = "attendee"
    organization: str = ""
    topics: list[str] = field(default_factory=list)


@dataclass
class AgendaItem:
    """An agenda item."""
    title: str
    time_allocated: int = 5  # minutes
    owner: str = ""
    description: str = ""
    importance: str = "normal"  # high, normal, low


@dataclass
class DocumentInfo:
    """Information about a related document."""
    name: str
    url: str = ""
    summary: str = ""
    last_modified: datetime | None = None
    relevance_score: float = 0.0


@dataclass
class MeetingPrepPlan:
    """Complete meeting preparation plan."""
    title: str
    meeting_date: datetime
    duration_minutes: int
    participants: list[MeetingParticipant]
    agenda: list[AgendaItem]
    documents: list[DocumentInfo]
    pre_tasks: list[dict]
    materials_ready: bool = False


# ============================================================================
# Meeting Preparation Assistant
# ============================================================================

class MeetingPrepAssistant:
    """Assists with meeting preparation."""
    
    def __init__(self):
        self.lookback_days = 30
        self.default_agenda = ["Introductions", "Main topic discussion", "Action items", "Next steps"]
    
    def create_prep_plan(self, title: str, participants: list[str], 
                        meeting_date: datetime, duration_minutes: int = 60,
                        topics: list[str] | None = None) -> MeetingPrepPlan:
        """Create a comprehensive meeting preparation plan."""
        # Parse participants
        participant_objs = []
        for p in participants:
            if "@" in p:  # Email provided
                name = p.split("@")[0]
                participant_objs.append(MeetingParticipant(name=name, email=p))
            else:
                participant_objs.append(MeetingParticipant(name=p))
        
        # Create agenda based on topics or default
        if topics:
            agenda = [
                AgendaItem(title=t, time_allocated=max(5, duration_minutes // len(topics)))
                for t in topics[:5]  # Limit to 5 topics
            ]
        else:
            agenda = [
                AgendaItem(title=item, time_allocated=duration_minutes // len(self.default_agenda))
                for item in self.default_agenda
            ]
        
        # Documents and pre-tasks will be populated via additional methods
        return MeetingPrepPlan(
            title=title,
            meeting_date=meeting_date,
            duration_minutes=duration_minutes,
            participants=participant_objs,
            agenda=agenda,
            documents=[],
            pre_tasks=[],
            materials_ready=False
        )
    
    def gather_documents(self, topics: list[str], lookback_days: int = 30) -> list[DocumentInfo]:
        """Gather relevant documents for meeting topics."""
        self.lookback_days = lookback_days
        documents = []
        
        # In production, would query document storage
        # For now, return simulated documents
        for topic in topics[:5]:
            documents.append(DocumentInfo(
                name=f"Documents for {topic}",
                url=f"/doc/{topic.replace(' ', '-')}",
                summary=f"Relevant documents for {topic} discussion",
                relevance_score=0.8
            ))
        
        return documents
    
    def create_agenda(self, title: str, topics: list[str], 
                     duration_minutes: int = 60,
                     owner: str = "Meeting Host") -> list[AgendaItem]:
        """Create an agenda from topics."""
        agenda = []
        time_per_topic = max(5, duration_minutes // max(1, len(topics)))
        
        for i, topic in enumerate(topics):
            agenda.append(AgendaItem(
                title=topic,
                time_allocated=time_per_topic,
                owner=owner if i == 0 else "",
                importance="high" if i == 0 else "normal"
            ))
        
        # Add wrap-up
        agenda.append(AgendaItem(
            title="Action Items & Next Steps",
            time_allocated=10,
            owner=owner
        ))
        
        return agenda
    
    def generate_summary_template(self, attendees: list[str], 
                                  key_decisions: list[str] | None = None) -> dict:
        """Generate a meeting summary template."""
        return {
            "meeting_title": "",
            "date": datetime.now(timezone.utc).isoformat(),
            "attendees": [{"name": a, "present": True} for a in (attendees or [])],
            "decisions_made": [],
            "action_items": [],
            "discussion_points": [],
            "next_meeting": {
                "date": "",
                "topic": ""
            },
            "notes": ""
        }
    
    def check_preparation_status(self, user_id: str, meeting_id: str) -> dict:
        """Check preparation status for a meeting."""
        db = SessionLocal()
        try:
            prep = db.scalar(
                select(MeetingPrep).where(MeetingPrep.id == meeting_id)
            )
            
            if not prep:
                return {"status": "not_found", "message": "Meeting preparation not found"}
            
            # Check what's ready
            checks = {
                "agenda_created": len(prep.agenda) > 0 if prep.agenda else False,
                "documents_gathered": len(prep.related_documents) > 0 if prep.related_documents else False,
                "pre_tasks_defined": len(prep.pre_tasks) > 0 if prep.pre_tasks else False,
                "materials_ready": prep.materials_ready
            }
            
            all_ready = all(checks.values())
            
            return {
                "meeting_id": meeting_id,
                "title": prep.title,
                "status": "ready" if all_ready else "in_progress",
                "checks": checks,
                "completion_percent": sum(checks.values()) / len(checks) * 100
            }
        finally:
            db.close()
    
    def suggest_reading(self, meeting_topic: str, 
                       participants: list[str]) -> list[dict]:
        """Suggest reading materials for participants."""
        return [
            {
                "topic": f"Background on {meeting_topic}",
                "items": [f"Document 1 about {meeting_topic}", f"Analysis report"],
                "recommended_time": "30 minutes"
            },
            {
                "topic": "Context documents",
                "items": [f"Previous meeting notes", f"Related proposals"],
                "recommended_time": "15 minutes"
            }
        ]


# Singleton instance
_meeting_assistant: MeetingPrepAssistant | None = None

def get_meeting_assistant() -> MeetingPrepAssistant:
    """Get or create the meeting preparation assistant singleton."""
    global _meeting_assistant
    if _meeting_assistant is None:
        _meeting_assistant = MeetingPrepAssistant()
    return _meeting_assistant