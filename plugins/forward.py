import re
import os
import sys
import math
import time

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
    first_msg_id = int(first_msg.id)
    
    client1, client2, client3, client4 = await initialize_clients(client, message, chat_id)
    await message.reply(f"{client1.username}\n{client2.username}\n{client3.username}\n{client4.username}")

    start_time = time.time()

    total_msg = last_msg_id
    invalid_msg = 0
    under = 0
    skip = 0
    transfer = 0
    fwd = None
    
    count = 0
    ids = []
    k = await message.reply("Starting Forwarding......")
    for i in range(first_msg_id, last_msg_id):
        try:
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
            elif transfer == 0:
                fwd = client1
            elif transfer == 1:
                fwd = client2
            elif transfer == 2:
                fwd = client3
            elif transfer == 3:
                fwd = client4
            await client1.send_message(message.from_user.id, "hi")
            await copy(fwd, i, from_chat) #copy files
            count += 1
            if count % 20 == 0:
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
                await k.edit(BAR.format(last_msg_id, i, count, last_msg_id-i-1, remaining_time_str, under, invalid_msg, skip))
            if transfer < 3:
                transfer += 1
            else:
                transfer = 0
        except Exception as e:
            return await message.reply(f"{e}\n\n{i}")


async def copy(client, i, source):
    await client.copy_messages(
        chat_id=DB,
        from_chat=source.id,
        message_id=i,
        caption=" "
    )
       

