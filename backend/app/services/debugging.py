"""Interactive debugging support (Phase 6).

Provides tools for debugging agent calls, tool invocations, and
code execution in the sandboxed environment.
"""
from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.errors import ToolResult

logger = logging.getLogger("jalora.debugging")


class DebugLevel(str, Enum):
    """Debugging verbosity levels."""
    SILENT = "silent"      # No debug output
    BASIC = "basic"        # Errors and warnings
    VERBOSE = "verbose"    # Step-by-step execution
    TRACE = "trace"        # Full execution trace


@dataclass
class DebugFrame:
    """Debug frame for tool or function execution."""
    id: str
    tool_name: str
    arguments: dict[str, Any]
    call_stack: list[str] = field(default_factory=list)
    local_vars: dict[str, Any] = field(default_factory=dict)
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: datetime | None = None
    status: str = "starting"  # starting, running, completed, failed
    result: Any = None
    error: str | None = None


@dataclass
class DebugSession:
    """Debug session for tracking execution."""
    session_id: str
    user_id: str
    conversation_id: str | None = None
    frames: list[DebugFrame] = field(default_factory=list)
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: datetime | None = None
    debug_level: DebugLevel = DebugLevel.BASIC
    
    def add_frame(self, frame: DebugFrame) -> None:
        """Add a debug frame to the session."""
        self.frames.append(frame)


@dataclass
class DebugBreakpoint:
    """Breakpoint for interactive debugging."""
    tool_name: str | None = None  # None = break on all tools
    argument_name: str | None = None
    argument_value: Any = None
    condition: str | None = None  # Python expression to evaluate
    action: str = "pause"  # pause, log, continue


class Debugger:
    """Interactive debugger for agent runs."""
    
    def __init__(self):
        """Initialize the debugger."""
        self.sessions: dict[str, DebugSession] = {}
        self.breakpoints: list[DebugBreakpoint] = []
        self._watch_expressions: list[str] = []
    
    def start_session(self, 
                     session_id: str,
                     user_id: str,
                     conversation_id: str | None = None,
                     debug_level: DebugLevel = DebugLevel.BASIC) -> DebugSession:
        """Start a new debug session."""
        session = DebugSession(
            session_id=session_id,
            user_id=user_id,
            conversation_id=conversation_id,
            debug_level=debug_level,
        )
        self.sessions[session_id] = session
        return session
    
    def end_session(self, session_id: str) -> DebugSession | None:
        """End a debug session."""
        session = self.sessions.pop(session_id, None)
        if session:
            session.end_time = datetime.now(timezone.utc)
        return session
    
    def create_frame(self,
                    session_id: str,
                    tool_name: str,
                    arguments: dict[str, Any]) -> DebugFrame | None:
        """Create a new debug frame."""
        session = self.sessions.get(session_id)
        if not session:
            return None
        
        frame = DebugFrame(
            id=f"frame_{len(session.frames)}",
            tool_name=tool_name,
            arguments=arguments,
        )
        session.add_frame(frame)
        return frame
    
    def complete_frame(self,
                      session_id: str,
                      frame_id: str,
                      result: Any,
                      error: str | None = None) -> bool:
        """Mark a frame as completed."""
        session = self.sessions.get(session_id)
        if not session:
            return False
        
        frame = next((f for f in session.frames if f.id == frame_id), None)
        if not frame:
            return False
        
        frame.end_time = datetime.now(timezone.utc)
        frame.result = result
        frame.error = error
        frame.status = "failed" if error else "completed"
        return True
    
    def add_breakpoint(self, breakpoint: DebugBreakpoint) -> None:
        """Add a breakpoint."""
        self.breakpoints.append(breakpoint)
    
    def clear_breakpoint(self, index: int) -> bool:
        """Clear a breakpoint by index."""
        if 0 <= index < len(self.breakpoints):
            del self.breakpoints[index]
            return True
        return False
    
    def should_break(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Check if execution should break at this point."""
        for bp in self.breakpoints:
            if bp.tool_name and bp.tool_name != tool_name:
                continue
            if bp.argument_name and bp.argument_name not in arguments:
                continue
            if bp.argument_value is not None:
                if bp.argument_name not in arguments:
                    continue
                if arguments[bp.argument_name] != bp.argument_value:
                    continue
            if bp.condition:
                try:
                    if not eval(bp.condition, {}, {**arguments}):
                        continue
                except Exception:
                    continue
            return True
        return False
    
    def add_watch(self, expression: str) -> None:
        """Add a watch expression."""
        self._watch_expressions.append(expression)
    
    def evaluate_watches(self, frame: DebugFrame) -> dict[str, Any]:
        """Evaluate all watch expressions in a frame."""
        results = {}
        context = {**frame.arguments, **frame.local_vars}
        
        for expr in self._watch_expressions:
            try:
                results[expr] = eval(expr, {}, context)
            except Exception as e:
                results[expr] = f"Error: {e}"
        
        return results
    
    def get_session_report(self, session_id: str) -> dict[str, Any]:
        """Get a debug session report."""
        session = self.sessions.get(session_id)
        if not session:
            return {"error": "Session not found"}
        
        frames_list = []
        for frame in session.frames:
            frames_list.append({
                "id": frame.id,
                "tool_name": frame.tool_name,
                "status": frame.status,
                "duration_ms": int(
                    (frame.end_time - frame.start_time).total_seconds() * 1000
                ) if frame.end_time else 0,
                "has_error": frame.error is not None,
            })
        
        return {
            "session_id": session.session_id,
            "user_id": session.user_id,
            "duration_seconds": int(
                (datetime.now(timezone.utc) - session.start_time).total_seconds()
            ),
            "frames_count": len(frames_list),
            "debug_level": session.debug_level.value,
            "frames": frames_list,
        }


# Singleton instance
_debugger: Debugger | None = None


def get_debugger() -> Debugger:
    """Get or create the debugger singleton."""
    global _debugger
    if _debugger is None:
        _debugger = Debugger()
    return _debugger


async def debug_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
    session_id: str | None = None
) -> ToolResult:
    """Debug a tool call with detailed tracing.
    
    Args:
        tool_name: Name of the tool
        arguments: Arguments passed to the tool
        session_id: Optional debug session ID
        
    Returns:
        ToolResult with debug information
    """
    debugger = get_debugger()
    
    # Create frame
    frame = None
    if session_id:
        frame = debugger.create_frame(session_id, tool_name, arguments)
    
    # Log the call
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool": tool_name,
        "arguments": arguments,
    }
    logger.debug(f"Tool call: {log_entry}")
    
    return ToolResult.ok(
        f"Debug logged for tool: {tool_name}",
        {
            "tool_name": tool_name,
            "arguments": arguments,
            "logged_at": datetime.now(timezone.utc).isoformat(),
        }
    )


async def inspect_call_stack() -> ToolResult:
    """Inspect the current call stack.
    
    Returns:
        ToolResult with call stack information
    """
    stack = inspect.stack()
    
    frames = []
    for i, frame_info in enumerate(stack[:10]):  # Limit to 10 frames
        frames.append({
            "frame": i,
            "filename": frame_info.filename,
            "function": frame_info.function,
            "line": frame_info.lineno,
        })
    
    return ToolResult.ok(
        "Call stack inspected",
        {"frames": frames, "count": len(frames)}
    )


async def get_memory_usage() -> ToolResult:
    """Get current memory usage of running processes.
    
    Returns:
        ToolResult with memory usage information
    """
    import sys
    
    # Basic memory info (would be more detailed in production)
    info = {
        "python_version": sys.version,
        "platform": sys.platform,
    }
    
    return ToolResult.ok(
        "Memory usage information",
        info
    )