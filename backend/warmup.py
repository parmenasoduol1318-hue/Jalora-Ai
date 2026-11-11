"""Pre-warming script to initialize all modules before serving requests.

Run this before starting the server or as a startup hook.

Usage:
    python -m warmup
    python warmup.py
"""
from __future__ import annotations

import asyncio
import time


def warm_all_tools() -> None:
    """Import all tool modules to warm up the cache."""
    print("Warming up tools...")
    
    tool_modules = [
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
    ]
    
    start = time.time()
    for module in tool_modules:
        try:
            __import__(module)
            print(f"  ✓ {module}")
        except Exception as e:
            print(f"  ✗ {module}: {e}")
    
    elapsed = time.time() - start
    print(f"Tool warm-up complete in {elapsed:.2f}s")


async def async_warm_all_tools() -> None:
    """Warm up async components."""
    print("Warming up async components...")
    
    from app.services.model_router import get_async_client
    from app.services.ollama_memory import get_monitor as get_ollama_monitor
    
    # Warm HTTP client
    get_async_client()
    print("  ✓ HTTP client ready")
    
    # Warm Ollama monitor
    get_ollama_monitor()
    print("  ✓ Ollama memory monitor ready")
    
    print("Async warm-up complete")


def main() -> None:
    """Run all warm-up tasks."""
    print("=" * 60)
    print("JALORA PRE-WARMING")
    print("=" * 60)
    
    start = time.time()
    
    # Warm config and database
    print("\n1. Warming configuration...")
    from app.config import get_settings
    settings = get_settings()
    print(f"   Environment: {settings.env}")
    
    print("\n2. Warming database...")
    from app.database.session import init_db
    init_db()
    print("   Database ready")
    
    # Warm all services
    print("\n3. Warming services...")
    from app.services.form_filling import FormCompletionService
    from app.services.content_creation import ContentCreator
    from app.services.code_review import CodeReviewer
    from app.services.feedback_learning import FeedbackLearner
    
    # Initialize singletons
    FormCompletionService()
    ContentCreator()
    CodeReviewer()
    FeedbackLearner()
    print("   All services ready")
    
    # Warm tools (this is the slow part)
    print("\n4. Warming tools...")
    warm_all_tools()
    
    # Warm async components
    print("\n5. Warming async components...")
    asyncio.run(async_warm_all_tools())
    
    total = time.time() - start
    print("\n" + "=" * 60)
    print(f"PRE-WARMING COMPLETE ({total:.2f}s)")
    print("=" * 60)


if __name__ == "__main__":
    main()