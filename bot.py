"""
Telethon control bot exposing a /forward command:

  1. User sends /forward
  2. Bot asks: "Send the start message id"
  3. Bot asks: "Send the end message id"
  4. Bot copies messages [start..end] from SOURCE_CHAT into DEST_CHAT,
     with a live dashboard that includes interactive control buttons:
       ⏸ Pause / ▶️ Resume, ⏹ Cancel (with a confirm step), 🔄 Refresh.

  Efficiency notes (this is the part that keeps API-call count low):
    - Messages are READ in chunks (FETCH_CHUNK_SIZE ids per call via
      get_messages(ids=[...])) instead of one get_messages() call per
      message id. A 5,000-message range that used to take 5,000 read
      calls now takes ~25-50.
    - Messages are FORWARDED in batches: each worker accumulates up to
      MAX_PER_WORKER valid message ids and sends them in a single
      forward_messages(..., ids_list, ...) call, instead of one
      forward_messages() call per message. This is what "16 messages"
      means per worker now -- one API request carrying 16 message ids,
      not 16 separate requests -- then that worker cools down.

  Dispatch model (per-worker rate limiting, queue-style advance):
    - Each forwarding sub-client (worker) sends one batch of up to
      MAX_PER_WORKER messages, then goes "cooling" for
      WORKER_COOLDOWN_SECONDS (default 60s).
    - The main loop never blocks the whole pool: for every batch it
      asks "which sub-client is ready right now?" and hands the batch
      to that one. Workers that are cooling are skipped until their
      cooldown expires, at which point they rejoin the ready pool
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

# --- Per-worker rate limiting (queue-style, now batched) ---
MAX_PER_WORKER = config.get("messages_per_worker", 16)
WORKER_COOLDOWN_SECONDS = config.get("worker_cooldown_seconds", 60)
POLL_INTERVAL_SECONDS = config.get("dispatch_poll_seconds", 1)

# How many source message ids to fetch in a single get_messages() call.
# Telegram accepts a couple hundred ids per call; 100 is a safe default.
FETCH_CHUNK_SIZE = config.get("fetch_chunk_size", 100)

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
    lines.append(f"🎚 Limit/Bot  : {MAX_PER_WORKER} msgs/batch, then {WORKER_COOLDOWN_SECONDS}s cooldown")
    lines.append(f"📡 Fetch chunk: {FETCH_CHUNK_SIZE} ids/call")
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
    lines.append(f"🌐 API calls  : ~{job.fetch_calls:,} fetch · {job.forward_calls:,} forward")
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
        self.fetch_calls = 0
        self.forward_calls = 0
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

    async def _get_messages_batch(self, reader: TelegramClient, id_chunk: list):
        """One get_messages() call covering up to FETCH_CHUNK_SIZE ids,
        instead of one call per id. Returns a list aligned with id_chunk
        (None where a message doesn't exist)."""
        self.fetch_calls += 1
        try:
            result = await reader.get_messages(SOURCE_CHAT, ids=id_chunk)
        except FloodWaitError as e:
            self.retry_count += 1
            await asyncio.sleep(e.seconds)
            self.fetch_calls += 1
            result = await reader.get_messages(SOURCE_CHAT, ids=id_chunk)
        if not isinstance(result, list):
            result = [result]
        return result

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

    async def _copy_batch(self, worker: Worker, msg_ids: list):
        """Forward a whole batch of message ids in ONE API call."""
        worker.status = "working"
        self.forward_calls += 1
        try:
            await worker.client.forward_messages(DEST_CHAT, msg_ids, SOURCE_CHAT, drop_author=True)
            worker.copied += len(msg_ids)
            self.copied += len(msg_ids)
            worker.sent_in_window += len(msg_ids)
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
            self.forward_calls += 1
            await self._copy_batch(worker, msg_ids)
        except Exception as e:
            worker.status = "error"
            worker.last_error = str(e)[:60]
            print(f"[copy batch failed] ids {msg_ids[:3]}...(+{len(msg_ids)-3} more): {e}")

    async def _dispatch_batch(self, batch: list, pending_tasks: list):
        worker = await self._get_ready_worker()
        if worker is None:
            return False
        task = asyncio.create_task(self._copy_batch(worker, batch))
        pending_tasks.append(task)
        return True

    async def run(self):
        self.start_time = time.time()
        reader = forward_workers[0].client

        all_ids = list(range(self.first_msg_id, self.last_msg_id + 1))
        pending_batch = []
        pending_tasks = []
        stopped = False

        for chunk_start in range(0, len(all_ids), FETCH_CHUNK_SIZE):
            if self.cancel_requested:
                break
            await self._wait_while_paused()
            if self.cancel_requested:
                break

            id_chunk = all_ids[chunk_start:chunk_start + FETCH_CHUNK_SIZE]
            msgs = await self._get_messages_batch(reader, id_chunk)

            for msg_id, msg in zip(id_chunk, msgs):
                if self.cancel_requested:
                    stopped = True
                    break

                self.processed += 1
                if self.processed % PROGRESS_EVERY == 0:
                    await self._update_status("Forwarding")

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

                pending_batch.append(msg_id)

                if len(pending_batch) >= MAX_PER_WORKER:
                    batch, pending_batch = pending_batch, []
                    ok = await self._dispatch_batch(batch, pending_tasks)
                    pending_tasks[:] = [t for t in pending_tasks if not t.done()]
                    if not ok:
                        stopped = True
                        break

            if stopped:
                break

        # Flush whatever's left under a full batch.
        if pending_batch and not self.cancel_requested:
            await self._dispatch_batch(pending_batch, pending_tasks)

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
