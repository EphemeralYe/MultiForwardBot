import re
import os
import sys
import math
import time
import pytz
import asyncio
import datetime

from pyrogram import Client, filters
from plugins.client import initialize_clients
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

DB = int(-1001995202278)

BAR = """
╔════❰ ғᴏʀᴡᴀʀᴅ sᴛᴀᴛᴜs  ❱═❍⊱❁۪۪
║ ╭━━━━❰ STATUS ❱━━━➣
║ ┣ <b>♻️ Total:</b> <code>{}</code>
║ ┣ <b>🔄 Fetched:</b> <code>{}</code>
║ ┣ <b>✅ Forwarded:</b> <code>{}</code>
║ ┣ <b>📬 Remaining:</b> <code>{}</code>
║ ┣ <b>⏰ Time Taken:</b> <code>{}</code>
║ ┣ <b>🧏 Status:</b> <code>{}</code>
║ ┣ <b>⏳ ETC:</b> <code>{}</code>
║ ╰━━━━━━━━━━━━━━━➣
║ ╭━━━━❰ FILTER ❱━━━➣
║ ┣ <b>⛔️ Under 50MB:</b> <code>{}</code>
║ ┣ <b>❌ Invalid:</b> <code>{}</code>
║ ┣ <b>🚫 Video Skip:</b> <code>{}</code>
║ ╰━━━━━━━━━━━━━━━➣ 
╚════❰ ᴘʀᴏɢʀᴇssɪɴɢ ❱══❍⊱❁۪۪
"""

@Client.on_message(filters.private & filters.command(["forward"]))
async def forward(client, message):
    if message.from_user.id != (6123610560):
        return await message.reply("Niceee 🦅")
    fromid = await client.ask(message.from_user.id, "**Forward me the last message from the SOURCE CHANNEL\n(you can also send me the link to last message)")
    if fromid.text and not fromid.forward_date:
        regex = re.compile(r"(https://)?(t\.me/|telegram\.me/|telegram\.dog/)(c/)?(\d+|[a-zA-Z_0-9]+)/(\d+)$")
        match = regex.match(fromid.text.replace("?single", ""))
        if not match:
            return await message.reply('Invalid link')
        chat_id = match.group(4)
        last_msg_id = int(match.group(5)) +1
        if chat_id.isnumeric():
            chat_id  = int(("-100" + chat_id))
    elif fromid.forward_from_chat.type in [enums.ChatType.CHANNEL]:
        last_msg_id = int(fromid.forward_from_message_id) + 1
        chat_id = fromid.forward_from_chat.username or fromid.forward_from_chat.id
        if last_msg_id == None:
           return await message.reply_text("**This may be a forwarded message from a group and sended by anonymous admin. instead of this please send last message link from group**")
    try:
        from_chat = await client.get_chat(chat_id)
    except Exception as e:
        return await message.reply(e)

    first_msg = await client.ask(message.from_user.id, "**Enter the ID of the starting message to copy**")
    first_msg_id = int(first_msg.text)
    
    client1, client2, client3, client4, client5, client6, client7, client8 = await initialize_clients(client, message, chat_id)
    await message.reply(f"{client1.username}\n{client2.username}\n{client3.username}\n{client4.username}")

    start_time = time.time()

    total_msg = last_msg_id
    invalid_msg = 0
    under = 0
    skip = 0
    transfer = 0
    fwd = None
    gathering =0
    count = 0
    ids = []
    k = await message.reply("Starting Forwarding......")
    for i in range(first_msg_id, last_msg_id):
        try: 
            if i % 60 == 0:
                percentage = (i - first_msg_id + 1) / (last_msg_id - first_msg_id + 1) * 100
                percentage_str = "{:.2f}%".format(percentage)
                green_squares = math.floor(percentage / 10)
                red_squares = 10 - green_squares
                progress = "🟩{0}{1} {2}".format(
                    ''.join(["🟩" for i in range(green_squares)]),
                    ''.join(["🟥" for i in range(red_squares)]),
                    percentage_str
                )
                button =  [[InlineKeyboardButton(progress, f'nooo')]]
                elapsed_time = time.time() - start_time
                remaining_time = (last_msg_id - i - 1) * elapsed_time / (i - first_msg_id + 1)
                remaining_time_str = str(datetime.timedelta(seconds=int(remaining_time)))
                elapsed_time_str = str(datetime.timedelta(seconds=int(elapsed_time)))
                await k.edit(BAR.format(last_msg_id, i, count, last_msg_id-i-1, elapsed_time_str, "Forwarding", remaining_time_str, under, invalid_msg, skip), reply_markup=InlineKeyboardMarkup(button))
                
            i_file = await client.get_messages(from_chat.id, i)
            if not i_file.media:
                invalid_msg += 1
                continue
            elif i_file.video:
                skip += 1
                continue
            elif i_file.document:
                if i_file.document.file_size < 50 * 1024 * 1024:
                    under += 1
                    continue
            fwd = [client1, client2, client3, client4, client5, client6, client7, client8][transfer]
            tasks = []
            tasks.append(asyncio.create_task(copy(fwd, i, from_chat))) #copy files
            gathering += 1
            if gathering == 130:
                asyncio.gather(*tasks)
                count += 120
                percentage = (i - first_msg_id + 1) / (last_msg_id - first_msg_id + 1) * 100
                percentage_str = "{:.2f}%".format(percentage)
                green_squares = math.floor(percentage / 10)
                red_squares = 10 - green_squares
                progress = "🟩{0}{1} {2}".format(
                    ''.join(["🟩" for i in range(green_squares)]),
                    ''.join(["🟥" for i in range(red_squares)]),
                    percentage_str
                )
                button =  [[InlineKeyboardButton(progress, f'nooo')]]
                elapsed_time = time.time() - start_time
                remaining_time = (last_msg_id - i - 1) * elapsed_time / (i - first_msg_id + 1)
                remaining_time_str = str(datetime.timedelta(seconds=int(remaining_time)))
                elapsed_time_str = str(datetime.timedelta(seconds=int(elapsed_time)))
                await k.edit(BAR.format(last_msg_id, i, count, last_msg_id-i-1, elapsed_time_str, "Sleeping 60 sec", remaining_time_str, under, invalid_msg, skip), reply_markup=InlineKeyboardMarkup(button))
                await asyncio.sleep(60)
            if transfer < 7:
                transfer += 1
            else:
                transfer -= 7
        except Exception as e:
            return await message.reply(f"{e}\n\n{i}")
    return await message.reply("complete")

async def copy(client, i, source):
    try:
        await client.copy_message(
            chat_id=DB,
            from_chat_id=source.id,
            message_id=i,
            caption=" "
        )
    except FloodWait as e:
        await asyncio.sleep(e.value)
        print(f"sleeping for {e.value}")
        return await copy(client, i, source)
