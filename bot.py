from pyrogram import Client, __version__
from pyrogram.raw.all import layer
from pyromod import listen

class Bot(Client):

    def __init__(self):
        self.start_time = None
        super().__init__(
            name="ForwardBot",
            api_id=int(15499130),
            api_hash="9a3fa3fdedff527e22d27b707475094e",
            bot_token="7174249611:AAEok-xT9hR5SwgafsyaKbRCAftpU_YWfL8",
            workers=200,
            plugins={"root": "plugins"},
            sleep_threshold=5,
        )

    async def start(self):
        await super().start()
        me = await self.get_me()
        self.username = '@' + me.username
        print(f"{me.first_name} with for Pyrogram v{__version__} (Layer {layer}) started on {me.username}.")

    async def stop(self, *args):
        await super().stop()
        print("Bot stopped. Bye.")

app = Bot()
app.run()
