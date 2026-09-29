"""On-demand BilimClass attachments: validate metadata and stream without disk cache."""

import re
from urllib.parse import urlsplit

from aiogram.types import InputFile


# Telegram's multipart document limit is 50 MB; leave room for transport overhead.
MAX_FILE_BYTES = 49_000_000
CHUNK_BYTES = 64 * 1024


class AttachmentError(ValueError):
    pass


def file_name(metadata: dict) -> str:
    raw = str(metadata.get("name") or "Файл")
    name = re.sub(r"[\x00-\x1f\x7f/\\]", "_", raw).strip(" .")[:160] or "Файл"
    extension = re.sub(r"[^a-zA-Z0-9]", "", str(metadata.get("extension") or ""))[:12]
    if extension and not name.lower().endswith("." + extension.lower()):
        name += "." + extension
    return name


def file_size(metadata: dict) -> int:
    try:
        size = int(metadata["sizeInBytes"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AttachmentError("BilimClass не указал размер файла.") from exc
    if size < 0 or size > MAX_FILE_BYTES:
        raise AttachmentError("Файл слишком большой для отправки через бота (лимит 49 МБ).")
    return size


def file_url(metadata: dict) -> str:
    link = metadata.get("link")
    if not isinstance(link, str):
        raise AttachmentError("Ссылка на файл недоступна.")
    url = urlsplit(link)
    if url.scheme != "https" or url.hostname != "storage.yandexcloud.kz" or url.username or url.password:
        raise AttachmentError("BilimClass вернул неподдерживаемую ссылку на файл.")
    return link


def size_label(size: int) -> str:
    return f"{size / 1_000_000:.1f} МБ" if size >= 1_000_000 else f"{max(1, round(size / 1_000))} КБ"


class StreamedAttachment(InputFile):
    """Feed Telegram directly from HTTPS in bounded chunks; never create a local file."""

    def __init__(self, metadata: dict):
        super().__init__(filename=file_name(metadata), chunk_size=CHUNK_BYTES)
        self.url = file_url(metadata)
        self.expected_size = file_size(metadata)

    async def read(self, bot):
        session = await bot.session.create_session()
        async with session.get(self.url, timeout=90, allow_redirects=False) as response:
            response.raise_for_status()
            if response.status != 200:
                raise AttachmentError("Ссылка на файл устарела. Открой список файлов ещё раз.")
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_FILE_BYTES:
                raise AttachmentError("Файл слишком большой для отправки через бота (лимит 49 МБ).")
            transferred = 0
            async for chunk in response.content.iter_chunked(self.chunk_size):
                transferred += len(chunk)
                if transferred > MAX_FILE_BYTES:
                    raise AttachmentError("Файл слишком большой для отправки через бота (лимит 49 МБ).")
                yield chunk
