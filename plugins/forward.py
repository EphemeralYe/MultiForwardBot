import re
import os
import sys

from pyrogram import Client, filters
from plugins.client import initialize_clients
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

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
    first_msg_id = int(start_msg.id)
    
    client1, client2, client3, client4 = await initialize_clients(client, message, chat_id)
    await message.reply(f"{client1.username}\n{client2.username}\n{client3.username}\n{client4.username}")

    start_time = time.time()

    total_msg = last_msg_id
    invalid_msg = 0
    under = 0
    skip = 0
    transfer = 0
        
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
        except Exception as e:
            await message.reply(e)


await copy(client1, client2, client3, client4, i)
