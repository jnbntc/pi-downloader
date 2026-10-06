"""Opt-in functional check. Sends small test files to a specified authorized chat.

Run inside the orchestrator container. Incoming updates are simulated; Bot API
uploads, getFile, disk copies, archive extraction and media downloads are real.
Only the directories uniquely created by this check are removed on completion.
"""
import argparse
import asyncio
import hashlib
import json
import logging
import shutil
import sys
import uuid
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
from aiogram import Bot, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

import config
import main
from worker import DownloadManager
from video import probe, validate_video


async def run(args):
    if args.chat_id not in config.ALLOWED_CHAT_IDS or args.chat_id not in config.ALLOWED_USER_IDS:
        raise ValueError("The test requires an authorized private chat/user ID")
    identifier = "functional-" + uuid.uuid4().hex[:12]
    vault = config.VAULT_DIR / identifier
    workspace = config.WORKSPACE_DIR / identifier
    vault.mkdir(mode=0o755)  # The local Telegram API runs under a separate UID and must read uploads.
    workspace.mkdir(mode=0o700)
    config.DIR_MEDIA_RRSS = vault / "rrss"
    config.DIR_MEDIA_EXTRACTED = vault / "extracted"
    config.DIR_TELEGRAM_SAVED = vault / "saved"
    config.DIR_TMP_MEDIA = workspace / "temp"
    config.DIR_TMP_DECOMPRESS = workspace / "decompress"
    config.STATE_DIR = workspace / "state"
    config.prepare_directories()
    api = TelegramAPIServer.from_base(config.TELEGRAM_API_URL.removesuffix("/bot"), is_local=True)
    bot = Bot(token=config.BOT_TOKEN, session=AiohttpSession(api=api, timeout=60))
    manager = DownloadManager()
    main.bot = bot
    main.manager = manager
    results = {}
    videos = []
    original_request = bot.session.make_request

    async def record_request(bot_instance, method, timeout=None):
        result = await original_request(bot_instance, method, timeout=timeout)
        if method.__api_method__ == "sendVideo":
            videos.append(result.video.file_size if result.video else 0)
        return result

    bot.session.make_request = record_request

    def incoming(sent, without_name=False):
        data = sent.model_dump(mode="json", exclude_none=True, by_alias=True)
        data["from"] = {"id": args.chat_id, "is_bot": False, "first_name": "Functional check"}
        data["caption"] = "Prueba de adjunto con texto"
        if without_name:
            data["document"].pop("file_name", None)
        return types.Message.model_validate(data, context={"bot": bot})

    try:
        await manager.start(bot)
        await bot.send_message(args.chat_id, "Prueba funcional de pi-downloader: archivos pequeños y multimedia de la publicación de prueba.")
        source = vault / "pi-check.txt"
        source.write_text("pi-downloader functional check\n" * 128)
        sent = await bot.send_document(args.chat_id, document="file://" + str(source), caption="Archivo pequeño de prueba")
        event = incoming(sent)
        await main.dp.feed_update(bot, types.Update(update_id=1, message=event))
        saved = list(config.DIR_TELEGRAM_SAVED.iterdir())
        results["attachment_with_caption"] = len(saved) == 1 and hashlib.sha256(saved[0].read_bytes()).digest() == hashlib.sha256(source.read_bytes()).digest()
        if not results["attachment_with_caption"]:
            raise AssertionError("Attachment with caption did not produce the expected copy")
        await main.dp.feed_update(bot, types.Update(update_id=2, message=incoming(sent, without_name=True)))
        results["attachment_without_name"] = len(list(config.DIR_TELEGRAM_SAVED.iterdir())) == 2
        if not results["attachment_without_name"]:
            raise AssertionError("Attachment without name did not produce a copy")
        archive = vault / "pi-check.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("nested/check.txt", "Archive check\n")
        sent_zip = await bot.send_document(args.chat_id, document="file://" + str(archive), caption="ZIP pequeño de prueba")
        await main.dp.feed_update(bot, types.Update(update_id=3, message=incoming(sent_zip)))
        await asyncio.wait_for(manager.queue.join(), 60)
        extracted = list(config.DIR_MEDIA_EXTRACTED.glob("*/nested/check.txt"))
        results["zip_extraction"] = len(extracted) == 1 and extracted[0].read_text() == "Archive check\n"
        if not results["zip_extraction"]:
            raise AssertionError("Archive extraction did not produce the expected file")
        media_message = types.Message.model_validate({
            "message_id": sent.message_id, "date": int(sent.date.timestamp()),
            "chat": {"id": args.chat_id, "type": "private"},
            "from": {"id": args.chat_id, "is_bot": False, "first_name": "Functional check"},
            "text": args.media_url, "link_preview_options": {"url": args.media_url},
        }, context={"bot": bot})
        await main.dp.feed_update(bot, types.Update(update_id=4, message=media_message))
        await asyncio.wait_for(manager.queue.join(), config.TASK_TIMEOUT + config.MAX_MEDIA_FILES * config.VIDEO_TIMEOUT + 60)
        outputs = list(config.DIR_MEDIA_RRSS.glob("*/*.mp4"))
        originals = list(config.DIR_MEDIA_RRSS.glob("*/originals/*"))
        results["media_downloaded"] = len(outputs)
        results["media_sent"] = len(videos)
        results["media_bytes"] = sum(p.stat().st_size for p in outputs)
        if len(videos) != len(outputs) or len(videos) < args.expected_videos:
            raise AssertionError("Not all expected media files were downloaded and sent")
        if len(originals) != len(outputs):
            raise AssertionError("The source video set does not match the converted outputs")
        for output in outputs:
            original = next((p for p in originals if output.stem[3:] == p.stem[:160]), None)
            if original is None:
                raise AssertionError("The converted output has no corresponding source")
            validate_video(await probe(output), float((await probe(original))["format"]["duration"]))
        results["whatsapp_video_format"] = True
        results["full_video_duration_preserved"] = True
        await bot.send_message(args.chat_id, "Prueba funcional completada: adjunto con texto, archivo sin nombre, ZIP y todos los videos enviados correctamente.")
        results["passed"] = True
        print(json.dumps(results, sort_keys=True))
    finally:
        await manager.stop()
        await bot.session.close()
        for path in [vault, workspace]:
            shutil.rmtree(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat-id", required=True, type=int)
    parser.add_argument("--media-url", required=True)
    parser.add_argument("--expected-videos", type=int, default=1)
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(arguments))
