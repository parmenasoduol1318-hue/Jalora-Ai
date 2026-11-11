from __future__ import annotations
import sys
sys.stdout.reconfigure(encoding='utf-8')
from app.config import get_settings as _real_get_settings
import app.services.voice_session as vs_mod
base = _real_get_settings()
s2 = base.model_copy(update={"voice_stream_tokens": True})
print('type(s2):', type(s2).__module__ + '.' + type(s2).__name__)
print('base.voice_stream_tokens:', getattr(base, 'voice_stream_tokens', '<<missing>>'))
print('s2.voice_stream_tokens:', getattr(s2, 'voice_stream_tokens', '<<missing>>'))
print('s2.budget:', s2.voice_latency_budget_ms, 'fallback:', s2.voice_fallback_provider)
# now patch and create a session to see what _settings it actually holds
import asyncio
vs_mod.get_settings = lambda: s2
uid = None
from app.database.session import SessionLocal
from app.models.user import User
with SessionLocal() as db:
    uid = str(db.scalar(__import__('sqlalchemy').select(User.id).limit(1)))
print('user_id:', uid)

async def main():
    sess = vs_mod.VoiceSession.create(user_id=uid, send=lambda ev: None)
    print('session._settings is s2:', sess._settings is s2)
    print('session._settings.voice_stream_tokens:', sess._settings.voice_stream_tokens)
    print('session._stream_tokens_enabled():', sess._stream_tokens_enabled())
    await sess.close('hangup')

asyncio.run(main())
