from fastapi import APIRouter

from app.api import (
    activity,
    artifacts,
    auth,
    calls,
    chat,
    comms,
    conversations,
    creative,
    devices,
    documents,
    emails,
    feedback,
    files,
    integrations,
    media,
    memory,
    notifications,
    people,
    runs,
    schedules,
    settings,
    system,
    tasks,
    voice,
    workspace,
)

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(conversations.router)
api_router.include_router(chat.router)
api_router.include_router(memory.router)
api_router.include_router(documents.router)
api_router.include_router(media.router)
api_router.include_router(files.router)
api_router.include_router(runs.router)
api_router.include_router(activity.router)
api_router.include_router(tasks.router)
api_router.include_router(settings.router)
api_router.include_router(comms.router)
api_router.include_router(artifacts.router)
api_router.include_router(integrations.router)
api_router.include_router(devices.router)
api_router.include_router(emails.router)
api_router.include_router(calls.router)
api_router.include_router(schedules.router)
api_router.include_router(notifications.router)
api_router.include_router(people.router)
api_router.include_router(voice.router)
api_router.include_router(workspace.router)
api_router.include_router(feedback.router)
api_router.include_router(creative.router)

# Also export as router for backwards compatibility
router = api_router
