"""
Telethon control bot exposing a /forward command:

  1. User sends /forward
  2. Bot asks: "Send the start message id"
  3. Bot asks: "Send the end message id"
  4. Bot copies messages [start..end] from SOURCE_CHAT into DEST_CHAT,
     round-robining across a pool of forwarding clients, with a live
     progress message (same batching/skip logic as range_copy_engine.py).

Only users listed in `allowed_user_ids` (bot_config.json) may run /forward.
Only use this against chats you own or have explicit permission to copy
content between.
"""

import asyncio
import datetime
import json
import math
import os
import time

from telethon import TelegramClient, events, Button
from telethon.errors import FloodWaitError
from telethon.tl.types import DocumentAttributeVideo

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_config.json")
SESSION_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        raise SystemExit(
            f"'{CONFIG_PATH}' not found. Copy bot_config.example.json to "
            "bot_config.json and fill in your values."
        )
    with open(CONFIG_PATH) as f:
        return json.load(f)


config = load_config()

API_ID = config["api_id"]
API_HASH = config["api_hash"]
CONTROL_BOT_TOKEN = config["control_bot_token"]
SOURCE_CHAT = config["source_chat"]
DEST_CHAT = config["dest_chat"]
ALLOWED_USER_IDS = set(config.get("allowed_user_ids", []))
BATCH_SIZE = config.get("batch_size", 170)
BATCH_SLEEP_SECONDS = config.get("batch_sleep_seconds", 60)
PROGRESS_EVERY = config.get("progress_update_every", 34)
MIN_DOC_MB = config.get("min_document_mb", 50)

os.makedirs(SESSION_DIR, exist_ok=True)

# The bot that talks to users and drives the /forward conversation.
control_client = TelegramClient(os.path.join(SESSION_DIR, "control_bot"), API_ID, API_HASH)

forward_session_cfgs = config["forward_sessions"]


class Worker:
    """One client in the round-robin copy pool, tracked for the dashboard."""

    def __init__(self, name: str, client: TelegramClient):
        self.name = name
        self.client = client
        self.status = "idle"  # idle | working | cooling | error | done
        self.copied = 0
        self.cooldown_until = 0.0

    def refresh(self):
        if self.status == "cooling" and time.time() >= self.cooldown_until:
            self.status = "idle"

    def cooldown_remaining(self) -> int:
        return max(0, int(self.cooldown_until - time.time()))


forward_workers = [
    Worker(
        s.get("name", f"Bot {i}"),
        TelegramClient(os.path.join(SESSION_DIR, s["session_name"]), API_ID, API_HASH),
    )
    for i, s in enumerate(forward_session_cfgs, start=1)
]

job_running = False  # simple lock so only one /forward job runs at a time

STATUS_ICON = {
    "idle": "🟢",
    "working": "🟡",
    "cooling": "🟠",
    "error": "🔴",
    "done": "⚪",
}

DIVIDER = "━" * 26


def is_allowed(user_id: int) -> bool:
    return not ALLOWED_USER_IDS or user_id in ALLOWED_USER_IDS


def format_td(seconds: float) -> str:
    return str(datetime.timedelta(seconds=int(max(0, seconds))))


def render_dashboard(job: "ForwardJob", state: str) -> str:
    lines = ["🚀 Range Forward Engine", "", DIVIDER, ""]

    active = 0
    for w in forward_workers:
        w.refresh()
        if w.status != "done":
            active += 1
    lines.append(f"🤖 Active Bot : {active} / {len(forward_workers)}")
    lines.append("")

    for w in forward_workers:
        icon = STATUS_ICON.get(w.status, "⚪")
        extra = f" (Cooling {w.cooldown_remaining()}s)" if w.status == "cooling" else ""
        lines.append(f"{icon} {w.name} : {w.copied} copied{extra}")

    lines.append("")
    lines.append(DIVIDER)
    lines.append("")

    total = job.last_msg_id - job.first_msg_id + 1
    processed = job.processed
    remaining = max(0, total - processed)
    elapsed = time.time() - job.start_time if job.start_time else 0
    speed = (job.copied / elapsed * 60) if elapsed > 0 else 0
    eta = (remaining / speed * 60) if speed > 0 else 0

    lines.append(f"📦 Total      : {total:,}")
    lines.append(f"📥 Processed  : {processed:,}")
    lines.append(f"✅ Copied     : {job.copied:,}")
    lines.append(f"📬 Remaining  : {remaining:,}")
    lines.append("")
    lines.append(f"⚡ Speed       : {speed:.0f} msg/min")
    lines.append(f"⏱ Elapsed     : {format_td(elapsed)}")
    lines.append(f"⌛ ETA         : {format_td(eta)}")
    lines.append("")
    lines.append(
        f"Skipped: no-media {job.invalid_msg} · video {job.skip_video} · <{MIN_DOC_MB}MB {job.under_size}"
    )
    if state:
        lines.append(f"State: {state}")
    lines.append(DIVIDER)
    return "\n".join(lines)


class ForwardJob:
    """One run of the copy engine, triggered from a /forward conversation."""

    def __init__(self, first_msg_id: int, last_msg_id: int, status_msg):
        self.first_msg_id = first_msg_id
        self.last_msg_id = last_msg_id
        self.status_msg = status_msg

        self.invalid_msg = 0
        self.skip_video = 0
        self.under_size = 0
        self.copied = 0
        self.processed = 0
        self.start_time = None

        # Reset worker state for a fresh run.
        for w in forward_workers:
            w.status = "idle"
            w.copied = 0
            w.cooldown_until = 0.0

    @staticmethod
    def _is_video(msg) -> bool:
        if getattr(msg, "video", None):
            return True
        doc = getattr(msg, "document", None)
        if not doc:
            return False
        return any(isinstance(a, DocumentAttributeVideo) for a in getattr(doc, "attributes", []))

    async def _get_message(self, reader: TelegramClient, msg_id: int):
        try:
            return await reader.get_messages(SOURCE_CHAT, ids=msg_id)
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
            return await reader.get_messages(SOURCE_CHAT, ids=msg_id)

    async def _copy_one(self, worker: Worker, msg_id: int):
        worker.status = "working"
        try:
            # drop_author=True copies without a "Forwarded from" header.
            await worker.client.forward_messages(DEST_CHAT, msg_id, SOURCE_CHAT, drop_author=True)
            worker.copied += 1
            self.copied += 1
            worker.status = "idle"
        except FloodWaitError as e:
            worker.status = "cooling"
            worker.cooldown_until = time.time() + e.seconds
            await asyncio.sleep(e.seconds)
            worker.status = "idle"
            await self._copy_one(worker, msg_id)
        except Exception as e:
            worker.status = "error"
            print(f"[copy failed] msg {msg_id}: {e}")

    async def _update_status(self, state: str):
        text = render_dashboard(self, state)
        try:
            await self.status_msg.edit(text)
        except Exception:
            pass  # e.g. "message not modified" -- safe to ignore

    async def run(self):
        self.start_time = time.time()
        n_workers = len(forward_workers)
        reader = forward_workers[0].client

        transfer = 0
        pending_tasks = []

        for i in range(self.first_msg_id, self.last_msg_id + 1):
            self.processed += 1

            if i % PROGRESS_EVERY == 0:
                await self._update_status("Forwarding")

            msg = await self._get_message(reader, i)
            if msg is None or not msg.media:
                self.invalid_msg += 1
                continue
            if self._is_video(msg):
                self.skip_video += 1
                continue
            if getattr(msg, "document", None):
                size = getattr(msg.document, "size", 0) or 0
                if size < MIN_DOC_MB * 1024 * 1024:
                    self.under_size += 1
                    continue

            worker = forward_workers[transfer]
            pending_tasks.append(asyncio.create_task(self._copy_one(worker, i)))
            transfer = (transfer + 1) % n_workers

            if len(pending_tasks) >= BATCH_SIZE:
                await asyncio.gather(*pending_tasks)
                pending_tasks = []
                await self._update_status(f"Sleeping {BATCH_SLEEP_SECONDS}s")
                await asyncio.sleep(BATCH_SLEEP_SECONDS)

        if pending_tasks:
            await asyncio.gather(*pending_tasks)

        for w in forward_workers:
            w.status = "done"
        await self._update_status("Complete ✅")


@control_client.on(events.NewMessage(pattern="/start"))
async def start_handler(event):
    await event.respond(
        "👋 Send /forward to copy a message-id range from the source chat "
        "into the destination chat."
    )


@control_client.on(events.NewMessage(pattern="/forward"))
async def forward_handler(event):
    global job_running

    if not is_allowed(event.sender_id):
        await event.respond("🚫 You're not allowed to use this command.")
        return

    if job_running:
        await event.respond("⏳ A forward job is already running. Please wait for it to finish.")
        return

    async with control_client.conversation(event.chat_id, timeout=120) as conv:
        try:
            await conv.send_message("Send the **start message id**:")
            start_reply = await conv.get_response()
            first_msg_id = int(start_reply.raw_text.strip())

            await conv.send_message("Send the **end message id**:")
            end_reply = await conv.get_response()
            last_msg_id = int(end_reply.raw_text.strip())
        except ValueError:
            await conv.send_message("❌ Message ids must be numbers. Run /forward again.")
            return
        except asyncio.TimeoutError:
            await conv.send_message("⌛ Timed out waiting for a reply. Run /forward again.")
            return

        if last_msg_id < first_msg_id:
            await conv.send_message("❌ End id must be greater than or equal to start id.")
            return

        status_msg = await conv.send_message(
            f"Starting forward job for messages {first_msg_id} → {last_msg_id}..."
        )

    job_running = True
    try:
        job = ForwardJob(first_msg_id, last_msg_id, status_msg)
        await job.run()
    except Exception as e:
        await status_msg.reply(f"❌ Job stopped with an error: {e}")
    finally:
        job_running = False


async def main():
    await control_client.start(bot_token=CONTROL_BOT_TOKEN)

    for worker, cfg in zip(forward_workers, forward_session_cfgs):
        bot_token = cfg.get("bot_token")
        if bot_token:
            await worker.client.start(bot_token=bot_token)
        else:
            await worker.client.start()  # first run prompts for phone/login code

    print("Bot is running. Send /forward to it on Telegram. Press Ctrl+C to stop.")
    await control_client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
