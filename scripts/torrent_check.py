"""Create and remove one tiny private test torrent, with a local HTTP web seed.

Only torrents with the check's unique info hash are removed. This checks torrent
ingestion, actual downloading and piece integrity, plus magnet ingestion. It
does not test connectivity to Internet peers or an external tracker.
"""
import argparse
import asyncio
import hashlib
import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
from qbittorrentapi import Client
import config


async def ingest_through_bot(client, chat_id, *, torrent=None, name=None, magnet=None):
    """Simulate an incoming update, using real Telegram transfers and bot handlers."""
    from aiogram import Bot, types
    from aiogram.client.session.aiohttp import AiohttpSession
    from aiogram.client.telegram import TelegramAPIServer
    import main

    if chat_id not in config.ALLOWED_USER_IDS or chat_id not in config.ALLOWED_CHAT_IDS:
        raise ValueError("The check requires an authorized private chat/user ID")
    api = TelegramAPIServer.from_base(config.TELEGRAM_API_URL.removesuffix("/bot"), is_local=True)
    bot = Bot(token=config.BOT_TOKEN, session=AiohttpSession(api=api, timeout=60))
    main.bot = bot
    main.qbit = client
    source = None
    try:
        if torrent is not None:
            source = config.VAULT_DIR / (name + ".torrent")
            with source.open("xb") as stream:
                stream.write(torrent)
            source.chmod(0o644)
            sent = await bot.send_document(chat_id, document="file://" + str(source),
                                           caption="Torrent pequeño de prueba funcional")
            data = sent.model_dump(mode="json", exclude_none=True, by_alias=True)
        else:
            data = {"message_id": 1, "date": int(time.time()),
                    "chat": {"id": chat_id, "type": "private"}, "text": magnet}
        data["from"] = {"id": chat_id, "is_bot": False, "first_name": "Functional check"}
        message = types.Message.model_validate(data, context={"bot": bot})
        await main.dp.feed_update(bot, types.Update(update_id=1, message=message))
    finally:
        if source:
            source.unlink(missing_ok=True)
        await bot.session.close()


def bencode(value):
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, list):
        return b"l" + b"".join(bencode(v) for v in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(bencode(k) + bencode(value[k]) for k in sorted(value)) + b"e"
    raise TypeError(type(value))


def run(chat_id=None):
    identifier = uuid.uuid4().hex
    name = "pi-functional-" + identifier + ".bin"
    payload = os.urandom(65536)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/" + name:
                self.send_error(404)
                return
            start, end = 0, len(payload) - 1
            if "Range" in self.headers:
                spec = self.headers["Range"].removeprefix("bytes=").split("-", 1)
                start = int(spec[0])
                end = min(int(spec[1]) if spec[1] else end, end)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(payload)}")
            else:
                self.send_response(200)
            data = payload[start:end + 1]
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    info = {"length": len(payload), "name": name, "piece length": 16384, "private": 1,
            "pieces": b"".join(hashlib.sha1(payload[i:i + 16384]).digest() for i in range(0, len(payload), 16384))}
    digest = hashlib.sha1(bencode(info)).hexdigest()
    torrent = bencode({"info": info, "url-list": f"http://download-orchestrator:{server.server_port}/{name}"})
    save_path = "/downloads/.functional-check/" + identifier
    path = config.TORRENT_DIR / ".functional-check" / identifier / name
    client = Client(host=config.QBIT_HOST, username=config.QBIT_USERNAME, password=config.QBIT_PASSWORD,
                    REQUESTS_ARGS={"timeout": 5})
    result = {"bytes": len(payload)}
    try:
        client.auth_log_in()
        if chat_id is None:
            response = client.torrents_add(torrent_files={name + ".torrent": torrent}, save_path=save_path)
            if str(response).lower().startswith("fail"):
                raise AssertionError("qBittorrent rejected the fixture")
        else:
            asyncio.run(ingest_through_bot(client, chat_id, torrent=torrent, name=name))
        for _ in range(20):
            if client.torrents_info(torrent_hashes=digest):
                break
            time.sleep(0.25)
        if not client.torrents_info(torrent_hashes=digest):
            raise AssertionError("The torrent was not accepted")
        if chat_id is not None:
            # The bot uses the normal download directory. Relocate only this test hash.
            client.torrents_stop(torrent_hashes=digest)
            client.torrents_set_location(location=save_path, torrent_hashes=digest)
            result["telegram_torrent_handler"] = True
        client.torrents_set_force_start(enable=True, torrent_hashes=digest)
        for _ in range(180):
            rows = client.torrents_info(torrent_hashes=digest)
            if rows and rows[0].progress == 1 and path.is_file():
                break
            time.sleep(0.25)
        result["torrent_download_integrity"] = path.is_file() and hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(payload).digest()
        if not result["torrent_download_integrity"]:
            raise AssertionError("The tiny torrent did not complete with the expected content")
        client.torrents_delete(delete_files=True, torrent_hashes=digest)
        for _ in range(40):
            if not client.torrents_info(torrent_hashes=digest):
                break
            time.sleep(0.1)
        magnet = "magnet:?xt=urn:btih:" + digest
        if chat_id is None:
            client.torrents_add(urls=magnet, save_path=save_path, is_stopped=True)
        else:
            asyncio.run(ingest_through_bot(client, chat_id, magnet=magnet))
        for _ in range(20):
            rows = client.torrents_info(torrent_hashes=digest)
            if rows:
                break
            time.sleep(0.25)
        result["magnet_ingestion"] = bool(rows)
        if not result["magnet_ingestion"]:
            raise AssertionError("The magnet was not accepted")
        if chat_id is not None:
            result["telegram_magnet_handler"] = True
        result["passed"] = True
    finally:
        try:
            client.torrents_delete(delete_files=True, torrent_hashes=digest)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
    result["fixture_removed"] = not bool(client.torrents_info(torrent_hashes=digest))
    if not result["fixture_removed"]:
        raise AssertionError("The test torrent could not be removed")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat-id", type=int, help="Also exercise bot handlers, sending a small torrent to this private chat")
    run(parser.parse_args().chat_id)
