"""Lazy tool loading to defer expensive imports until needed.

This module provides decorators and utilities to lazily load tool modules,
speeding up application startup for simple queries like "hello".
"""
from __future__ import annotations

import functools
import importlib
import logging
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from app.tools.base import ToolRegistry

logger = logging.getLogger("jalora.tools.lazy")

# Track which modules have been loaded
_loaded_modules: set[str] = set()

# Tools that require internet can always be lazy-loaded
_INTERNET_TOOLS = {
    "search_web", "open_url", "search_profiles", "search_companies", "search_jobs",
    "get_weather", "get_maps", "translate_text", "open_email",
    "send_email", "send_email_batch", "make_call", "send_sms", "create_event",
    "get_contact", "upload_file", "download_file", "create_document",
}

# Simple tools that are safe to load lazily
_LAZY_TOOLS = {
    "generate_content", "generate_blog_post", "generate_linkedin_post",
    "generate_twitter_thread", "generate_email", "generate_sales_pitch",
    "analyze_code", "check_security", "review_code",
    "update_preferences", "process_feedback",
}


def lazy_import(module_name: str, func_names: list[str] | None = None):
    """Decorator to lazy load a module when function is called.
    
    Usage:
        @lazy_import("app.tools.web", ["search_web", "open_url"])
        def search_web(...): ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            module = importlib.import_module(module_name)
            if func_names:
                # Get the actual function from the module
                for func_name in func_names:
                    if hasattr(module, func_name):
                        actual_func = getattr(module, func_name)
                        return actual_func(*args, **kwargs)
            return func(*args, **kwargs)
        return wrapper
    return decorator


class LazyToolRegistry:
    """Registry that defers tool registration until first use."""
    
    def __init__(self) -> None:
        self._tools_by_module: dict[str, str] = {}  # tool_name -> module_name
        self._tools: dict[str, Any] = {}  # tool_name -> registered_tool
        self._loaded: dict[str, bool] = {}  # tool_name -> is_loaded
    
    def register_lazy(self, module_name: str, tool_name: str) -> None:
        """Register a tool to be lazily loaded from a module."""
        self._tools_by_module[tool_name] = module_name
        self._loaded[tool_name] = False
    
    def get_tool(self, tool_name: str) -> Any:
        """Get a tool, loading it if necessary."""
        if tool_name in self._tools:
            return self._tools[tool_name]
        
        if tool_name not in self._tools_by_module:
            raise KeyError(f"Tool '{tool_name}' not registered")
        
        # Lazy load the module
        module_name = self._tools_by_module[tool_name]
        if module_name not in _loaded_modules:
            logger.debug(f"Lazy loading module: {module_name}")
            importlib.import_module(module_name)
            _loaded_modules.add(module_name)
        
        self._loaded[tool_name] = True
        return self._tools.get(tool_name)
    
    def mark_loaded(self, tool_name: str, tool: Any) -> None:
        """Mark a tool as loaded with its actual LangChain tool object."""
        self._tools[tool_name] = tool
        if tool_name in self._loaded:
            self._loaded[tool_name] = True


# Default lazy registry
lazy_registry = LazyToolRegistry()


def is_tool_lazy(tool_name: str) -> bool:
    """Check if a tool is registered for lazy loading."""
    return tool_name in {
        "search_web", "search_companies", "open_url", "web_search", "open_url_tool", "search_profiles",
        "generate_content", "generate_blog_post", "generate_linkedin_post",
        "generate_twitter_thread", "generate_email", "generate_sales_pitch",
        "analyze_code", "review_code", "check_security", "check_performance",
        "update_preferences", "process_feedback", "summarize_feedback",
    }


def should_lazy_import(module_name: str) -> bool:
    """Determine if a module should be lazily imported based on its name."""
    lazy_modules = {
        "app.tools.web",
        "app.tools.search_service",
        "app.tools.model_router",
        "app.tools.ollama_memory",
        "app.tools.creative_tools",
        "app.tools.coding_tools",
    }
    return any(module_name.startswith(m) for m in lazy_modules)


def get_tools_to_import(eager: bool = False) -> list[str]:
    """Get list of tool module names to import.
    
    Args:
        eager: If True, return all modules (for full warmup).
               If False, return only essential modules for fast startup.
    """
    if eager:
        return [
            "app.tools.web",
            "app.tools.browse",
            "app.tools.browser",
            "app.tools.comms",
            "app.tools.memory_tools",
            "app.tools.document_tools",
            "app.tools.media_tools",
            "app.tools.generation_tools",
            "app.tools.creative_tools",
            "app.tools.plan",
            "app.tools.email_tools",
            "app.tools.voice_tools",
            "app.tools.schedule_tools",
            "app.tools.calendar_tools",
            "app.tools.drive_tools",
            "app.tools.contact_tools",
            "app.tools.maps_tools",
            "app.tools.weather_tools",
            "app.tools.translate_tools",
            "app.tools.coding_tools",
            "app.tools.math_tools",
            "app.tools.plot_tool",
            "app.tools.payment_tools",
            "app.tools.travel_tools",
            "app.tools.job_tools",
            "app.tools.preferences_tools",
            "app.tools.support_tools",
            "app.tools.verification_tools",
            "app.tools.meeting_prep_tools",
            "app.tools.form_tools",
            "app.tools.workflow_tools",
            "app.tools.content_tools",
            "app.tools.base",
            "app.tools.feedback_learning",
            "app.tools.code_review",
        ]
    else:
        # Only import essential modules for simple queries
        return [
            "app.tools.base",  # Always needed for registry
            "app.tools.form_tools",  # Form handling
            "app.tools.preferences_tools",  # Preferences
            "app.tools.support_tools",  # Support
            # Web/search tools deferred until needed
        ]


def import_tools_lazy(eager: bool = False) -> None:
    """Import tools lazily or eagerly."""
    import time
    
    start = time.time()
    modules = get_tools_to_import(eager=eager)
    
    for module in modules:
        try:
            importlib.import_module(module)
        except Exception as e:
            logger.warning(f"Failed to import {module}: {e}")
    
    elapsed = time.time() - start
    if eager:
        logger.info(f"Imported {len(modules)} tool modules in {elapsed:.2f}s")
    else:
        if elapsed > 0.1:
            logger.info(f"Imported {len(modules)} essential modules in {elapsed:.2f}s")