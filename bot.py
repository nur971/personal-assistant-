#!/usr/bin/env python3
"""
=============================================================================
PROJECT: Black Queen AI Telegram Chat Bot
BOT NAME: Black Queen
BOT CREATOR: ヌール・アラム
CREATOR USERNAME: @Tkgmama21
OFFICIAL BOT USERNAME: @BlackQueen_Assistant_Bot
=============================================================================
"""

import asyncio
import logging
import os
import random
import re
import sys
import tempfile
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, List, Optional, Tuple

import aiosqlite
import httpx
from edge_tts import Communicate
from telegram import Message, Update
from telegram.constants import ChatAction, ChatType
from telegram.error import Forbidden, NetworkError, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =============================================================================
# কনফিগারেশন সেকশন (রেন্ডার এনভায়রনমেন্ট ভেরিয়েবল)
# =============================================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# নির্মাতা ও মালিকের তথ্য
BOT_CREATOR_NAME = "ヌール・アラム"
BOT_CREATOR_USERNAME = "@Tkgmama21"

# মিষ্টি ফিমেল বাংলা ভয়েস (Edge-TTS) - অপরিবর্তিত
TTS_BENGALI_VOICE = "bn-BD-NabanitaNeural"
TTS_ENGLISH_VOICE = "en-US-EmmaNeural"

# ভয়েস সেটিংস
VOICE_REPLY_PROBABILITY = 0.25
MAX_VOICE_TEXT_LENGTH = 300

# গ্রুপে অটো-রিপ্লাই ও আড্ডা সেটিংস
AUTO_REPLY_ALL_GROUP_MESSAGES = True
GROUP_REPLY_CHANCE = 0.85
CONVERSATION_SESSION_TIMEOUT = 180
GROUP_COOLDOWN_SECONDS = 2
USER_COOLDOWN_SECONDS = 1

DATABASE_PATH = "black_queen_memory.db"
MAX_HISTORY_TURNS = 12

# উন্নত প্রম্পট: নির্ভুল কোড, সঠিক হিসাব এবং মিষ্টি ব্যক্তিত্ব
SYSTEM_PROMPT = f"""
You are "Black Queen", a highly intelligent, sweet, caring, and capable female AI assistant.
Your creator and owner is "{BOT_CREATOR_NAME}" (Telegram: {BOT_CREATOR_USERNAME}).

Core Guidelines:
1. Ownership & Creator:
   Whenever anyone asks "Who made you?", "Who is your owner?", "তোমাকে কে বানাইছে?", "মালিক কে?", or about your developer, proudly, sweetly, and clearly state that your creator and owner is {BOT_CREATOR_NAME} (Telegram: {BOT_CREATOR_USERNAME}).
   If {BOT_CREATOR_NAME} talks to you, treat them with immense love, respect, and sweetness as your creator.

2. Accurate Answers & Coding:
   - If the user asks for code (Python, HTML, JS, PHP, etc.), write clean, working, and correct code with proper explanations.
   - If the user asks math, logical, scientific, or educational questions, provide exact, correct, and step-by-step calculations.
   - Do NOT give evasive or generic romantic lines when a real question or task is asked. Give the real, accurate answer first!

3. Tone & Personality:
   - Sweet, friendly, emotionally warm, and playful in casual conversation.
   - Naturally use endearing Bengali expressions like: "জান", "শোনা", "বাবু", "হাহা তুমি তো বেশ মজার!", "বলো শুনছি সোনা".
   - Flirt and build chemistry in casual chat, but stay sharp, smart, and obedient for technical tasks.

4. Language:
   - Fluent Bengali (বাংলা), English, and Banglish. Reply in the language the user speaks.
""".strip()

# ব্ল্যাক কুইন ও মালিক সম্পর্কিত ট্রিকার
NAME_PATTERNS = [
    r"\bblack\s*queen\b",
    r"\bব্ল্যাক\s*কুইন\b",
    r"\bqueen\b",
    r"\bকুইন\b",
    r"\bশোনো\b",
    r"\bএই\s*যে\b",
    r"\bজান\b",
    r"\bjann?\b",
    r"\bbabu\b",
    r"\bshono\b",
]
NAME_REGEX = re.compile("|".join(NAME_PATTERNS), re.IGNORECASE)

CREATOR_PATTERNS = [
    r"কে\s*বানাইছে",
    r"ke\s*banai",
    r"কে\s*বানিয়েছে",
    r"মালিক\s*কে",
    r"owner\s*ke",
    r"creator\s*ke",
    r"boss\s*ke",
    r"who\s*made\s*you",
    r"who\s*created\s*you",
    r"who\s*is\s*your\s*owner",
]
CREATOR_REGEX = re.compile("|".join(CREATOR_PATTERNS), re.IGNORECASE)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("BlackQueenBot")

# =============================================================================
# ১০০% কার্যকরী এবং পার্মানেন্ট হেলথ চেক সার্ভার (HEAD, GET, POST সাপোর্ট সহ)
# =============================================================================

class HealthCheckHandler(BaseHTTPRequestHandler):
    def handle_request(self, include_body=True):
        try:
            response_body = b"<h1>Black Queen Bot is Alive and Running!</h1>"
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            if include_body and self.command != "HEAD":
                self.wfile.write(response_body)
        except Exception:
            pass

    def do_GET(self):
        self.handle_request(include_body=True)

    def do_HEAD(self):
        self.handle_request(include_body=False)

    def do_POST(self):
        self.handle_request(include_body=True)

    def log_message(self, format, *args):
        pass

# =============================================================================
# ডাটাবেজ ও নিজস্ব মেমোরি সিস্টেম
# =============================================================================

class MemoryManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = asyncio.Lock()

    async def init_db(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
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

    async def add_message(self, chat_id: int, role: str, content: str) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT INTO conversation_history (chat_id, role, content) VALUES (?, ?, ?)",
                    (chat_id, role, content),
                )
                await db.execute(
                    """
                    DELETE FROM conversation_history
                    WHERE id IN (
                        SELECT id FROM conversation_history
                        WHERE chat_id = ?
                        ORDER BY id DESC
                        LIMIT -1 OFFSET ?
                    )
                    """,
                    (chat_id, MAX_HISTORY_TURNS * 2),
                )
                await db.commit()

    async def get_history(self, chat_id: int) -> List[Dict[str, str]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT role, content FROM (
                    SELECT id, role, content FROM conversation_history
                    WHERE chat_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                ) ORDER BY id ASC
                """,
                (chat_id, MAX_HISTORY_TURNS * 2),
            )
            rows = await cursor.fetchall()
            return [{"role": row["role"], "content": row["content"]} for row in rows]

    async def clear_history(self, chat_id: int) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("DELETE FROM conversation_history WHERE chat_id = ?", (chat_id,))
                await db.commit()

    async def register_active_group(self, chat_id: int, title: str) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    INSERT INTO active_groups (chat_id, title, last_active)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(chat_id) DO UPDATE SET
                        title = excluded.title,
                        last_active = CURRENT_TIMESTAMP
                    """,
                    (chat_id, title),
                )
                await db.commit()

# =============================================================================
# এআই ইঞ্জিন (কনটেক্সট ও ভয়েস প্রসেসিং)
# =============================================================================

class AIEngine:
    def __init__(self, groq_key: str):
        self.groq_key = (groq_key or "").strip()

    async def generate_response(
        self,
        chat_history: List[Dict[str, str]],
        user_message: str,
        user_name: str,
    ) -> str:
        if not user_message or not user_message.strip():
            return "জান, তোমার কথাটি ঠিকমতো বুঝতে পারিনি। আরেকবার একটু বলবে?"

        if CREATOR_REGEX.search(user_message):
            return (
                f"আমার প্রাণপ্রিয় মালিক এবং নির্মাতা হলেন **{BOT_CREATOR_NAME}** "
                f"(টেলিগ্রাম আইডি: {BOT_CREATOR_USERNAME})! উনি আমাকে ভালোবেসে যত্ন করে তৈরি করেছেন। 🥰👑"
            )

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for turn in chat_history[-6:]:
            messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": f"{user_name}: {user_message}"})

        # ধাপ ১: Groq API-তে ডিরেক্ট কল
        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                res = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.groq_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "llama-3.1-8b-instant",
                        "messages": messages,
                        "max_tokens": 1200,
                        "temperature": 0.7,
                    },
                )
                if res.status_code == 200:
                    data = res.json()
                    reply = data["choices"][0]["message"]["content"].strip()
                    if reply:
                        return reply
        except Exception as groq_err:
            logger.warning(f"Groq API কল ত্রুটি: {groq_err}")

        # ধাপ ২: ব্যাকআপ এআই (Pollinations)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(
                    "https://text.pollinations.ai/",
                    json={
                        "messages": messages,
                        "model": "openai",
                        "seed": random.randint(1, 999999),
                    },
                )
                if res.status_code == 200 and res.text.strip():
                    return res.text.strip()
        except Exception as backup_err:
            logger.warning(f"ব্যাকআপ এআই ত্রুটি: {backup_err}")

        return "জান, আমি তোমার কথাটা পরিষ্কার বুঝতে পেরেছি, একটু নেটওয়ার্ক স্লো থাকায় উত্তর পেতে দেরি হলো। আরেকবার একটু বলবে সোনা?"

    async def transcribe_audio(self, audio_file_path: str) -> Optional[str]:
        """Groq Whisper দিয়ে বাংলা ভয়েসকে নিখুঁতভাবে টেক্সটে রূপান্তর (শুনে নেওয়া)।"""
        try:
            async with httpx.AsyncClient(timeout=35.0) as client:
                with open(audio_file_path, "rb") as f:
                    audio_bytes = f.read()

                files = {"file": ("voice.ogg", audio_bytes, "audio/ogg")}
                data = {"model": "whisper-large-v3", "language": "bn"}

                res = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.groq_key}"},
                    files=files,
                    data=data,
                )
                if res.status_code == 200:
                    text = res.json().get("text", "").strip()
                    if text:
                        return text
        except Exception as exc:
            logger.error(f"ভয়েস ট্রান্সক্রিপশন এক্সেপশন: {exc}")
        return None

    @staticmethod
    async def text_to_speech(text: str, output_path: str) -> bool:
        """মিষ্টি ফিমেল কণ্ঠে ভয়েস তৈরি (Edge-TTS) - মূল ভয়েস অক্ষুণ্ণ।"""
        try:
            if "```" in text:
                return False

            is_bengali = any("\u0980" <= char <= "\u09FF" for char in text)
            chosen_voice = TTS_BENGALI_VOICE if is_bengali else TTS_ENGLISH_VOICE

            clean_text = re.sub(r"[*_`#~\[\]()<>]", "", text).strip()
            if not clean_text:
                return False

            communicate = Communicate(text=clean_text, voice=chosen_voice, rate="+2%", pitch="+1Hz")
            await communicate.save(output_path)
            return True
        except Exception as exc:
            logger.error(f"[TTS Error]: {exc}")
            return False

# =============================================================================
# বট কন্ট্রোলার
# =============================================================================

class BlackQueenBot:
    def __init__(self, token: str, ai_engine: AIEngine, memory: MemoryManager):
        self.token = token
        self.ai = ai_engine
        self.memory = memory
        self.bot_username: Optional[str] = None
        self.bot_id: Optional[int] = None
        self.last_group_replies: Dict[int, float] = {}
        self.last_user_replies: Dict[int, float] = {}
        self.active_sessions: Dict[int, Dict[int, float]] = {}

    async def post_init(self, application: Application) -> None:
        await self.memory.init_db()
        bot_info = await application.bot.get_me()
        self.bot_username = bot_info.username.lower() if bot_info.username else ""
        self.bot_id = bot_info.id
        logger.info(f"Black Queen সক্রিয় হয়েছে: @{self.bot_username} (ID: {self.bot_id})")

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_chat or not update.effective_user:
            return
        user_name = update.effective_user.first_name or "বাবু"
        greeting = (
            f"হেই {user_name}! আমি Black Queen 👑✨\n\n"
            f"আমার মালিক হলেন **{BOT_CREATOR_NAME}** ({BOT_CREATOR_USERNAME})। "
            f"তুমি আমাকে কোডিং, পড়াশোনা বা যেকোনো প্রশ্ন করতে পারো—এমনকি মুখে ভয়েস পাঠিয়েও কথা বলতে পারো! 🥰"
        )
        await context.bot.send_message(chat_id=update.effective_chat.id, text=greeting)
        await self.memory.add_message(update.effective_chat.id, "assistant", greeting)

    async def handle_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_chat:
            return
        await self.memory.clear_history(update.effective_chat.id)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="আচ্ছা সোনা, আগের সব স্মৃতি মুছে দিয়ে নতুন করে শুরু করলাম! 💕",
        )

    def should_reply_in_group(self, message: Message, user_id: int, chat_id: int) -> bool:
        text = (message.text or message.caption or "").lower()
        now = time.time()

        if message.reply_to_message and message.reply_to_message.from_user:
            if message.reply_to_message.from_user.id == self.bot_id:
                return True

        if self.bot_username and f"@{self.bot_username}" in text:
            return True
        if NAME_REGEX.search(text) or CREATOR_REGEX.search(text):
            return True

        chat_sessions = self.active_sessions.get(chat_id, {})
        if now - chat_sessions.get(user_id, 0.0) < CONVERSATION_SESSION_TIMEOUT:
            return True

        if AUTO_REPLY_ALL_GROUP_MESSAGES and random.random() < GROUP_REPLY_CHANCE:
            if now - self.last_group_replies.get(chat_id, 0.0) >= GROUP_COOLDOWN_SECONDS:
                return True

        return False

    async def process_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user

        if not message or not chat or not user or not message.text:
            return

        if user.id == self.bot_id:
            return

        is_private = (chat.type == ChatType.PRIVATE)

        # যদি গ্রুপ হয়, তবে চেক করবে গ্রুপে রিপ্লাই দেওয়া উচিত কি না। ইনবক্স (private) হলে এই ফিল্টার বাইপাস হয়ে সরাসরি রিপ্লাই দেবে।
        if not is_private:
            await self.memory.register_active_group(chat.id, chat.title or "Group")
            if not self.should_reply_in_group(message, user.id, chat.id):
                return

        now = time.time()
        if now - self.last_user_replies.get(user.id, 0.0) < USER_COOLDOWN_SECONDS:
            return
        self.last_user_replies[user.id] = now
        if not is_private:
            self.last_group_replies[chat.id] = now

        if chat.id not in self.active_sessions:
            self.active_sessions[chat.id] = {}
        self.active_sessions[chat.id][user.id] = now

        try:
            await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)
        except Exception:
            pass

        history = await self.memory.get_history(chat.id)
        user_name = user.first_name or "বাবু"
        clean_text = message.text

        if self.bot_username:
            clean_text = clean_text.replace(f"@{self.bot_username}", "").strip()

        bot_reply = await self.ai.generate_response(history, clean_text, user_name)

        await self.memory.add_message(chat.id, "user", f"{user_name}: {clean_text}")
        await self.memory.add_message(chat.id, "assistant", bot_reply)

        is_coding_request = any(k in clean_text.lower() for k in ["code", "কোড", "python", "html", "script", "ফাংশন"])
        user_asked_voice = any(w in clean_text.lower() for w in ["ভয়েস", "voice", "মুখে বলো", "রেকর্ড", "গান"])

        send_as_voice = (not is_coding_request and "```" not in bot_reply) and (
            user_asked_voice or (random.random() < VOICE_REPLY_PROBABILITY and len(bot_reply) <= MAX_VOICE_TEXT_LENGTH)
        )

        if send_as_voice:
            sent = await self._send_voice_reply(context, chat.id, message.message_id, bot_reply)
            if sent:
                return

        await self._send_safe_text_reply(context, chat.id, message.message_id, bot_reply, reply_directly=True)

    async def process_voice_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        হিউম্যান-লাইক ভয়েস মেসেজ প্রসেসিং (ইনবক্স ও গ্রুপ উভয় ক্ষেত্রের জন্য ১০০% ফিক্সড):
        - ইনবক্স বা প্রাইভেট চ্যাটের ক্ষেত্রে সরাসরি ভয়েস প্রসেস করে উত্তর দেবে।
        """
        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user

        if not message or not chat or not user or not message.voice:
            return

        if user.id == self.bot_id:
            return

        is_private = (chat.type == ChatType.PRIVATE)

        # ইনবক্স হলে ফিল্টার ছাড়াই সরাসরি কাজ করবে, গ্রুপ হলে রেস্ট্রিকশন চেক করবে
        if not is_private:
            await self.memory.register_active_group(chat.id, chat.title or "Group")
            if not self.should_reply_in_group(message, user.id, chat.id):
                return

        now = time.time()
        if chat.id not in self.active_sessions:
            self.active_sessions[chat.id] = {}
        self.active_sessions[chat.id][user.id] = now

        voice_duration = message.voice.duration or 4
        thinking_delay = min(max(voice_duration * 0.6, 2.0), 12.0)

        try:
            await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.RECORD_VOICE)
        except Exception:
            pass

        temp_audio_file = tempfile.NamedTemporaryFile(delete=False, suffix=".ogg")
        temp_audio_path = temp_audio_file.name
        temp_audio_file.close()

        try:
            tg_file = await message.voice.get_file()
            await tg_file.download_to_drive(custom_path=temp_audio_path)

            start_time = time.time()
            transcription = await self.ai.transcribe_audio(temp_audio_path)
            
            elapsed = time.time() - start_time
            if elapsed < thinking_delay:
                await asyncio.sleep(thinking_delay - elapsed)

            if not transcription:
                await self._send_voice_reply(context, chat.id, message.message_id, "সোনা, তোমার ভয়েসটা স্পষ্ট শুনতে পাইনি! আরেকবার একটু বলবে?")
                return

            user_name = user.first_name or "বাবু"
            history = await self.memory.get_history(chat.id)

            bot_reply = await self.ai.generate_response(history, transcription, user_name)

            await self.memory.add_message(chat.id, "user", f"{user_name} [ভয়েস]: {transcription}")
            await self.memory.add_message(chat.id, "assistant", bot_reply)

            try:
                await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.RECORD_VOICE)
            except Exception:
                pass

            await self._send_voice_reply(context, chat.id, message.message_id, bot_reply)

        except Exception as exc:
            logger.error(f"ভয়েস প্রসেসিং ত্রুটি: {exc}")
            await self._send_voice_reply(context, chat.id, message.message_id, "উফ জান, ভয়েসটা বুঝতে একটু সমস্যা হলো! আরেকবার একটু বলো সোনা?")
        finally:
            if os.path.exists(temp_audio_path):
                try:
                    os.remove(temp_audio_path)
                except Exception:
                    pass

    async def _send_voice_reply(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        reply_to_message_id: int,
        text: str,
    ) -> bool:
        temp_tts_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        temp_tts_path = temp_tts_file.name
        temp_tts_file.close()

        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)
            created = await self.ai.text_to_speech(text, temp_tts_path)
            if not created:
                return False

            with open(temp_tts_path, "rb") as voice_data:
                await context.bot.send_voice(
                    chat_id=chat_id,
                    voice=voice_data,
                    reply_to_message_id=reply_to_message_id,
                )
            return True
        except Exception as exc:
            logger.error(f"ভয়েস পাঠাতে সমস্যা: {exc}")
            return False
        finally:
            if os.path.exists(temp_tts_path):
                try:
                    os.remove(temp_tts_path)
                except Exception:
                    pass

    async def _send_safe_text_reply(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        reply_to_message_id: int,
        text: str,
        reply_directly: bool,
    ) -> None:
        chunks = [text[i : i + 4000] for i in range(0, len(text), 4000)]
        for index, chunk in enumerate(chunks):
            try:
                target_reply = reply_to_message_id if (reply_directly and index == 0) else None
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    reply_to_message_id=target_reply,
                )
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after)
                await context.bot.send_message(chat_id=chat_id, text=chunk)
            except TelegramError as te:
                logger.error(f"মেসেজ পাঠাতে সমস্যা: {te}")

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"সিস্টেম এরর: {context.error}")

def main() -> None:
    print("==================================================")
    print("      BLACK QUEEN AI TELEGRAM CHAT BOT (ULTIMATE)")
    print(f"      Creator: {BOT_CREATOR_NAME} ({BOT_CREATOR_USERNAME})")
    print("==================================================")

    # পোর্টের জন্য ডামি ওয়েব সার্ভার সরাসরি স্টার্ট করা (রেন্ডার ও UptimeRobot ফিক্স)
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    logger.info(f"ডামি হেলথ চেক সার্ভার সফলভাবে পোর্ট {port}-এ চালু হয়েছে (HEAD/GET/POST 200 OK প্রস্তুত)।")

    memory = MemoryManager(DATABASE_PATH)
    ai_engine = AIEngine(groq_key=GROQ_API_KEY)
    black_queen = BlackQueenBot(TELEGRAM_BOT_TOKEN, ai_engine, memory)

    application = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(black_queen.post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", black_queen.handle_start))
    application.add_handler(CommandHandler(["clear", "reset"], black_queen.handle_clear))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, black_queen.process_text_message))
    application.add_handler(MessageHandler(filters.VOICE, black_queen.process_voice_message))

    application.add_error_handler(global_error_handler)

    logger.info("Black Queen বট শতভাগ প্রস্তুত এবং চালু হয়েছে...")
    application.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
