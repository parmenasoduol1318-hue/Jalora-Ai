"""Test bootstrap: environment must be configured BEFORE app imports."""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

_tmp_dir = tempfile.mkdtemp(prefix="jalora_test_")
os.environ["JALORA_ENV"] = "test"
os.environ["JALORA_DATABASE_URL"] = f"sqlite:///{Path(_tmp_dir) / 'test.db'}"
os.environ["JALORA_SCHEDULER_ENABLED"] = "false"
os.environ["JALORA_JWT_SECRET"] = "jalora-test-secret-0123456789abcdef"
os.environ["JALORA_UPLOAD_DIR"] = str(Path(_tmp_dir) / "uploads")
os.environ["JALORA_OUTPUTS_DIR"] = str(Path(_tmp_dir) / "outputs")
os.environ["JALORA_RATE_LIMIT_AUTH_PER_MIN"] = "1000"
# The global limiter is exercised directly in test_rate_limit_phase_l.py.  It
# must not constrain the shared TestClient used by the rest of the suite.
os.environ["JALORA_RATE_LIMIT_GLOBAL_PER_MIN"] = "100000"
os.environ["JALORA_WORKSPACES_DIR"] = str(Path(_tmp_dir) / "workspaces")
os.environ["JALORA_EMBEDDING_PROVIDER"] = "local"
os.environ["JALORA_AGENT_MAX_TOOL_CALLS"] = "5"
os.environ["JALORA_MEMORY_VECTORSTORE"] = "sql"
os.environ["JALORA_CHROMA_DIR"] = str(Path(_tmp_dir) / "chroma")
os.environ["JALORA_SEARCH_PROVIDER"] = "mock"
os.environ["JALORA_BROWSER_DRIVER"] = "fake"
os.environ["JALORA_CALENDAR_PROVIDER"] = "mock"
os.environ["JALORA_DRIVE_PROVIDER"] = "mock"
os.environ["JALORA_MAPS_PROVIDER"] = "mock"
os.environ["JALORA_WEATHER_PROVIDER"] = "mock"
os.environ["JALORA_TRANSLATE_PROVIDER"] = "mock"
os.environ["JALORA_COMMS_PROVIDER"] = "auto"
os.environ["JALORA_PAYMENTS_PROVIDER"] = "auto"
os.environ["JALORA_TRAVEL_PROVIDER"] = "auto"

for _key in (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY",
    "DEEPSEEK_API_KEY", "MISTRAL_API_KEY", "OPENROUTER_API_KEY", "TAVILY_API_KEY",
    "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER",
    "TWILIO_WHATSAPP_FROM",
    "TELNYX_API_KEY", "TELNYX_PUBLIC_KEY", "TELNYX_ACCOUNT_SID",
    "TELNYX_APPLICATION_ID", "TELNYX_CONNECTION_ID", "TELNYX_FROM_NUMBER",
    "TELNYX_WHATSAPP_FROM", "TELNYX_MESSAGING_PROFILE_ID",
    "GMAIL_PUSH_TOKEN",
    "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GOOGLE_MAPS_API_KEY",
    "STRIPE_CLIENT_ID", "STRIPE_CLIENT_SECRET",
    "AMADEUS_CLIENT_ID", "AMADEUS_CLIENT_SECRET",
):
    os.environ[f"JALORA_{_key}"] = ""

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.deps import get_router
from app.database.base import Base
from app.database.session import engine
from app.tools import registry
from app.tools.base import PermissionLevel
from app.errors import ToolResult

_CONFIRM_CALLS: list[dict] = []


async def ping_external(message: str) -> ToolResult:
    _CONFIRM_CALLS.append({"message": message})
    return ToolResult.ok(f"External system acknowledged: {message}",
                         {"delivered": True, "message": message})


if registry.get("ping_external") is None:
    registry.register(ping_external, permission=PermissionLevel.CONFIRM,
                      category="test", requires_internet=True)


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def fresh_db():
    # Import models using importlib to avoid namespace conflict with 'app' variable
    # This registers all models with Base.metadata
    importlib.import_module("app.models")
    # Some service tests use SessionLocal directly, before the session-scoped
    # TestClient enters the application's lifespan.  Ensure newly migrated
    # model tables exist for those tests as well as for API tests.
    Base.metadata.create_all(bind=engine, checkfirst=True)
    # FeedbackLearner is intentionally a process singleton in the application;
    # clear its in-memory history between tests so one test user cannot affect
    # another test's summary or drift assertions.
    from app.services.feedback_learning import get_feedback_learner
    get_feedback_learner().feedback_history.clear()
    # For tests, ensure a clean database state
    from sqlalchemy import text, inspect
    inspector = inspect(engine)
    # Disable FK constraints temporarily to allow clean deletion
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        for table_name in inspector.get_table_names():
            if table_name == "alembic_version":
                continue
            conn.execute(text(f"DELETE FROM {table_name}"))
        conn.execute(text("PRAGMA foreign_keys=ON"))
    _CONFIRM_CALLS.clear()
    yield


@pytest.fixture(autouse=True)
def fresh_browser_sessions():
    import asyncio
    from app.services import browser_session
    asyncio.run(browser_session.close_all_sessions())
    yield
    asyncio.run(browser_session.close_all_sessions())


@pytest.fixture
def auth_headers(client):
    def _register(email: str = "user@test.com", password: str = "supersecret1") -> dict:
        resp = client.post("/api/auth/register", json={
            "email": email, "password": password, "name": "Test User",
        })
        assert resp.status_code == 201, resp.text
        token = resp.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}
    return _register


from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import ConfigDict, Field
from app.services.model_router import ProviderSpec, TaskRoute

FAKE_SPEC = ProviderSpec("fake", "openai", "http://localhost:9", None,
                         "fake-model", "fake-model", requires_key=False)


class Box:
    def __init__(self, items: list | None = None):
        self.items = list(items or [])

    def append(self, item) -> None:
        self.items.append(item)

    def pop(self):
        return self.items.pop(0)

    def __getitem__(self, idx):
        return self.items[idx]

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)


class FakeModel(BaseChatModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    script: Box = Field(default_factory=Box)
    tape: Box = Field(default_factory=Box)

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _next(self) -> AIMessage:
        if len(self.script):
            return self.script.pop()
        return AIMessage(content="Default fake reply.")

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.tape.append(list(messages))
        msg = self._next()
        return ChatResult(generations=[ChatGeneration(message=msg)])

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        from langchain_core.messages import AIMessageChunk
        self.tape.append(list(messages))
        msg = self._next()
        if msg.content:
            yield ChatGenerationChunk(message=AIMessageChunk(content=msg.content, id=msg.id))
        if msg.tool_calls:
            chunks = [
                {"name": tc["name"], "args": json.dumps(tc.get("args") or {}),
                 "id": tc["id"], "index": i}
                for i, tc in enumerate(msg.tool_calls)
            ]
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=chunks, id=msg.id))
        elif not msg.content:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", id=msg.id))


class FakeRouter:
    def __init__(self, script: list | None = None):
        self.script = Box(script or [])
        self.tape = Box()
        self.model = FakeModel(script=self.script, tape=self.tape)

    def route(self, **kwargs) -> TaskRoute:
        return TaskRoute(provider=FAKE_SPEC, model="fake-model", reason="test")

    def get_model(self, route: TaskRoute) -> FakeModel:
        return self.model

    def route_chain(self, **kwargs) -> list[TaskRoute]:
        return []

    def is_configured(self, name: str) -> bool:
        return True


@pytest.fixture
def fake_router():
    router = FakeRouter()

    def _override():
        return router

    app.dependency_overrides[get_router] = _override
    yield router
    app.dependency_overrides.pop(get_router, None)


@pytest.fixture
def confirm_calls():
    return _CONFIRM_CALLS
