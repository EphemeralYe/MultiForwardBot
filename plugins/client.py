import os
import re
import sys
import datetime
import asyncio
import pytz
import subprocess

from pyrogram import Client, filters

ADMINS = [int(admin) if re.compile(r'^.\d+$').search(admin) else admin for admin in '6123610560 6754405215').split()] 


@Client.on_message(filters.command('update') & filters.user(ADMINS))
async def git_update(bot, event):
    try:
        git_output = subprocess.check_output(['git', 'pull'], stderr=subprocess.STDOUT, universal_newlines=True)
        update = await event.reply(f'<pre>{git_output}</pre>')
        if "Already up to date" in update.text.strip():
            return
        restart_message = await update.reply("<code>Bᴏᴛ Restarted...</code>")
        os.execl(sys.executable, sys.executable, 'bot.py')
    except subprocess.CalledProcessError as e:
        await event.reply(f'Git pull failed:\n{e.output}')
    except Exception as e:
        await event.reply(f'Error occurred during update: {str(e)}')

@Client.on_message(filters.command('restart') & filters.user(ADMINS))
async def restart_bot(bot, message):
    restart_message = await message.reply("`Bᴏᴛ Rᴇsᴛᴀʀᴛɪɴɢ`")
    os.execl(sys.executable, sys.executable, "bot.py")
    
async def initialize_clients(client, message, chat_id):
    clients = []

    for i in range(1, 9):
        client_m = await client.ask(message.from_user.id, f"<b>{i}) create a bot using @BotFather\n2) Then you will get a message with bot token\n3) Forward that message to me</b>")
        client_raw = re.findall(r'\d[0-9]{8,10}:[0-9A-Za-z_-]{35}', client_m.text, re.IGNORECASE)
        client_token = client_raw[0] if client_raw else None
        new_client = Client(f"{client_token}", int(15499130), "9a3fa3fdedff527e22d27b707475094e", bot_token=client_token)
        try:
            await new_client.start()
            await new_client.get_chat(chat_id)
            me = await new_client.get_me()
            new_client.username = me.username  # Assigning the client's username as an attribute
            clients.append(new_client)
        except Exception as e:
            await message.reply(f"Error initializing client {i}: {e}")

    return clients[0], clients[1], clients[2], clients[3], clients[4], clients[5], clients[6], clients[7]
    
