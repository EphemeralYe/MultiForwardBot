"""
Telethon control bot exposing a /forward command:

  1. User sends /forward
  2. Bot asks: "Send the start message id"
  3. Bot asks: "Send the end message id"
  4. Bot copies messages [start..end] from SOURCE_CHAT into DEST_CHAT,
     with a live dashboard that includes interactive control buttons:
       ⏸ Pause / ▶️ Resume, ⏹ Cancel (with a confirm step), 🔄 Refresh.

  Dispatch model (per-worker rate limiting, queue-style advance):
    - Each forwarding sub-client (worker) may send up to MAX_PER_WORKER
      messages (default 16), then it goes "cooling" for
      WORKER_COOLDOWN_SECONDS (default 60s).
    - The main loop never blocks the whole pool: for every message it
      asks "which sub-client is ready right now?" and hands the next
      message to that one. Workers that are cooling are skipped until
      their cooldown expires, at which point they rejoin the ready pool
      automatically (checked continuously, not on a fixed global timer).
    - This means workers advance independently, like separate queues,
      instead of the whole pool pausing together after one shared batch.

Only users listed in `allowed_user_ids` (bot_config.json) may run /forward.
Only use this against chats you own or have explicit permission to copy
content between.
"""

import asyncio
import datetime
import json
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
PROGRESS_EVERY = config.get("progress_update_every", 34)
MIN_DOC_MB = config.get("min_document_mb", 50)

# --- Per-worker rate limiting (queue-style) ---
MAX_PER_WORKER = config.get("messages_per_worker", 16)
WORKER_COOLDOWN_SECONDS = config.get("worker_cooldown_seconds", 60)
POLL_INTERVAL_SECONDS = config.get("dispatch_poll_seconds", 1)

os.makedirs(SESSION_DIR, exist_ok=True)

control_client = TelegramClient(os.path.join(SESSION_DIR, "control_bot"), API_ID, API_HASH)

forward_session_cfgs = config["forward_sessions"]


class Worker:
    """One client in the forwarding pool, tracked for the dashboard."""

    def __init__(self, name: str, client: TelegramClient):
        self.name = name
        self.client = client
        self.status = "idle"  # idle | working | cooling | error | done
        self.copied = 0
        self.cooldown_until = 0.0
        self.sent_in_window = 0
        self.last_error = None

    def refresh(self):
        if self.status == "cooling" and time.time() >= self.cooldown_until:
            self.status = "idle"
            self.sent_in_window = 0

    def cooldown_remaining(self) -> int:
        return max(0, int(self.cooldown_until - time.time()))

    def is_ready(self) -> bool:
        self.refresh()
        return self.status == "idle"


forward_workers = [
    Worker(
        s.get("name", f"Bot {i}"),
        TelegramClient(os.path.join(SESSION_DIR, s["session_name"]), API_ID, API_HASH),
    )
    for i, s in enumerate(forward_session_cfgs, start=1)
]

job_running = False
current_job = None  # the ForwardJob currently in flight, for button callbacks

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


def progress_bar(fraction: float, width: int = 18) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = round(width * fraction)
    return "▓" * filled + "░" * (width - filled)


def mini_bar(value: int, total: int, width: int = 8) -> str:
    if total <= 0:
        return "░" * width
    return progress_bar(value / total, width)


def render_dashboard(job: "ForwardJob", state: str) -> str:
    lines = ["🚀 Range Forward Engine", "", DIVIDER, ""]

    active = 0
    for w in forward_workers:
        w.refresh()
        if w.status != "done":
            active += 1
    lines.append(f"🤖 Active Bot : {active} / {len(forward_workers)}")
    lines.append(f"🎚 Limit/Bot  : {MAX_PER_WORKER} msgs, then {WORKER_COOLDOWN_SECONDS}s cooldown")
    lines.append("")

    for w in forward_workers:
        icon = STATUS_ICON.get(w.status, "⚪")
        bar = mini_bar(w.sent_in_window, MAX_PER_WORKER)
        extra = f" (cooling {w.cooldown_remaining()}s)" if w.status == "cooling" else ""
        err = f" ⚠️ {w.last_error}" if w.last_error else ""
        lines.append(f"{icon} {w.name} [{bar}] {w.copied} copied{extra}{err}")

    lines.append("")
    lines.append(DIVIDER)
    lines.append("")

    total = job.last_msg_id - job.first_msg_id + 1
    processed = job.processed
    remaining = max(0, total - processed)
    elapsed = time.time() - job.start_time if job.start_time else 0
    speed = (job.copied / elapsed * 60) if elapsed > 0 else 0
    eta = (remaining / speed * 60) if speed > 0 else 0
    pct = (processed / total * 100) if total > 0 else 0

    lines.append(f"{progress_bar(processed / total if total else 0)}  {pct:5.1f}%")
    lines.append("")
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
    if job.retry_count:
        lines.append(f"🔁 Flood-wait retries: {job.retry_count}")
    if state:
        tag = "⏸ PAUSED" if job.paused and state not in ("done", "cancelled") else state
        lines.append(f"State: {tag}")
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
        self.retry_count = 0
        self.start_time = None

        self.state = "starting"
        self.paused = False
        self.cancel_requested = False
        self.awaiting_cancel_confirm = False

        self._rr_index = 0

        for w in forward_workers:
            w.status = "idle"
            w.copied = 0
            w.cooldown_until = 0.0
            w.sent_in_window = 0
            w.last_error = None

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
            self.retry_count += 1
            await asyncio.sleep(e.seconds)
            return await reader.get_messages(SOURCE_CHAT, ids=msg_id)

    def _buttons(self):
        if self.state in ("done", "cancelled"):
            return None
        if self.awaiting_cancel_confirm:
            return [[Button.inline("✅ Yes, cancel", b"cancel_yes"),
                     Button.inline("↩️ No, keep going", b"cancel_no")]]
        top = []
        if self.paused:
            top.append(Button.inline("▶️ Resume", b"resume"))
        else:
            top.append(Button.inline("⏸ Pause", b"pause"))
        top.append(Button.inline("⏹ Cancel", b"cancel_ask"))
        bottom = [Button.inline("🔄 Refresh", b"refresh")]
        return [top, bottom]

    async def _update_status(self, state: str):
        self.state = state
        text = render_dashboard(self, state)
        try:
            await self.status_msg.edit(text, buttons=self._buttons())
        except Exception:
            pass  # e.g. "message not modified" -- safe to ignore

    async def _wait_while_paused(self):
        while self.paused and not self.cancel_requested:
            await self._update_status("⏸ Paused")
            await asyncio.sleep(1)

    async def _get_ready_worker(self):
        """Returns the next ready worker, or None if the job was cancelled
        while waiting."""
        n = len(forward_workers)
        while True:
            if self.cancel_requested:
                return None
            await self._wait_while_paused()
            if self.cancel_requested:
                return None

            for _ in range(n):
                idx = self._rr_index
                self._rr_index = (self._rr_index + 1) % n
                w = forward_workers[idx]
                if w.is_ready():
                    return w

            await self._update_status("Waiting for a ready bot...")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def _copy_one(self, worker: Worker, msg_id: int):
        worker.status = "working"
        try:
            await worker.client.forward_messages(DEST_CHAT, msg_id, SOURCE_CHAT, drop_author=True)
            worker.copied += 1
            self.copied += 1
            worker.sent_in_window += 1
            worker.last_error = None

            if worker.sent_in_window >= MAX_PER_WORKER:
                worker.status = "cooling"
                worker.cooldown_until = time.time() + WORKER_COOLDOWN_SECONDS
                worker.sent_in_window = 0
            else:
                worker.status = "idle"
        except FloodWaitError as e:
            self.retry_count += 1
            worker.status = "cooling"
            worker.cooldown_until = time.time() + e.seconds
            worker.sent_in_window = 0
            worker.last_error = f"flood-wait {e.seconds}s"
            await asyncio.sleep(e.seconds)
            worker.status = "idle"
            await self._copy_one(worker, msg_id)
        except Exception as e:
            worker.status = "error"
            worker.last_error = str(e)[:60]
            print(f"[copy failed] msg {msg_id}: {e}")

    async def run(self):
        self.start_time = time.time()
        reader = forward_workers[0].client

        pending_tasks = []

        for i in range(self.first_msg_id, self.last_msg_id + 1):
            if self.cancel_requested:
                break

            await self._wait_while_paused()
            if self.cancel_requested:
                break

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

            worker = await self._get_ready_worker()
            if worker is None:
                break

            task = asyncio.create_task(self._copy_one(worker, i))
            pending_tasks.append(task)
            pending_tasks = [t for t in pending_tasks if not t.done()]

        if pending_tasks:
            await asyncio.gather(*pending_tasks)

        for w in forward_workers:
            w.status = "done"

        if self.cancel_requested:
            await self._update_status("cancelled")
        else:
            await self._update_status("done")


@control_client.on(events.NewMessage(pattern="/start"))
async def start_handler(event):
    await event.respond(
        "👋 Send /forward to copy a message-id range from the source chat "
        "into the destination chat."
    )


@control_client.on(events.NewMessage(pattern="/forward"))
async def forward_handler(event):
    global job_running, current_job

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
        current_job = job
        await job.run()
    except Exception as e:
        await status_msg.reply(f"❌ Job stopped with an error: {e}")
    finally:
        job_running = False
        current_job = None


@control_client.on(events.CallbackQuery)
async def callback_handler(event):
    if not is_allowed(event.sender_id):
        await event.answer("🚫 You're not allowed to control this job.", alert=True)
        return

    job = current_job
    if job is None:
        await event.answer("No active job right now.", alert=True)
        return

    data = event.data

    if data == b"pause":
        job.paused = True
        await event.answer("Paused")
        await job._update_status(job.state)

    elif data == b"resume":
        job.paused = False
        await event.answer("Resumed")
        await job._update_status("Forwarding")

    elif data == b"cancel_ask":
        job.awaiting_cancel_confirm = True
        await event.answer()
        await job._update_status(job.state)

    elif data == b"cancel_yes":
        job.awaiting_cancel_confirm = False
        job.cancel_requested = True
        job.paused = False
        await event.answer("Cancelling job...")
        await job._update_status("cancelling")

    elif data == b"cancel_no":
        job.awaiting_cancel_confirm = False
        await event.answer("Keeping the job running.")
        await job._update_status(job.state)

    elif data == b"refresh":
        await event.answer("Refreshed")
        await job._update_status(job.state)

    else:
        await event.answer()


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
