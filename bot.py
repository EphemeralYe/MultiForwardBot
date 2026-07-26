import asyncio
import time

from telethon import TelegramClient
from telethon.errors import FloodWaitError

API_ID = 12345678
API_HASH = "YOUR_API_HASH"

SESSION = "forward"

SOURCE_CHANNEL = -1001111111111
DEST_CHANNEL = -1002222222222

MIN_SIZE = 50 * 1024 * 1024      # 50MB

UPDATE_EVERY = 5                 # Update status every 5 messages

# ==========================================

bot = TelegramClient(SESSION, API_ID, API_HASH)


def format_time(seconds):
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"

def status_text(
    total,
    fetched,
    forwarded,
    skipped,
    invalid,
    start,
):
    elapsed = time.time() - start
    remaining = total - fetched
    progress = (fetched / total * 100) if total else 0
    speed = forwarded / elapsed if elapsed else 0
    eta = remaining / speed if speed else 0
    bar_length = 20
    filled = int(progress / 100 * bar_length)
    bar = "🟩" * filled + "⬜" * (bar_length - filled)
    status = "🟢 RUNNING"
    if remaining == 0:
        status = "✅ COMPLETED"
    return f"""
        <b>🚀 CHANNEL COPY ENGINE</b>

        {bar}

<b>Progress:</b> <code>{progress:.2f}%</code>

━━━━━━━━━━━━━━━━━━

📦 <b>Total</b>
<code>{total:,}</code>

📥 <b>Fetched</b>
<code>{fetched:,}</code>

✅ <b>Forwarded</b>
<code>{forwarded:,}</code>

📬 <b>Remaining</b>
<code>{remaining:,}</code>

━━━━━━━━━━━━━━━━━━

⚡ <b>Speed</b>
<code>{speed:.2f} msg/sec</code>

⏰ <b>Elapsed</b>
<code>{format_time(elapsed)}</code>

⌛ <b>ETA</b>
<code>{format_time(eta)}</code>

📌 <b>Status</b>
<code>{status}</code>

━━━━━━━━━━━━━━━━━━

🛡 <b>FILTERS</b>

⛔ Under 50MB
<code>{skipped}</code>

❌ Invalid
<code>{invalid}</code>

━━━━━━━━━━━━━━━━━━

🤖 Telethon Forward Manager
"""


async def main():
    await bot.start()
    print("Logged In")

    source = await bot.get_entity(SOURCE_CHANNEL)

    destination = await bot.get_entity(DEST_CHANNEL)

    messages = []

    async for msg in bot.iter_messages(source, reverse=True):
        messages.append(msg)

    total = len(messages)

    fetched = 0
    forwarded = 0
    skipped = 0
    invalid = 0

    start = time.time()

    status = await bot.send_message(
        "me",
        "Initializing...",
        parse_mode="html"
    )

    for msg in messages:

        fetched += 1

        try:

            if not msg.media:
                invalid += 1
                continue

            size = 0

            if getattr(msg.media, "document", None):
                size = msg.media.document.size or 0

            elif getattr(msg.media, "photo", None):
                size = MIN_SIZE

            if size < MIN_SIZE:
                skipped += 1
                continue

            while True:

                try:

                    await bot.forward_messages(
                        destination,
                        msg.id,
                        source
                    )

                    forwarded += 1
                    break

                except FloodWaitError as e:

                    await status.edit(
                        status_text(
                            total,
                            fetched,
                            forwarded,
                            skipped,
                            invalid,
                            start
                        )
                        + f"\n\n⚠️ <b>FloodWait:</b> <code>{e.seconds}s</code>",
                        parse_mode="html"
                    )

                    await asyncio.sleep(e.seconds)

        except Exception:
            invalid += 1

        if fetched % UPDATE_EVERY == 0 or fetched == total:

            await status.edit(
                status_text(
                    total,
                    fetched,
                    forwarded,
                    skipped,
                    invalid,
                    start
                ),
                parse_mode="html"
            )

    await status.edit(
        status_text(
            total,
            fetched,
            forwarded,
            skipped,
            invalid,
            start
        ),
        parse_mode="html"
    )

    print("Completed")


with bot:
    bot.loop.run_until_complete(main())
