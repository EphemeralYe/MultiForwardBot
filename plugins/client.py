import os
import re
import sys
import datetime
import asyncio
import pytz
import subprocess

from pyrogram import Client, filters


@Client.on_message(filters.command('update') & filters.user(6123610560))
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
      

async def initialize_clients(client, message, chat_id):
    # client1
    client1_m = await client.ask(message.from_user.id, "<b>1) create a bot using @BotFather\n2) Then you will get a message with bot token\n3) Forward that message to me</b>")
    client1_raw = re.findall(r'\d[0-9]{8,10}:[0-9A-Za-z_-]{35}', client1_m.text, re.IGNORECASE)
    client1_token = client1_raw[0] if client1_raw else None
    client1 = Client(f"{client1_token}", int(15499130), "9a3fa3fdedff527e22d27b707475094e", bot_token=client1_token)
    try:
        await client1.start()
        await client1.get_chat(chat_id)
    except Exception as e:
        await message.reply(e)

    # client2
    client2_m = await client.ask(message.from_user.id, "<b>1) create a bot using @BotFather\n2) Then you will get a message with bot token\n3) Forward that message to me</b>")
    client2_raw = re.findall(r'\d[0-9]{8,10}:[0-9A-Za-z_-]{35}', client2_m.text, re.IGNORECASE)
    client2_token = client2_raw[0] if client2_raw else None
    client2 = Client(f"{client2_token}", int(15499130), "9a3fa3fdedff527e22d27b707475094e", bot_token=client2_token)
    try:
        await client2.start()
        await client2.get_chat(chat_id)
    except Exception as e:
        await message.reply(e)

    # client3
    client3_m = await client.ask(message.from_user.id, "<b>1) create a bot using @BotFather\n2) Then you will get a message with bot token\n3) Forward that message to me</b>")
    client3_raw = re.findall(r'\d[0-9]{8,10}:[0-9A-Za-z_-]{35}', client3_m.text, re.IGNORECASE)
    client3_token = client3_raw[0] if client3_raw else None
    client3 = Client(f"{client3_token}", int(15499130), "9a3fa3fdedff527e22d27b707475094e", bot_token=client3_token)
    try:
        await client3.start()
        await client3.get_chat(chat_id)
    except Exception as e:
        await message.reply(e)

    # client4
    client4_m = await client.ask(message.from_user.id, "<b>1) create a bot using @BotFather\n2) Then you will get a message with bot token\n3) Forward that message to me</b>")
    client4_raw = re.findall(r'\d[0-9]{8,10}:[0-9A-Za-z_-]{35}', client4_m.text, re.IGNORECASE)
    client4_token = client4_raw[0] if client4_raw else None
    client4 = Client(f"{client4_token}", int(15499130), "9a3fa3fdedff527e22d27b707475094e", bot_token=client4_token)
    try:
        await client4.start()
        await client4.get_chat(chat_id)
    except Exception as e:
        await message.reply(e)

    return client1, client2, client3, client4
    
