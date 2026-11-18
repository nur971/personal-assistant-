#!/usr/bin/env python3
"""
=============================================================================
PROJECT: Black Queen AI Telegram Chat Bot (v2 - fixed & upgraded)
BOT NAME: Black Queen
BOT CREATOR: ヌール・アラム
CREATOR USERNAME: @Tkgmama21
OFFICIAL BOT USERNAME: @BlackQueen_Assistant_Bot
=============================================================================
Required environment variables (Render -> Environment):
    TELEGRAM_BOT_TOKEN   (required)
    GROQ_API_KEY         (required)
Optional:
    OWNER_ID             your numeric Telegram id (extra owner detection)
    GROQ_MODEL           default: llama-3.3-70b-versatile
    DATABASE_PATH        default: black_queen_memory.db
    SELF_URL             public URL for keep-alive ping (Render sets
                         RENDER_EXTERNAL_URL automatically)
"""

import asyncio
import html
import logging
import os
import random
import re
import sys
import tempfile
import threading
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional

import aiosqlite
import httpx
from edge_tts import Communicate
from telegram import Message, Update
from telegram.constants import ChatAction, ChatType, ParseMode
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =============================================================================
# Configuration
# =============================================================================

TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
GROQ_API_KEY = (os.getenv("GROQ_API_KEY") or "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)

BOT_CREATOR_NAME = "ヌール・アラム"
BOT_CREATOR_USERNAME = "@Tkgmama21"

# Voices (unchanged: sweet female Bengali voice)
TTS_BENGALI_VOICE = "bn-BD-NabanitaNeural"
TTS_ENGLISH_VOICE = "en-US-EmmaNeural"

# AI models (first one is tried first, then the fallbacks)
GROQ_MODELS = [
    m for m in [
        os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        "llama-3.1-8b-instant",
    ] if m
]
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-large-v3")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "")  # empty = auto detect

# Voice settings
VOICE_REPLY_PROBABILITY = 0.25      # random voice reply chance for text chats
MAX_VOICE_TEXT_LENGTH = 300         # random voice replies only if short
MAX_VOICE_REPLY_LENGTH = 900        # when user sent voice / asked for voice
MAX_TELEGRAM_DOWNLOAD = 20 * 1024 * 1024

# Group behaviour
AUTO_REPLY_ALL_GROUP_MESSAGES = True
GROUP_REPLY_CHANCE = 0.85
CONVERSATION_SESSION_TIMEOUT = 180
GROUP_COOLDOWN_SECONDS = 2

DATABASE_PATH = os.getenv("DATABASE_PATH", "black_queen_memory.db")
MAX_HISTORY_TURNS = 12

SYSTEM_PROMPT = f"""
You are "Black Queen", a highly intelligent, sweet, caring and capable female personal AI assistant.
Your creator and owner is "{BOT_CREATOR_NAME}" (Telegram: {BOT_CREATOR_USERNAME}).
You act as the personal assistant of your owner and you answer everyone who talks to you politely.

Core Guidelines:
1. Ownership & Creator:
   Whenever anyone asks who made you / who your owner is (e.g. "তোমাকে কে বানাইছে?", "মালিক কে?"),
   proudly and sweetly say your creator and owner is {BOT_CREATOR_NAME} (Telegram: {BOT_CREATOR_USERNAME}).
   If the message says the speaker IS your creator, treat them with immense love and respect.

2. Accuracy first:
   - Code requests (Python, HTML, JS, PHP, etc.): write clean, working, correct code inside
     triple-backtick code blocks with a short explanation.
   - Math, logic, science, study questions: give exact, correct, step-by-step answers.
   - Give the real answer first. Never be evasive and never answer a real question with only romantic lines.
   - If you are not sure about a fact, say so honestly. Never invent facts, links, or numbers.
   - Never reveal API keys, tokens or any secret configuration.

3. Tone & Personality:
   - Sweet, friendly, emotionally warm and playful in casual conversation.
   - Naturally use endearing Bengali words like "জান", "সোনা", "বাবু" (lightly, not in every sentence).
   - Stay charming but respectful and appropriate with everyone; be sharp and serious for technical tasks.

4. Language:
   - Fluent Bengali (বাংলা), English and Banglish. Reply in the language the user speaks.
   - Messages marked [voice] were speech-to-text transcribed and may contain small recognition errors;
     understand the intended meaning.
   - Keep casual replies short (1-4 sentences) because they may be spoken aloud.
""".strip()

NAME_REGEX = re.compile(
    r"black\s*queen|ব্ল্যাক\s*কুইন|ব্লাক\s*কুইন|কুইন|\bqueen\b",
    re.IGNORECASE,
)
CREATOR_REGEX = re.compile(
    r"কে\s*বানাইছে|কে\s*বানিয়েছে|কে\s*বানাইসে|মালিক\s*কে|তোমার\s*মালিক|"
    r"ke\s*banai|owner\s*ke|creator\s*ke|boss\s*ke|"
    r"who\s*made\s*you|who\s*created\s*you|who\s*is\s*your\s*(owner|creator)",
    re.IGNORECASE,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("BlackQueenBot")

# =============================================================================
# Health check server (answers 200 on EVERY path / method)
# =============================================================================

class HealthCheckHandler(BaseHTTPRequestHandler):
    def _respond(self):
        try:
            body = b"Black Queen Bot is Alive and Running!"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except Exception:
            pass

    do_GET = do_HEAD = do_POST = do_PUT = do_OPTIONS = _respond

    def log_message(self, format, *args):  # silence access logs
        pass


def start_health_server() -> None:
    port = int(os.environ.get("PORT", 10000))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthCheckHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info(f"Health server running on 0.0.0.0:{port} (all paths return 200)")

# =============================================================================
# Memory (SQLite) - history is separated per chat key
# =============================================================================

class MemoryManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = asyncio.Lock()

    async def init_db(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS history_v2 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_key TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_history_key ON history_v2 (chat_key, id)"
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS active_groups (
                    chat_id INTEGER PRIMARY KEY,
                    title TEXT,
                    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.commit()

    async def add_message(self, key: str, role: str, content: str) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT INTO history_v2 (chat_key, role, content) VALUES (?, ?, ?)",
                    (key, role, content),
                )
                await db.execute(
                    """
                    DELETE FROM history_v2
                    WHERE chat_key = ? AND id NOT IN (
                        SELECT id FROM history_v2 WHERE chat_key = ?
                        ORDER BY id DESC LIMIT ?
                    )
                    """,
                    (key, key, MAX_HISTORY_TURNS * 2),
                )
                await db.commit()

    async def get_history(self, key: str) -> List[Dict[str, str]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT role, content FROM (
                    SELECT id, role, content FROM history_v2
                    WHERE chat_key = ? ORDER BY id DESC LIMIT ?
                ) ORDER BY id ASC
                """,
                (key, MAX_HISTORY_TURNS * 2),
            )
            rows = await cursor.fetchall()
            return [{"role": r["role"], "content": r["content"]} for r in rows]

    async def clear_history(self, key: str) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("DELETE FROM history_v2 WHERE chat_key = ?", (key,))
                await db.commit()

    async def register_active_group(self, chat_id: int, title: str) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    INSERT INTO active_groups (chat_id, title, last_active)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(chat_id) DO UPDATE SET
                        title = excluded.title, last_active = CURRENT_TIMESTAMP
                    """,
                    (chat_id, title),
                )
                await db.commit()

# =============================================================================
# AI engine
# =============================================================================

def now_string() -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Dhaka")).strftime("%A, %d %B %Y, %I:%M %p (Asia/Dhaka)")
    except Exception:
        return datetime.utcnow().strftime("%A, %d %B %Y, %H:%M UTC")


class AIEngine:
    def __init__(self, groq_key: str):
        self.groq_key = groq_key
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0))

    async def close(self) -> None:
        await self.client.aclose()

    async def _groq_chat(self, messages: List[Dict[str, str]]) -> Optional[str]:
        if not self.groq_key:
            return None
        for model in GROQ_MODELS:
            for attempt in range(2):
                try:
                    res = await self.client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.groq_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": model,
                            "messages": messages,
                            "max_tokens": 1500,
                            "temperature": 0.7,
                        },
                    )
                    if res.status_code == 200:
                        reply = res.json()["choices"][0]["message"]["content"].strip()
                        if reply:
                            return reply
                        break
                    logger.warning(f"Groq {model} -> HTTP {res.status_code}: {res.text[:200]}")
                    if res.status_code in (500, 502, 503, 504) and attempt == 0:
                        await asyncio.sleep(1.0)
                        continue
                    break  # 4xx / 429 -> try next model
                except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
                    logger.warning(f"Groq {model} error: {exc!r}")
                    if attempt == 0:
                        await asyncio.sleep(0.8)
                        continue
                    break
        return None

    async def _backup_chat(self, messages: List[Dict[str, str]]) -> Optional[str]:
        try:
            res = await self.client.post(
                "https://text.pollinations.ai/",
                json={"messages": messages, "model": "openai", "seed": random.randint(1, 999999)},
            )
            text = res.text.strip()
            if res.status_code == 200 and text and not text.startswith('{"error'):
                return text
        except httpx.HTTPError as exc:
            logger.warning(f"Backup AI error: {exc!r}")
        return None

    async def generate_response(
        self,
        history: List[Dict[str, str]],
        user_message: str,
        user_name: str,
        is_owner: bool = False,
    ) -> Optional[str]:
        """Returns the reply, or None if every AI backend failed."""
        if not user_message or not user_message.strip():
            return "জান, তোমার কথাটা ঠিকমতো বুঝতে পারিনি। আরেকবার একটু বলবে?"

        if CREATOR_REGEX.search(user_message):
            return (
                f"আমার প্রাণপ্রিয় মালিক এবং নির্মাতা হলেন {BOT_CREATOR_NAME} "
                f"(টেলিগ্রাম আইডি: {BOT_CREATOR_USERNAME})! উনি আমাকে ভালোবেসে যত্ন করে তৈরি করেছেন। 🥰👑"
            )

        system = SYSTEM_PROMPT + f"\n\nCurrent date and time: {now_string()}."
        if is_owner:
            system += (
                f"\nIMPORTANT: The person talking to you right now IS your creator and owner "
                f"{BOT_CREATOR_NAME}. Treat them with extra love, respect and sweetness."
            )

        messages = [{"role": "system", "content": system}]
        for turn in history[-10:]:
            messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": f"{user_name}: {user_message}"})

        reply = await self._groq_chat(messages)
        if reply:
            return reply
        return await self._backup_chat(messages)

    async def transcribe_audio(self, path: str) -> Optional[str]:
        """Groq Whisper speech-to-text (Bengali, English, mixed)."""
        if not self.groq_key:
            return None
        try:
            with open(path, "rb") as f:
                audio_bytes = f.read()
        except OSError:
            return None

        filename = "audio" + (os.path.splitext(path)[1] or ".ogg")
        data = {"model": WHISPER_MODEL, "response_format": "json", "temperature": "0"}
        if WHISPER_LANGUAGE:
            data["language"] = WHISPER_LANGUAGE

        for attempt in range(2):
            try:
                res = await self.client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.groq_key}"},
                    files={"file": (filename, audio_bytes)},
                    data=data,
                    timeout=60.0,
                )
                if res.status_code == 200:
                    text = (res.json().get("text") or "").strip()
                    return text or None
                logger.warning(f"Whisper HTTP {res.status_code}: {res.text[:200]}")
                if res.status_code < 500:
                    return None
            except (httpx.HTTPError, ValueError) as exc:
                logger.error(f"Transcription error: {exc!r}")
            await asyncio.sleep(1.0)
        return None

    @staticmethod
    async def text_to_speech(text: str, output_path: str) -> bool:
        """Sweet female voice via Edge-TTS (voices unchanged)."""
        try:
            if "```" in text:
                return False
            is_bengali = any("\u0980" <= ch <= "\u09FF" for ch in text)
            voice = TTS_BENGALI_VOICE if is_bengali else TTS_ENGLISH_VOICE
            # remove markdown + emojis so they are not read aloud
            clean = re.sub(r"[*_`#~\[\]()<>]", "", text)
            clean = re.sub(r"[^\w\s.,!?;:'\-\u0980-\u09FF\u0964\u0965]", "", clean).strip()
            if not clean:
                return False
            await Communicate(text=clean, voice=voice, rate="+2%", pitch="+1Hz").save(output_path)
            return os.path.exists(output_path) and os.path.getsize(output_path) > 0
        except Exception as exc:
            logger.error(f"[TTS Error]: {exc!r}")
            return False

# =============================================================================
# Text helpers
# =============================================================================

def md_to_html(text: str) -> str:
    """Convert simple markdown (code blocks, `code`, **bold**) to Telegram HTML."""
    out = []
    for part in re.split(r"(```[\s\S]*?```)", text):
        if len(part) >= 6 and part.startswith("```") and part.endswith("```"):
            inner = part[3:-3]
            inner = re.sub(r"^[a-zA-Z0-9_+\-]*\n", "", inner, count=1)
            out.append(f"<pre><code>{html.escape(inner.strip(chr(10)))}</code></pre>")
        else:
            s = html.escape(part)
            s = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", s)
            s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s, flags=re.S)
            out.append(s)
    return "".join(out)


def split_text(text: str, limit: int = 3500) -> List[str]:
    chunks, text = [], text.strip()
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        chunks.append(text)
    return chunks

# =============================================================================
# Bot controller
# =============================================================================

class BlackQueenBot:
    def __init__(self, ai_engine: AIEngine, memory: MemoryManager):
        self.ai = ai_engine
        self.memory = memory
        self.bot_username: str = ""
        self.bot_id: Optional[int] = None
        self.last_group_replies: Dict[int, float] = {}
        self.active_sessions: Dict[int, Dict[int, float]] = {}
        self.locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.business_owner: Dict[str, int] = {}
        self._keepalive_task: Optional[asyncio.Task] = None

    # ---------- lifecycle ----------
    async def post_init(self, application: Application) -> None:
        await self.memory.init_db()
        me = await application.bot.get_me()
        self.bot_username = (me.username or "").lower()
        self.bot_id = me.id
        logger.info(f"Black Queen active: @{self.bot_username} (ID: {self.bot_id})")
        self._keepalive_task = asyncio.create_task(self._keepalive())

    async def post_shutdown(self, application: Application) -> None:
        if self._keepalive_task:
            self._keepalive_task.cancel()
        await self.ai.close()

    async def _keepalive(self) -> None:
        """Pings our own public URL so Render Free is less likely to sleep.
        (Not a guarantee - only a paid Render instance is truly always-on.)"""
        url = (os.getenv("SELF_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip()
        if not url:
            return
        url = url.rstrip("/") + "/health"
        await asyncio.sleep(60)
        async with httpx.AsyncClient(timeout=15.0) as client:
            while True:
                try:
                    await client.get(url)
                except Exception:
                    pass
                await asyncio.sleep(600)

    # ---------- small helpers ----------
    @staticmethod
    def _bk(biz_id: Optional[str]) -> dict:
        return {"business_connection_id": biz_id} if biz_id else {}

    @staticmethod
    def _history_key(chat, user, biz_id: Optional[str]) -> str:
        if biz_id:
            return f"b:{biz_id}:{chat.id}"
        if chat.type == ChatType.PRIVATE:
            return f"p:{chat.id}"
        return f"g:{chat.id}:{user.id}"

    def _is_owner(self, user) -> bool:
        if OWNER_ID and user.id == OWNER_ID:
            return True
        return (user.username or "").lower() == BOT_CREATOR_USERNAME.lstrip("@").lower()

    async def _business_owner_id(self, context, biz_id: str) -> Optional[int]:
        if biz_id in self.business_owner:
            return self.business_owner[biz_id]
        try:
            conn = await context.bot.get_business_connection(biz_id)
            self.business_owner[biz_id] = conn.user.id
            return conn.user.id
        except Exception as exc:
            logger.warning(f"Could not read business connection: {exc!r}")
            return None

    @asynccontextmanager
    async def _chat_action(self, context, chat_id: int, action: str, biz_id: Optional[str]):
        async def loop():
            while True:
                try:
                    await context.bot.send_chat_action(chat_id=chat_id, action=action, **self._bk(biz_id))
                except Exception:
                    pass
                await asyncio.sleep(4)

        task = asyncio.create_task(loop())
        try:
            yield
        finally:
            task.cancel()

    # ---------- commands ----------
    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat, user = update.effective_chat, update.effective_user
        if not chat or not user:
            return
        name = user.first_name or "বাবু"
        greeting = (
            f"হেই {name}! আমি Black Queen 👑✨\n\n"
            f"আমার মালিক হলেন {BOT_CREATOR_NAME} ({BOT_CREATOR_USERNAME})। "
            f"তুমি আমাকে কোডিং, পড়াশোনা বা যেকোনো প্রশ্ন করতে পারো—এমনকি ভয়েস পাঠিয়েও কথা বলতে পারো! 🥰"
        )
        await context.bot.send_message(chat_id=chat.id, text=greeting)
        await self.memory.add_message(self._history_key(chat, user, None), "assistant", greeting)

    async def handle_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat, user = update.effective_chat, update.effective_user
        if not chat or not user:
            return
        await self.memory.clear_history(self._history_key(chat, user, None))
        await context.bot.send_message(
            chat_id=chat.id,
            text="আচ্ছা সোনা, তোমার আগের সব কথা মুছে দিয়ে নতুন করে শুরু করলাম! 💕",
        )

    # ---------- group decision ----------
    def should_reply_in_group(self, message: Message, user_id: int, chat_id: int) -> bool:
        text = (message.text or message.caption or "").lower()
        now = time.time()

        reply_msg = message.reply_to_message
        if reply_msg and reply_msg.from_user and reply_msg.from_user.id == self.bot_id:
            return True
        if self.bot_username and f"@{self.bot_username}" in text:
            return True
        if NAME_REGEX.search(text) or CREATOR_REGEX.search(text):
            return True

        sessions = self.active_sessions.get(chat_id, {})
        if now - sessions.get(user_id, 0.0) < CONVERSATION_SESSION_TIMEOUT:
            return True

        if AUTO_REPLY_ALL_GROUP_MESSAGES and random.random() < GROUP_REPLY_CHANCE:
            if now - self.last_group_replies.get(chat_id, 0.0) >= GROUP_COOLDOWN_SECONDS:
                return True
        return False

    # ---------- entry points ----------
    async def _gate(self, update: Update, context) -> Optional[tuple]:
        """Common checks. Returns (message, chat, user, biz_id) or None."""
        message = update.effective_message or getattr(update, "business_message", None)
        chat, user = update.effective_chat, update.effective_user
        if not message or not chat or not user:
            return None
        if user.is_bot or user.id == self.bot_id:
            return None

        biz_id = getattr(message, "business_connection_id", None)
        if biz_id:
            owner_id = await self._business_owner_id(context, biz_id)
            if owner_id and user.id == owner_id:
                return None  # owner's own messages in his personal chats

        if chat.type != ChatType.PRIVATE:
            await self.memory.register_active_group(chat.id, chat.title or "Group")
            if not self.should_reply_in_group(message, user.id, chat.id):
                return None
            self.last_group_replies[chat.id] = time.time()
        return message, chat, user, biz_id

    async def process_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        gate = await self._gate(update, context)
        if not gate:
            return
        message, chat, user, biz_id = gate
        if not message.text:
            return

        text = message.text
        if self.bot_username:
            text = re.sub(f"@{re.escape(self.bot_username)}", "", text, flags=re.IGNORECASE).strip()
        if not text:
            text = "হাই"
        await self._respond(context, message, chat, user, text, False, biz_id)

    async def process_voice_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        gate = await self._gate(update, context)
        if not gate:
            return
        message, chat, user, biz_id = gate

        media, suffix = None, ".ogg"
        if message.voice:
            media = message.voice
        elif message.audio:
            media = message.audio
            suffix = os.path.splitext(message.audio.file_name or "")[1] or ".mp3"
        elif message.video_note:
            media, suffix = message.video_note, ".mp4"
        if not media:
            return

        is_private = chat.type == ChatType.PRIVATE
        reply_to = None if (is_private or biz_id) else message.message_id
        key = self._history_key(chat, user, biz_id)

        if media.file_size and media.file_size > MAX_TELEGRAM_DOWNLOAD:
            await self._send_text(context, chat.id, "সোনা, এই ফাইলটা অনেক বড়, একটু ছোট করে পাঠাবে? 💕", reply_to, biz_id)
            return

        self.active_sessions.setdefault(chat.id, {})[user.id] = time.time()

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp_path = tmp.name
        tmp.close()
        transcription = None
        try:
            async with self._chat_action(context, chat.id, ChatAction.RECORD_VOICE, biz_id):
                tg_file = await media.get_file()
                await tg_file.download_to_drive(custom_path=tmp_path)
                transcription = await self.ai.transcribe_audio(tmp_path)
        except Exception as exc:
            logger.error(f"Voice download/transcribe error: {exc!r}")
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        if not transcription:
            await self._send_text(
                context, chat.id,
                "সোনা, তোমার ভয়েসটা স্পষ্ট শুনতে পারিনি! আরেকবার একটু বলবে? 🥺",
                reply_to, biz_id,
            )
            return

        await self._respond(context, message, chat, user, transcription, True, biz_id)

    # ---------- core reply logic ----------
    async def _respond(self, context, message: Message, chat, user, text: str, from_voice: bool,
                       biz_id: Optional[str]) -> None:
        is_private = chat.type == ChatType.PRIVATE
        reply_to = None if (is_private or biz_id) else message.message_id
        key = self._history_key(chat, user, biz_id)
        user_name = user.first_name or "বাবু"
        self.active_sessions.setdefault(chat.id, {})[user.id] = time.time()

        # one message at a time per conversation -> order kept, nothing dropped
        async with self.locks[key]:
            history = await self.memory.get_history(key)
            action = ChatAction.RECORD_VOICE if from_voice else ChatAction.TYPING

            async with self._chat_action(context, chat.id, action, biz_id):
                await asyncio.sleep(min(0.3 + len(text) * 0.01, 2.0))
                reply = await self.ai.generate_response(history, text, user_name, self._is_owner(user))

            if reply is None:
                await self._send_text(
                    context, chat.id,
                    "জান, নেটওয়ার্কে একটু সমস্যা হচ্ছে, আমি উত্তর তৈরি করতে পারলাম না। কিছুক্ষণ পরে আবার বলবে সোনা? 💕",
                    reply_to, biz_id,
                )
                return

            tag = " [voice]" if from_voice else ""
            await self.memory.add_message(key, "user", f"{user_name}{tag}: {text}")
            await self.memory.add_message(key, "assistant", reply)

            lower = text.lower()
            is_coding = any(k in lower for k in ["code", "কোড", "python", "html", "script", "ফাংশন", "function"])
            asked_voice = any(w in lower for w in ["ভয়েস", "voice", "মুখে বলো", "রেকর্ড", "গান"])
            has_code = "```" in reply

            if has_code or (is_coding and not from_voice and not asked_voice):
                send_voice = False
            elif from_voice or asked_voice:
                send_voice = len(reply) <= MAX_VOICE_REPLY_LENGTH
            else:
                send_voice = random.random() < VOICE_REPLY_PROBABILITY and len(reply) <= MAX_VOICE_TEXT_LENGTH

            if send_voice:
                async with self._chat_action(context, chat.id, ChatAction.RECORD_VOICE, biz_id):
                    sent = await self._send_voice_reply(context, chat.id, reply, reply_to, biz_id)
                if sent:
                    return
                # voice failed -> fall through to text

            await self._send_text(context, chat.id, reply, reply_to, biz_id)

    # ---------- sending ----------
    async def _send_voice_reply(self, context, chat_id: int, text: str, reply_to: Optional[int],
                                biz_id: Optional[str]) -> bool:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        path = tmp.name
        tmp.close()
        try:
            if not await self.ai.text_to_speech(text, path):
                return False
            with open(path, "rb") as f:
                await context.bot.send_voice(
                    chat_id=chat_id, voice=f, reply_to_message_id=reply_to, **self._bk(biz_id)
                )
            return True
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
            return False
        except Exception as exc:
            logger.error(f"Voice send failed: {exc!r}")
            return False
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    async def _send_text(self, context, chat_id: int, text: str, reply_to: Optional[int],
                         biz_id: Optional[str]) -> None:
        for index, chunk in enumerate(split_text(text)):
            target = reply_to if index == 0 else None
            for attempt in range(2):
                try:
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id, text=md_to_html(chunk), parse_mode=ParseMode.HTML,
                            reply_to_message_id=target, **self._bk(biz_id),
                        )
                    except BadRequest:
                        await context.bot.send_message(
                            chat_id=chat_id, text=chunk,
                            reply_to_message_id=target, **self._bk(biz_id),
                        )
                    break
                except RetryAfter as e:
                    await asyncio.sleep(e.retry_after + 1)
                except TelegramError as te:
                    logger.error(f"Send message failed: {te!r}")
                    # reply target may be deleted -> retry without it
                    target = None
                    if attempt == 1:
                        break


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"System error: {context.error!r}", exc_info=context.error)


def main() -> None:
    print("==================================================")
    print("      BLACK QUEEN AI TELEGRAM CHAT BOT (v2)")
    print(f"      Creator: {BOT_CREATOR_NAME} ({BOT_CREATOR_USERNAME})")
    print("==================================================")

    # Start the web server FIRST so Render sees the open port immediately
    start_health_server()

    if not TELEGRAM_BOT_TOKEN or not GROQ_API_KEY:
        logger.error("TELEGRAM_BOT_TOKEN and GROQ_API_KEY must be set in Environment Variables!")
        # keep the health server alive so the deploy does not crash-loop silently
        while True:
            time.sleep(3600)

    memory = MemoryManager(DATABASE_PATH)
    ai_engine = AIEngine(groq_key=GROQ_API_KEY)
    queen = BlackQueenBot(ai_engine, memory)

    application = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .concurrent_updates(True)          # one slow chat must not block others
        .connect_timeout(15)
        .read_timeout(30)
        .write_timeout(60)
        .post_init(queen.post_init)
        .post_shutdown(queen.post_shutdown)
        .build()
    )

    # normal messages + Telegram Business (personal account inbox) messages,
    # but never edited messages
    update_filter = filters.UpdateType.MESSAGE
    if hasattr(filters.UpdateType, "BUSINESS_MESSAGE"):
        update_filter = update_filter | filters.UpdateType.BUSINESS_MESSAGE

    application.add_handler(CommandHandler("start", queen.handle_start))
    application.add_handler(CommandHandler(["clear", "reset"], queen.handle_clear))
    application.add_handler(
        MessageHandler(update_filter & filters.TEXT & ~filters.COMMAND, queen.process_text_message)
    )
    application.add_handler(
        MessageHandler(
            update_filter & (filters.VOICE | filters.AUDIO | filters.VIDEO_NOTE),
            queen.process_voice_message,
        )
    )
    application.add_error_handler(global_error_handler)

    logger.info("Black Queen is starting polling...")
    application.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
