"""Debugging tools (Phase 6).

Tools for debugging agent calls, tool chains, and code execution.
"""
from __future__ import annotations

from app.context import get_tool_context
from app.errors import ToolResult
from app.services.debugging import (
    get_debugger,
    Debugger,
    DebugLevel,
)
from app.tools.base import PermissionLevel, registry


def _user_id() -> str:
    return get_tool_context().user_id


# ============================================================================
# Debug Tools
# ============================================================================

async def start_debug_session(
    session_id: str,
    debug_level: str = "basic",
    conversation_id: str | None = None
) -> ToolResult:
    """Start a new debug session.
    
    Args:
        session_id: Unique identifier for this session
        debug_level: Debug verbosity (silent, basic, verbose, trace)
        conversation_id: Optional conversation ID to attach
        
    Returns:
        ToolResult with session info
    """
    debugger = get_debugger()
    
    try:
        level = DebugLevel(debug_level.lower())
    except ValueError:
        level = DebugLevel.BASIC
    
    session = debugger.start_session(
        session_id=session_id,
        user_id=_user_id(),
        conversation_id=conversation_id,
        debug_level=level,
    )
    
    return ToolResult.ok(
        f"Debug session started: {session_id}",
        {
            "session_id": session.session_id,
            "user_id": session.user_id,
            "debug_level": level.value,
            "started_at": session.start_time.isoformat(),
        }
    )


async def stop_debug_session(session_id: str) -> ToolResult:
    """Stop a debug session and get report.
    
    Args:
        session_id: Debug session ID
        
    Returns:
        ToolResult with session report
    """
    debugger = get_debugger()
    
    session = debugger.end_session(session_id)
    if not session:
        return ToolResult.fail("Session not found", f"No session with ID: {session_id}")
    
    report = debugger.get_session_report(session_id)
    
    return ToolResult.ok(
        f"Debug session stopped: {session_id}",
        report
    )


async def trace_tool_call(
    tool_name: str,
    arguments: dict,
    session_id: str
) -> ToolResult:
    """Trace a tool call.
    
    Args:
        tool_name: Name of the tool
        arguments: Arguments passed to the tool
        session_id: Debug session ID
        
    Returns:
        ToolResult with trace info
    """
    debugger = get_debugger()
    
    frame = debugger.create_frame(session_id, tool_name, arguments)
    if not frame:
        return ToolResult.fail("Session not found", "Invalid debug session")
    
    return ToolResult.ok(
        f"Traced tool call: {tool_name}",
        {
            "session_id": session_id,
            "frame_id": frame.id,
            "tool_name": tool_name,
        }
    )


async def complete_trace(
    session_id: str,
    frame_id: str,
    result: dict | None = None,
    error: str | None = None
) -> ToolResult:
    """Complete a tool call trace.
    
    Args:
        session_id: Debug session ID
        frame_id: Frame ID to complete
        result: Result of the tool call
        error: Error message if failed
        
    Returns:
        ToolResult with completion status
    """
    debugger = get_debugger()
    
    success = debugger.complete_frame(session_id, frame_id, result, error)
    if not success:
        return ToolResult.fail("Frame not found", "Invalid frame ID")
    
    status = "failed" if error else "completed"
    
    return ToolResult.ok(
        f"Trace completed: {status}",
        {
            "session_id": session_id,
            "frame_id": frame_id,
            "status": status,
        }
    )


async def list_debug_sessions() -> ToolResult:
    """List all active debug sessions.
    
    Returns:
        ToolResult with session list
    """
    debugger = get_debugger()
    
    sessions = []
    for session_id, session in debugger.sessions.items():
        sessions.append({
            "session_id": session_id,
            "user_id": session.user_id,
            "debug_level": session.debug_level.value,
            "frames_count": len(session.frames),
            "started_at": session.start_time.isoformat(),
        })
    
    return ToolResult.ok(
        f"Found {len(sessions)} active debug sessions",
        {"sessions": sessions}
    )


async def inspect_conversation(conversation_id: str) -> ToolResult:
    """Inspect a conversation's debugging information.
    
    Args:
        conversation_id: Conversation ID to inspect
        
    Returns:
        ToolResult with conversation trace
    """
    # This would integrate with the conversation storage
    return ToolResult.ok(
        f"Conversation inspection: {conversation_id}",
        {
            "conversation_id": conversation_id,
            "messages_count": 0,  # Would be populated from storage
            "tools_used": [],
        }
    )


# ============================================================================
# Tool Registration
# ============================================================================

registry.register(
    start_debug_session,
    permission=PermissionLevel.AUTO,
    category="debug",
    tags=["debug", "session", "start"]
)

registry.register(
    stop_debug_session,
    permission=PermissionLevel.AUTO,
    category="debug",
    tags=["debug", "session", "stop"]
)

registry.register(
    trace_tool_call,
    permission=PermissionLevel.AUTO,
    category="debug",
    tags=["debug", "trace", "tool"]
)

registry.register(
    complete_trace,
    permission=PermissionLevel.AUTO,
    category="debug",
    tags=["debug", "complete", "trace"]
)

registry.register(
    list_debug_sessions,
    permission=PermissionLevel.AUTO,
    category="debug",
    tags=["debug", "list", "sessions"]
)

registry.register(
    inspect_conversation,
    permission=PermissionLevel.NOTIFY,
    category="debug",
    tags=["debug", "inspect", "conversation"]
)