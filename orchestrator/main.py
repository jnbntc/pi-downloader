import asyncio
import json
import logging
import time
import uuid
from pathlib import Path

import psutil
from aiogram import BaseMiddleware, Bot, Dispatcher, F, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import Command
from qbittorrentapi import Client

import config
from safety import contained_path, copy_from_cache, media_url, notify, redact, safe_filename, torrent_url
from worker import TaskItem, manager

logger = logging.getLogger("pi.bot")
dp = Dispatcher()
bot = None
qbit = None
qbit_lock = asyncio.Lock()


class AuthorizationMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user_id = event.from_user.id if event.from_user else None
        if user_id not in config.ALLOWED_USER_IDS or event.chat.id not in config.ALLOWED_CHAT_IDS:
            logger.warning("Rejected an unauthorized message")
            return None
        return await handler(event, data)


dp.message.outer_middleware(AuthorizationMiddleware())


async def qbit_call(method, **kwargs):
    async with qbit_lock:
        def call():
            qbit.auth_log_in()
            return getattr(qbit, method)(**kwargs)
        return await asyncio.to_thread(call)


def format_bytes(size):
    for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PiB"


@dp.message(F.text, Command("status", "stats"))
async def handle_status(message):
    ram = psutil.virtual_memory()
    try:
        temp = float(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
        temperature = f"{temp:.1f} °C"
    except (OSError, ValueError):
        temperature = "no disponible"
    lines = [f"Estado del sistema\nTemperatura: {temperature}",
             f"RAM disponible: {format_bytes(ram.available)} / {format_bytes(ram.total)}"]
    for label, path in [("Almacenamiento final", config.VAULT_DIR), ("Disco de trabajo", config.WORKSPACE_DIR)]:
        disk = await asyncio.to_thread(psutil.disk_usage, path)
        lines.append(f"{label}: {disk.percent:.1f}% usado; {format_bytes(disk.free)} libre")
    try:
        transfer = await qbit_call("transfer_info")
        lines.append(f"qBittorrent: ↓ {format_bytes(transfer['dl_info_speed'])}/s · ↑ {format_bytes(transfer['up_info_speed'])}/s")
    except Exception as error:
        logger.warning("qBittorrent status failed: %s", redact(error))
        lines.append("qBittorrent: no disponible")
    live = sum(not task.done() for task in manager.workers)
    lines.append(f"Workers: {live}/{config.MAX_CONCURRENT_TASKS}; trabajos activos: {manager.active_tasks}; en cola: {manager.queue.qsize()}")
    await message.reply("\n".join(lines))


@dp.message(F.text, Command("unzip", "extract"))
async def handle_manual_extract(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("Uso: /unzip /ruta/al/archivo.zip\nLa ruta debe estar dentro de media o torrents.")
        return
    try:
        path = contained_path(args[1].strip(), [config.VAULT_DIR, config.TORRENT_DIR])
        if not path.is_file():
            raise ValueError("El archivo no existe")
        status = await message.reply("Preparando extracción…")
        await manager.add_task(TaskItem("decompress", str(path), message, status))
    except Exception as error:
        await message.reply(f"No se puede extraer: {redact(error)}")


async def robust_get_file(file_id, status):
    for attempt in range(3):
        try:
            return await bot.get_file(file_id)
        except TelegramNetworkError:
            if attempt == 2:
                raise
            await notify(status, "Esperando al servidor local de Telegram…")
            await asyncio.sleep(5)


# Attachments come before text/caption handlers; a caption must not swallow a file.
@dp.message(F.document | F.video | F.audio)
async def handle_telegram_media(message):
    media = message.document or message.video or message.audio
    extension = ".mp4" if message.video else ".mp3" if message.audio else ".bin"
    fallback = "archivo_" + media.file_unique_id[-16:] + extension
    status = await message.reply("Obteniendo archivo de Telegram…")
    temporary = None
    try:
        name = safe_filename(getattr(media, "file_name", None), fallback)
        if (media.file_size or 0) > config.MAX_DOWNLOAD_BYTES:
            raise ValueError("El archivo supera el tamaño máximo configurado")
        async with manager.capacity:
            manager.active_tasks += 1
            try:
                file = await robust_get_file(media.file_id, status)
                if not file.file_path:
                    raise ValueError("Telegram no devolvió la ruta del archivo")
                identifier = uuid.uuid4().hex[:12]
                if name.lower().endswith(".torrent"):
                    temporary = config.WORKSPACE_DIR / f"{identifier}_{name}"
                    await copy_from_cache(file.file_path, temporary, status)
                    payload = await asyncio.to_thread(temporary.read_bytes)
                    response = await qbit_call("torrents_add", torrent_files={name: payload})
                    if str(response).lower().startswith("fail"):
                        raise RuntimeError("qBittorrent rechazó el torrent")
                    await notify(status, "Archivo .torrent cargado en qBittorrent.")
                    return
                destination = config.DIR_TELEGRAM_SAVED / f"{identifier}_{name}"
                await copy_from_cache(file.file_path, destination, status)
            finally:
                manager.active_tasks -= 1
        if destination.suffix.lower() in {".rar", ".zip", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz"}:
            await manager.add_task(TaskItem("decompress", str(destination), message, status))
        else:
            await notify(status, f"Archivo guardado: {destination}")
    except Exception as error:
        logger.error("Attachment failed: %s", redact(error))
        await notify(status, f"No se pudo guardar el archivo: {redact(error)[:1500]}")
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


@dp.message(F.text)
async def handle_text_and_links(message):
    raw = message.text.strip()
    link = torrent_url(raw)
    if link:
        status = await message.reply("Enviando torrent a qBittorrent…")
        try:
            response = await qbit_call("torrents_add", urls=link)
            if str(response).lower().startswith("fail"):
                raise RuntimeError("qBittorrent rechazó el enlace")
            await notify(status, "Torrent recibido por qBittorrent.")
        except Exception as error:
            logger.error("Torrent failed: %s", redact(error))
            await notify(status, f"No se pudo agregar el torrent: {redact(error)[:1500]}")
        return
    link = media_url(raw)
    if link:
        status = await message.reply("Preparando descarga…")
        await manager.add_task(TaskItem("media_dl", link, message, status))
        return
    await message.reply("Enviá un enlace de una red social, un magnet, un .torrent o un documento, video o audio.\nComandos: /status y /unzip.")


@dp.message()
async def handle_other(message):
    await message.reply("Ese tipo de mensaje no está soportado. Podés enviar documentos, videos, audios o enlaces.")


async def health_loop():
    while True:
        for index, task in enumerate(manager.workers):
            if task.done():
                if not task.cancelled():
                    error = task.exception()
                    logger.error("Restarting worker after unexpected exit: %s", redact(error))
                manager.workers[index] = asyncio.create_task(manager.worker(index + 1))
        marker = config.STATE_DIR / "health.json"
        temporary = marker.with_suffix(".tmp")
        temporary.write_text(json.dumps({"timestamp": time.time(), "workers": sum(not t.done() for t in manager.workers)}))
        temporary.replace(marker)
        await asyncio.sleep(10)


async def main():
    global bot, qbit
    config.validate()
    config.prepare_directories()
    api = TelegramAPIServer.from_base(config.TELEGRAM_API_URL.removesuffix("/bot"), is_local=True)
    session = AiohttpSession(api=api, timeout=config.TELEGRAM_TIMEOUT)
    bot = Bot(token=config.BOT_TOKEN, session=session)
    qbit = Client(host=config.QBIT_HOST, username=config.QBIT_USERNAME, password=config.QBIT_PASSWORD,
                  REQUESTS_ARGS={"timeout": 5})
    await manager.start(bot)
    health = asyncio.create_task(health_loop())
    try:
        logger.info("Bot started with authorization enabled")
        await dp.start_polling(bot)
    finally:
        health.cancel()
        await asyncio.gather(health, return_exceptions=True)
        await manager.stop()
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
