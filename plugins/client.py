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
      
