"""Bounded optional Telegram photos; callers must verify channel ownership first."""
import asyncio
import base64
import io
import time
from collections import OrderedDict


class _SmallImage(io.BytesIO):
    def write(self, value):
        if self.tell()+len(value)>65536:
            raise ValueError('avatar too large')
        return super().write(value)


class TelegramAvatarCache:
    def __init__(self, bot):
        self.bot=bot
        self.entries=OrderedDict()
        self.lock=asyncio.Lock()

    async def get(self, file_id):
        if not isinstance(file_id,str) or not file_id or len(file_id)>1024 or self.bot is None:
            return None
        try:
            async with asyncio.timeout(1):
                async with self.lock:
                    cached=self.entries.get(file_id)
                    if cached and time.monotonic()-cached[0]<(300 if cached[1] else 30):
                        self.entries.move_to_end(file_id)
                        return cached[1]
                    result=None
                    try:
                        info=await self.bot.get_file(file_id)
                        if info.file_path:
                            image=_SmallImage()
                            await self.bot.download_file(info.file_path,destination=image,timeout=1)
                            data=image.getvalue()
                            kind='jpeg' if data.startswith(b'\xff\xd8\xff') else 'png' if data.startswith(b'\x89PNG\r\n\x1a\n') else None
                            if kind:result=f'data:image/{kind};base64,'+base64.b64encode(data).decode('ascii')
                    except Exception:
                        pass  # An unavailable picture must not change permissions.
                    self.entries[file_id]=(time.monotonic(),result)
                    while len(self.entries)>128:self.entries.popitem(last=False)
                    return result
        except TimeoutError:
            return None
