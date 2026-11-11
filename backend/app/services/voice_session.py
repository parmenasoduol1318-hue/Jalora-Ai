"""Live in-app voice call pipeline (Phase 1).

Transport-agnostic, server-side two-way voice: the client streams raw PCM16
audio frames up a WebSocket and receives synthesised PCM16 audio frames back,
interleaved with transcript/tool/status events. The pipeline is:

    audio frames -> energy VAD -> utterance -> STT -> LLM (tools bound) -> TTS -> audio frames

Design notes:
  * Local-first: STT/TTS default to faster-whisper + Piper, the LLM route is
    Ollama-first via ``ModelRouter.route_chain`` with automatic cross-provider
    fallback (the live-voice latency policy).
  * The session owns a ``Call`` row (``kind="inapp"``) so turns and the final
    summary persist exactly like phone calls; the owner sees one uniform record.
  * Everything is injectable (``stt``/``tts``/``llm``/``send``) so the whole loop
    is exercisable offline with the deterministic mock providers — no model,
    network or audio device required.

This module deliberately implements the pipeline natively rather than embedding
a full Pipecat ``Pipeline``: Phase 1 has no telephony transport, and a native
loop composes cleanly with Jalora's auth, tool registry and confirmation policy.
Pipecat remains the intended transport layer for Phase 3 (Twilio media streams).
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import numpy as np
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from sqlalchemy import select

from app.config import get_settings
from app.context import ToolContext, set_tool_context
from app.database.session import SessionLocal
from app.errors import ToolResult
from app.models.call import Call
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.services import voice as voice_service
from app.services.model_router import TaskRoute, get_model_router
from app.services.policy import (
    Decision,
    normalize_autonomy,
    normalize_tool_policies,
)
from app.services.tool_execution import invoke_registered_tool
from app.services.tool_preflight import validate_tool_arguments
from app.services.voice_providers import (
    STTChain,
    TTSChain,
    build_stt,
    build_tts,
    float32_to_pcm16,
    pcm16_to_float32,
    resample,
)
from app.tools import registry
from app.tools.base import ToolSpec
from app.tools.routing import voice_tool_names

logger = logging.getLogger("jalora.voice.live")

Send = Callable[[dict[str, Any]], Awaitable[None]]

# --- energy VAD + turn shaping (module-level so tests can retune) ---------- #
SILENCE_MS = 300            # trailing silence that ends an utterance (overridable via settings.voice_vad_silence_ms)
MIN_SPEECH_MS = 250         # ignore blips shorter than this
MAX_UTTERANCE_MS = 30_000   # hard cap so a long monologue still turns
ENERGY_RMS = 420.0          # int16 RMS above which a frame counts as speech
TTS_FRAME_MS = 20           # size of each audio frame pushed to the client
MAX_STEPS = 6               # model<->tool iterations before forcing a reply

GREETING = ("Hello! I'm Jalora, your AI assistant. "
            "I'm listening — go ahead and ask me anything.")

AFFIRMATIVE = {
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "go ahead", "do it",
    "please do", "please", "confirm", "confirmed", "correct", "right",
    "that's right", "sounds good", "affirmative", "absolutely",
}
NEGATIVE = {
    "no", "nope", "nah", "don't", "do not", "cancel", "stop", "not now",
    "never mind", "nevermind", "negative", "forget it",
}


def _utcnow_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _rms(pcm16: bytes) -> float:
    if not pcm16:
        return 0.0
    samples = np.frombuffer(pcm16, dtype="<i2")
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))


def _pieces(chunk: Any) -> list[str]:
    content = getattr(chunk, "content", chunk)
    if isinstance(content, str):
        return [content] if content else []
    if isinstance(content, list):
        out: list[str] = []
        for part in content:
            if isinstance(part, str) and part:
                out.append(part)
            elif isinstance(part, dict) and part.get("text"):
                out.append(str(part["text"]))
        return out
    return []


def _classify(text: str) -> str:
    """yes / no / unclear — for the CONFIRM-tool spoken gate."""
    words = (text or "").strip().lower().rstrip(".!")
    if words in AFFIRMATIVE:
        return "yes"
    if words in NEGATIVE:
        return "no"
    if words.startswith(("yes", "yeah", "sure", "ok", "go ahead", "do it", "please")):
        return "yes"
    if words.startswith(("no", "nope", "don't", "do not", "cancel", "stop")):
        return "no"
    return "unclear"


def _arg_summary(name: str, args: dict[str, Any]) -> str:
    parts = ", ".join(f"{k}={v}" for k, v in (args or {}).items() if v not in ("", None))
    return f"{name}({parts})" if parts else name


class VoiceSession:
    """One in-app live call. Construct via :meth:`create`."""

    def __init__(
        self,
        *,
        user_id: str,
        send: Send,
        stt: STTChain,
        tts: TTSChain,
        settings,
        call_id: str,
        purpose: str = "",
        conversation_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        router=None,
        pending_approval: dict[str, Any] | None = None,
        llm_provider: str | None = None,
        autonomy: int = 2,
        tool_policies: dict[str, str] | None = None,
        kind: str = "inapp",
    ) -> None:
        self.user_id = user_id
        self.call_id = call_id
        self.kind = kind
        self.purpose = purpose
        self.conversation_id = conversation_id
        self.task_id = task_id
        self.run_id = run_id
        self._router = router
        self._pending_approval = pending_approval
        self._autonomy = autonomy
        self._tool_policies = tool_policies or {}
        self._send = send
        self._stt = stt
        self._tts = tts
        self._settings = settings
        self._llm_provider = llm_provider

        self._messages: list[BaseMessage] = [
            SystemMessage(content=self._system_prompt())
        ]
        self._audio = bytearray()
        self._speech_ms = 0
        self._silence_ms = 0
        self._in_speech = False
        self._closed = False
        self._seq = 0
        self._processing = asyncio.Lock()

        self._pending_tool: dict[str, Any] | None = None
        self._last_route = ""
        # Live-call speed: downgrade to a fast provider/model mid-call when the
        # first-token latency blows the configured budget (default 1200 ms).
        self._fast_mode = False
        self._last_ttfb_ms = 0.0
        self._downgraded_from: str | None = None

    # ------------------------------------------------------------------ #
    # construction
    # ------------------------------------------------------------------ #

    @classmethod
    def create(
        cls,
        *,
        user_id: str,
        send: Send,
        purpose: str = "",
        conversation_id: str | None = None,
        task_id: str | None = None,
        router=None,
        settings=None,
        stt: STTChain | None = None,
        tts: TTSChain | None = None,
        call_id: str | None = None,
        kind: str = "inapp",
    ) -> "VoiceSession":
        settings = settings or get_settings()
        with SessionLocal() as db:
            user = db.get(User, user_id)
            if user is None:
                raise RuntimeError("Voice session owner was not found.")
            preferences = user.prefs if isinstance(user.prefs, dict) else {}
        autonomy = normalize_autonomy(preferences.get("autonomy"))
        tool_policies = normalize_tool_policies(preferences.get("tool_policies"))
        run_id = None
        pending_approval = None
        if call_id is None:
            call_id = cls._create_call_row(
                user_id=user_id, purpose=purpose,
                conversation_id=conversation_id, task_id=task_id, kind=kind,
            )
        else:
            from app.models.agent import Approval

            with SessionLocal() as db:
                call = db.get(Call, call_id)
                if call is None or call.user_id != user_id:
                    raise RuntimeError("The voice call was not found for this user.")
                conversation_id = call.conversation_id or conversation_id
                task_id = call.task_id or task_id
                run_id = call.run_id
                call.status = "in_progress"
                call.ended_at = None
                call.updated_at = datetime.now(timezone.utc)
                if task_id:
                    approval = db.scalar(
                        select(Approval)
                        .where(
                            Approval.user_id == user_id,
                            Approval.task_id == task_id,
                            Approval.status == "pending",
                        )
                        .order_by(Approval.created_at.desc())
                        .limit(1)
                    )
                    if approval is not None:
                        pending_approval = {
                            "id": approval.id,
                            "run_id": approval.run_id,
                            "task_id": approval.task_id,
                            "tool_name": approval.tool_name,
                        }
                db.commit()
        llm_provider = settings.voice_llm_provider if settings.voice_llm_provider != "auto" else None
        return cls(
            user_id=user_id, send=send,
            stt=stt or build_stt(settings), tts=tts or build_tts(settings),
            settings=settings, call_id=call_id, purpose=purpose,
            conversation_id=conversation_id, task_id=task_id, run_id=run_id,
            router=router, pending_approval=pending_approval,
            llm_provider=llm_provider, autonomy=autonomy,
            tool_policies=tool_policies, kind=kind,
        )

    @staticmethod
    def _create_call_row(
        *, user_id: str, purpose: str,
        conversation_id: str | None, task_id: str | None, kind: str = "inapp",
    ) -> str:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            call = Call(
                user_id=user_id, conversation_id=conversation_id, task_id=task_id,
                to_phone=None, provider="inapp", kind=kind,
                purpose=purpose or "an in-app call",
                status="in_progress", answers={}, transcript=[],
                max_turns=settings_call_max_turns(),
                max_duration_seconds=getattr(get_settings(), "voice_max_seconds", 600),
                created_at=now, updated_at=now, started_at=now,
            )
            db.add(call)
            db.commit()
            db.refresh(call)
            return call.id

    def _system_prompt(self) -> str:
        from app.agents.prompts import CALL_BRIEF, build_system_prompt

        base = f"{build_system_prompt()}\n\n{CALL_BRIEF}"
        if self.purpose:
            base += f"\n\nCurrent call goal: {self.purpose}"
        return base

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        set_tool_context(ToolContext(
            user_id=self.user_id, task_id=self.task_id,
            conversation_id=self.conversation_id,
        ))
        await self._emit({
            "type": "ready",
            "call_id": self.call_id,
            "conversation_id": self.conversation_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "kind": self.kind,
            "sample_rate": getattr(self._settings, "voice_sample_rate", 16000),
            "tts_sample_rate": getattr(self._settings, "voice_tts_sample_rate", 22050),
            "stt": self._stt.describe(),
            "tts": self._tts.describe(),
        })
        # Greet immediately on call start (like ChatGPT's live call):
        # persist to the call transcript, add to conversation history so the
        # LLM has context, emit the text for the UI, and speak via TTS so the
        # user hears *and* reads the greeting before they start talking.
        self._persist("agent", "greeting", GREETING)
        self._messages.append(AIMessage(content=GREETING))
        await self._emit({"type": "greeting", "text": GREETING})
        await self._speak(GREETING)
        if self._router is not None and self._pending_approval is not None:
            await self._emit_pending_approval()

    async def close(self, reason: str = "hangup") -> None:
        if self._closed:
            return
        self._closed = True
        summary = self._summarize(reason)
        with SessionLocal() as db:
            call = voice_service.get_call(db, self.call_id, self.user_id)
            voice_service.finalize(db, call, "completed", summary=summary)
        set_tool_context(None)
        await self._emit({"type": "ended", "call_id": self.call_id,
                          "reason": reason, "summary": summary})

    # ------------------------------------------------------------------ #
    # inbound events
    # ------------------------------------------------------------------ #

    async def handle_audio(self, pcm16: bytes) -> None:
        if self._closed or not pcm16:
            return
        frame_ms = int(len(pcm16) / 2 / max(1, self._sample_rate) * 1000)
        rms = _rms(pcm16)
        if rms >= ENERGY_RMS:
            self._in_speech = True
            self._speech_ms += frame_ms
            self._silence_ms = 0
            self._audio.extend(pcm16)
        elif self._in_speech:
            self._silence_ms += frame_ms
            self._audio.extend(pcm16)
        else:
            return  # leading silence: nothing to buffer

        total_ms = self._speech_ms + self._silence_ms
        silence_budget = int(getattr(self._settings, "voice_vad_silence_ms", SILENCE_MS))
        if (self._in_speech and self._silence_ms >= silence_budget
                and self._speech_ms >= MIN_SPEECH_MS):
            await self._finalize_utterance()
        elif total_ms >= MAX_UTTERANCE_MS:
            await self._finalize_utterance()

    async def handle_control(self, msg: dict[str, Any]) -> None:
        if self._closed:
            return
        kind = (msg.get("type") or msg.get("event") or "").lower()
        if kind == "end":            # push-to-talk: user finished speaking
            await self._finalize_utterance()
        elif kind in ("hangup", "stop", "close"):
            await self.close("hangup")
        elif kind == "text":         # typed fallback / testing
            text = (msg.get("text") or "").strip()
            if text:
                await self._respond_to_user(text)
        elif kind == "confirm":
            await self.decide_pending_approval(
                bool(msg.get("yes")),
                approval_id=str(msg.get("approval_id") or ""),
            )
        elif kind == "start":
            purpose = (msg.get("purpose") or "").strip()
            if purpose:
                self.purpose = purpose
        else:
            logger.debug("ignoring control %r", kind)

    async def decide_pending_approval(
        self,
        yes: bool,
        *,
        approval_id: str | None = None,
    ) -> None:
        if self._router is None:
            await self._resolve_confirmation(yes)
            return
        pending_id = str((self._pending_approval or {}).get("id") or "")
        await self._resolve_shared_approval(
            yes,
            approval_id if approval_id is not None else pending_id,
        )

    # ------------------------------------------------------------------ #
    # utterance -> reply
    # ------------------------------------------------------------------ #

    async def _finalize_utterance(self) -> None:
        audio = bytes(self._audio)
        self._audio = bytearray()
        self._speech_ms = self._silence_ms = 0
        self._in_speech = False
        if len(audio) < 2 * MIN_SPEECH_MS * self._sample_rate // 1000:
            return
        async with self._processing:
            text, provider = await self._stt.transcribe(audio, self._sample_rate)
            text = (text or "").strip()
            if not text:
                await self._emit({"type": "stt_empty", "provider": provider})
                return
            await self._respond_to_user(text)

    async def _respond_to_user(self, text: str) -> None:
        if self._router is not None:
            await self._respond_through_central_agent(text)
            return
        if self._pending_tool is not None:
            verdict = _classify(text)
            if verdict != "unclear":
                await self._resolve_confirmation(verdict == "yes")
                return
            # Unclear: fall through and treat as a fresh utterance, dropping the
            # stale pending request.
            self._pending_tool = None

        # Small-task instant fast-path (mirrors chat_service's math fast-path):
        # a bare math expression/equation is answered locally with SymPy — no
        # model round-trip — so "what's 2+2"-class queries are instant on the
        # voice channel too. Anything that isn't a direct local computation
        # falls through to the LLM agent loop.
        if (instant := await self._instant_answer(text)) is not None:
            # Persist the user's spoken question so the transcript stays
            # complete, then answer instantly (no model cost).
            self._persist("user", "speech", text)
            self._persist("agent", "say", instant)
            await self._emit({
                "type": "agent", "text": instant, "model": "sympy",
                "instant": True, "route_reason": "math-direct",
            })
            await self._speak(instant)
            return

        self._persist("user", "speech", text)
        await self._emit({"type": "user", "text": text})
        self._messages.append(HumanMessage(content=text))
        await self._agent_loop()

    async def _respond_through_central_agent(self, text: str) -> None:
        if self._pending_approval is not None:
            verdict = _classify(text)
            if verdict != "unclear":
                self._persist("user", "speech", text, persist_conversation=False)
                self._messages.append(HumanMessage(content=text))
                await self._resolve_shared_approval(
                    verdict == "yes",
                    str(self._pending_approval["id"]),
                )
                return
            self._persist("user", "speech", text, persist_conversation=False)
            self._messages.append(HumanMessage(content=text))
            reply = (
                "There is an action waiting for approval. Please say yes or no, "
                "or decide it in the Tasks screen."
            )
            await self._say_voice_reply(reply)
            return

        self._persist("user", "speech", text, persist_conversation=False)
        self._messages.append(HumanMessage(content=text))
        await self._emit({"type": "user", "text": text})
        from app.events import ListEmitter
        from app.services.chat_service import execute_chat

        outcome = await execute_chat(
            user_id=self.user_id,
            request_message=text,
            conversation_id=self.conversation_id,
            router=self._router,
            emitter=ListEmitter(),
            provider_override=self._llm_provider,
            mode="agent",
        )
        self.conversation_id = outcome.conversation_id or self.conversation_id
        self.task_id = outcome.task_id or self.task_id
        self.run_id = outcome.run_id or self.run_id
        self._link_call_to_execution()
        self._tag_execution_messages(voice_text=text)
        await self._emit({
            "type": "task_state",
            "conversation_id": self.conversation_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "status": outcome.status,
        })

        if outcome.status == "paused_approval" and outcome.approval:
            self._pending_approval = dict(outcome.approval)
            self._pending_approval.setdefault("task_id", outcome.task_id)
            await self._emit_pending_approval()
            return

        reply = outcome.reply or (
            "I couldn't complete that request. Check the task status for details."
            if outcome.status != "completed"
            else "Done."
        )
        await self._say_voice_reply(reply)

    async def _resolve_shared_approval(
        self,
        approve: bool,
        approval_id: str,
    ) -> None:
        pending = self._pending_approval
        if pending is None:
            await self._emit({
                "type": "approval_state",
                "status": "none_pending",
            })
            return
        if not approval_id or approval_id != str(pending.get("id") or ""):
            await self._emit({
                "type": "approval_error",
                "code": "APPROVAL_ID_MISMATCH",
                "detail": "Decide only the exact pending action shown in this call.",
            })
            return

        from app.models.agent import Approval

        with SessionLocal() as db:
            row = db.get(Approval, approval_id)
            if row is None or row.user_id != self.user_id:
                self._pending_approval = None
                await self._emit({
                    "type": "approval_state",
                    "status": "not_found",
                    "approval_id": approval_id,
                })
                return
            if row.status != "pending":
                self._pending_approval = None
                self._tag_execution_messages()
                await self._emit({
                    "type": "approval_state",
                    "status": row.status,
                    "approval_id": approval_id,
                    "task_id": row.task_id,
                    "run_id": row.run_id,
                })
                reply = (
                    f"This action was already {row.status}. "
                    "I will not submit it again."
                )
                await self._say_voice_reply(reply)
                return

        from app.errors import BadRequestError
        from app.services.chat_service import resume_approval

        try:
            outcome = await resume_approval(
                user_id=self.user_id,
                approval_id=approval_id,
                approve=approve,
                router=self._router,
            )
        except BadRequestError as exc:
            await self._emit({
                "type": "approval_error",
                "approval_id": approval_id,
                "code": exc.code,
                "detail": exc.message,
            })
            return

        self._pending_approval = (
            dict(outcome.approval)
            if outcome.status == "paused_approval" and outcome.approval
            else None
        )
        self.conversation_id = outcome.conversation_id or self.conversation_id
        self.task_id = outcome.task_id or self.task_id
        self.run_id = outcome.run_id or self.run_id
        self._link_call_to_execution()
        self._tag_execution_messages()
        await self._emit({
            "type": "task_state",
            "conversation_id": self.conversation_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "status": outcome.status,
            "approval_id": approval_id,
            "decision": "approved" if approve else "rejected",
        })
        if self._pending_approval is not None:
            await self._emit_pending_approval()
            return
        reply = outcome.reply or (
            "The action was not completed. Check the task status for details."
            if outcome.status != "completed"
            else "The approved task is complete."
        )
        await self._say_voice_reply(reply)

    async def _emit_pending_approval(self) -> None:
        pending = self._pending_approval
        if pending is None:
            return
        tool_name = str(pending.get("tool_name") or "action").replace("_", " ")
        approval_id = str(pending.get("id") or "")
        question = (
            f"The {tool_name} action is waiting for approval. "
            "Say yes or no, use the buttons, or decide it in Tasks."
        )
        self._persist("agent", "ask", question, persist_conversation=False)
        await self._emit({
            "type": "needs_confirmation",
            "approval_id": approval_id,
            "task_id": pending.get("task_id", self.task_id),
            "run_id": pending.get("run_id", self.run_id),
            "tool": pending.get("tool_name"),
            "question": question,
        })
        await self._speak(question)

    async def _say_voice_reply(self, text: str) -> None:
        self._persist("agent", "say", text, persist_conversation=False)
        self._messages.append(AIMessage(content=text))
        await self._emit({"type": "agent", "text": text})
        await self._speak(text)

    def _link_call_to_execution(self) -> None:
        with SessionLocal() as db:
            call = voice_service.get_call(db, self.call_id, self.user_id)
            call.conversation_id = self.conversation_id
            call.task_id = self.task_id
            call.run_id = self.run_id
            db.commit()

    def _tag_execution_messages(self, *, voice_text: str | None = None) -> None:
        if not self.conversation_id or not self.run_id:
            return
        with SessionLocal() as db:
            rows = db.scalars(
                select(Message)
                .where(Message.conversation_id == self.conversation_id)
                .order_by(Message.created_at.desc())
                .limit(30)
            ).all()
            for row in rows:
                meta = dict(row.meta or {})
                is_voice_user_message = (
                    voice_text is not None
                    and row.role == "user"
                    and row.content == voice_text
                    and "source" not in meta
                )
                if meta.get("run_id") != self.run_id and not is_voice_user_message:
                    continue
                meta.update({"source": "voice", "call_id": self.call_id})
                row.meta = meta
            db.commit()

    async def _instant_answer(self, text: str) -> str | None:
        """Deterministic, model-free reply for small tasks (math). Returns the
        reply text, or None to fall through to the LLM agent loop.

        Reuses the same SymPy engine as the chat math fast-path so voice and
        text agree on answers."""
        if not text:
            return None
        try:
            from app.services import math_engine as _math_engine

            signal = _math_engine.classify_math(text)
        except Exception:  # noqa: BLE001 - never let a classifier hiccup block voice
            return None
        if signal is None or not getattr(signal, "direct", False):
            return None
        try:
            result = _math_engine.solve(text, signal.intent)
        except Exception:  # noqa: BLE001
            return None
        if not getattr(result, "ok", False):
            return None
        try:
            return _math_engine.format_reply(result)
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------ #
    # agent loop (LLM + tools + TTS)
    # ------------------------------------------------------------------ #

    async def _agent_loop(self) -> None:
        if self._stream_tokens_enabled():
            await self._agent_loop_streaming()
        else:
            await self._agent_loop_buffered()

    def _stream_tokens_enabled(self) -> bool:
        """Incremental token->TTS overlap is opt-in (default off) so the buffered
        path — and the existing offline tests that monkeypatch ``_stream_ai`` —
        keep their behaviour until the streaming sink is proven stable."""
        return bool(getattr(self._settings, "voice_stream_tokens", False))

    async def _agent_loop_buffered(self) -> None:
        for _ in range(MAX_STEPS):
            ai, _provider = await self._stream_ai(self._messages)
            self._messages.append(ai)
            tool_calls = getattr(ai, "tool_calls", None) or []

            if tool_calls:
                await self._emit({"type": "tool_calls",
                                  "names": [tc.get("name") for tc in tool_calls]})
                confirmed = await self._execute_tools(tool_calls)
                if not confirmed:
                    return  # a CONFIRM tool is pending; wait for the user
                continue

            text = ai.content if isinstance(ai.content, str) else _join(ai.content)
            text = (text or "").strip()
            if not text:
                text = "Sorry, I did not catch that. Could you say it again?"
            self._persist("agent", "say", text)
            await self._emit({"type": "agent", "text": text})
            await self._speak(text)
            return

        fallback = "Let's keep this simple — could you repeat what you need?"
        self._persist("agent", "say", fallback)
        await self._emit({"type": "agent", "text": fallback})
        await self._speak(fallback)

    async def _agent_loop_streaming(self) -> None:
        """Incremental path: LLM tokens are pushed into TTS as they arrive so
        speech overlaps generation. The reply text is spoken incrementally
        inside ``_stream_ai_incremental``; here we persist the final text, emit
        a completion event, and handle tool-call turns."""
        for _ in range(MAX_STEPS):
            ai, provider = await self._stream_ai_incremental(self._messages)
            self._messages.append(ai)
            tool_calls = getattr(ai, "tool_calls", None) or []

            if tool_calls:
                await self._emit({"type": "tool_calls",
                                  "names": [tc.get("name") for tc in tool_calls]})
                confirmed = await self._execute_tools(tool_calls)
                if not confirmed:
                    return  # a CONFIRM tool is pending; wait for the user
                continue

            text = ai.content if isinstance(ai.content, str) else _join(ai.content)
            text = (text or "").strip()
            self._persist("agent", "say", text)
            await self._emit({"type": "agent", "text": text, "model": provider,
                              "streamed": True})
            return

        fallback = "Let's keep this simple — could you repeat what you need?"
        self._persist("agent", "say", fallback)
        await self._emit({"type": "agent", "text": fallback})
        await self._speak(fallback)

    def _fast_provider(self) -> str | None:
        """Provider name to degrade to when over the latency budget, or None."""
        v = getattr(self._settings, "voice_fallback_provider", None)
        return v or None

    def _fast_model(self) -> str | None:
        """Smaller model name to prefer in fast-mode, or None to keep default."""
        v = getattr(self._settings, "voice_fast_model", None)
        return v or None

    async def _maybe_downgrade(self, first_token_ms: float,
                               current_provider: str) -> bool:
        """Evaluate the live-call latency budget. If the time-to-first-token
        for the current provider exceeds ``voice_latency_budget_ms`` and a fast
        fallback is configured, lock the session into fast-mode so the next
        turn uses the faster provider/model, and emit a ``provider_switched_fast``
        event to the client.

        Returns True when a downgrade was applied this turn. The current turn
        still completes on its original provider (we can't abort mid-token
        safely); the downgrade governs all subsequent turns in this call.
        """
        budget = int(getattr(self._settings, "voice_latency_budget_ms", 1200))
        self._last_ttfb_ms = first_token_ms
        if first_token_ms <= budget:
            return False
        if self._fast_mode:
            return False
        fast_provider = self._fast_provider()
        fast_model = self._fast_model()
        if not fast_provider and not fast_model:
            return False
        self._fast_mode = True
        self._downgraded_from = current_provider
        target = fast_provider or f"{current_provider}(fast-model)"
        await self._emit({
            "type": "provider_switched_fast",
            "from": current_provider,
            "to": target,
            "ttfb_ms": round(first_token_ms, 1),
            "budget_ms": budget,
            "reason": "ttfb_exceeds_latency_budget_ms",
        })
        return True

    def _route_memory_mb(self, route: TaskRoute) -> int | None:
        """Estimated resident footprint of ``route``'s model in MB, or None
        when the footprint is unknown (e.g. cloud models absent from the
        local Ollama snapshot). Honors an explicit ``memory_mb`` annotation
        on the route so tests and explicit routing can opt in without Ollama
        state."""
        mb = getattr(route, "memory_mb", None)
        if mb is not None:
            return int(mb)
        try:
            from app.services import ollama_memory as om
            info = om._get_snapshot().tags.get(route.model)
            if info is not None and info.size_gb > 0:
                return int(round(om.estimate_resident_gb(info, 0.0) * 1024))
        except Exception:  # noqa: BLE001 - a broken probe must never block routing
            logger.debug("memory footprint lookup failed for %r", route.model)
        return None

    async def _within_memory_budget(self, routes: list[TaskRoute]) -> list[TaskRoute]:
        """Filter out routes whose model exceeds ``voice_model_memory_budget_mb``
        (§1.14/§1.17), emitting ``model_memory_limited`` for each skip so
        the client sees why a heavier provider was not selected. Routes with
        an unknown footprint are always allowed; if every route is over budget
        the full chain is returned (best-effort) so a turn never starves."""
        budget = int(getattr(self._settings, "voice_model_memory_budget_mb", 4096))
        kept: list[TaskRoute] = []
        for route in routes:
            mb = self._route_memory_mb(route)
            if mb is not None and mb > budget:
                await self._emit({
                    "type": "model_memory_limited",
                    "from_model": route.model,
                    "provider": route.provider.name,
                    "memory_mb": mb,
                    "budget_mb": budget,
                    "reason": "exceeds_voice_memory_budget_mb",
                })
                continue
            kept.append(route)
        if not kept and routes:
            kept = list(routes)
        return kept

    async def _stream_ai(self, messages: list[BaseMessage]) -> tuple[AIMessage, str]:
        """Stream a reply, advancing providers on failure (Ollama-first chain)."""
        router = get_model_router()
        # In fast-mode (set by the latency-budget downgrade) prefer the
        # configured fast provider/model so subsequent turns stay snappy.
        provider_override = self._llm_provider
        model_override = None
        if self._fast_mode:
            provider_override = self._fast_provider() or provider_override
            model_override = self._fast_model()
        chain = router.route_chain(
            provider_override=provider_override,
            ollama_first=True,
            model_override=model_override,
        )
        chain = await self._within_memory_budget(chain)
        names = voice_tool_names()
        tools = [s.tool for s in registry.all() if s.name in names]
        last_error: Exception | None = None
        for route in chain:
            model = router.get_model(route)
            if tools:
                model = model.bind_tools(tools)
            full = None
            start = time.perf_counter()
            first_token_ms = 0.0
            try:
                async for chunk in model.astream(messages):
                    if full is None:
                        first_token_ms = (time.perf_counter() - start) * 1000.0
                    full = chunk if full is None else full + chunk
            except Exception as exc:  # noqa: BLE001 - try the next provider
                last_error = exc
                logger.warning("voice LLM provider %s failed: %s",
                               route.provider.name, exc)
                await self._emit({"type": "provider_fallback",
                                  "provider": route.provider.name})
                continue
            if full is None:
                last_error = RuntimeError("empty stream")
                continue
            self._last_route = route.provider.name
            if first_token_ms:
                await self._maybe_downgrade(first_token_ms, route.provider.name)
            ai = AIMessage(
                content=full.content,
                tool_calls=getattr(full, "tool_calls", None) or [],
                id=getattr(full, "id", None) or "",
            )
            return ai, route.provider.name
        if last_error is not None:
            raise last_error
        raise RuntimeError("no usable chat provider")

    async def _stream_ai_incremental(self, messages: list[BaseMessage]) -> tuple[AIMessage, str]:
        """Incremental variant of ``_stream_ai``: each LLM token chunk is emitted
        as a ``token`` event and pushed straight into the streaming TTS sink, so
        the assistant begins speaking before the model has finished generating.

        Gated by ``voice_stream_tokens`` (default off). Mirrors ``_stream_ai``'s
        provider-fallback semantics — including the latency-budget downgrade —
        but interleaves token->TTS instead of buffering the whole reply first.
        """
        router = get_model_router()
        # In fast-mode (set by the latency-budget downgrade) prefer the
        # configured fast provider/model so subsequent turns stay snappy.
        provider_override = self._llm_provider
        model_override = None
        if self._fast_mode:
            provider_override = self._fast_provider() or provider_override
            model_override = self._fast_model()
        chain = router.route_chain(
            provider_override=provider_override,
            ollama_first=True,
            model_override=model_override,
        )
        chain = await self._within_memory_budget(chain)
        names = voice_tool_names()
        tools = [s.tool for s in registry.all() if s.name in names]
        last_error: Exception | None = None
        for route in chain:
            model = router.get_model(route)
            if tools:
                model = model.bind_tools(tools)
            start = time.perf_counter()
            state = {"full": None, "first_ms": 0.0}

            async def _token_pieces():
                async for chunk in model.astream(messages):
                    if state["full"] is None:
                        state["first_ms"] = (time.perf_counter() - start) * 1000.0
                    state["full"] = chunk if state["full"] is None else state["full"] + chunk
                    for piece in _pieces(chunk):
                        if not piece:
                            continue
                        await self._emit({"type": "token", "text": piece})
                        yield piece

            tts_rate = getattr(self._settings, "voice_tts_sample_rate", 22050)
            audio_started = False
            try:
                await self._emit({"type": "audio_begin", "sample_rate": tts_rate,
                                  "provider": route.provider.name, "bytes": 0})
                audio_started = True
                async for audio, rate, _prov in self._tts.synthesize_stream(_token_pieces()):
                    await self._push_tts_chunk(audio, rate)
            except Exception as exc:  # noqa: BLE001 - try the next provider
                if audio_started:
                    await self._emit({"type": "audio_end", "seq": self._seq})
                    audio_started = False
                last_error = exc
                logger.warning("voice LLM provider %s failed: %s",
                               route.provider.name, exc)
                await self._emit({"type": "provider_fallback",
                                  "provider": route.provider.name})
                continue
            if state["full"] is None:
                last_error = RuntimeError("empty stream")
                continue
            if audio_started:
                await self._emit({"type": "audio_end", "seq": self._seq})
                audio_started = False
            self._last_route = route.provider.name
            if state["first_ms"]:
                await self._maybe_downgrade(state["first_ms"], route.provider.name)
            full = state["full"]
            ai = AIMessage(
                content=full.content,
                tool_calls=getattr(full, "tool_calls", None) or [],
                id=getattr(full, "id", None) or "",
            )
            return ai, route.provider.name
        if last_error is not None:
            raise last_error
        raise RuntimeError("no usable chat provider")

    async def _execute_tools(self, tool_calls: list[dict[str, Any]]) -> bool:
        """Apply shared policy and gate tools on spoken confirmation as needed.

        Returns True when the loop may continue, False when a confirmation is
        pending (the caller must stop and wait for the user's answer).
        """
        for tc in tool_calls:
            name = tc.get("name", "")
            raw_args = tc.get("args")
            args = raw_args if isinstance(raw_args, dict) else None
            call_id = str(tc.get("id", ""))
            spec = registry.get(name)
            if spec is None:
                await self._record_tool_failure(
                    name=name,
                    call_id=call_id,
                    error=ToolResult.fail(
                        "UNKNOWN_TOOL", f"'{name}' is not registered.",
                        message="The requested voice capability is unavailable.",
                    ),
                    args={},
                )
                continue

            if args is None:
                await self._record_tool_failure(
                    name=name,
                    call_id=call_id,
                    error=ToolResult.fail(
                        "INVALID_TOOL_ARGUMENTS",
                        "Voice tool arguments must be an object.",
                    ),
                    args={},
                )
                continue
            args, validation_errors = validate_tool_arguments(spec, args)
            if validation_errors is not None:
                await self._record_tool_failure(
                    name=name,
                    call_id=call_id,
                    error=ToolResult.fail(
                        "INVALID_TOOL_ARGUMENTS",
                        "Voice tool arguments do not match the capability schema.",
                        data={"errors": validation_errors},
                    ),
                    args=args,
                )
                continue

            decision = self._tool_policy_decision(spec)
            if not decision.allowed:
                await self._record_tool_failure(
                    name=name,
                    call_id=call_id,
                    error=ToolResult.fail(decision.code, decision.detail),
                    args=args,
                )
                continue
            if decision.require_approval:
                self._pending_tool = {
                    "name": name, "args": args, "call_id": call_id,
                }
                summary = _arg_summary(name, args)
                question = (
                    f"This needs your okay: { _spoken(name) } — {summary}. "
                    "Should I go ahead?"
                )
                self._persist("agent", "ask", question)
                await self._emit({
                    "type": "needs_confirmation", "tool": name,
                    "args": args, "summary": summary, "question": question,
                })
                await self._speak(question)
                return False

            await self._invoke_voice_tool(
                spec=spec, args=args, call_id=call_id, approved=None,
            )
        return True

    def _tool_policy_decision(self, spec: ToolSpec) -> Decision:
        from app.services.policy import decide

        return decide(
            permission=spec.permission,
            autonomy=self._autonomy,
            policy=self._tool_policies.get(spec.name, ""),
            tags=spec.tags,
        )

    async def _record_tool_failure(
        self,
        *,
        name: str,
        call_id: str,
        error: ToolResult,
        args: dict[str, Any],
        approved: bool | None = None,
        permission: str | None = None,
    ) -> None:
        self._messages.append(ToolMessage(
            content=error.model_dump_json(), tool_call_id=call_id,
        ))
        self._persist("tool", name, _arg_summary(name, args))
        event: dict[str, Any] = {
            "type": "tool_result",
            "name": name,
            "success": False,
            "error_code": error.error.code if error.error else "TOOL_FAILED",
        }
        if permission is not None:
            event["permission"] = permission
        if approved is not None:
            event["approved"] = approved
        await self._emit(event)

    async def _invoke_voice_tool(
        self,
        *,
        spec: ToolSpec,
        args: dict[str, Any],
        call_id: str,
        approved: bool | None,
    ) -> None:
        result = await invoke_registered_tool(
            spec, args, label="Voice capability",
        )
        if not result.success and result.error is not None and result.error.code in {
            "TOOL_EXCEPTION", "TIMEOUT", "MALFORMED_TOOL_RESULT",
        }:
            await self._record_tool_failure(
                name=spec.name,
                call_id=call_id,
                error=result,
                args=args,
                approved=approved,
                permission=spec.permission.name,
            )
            return

        self._messages.append(ToolMessage(
            content=result.model_dump_json(), tool_call_id=call_id,
        ))
        self._persist("tool", spec.name, _arg_summary(spec.name, args))
        event: dict[str, Any] = {
            "type": "tool_result",
            "name": spec.name,
            "permission": spec.permission.name,
            "success": result.success,
        }
        if result.error is not None:
            event["error_code"] = result.error.code
        if approved is not None:
            event["approved"] = approved
        await self._emit(event)

    async def _resolve_confirmation(self, yes: bool) -> None:
        pending = self._pending_tool
        self._pending_tool = None
        if pending is None:
            return
        spec = registry.get(pending["name"])
        if not yes or spec is None:
            self._messages.append(ToolMessage(
                content='{"ok": false, "error": "declined by user"}',
                tool_call_id=pending["call_id"],
            ))
            self._persist("tool", pending["name"], "declined")
            await self._emit({"type": "tool_result", "name": pending["name"],
                              "permission": (
                                  spec.permission.name
                                  if spec is not None else "UNKNOWN"
                              ),
                              "approved": False})
            reply = "Okay, I won't do that."
            self._persist("agent", "say", reply)
            await self._emit({"type": "agent", "text": reply})
            await self._speak(reply)
            return
        decision = self._tool_policy_decision(spec)
        if not decision.allowed:
            await self._record_tool_failure(
                name=pending["name"],
                call_id=pending["call_id"],
                error=ToolResult.fail(decision.code, decision.detail),
                args=pending["args"],
                approved=True,
                permission=spec.permission.name,
            )
            reply = "I can't do that because your current action policy blocks it."
            self._persist("agent", "say", reply)
            await self._emit({"type": "agent", "text": reply})
            await self._speak(reply)
            return
        await self._invoke_voice_tool(
            spec=spec,
            args=pending["args"],
            call_id=pending["call_id"],
            approved=True,
        )
        await self._agent_loop()

    # ------------------------------------------------------------------ #
    # TTS + transport
    # ------------------------------------------------------------------ #

    async def _speak(self, text: str) -> None:
        try:
            audio, rate, provider = await self._tts.synthesize(text)
        except Exception as exc:  # noqa: BLE001 - never fail the turn on TTS
            logger.warning("TTS failed: %s", exc)
            await self._emit({"type": "tts_error", "detail": str(exc)})
            return
        await self._emit_audio_frames(audio, rate, provider)

    async def _emit_audio_frames(self, audio: bytes, rate: int, provider: str) -> None:
        """Buffered whole-utterance path: emit ``audio_begin`` + data frames +
        ``audio_end`` (used by ``_speak``). Preserves the original event shape
        so the existing offline audio-assertion tests keep seeing the same
        events."""
        if self._closed:
            return
        target = getattr(self._settings, "voice_tts_sample_rate", rate)
        if rate != target and audio:
            audio = float32_to_pcm16(resample(pcm16_to_float32(audio), rate, target))
        await self._emit({"type": "audio_begin", "sample_rate": target,
                          "provider": provider, "bytes": len(audio)})
        await self._push_tts_chunk(audio, target)
        await self._emit({"type": "audio_end", "seq": self._seq})

    async def _push_tts_chunk(self, audio: bytes, rate: int) -> None:
        """Resample ``audio`` to the output rate (if needed) and emit only its
        ``audio`` data frames — no ``audio_begin``/``audio_end`` boundaries.
        Shared by the buffered and incremental token->TTS streaming paths."""
        if not audio or self._closed:
            return
        target = getattr(self._settings, "voice_tts_sample_rate", rate)
        if rate != target:
            audio = float32_to_pcm16(resample(pcm16_to_float32(audio), rate, target))
            rate = target
        frame_bytes = max(2, int(rate * TTS_FRAME_MS / 1000) * 2)
        for off in range(0, len(audio), frame_bytes):
            if self._closed:
                return
            frame = audio[off:off + frame_bytes]
            self._seq += 1
            await self._emit({
                "type": "audio", "seq": self._seq, "sample_rate": rate,
                "data": base64.b64encode(frame).decode("ascii"),
            })
            await asyncio.sleep(0)  # let the socket flush between frames

    async def _emit(self, event: dict[str, Any]) -> None:
        event.setdefault("call_id", self.call_id)
        event.setdefault("ts", _utcnow_iso())
        try:
            await self._send(event)
        except Exception:  # noqa: BLE001 - client may have gone away
            logger.debug("dropping event %s (socket closed)", event.get("type"))

    # ------------------------------------------------------------------ #
    # persistence helpers
    # ------------------------------------------------------------------ #

    def _persist(
        self,
        role: str,
        kind: str,
        text: str,
        *,
        persist_conversation: bool = True,
    ) -> None:
        try:
            with SessionLocal() as db:
                call = voice_service.get_call(db, self.call_id, self.user_id)
                voice_service.append_turn(db, call, role, kind, text, ts=time.time())
                if (
                    persist_conversation
                    and self.conversation_id
                    and role in {"user", "agent"}
                ):
                    conversation = db.get(Conversation, self.conversation_id)
                    if conversation is not None and conversation.user_id == self.user_id:
                        db.add(Message(
                            conversation_id=conversation.id,
                            role="assistant" if role == "agent" else "user",
                            content=text,
                            meta={"source": "voice", "call_id": self.call_id},
                        ))
                        conversation.updated_at = datetime.now(timezone.utc)
                        db.commit()
        except Exception:  # noqa: BLE001 - transcript must never break the call
            logger.exception("failed to persist voice turn")

    def _summarize(self, reason: str) -> str:
        user_lines = [
            m.content for m in self._messages
            if isinstance(m, HumanMessage) and isinstance(m.content, str)
        ]
        goal = self.purpose or "an in-app conversation"
        last = user_lines[-1].strip() if user_lines else "no spoken turns"
        return (
            f"Goal: {goal}. The call ran as an in-app voice session with "
            f"{len(user_lines)} spoken turn{'s' if len(user_lines) != 1 else ''} "
            f"and ended ({reason}). Last thing the user said: {last}"
        )

    # ------------------------------------------------------------------ #
    # misc
    # ------------------------------------------------------------------ #

    @property
    def _sample_rate(self) -> int:
        return int(getattr(self._settings, "voice_sample_rate", 16000))


def _join(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            p if isinstance(p, str) else str(p.get("text", ""))
            for p in content
        )
    return str(content or "")


def _spoken(name: str) -> str:
    return {
        "send_sms": "send a text message",
        "send_whatsapp": "send a WhatsApp message",
        "create_event": "add a calendar event",
    }.get(name, name.replace("_", " "))


def settings_call_max_turns() -> int:
    return int(getattr(get_settings(), "call_max_turns", 20))
