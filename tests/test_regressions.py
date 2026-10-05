import asyncio
import json
import shutil
import sys
import tempfile
import tarfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
from aiogram import types
import config
import healthcheck
import main
import safety
import worker
from video import MediaOutput


class FakeBot:
    def __init__(self):
        self.calls = []
        self.file_path = None

    async def __call__(self, method, **kwargs):
        self.calls.append(method)
        return message(self, message_id=len(self.calls) + 10)

    async def get_file(self, file_id):
        return types.File(file_id=file_id, file_unique_id="fixture", file_path=str(self.file_path))


def message(bot, **extra):
    data = {"message_id": 1, "date": int(time.time()), "chat": {"id": 1, "type": "private"},
            "from": {"id": 1, "is_bot": False, "first_name": "Test"}}
    data.update(extra)
    return types.Message.model_validate(data, context={"bot": bot})


class RegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = patch.multiple(config,
            VAULT_DIR=self.root / "vault", WORKSPACE_DIR=self.root / "workspace",
            TORRENT_DIR=self.root / "torrents", TELEGRAM_CACHE_DIR=self.root / "cache",
            DIR_MEDIA_RRSS=self.root / "vault/rrss", DIR_MEDIA_EXTRACTED=self.root / "vault/extracted",
            DIR_TELEGRAM_SAVED=self.root / "vault/telegram-saved", STATE_DIR=self.root / "workspace/state",
            DIR_TMP_MEDIA=self.root / "workspace/temp", DIR_TMP_DECOMPRESS=self.root / "workspace/decompress",
            ALLOWED_USER_IDS=frozenset({1}), ALLOWED_CHAT_IDS=frozenset({1}))
        self.settings.start()
        config.prepare_directories()
        config.TELEGRAM_CACHE_DIR.mkdir()
        config.TORRENT_DIR.mkdir()
        self.bot = FakeBot()
        self.bot.file_path = config.TELEGRAM_CACHE_DIR / "fixture.bin"
        self.bot.file_path.write_bytes(b"functional fixture\n" * 100)
        self.manager = worker.DownloadManager()
        self.manager_patch = patch.object(main, "manager", self.manager)
        self.bot_patch = patch.object(main, "bot", self.bot)
        self.manager_patch.start()
        self.bot_patch.start()

    async def asyncTearDown(self):
        await self.manager.stop()
        self.bot_patch.stop()
        self.manager_patch.stop()
        self.settings.stop()
        self.temp.cleanup()

    def attachment(self, name="fixture.bin", caption=None):
        doc = {"file_id": "fixture", "file_unique_id": "fixtureunique", "file_size": 1900}
        if name is not None:
            doc["file_name"] = name
        extra = {"document": doc}
        if caption:
            extra["caption"] = caption
        return message(self.bot, **extra)

    async def test_caption_does_not_swallow_attachment(self):
        await main.dp.message.trigger(self.attachment(caption="un archivo con texto"))
        files = list(config.DIR_TELEGRAM_SAVED.iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_bytes(), self.bot.file_path.read_bytes())

    async def test_document_without_name_gets_fallback(self):
        await main.dp.message.trigger(self.attachment(name=None))
        self.assertEqual(len(list(config.DIR_TELEGRAM_SAVED.glob("*.bin"))), 1)

    async def test_duplicate_names_do_not_overwrite(self):
        await main.handle_telegram_media(self.attachment())
        original = list(config.DIR_TELEGRAM_SAVED.iterdir())[0].read_bytes()
        self.bot.file_path.write_bytes(b"different file")
        await main.handle_telegram_media(self.attachment())
        contents = [p.read_bytes() for p in config.DIR_TELEGRAM_SAVED.iterdir()]
        self.assertCountEqual(contents, [original, b"different file"])

    async def test_unsafe_attachment_name_is_rejected(self):
        await main.handle_telegram_media(self.attachment(name="../outside.bin"))
        self.assertEqual(list(config.DIR_TELEGRAM_SAVED.iterdir()), [])

    async def test_authorization_rejects_wrong_user_and_chat(self):
        handler = AsyncMock()
        middleware = main.AuthorizationMiddleware()
        for extra in [{"from": {"id": 2, "is_bot": False, "first_name": "Other"}},
                      {"chat": {"id": -2, "type": "group"}}]:
            await middleware(handler, message(self.bot, **extra), {})
        handler.assert_not_awaited()
        event = message(self.bot)
        await middleware(handler, event, {})
        handler.assert_awaited_once_with(event, {})

    def test_url_hostname_and_torrent_query_validation(self):
        self.assertIsNone(safety.media_url("https://youtube.com.example.invalid/watch"))
        self.assertIsNone(safety.media_url("https://user:pass@youtube.com/watch"))
        self.assertEqual(safety.media_url("mirá https://m.youtube.com/watch?v=x"), "https://m.youtube.com/watch?v=x")
        self.assertEqual(safety.torrent_url("https://example.org/file.torrent?download=1"), "https://example.org/file.torrent?download=1")
        self.assertIsNone(safety.torrent_url("magnet:?xt=urn:btih:invalid"))
        value = "magnet:?xt=urn:btih:" + "a" * 40
        self.assertEqual(safety.torrent_url(value), value)

    async def test_cache_paths_and_existing_files_are_protected(self):
        destination = config.DIR_TELEGRAM_SAVED / "fixture.bin"
        status = message(self.bot)
        await safety.copy_from_cache(str(self.bot.file_path), destination, status)
        with self.assertRaises(FileExistsError):
            await safety.copy_from_cache(str(self.bot.file_path), destination, status)
        with self.assertRaises(ValueError):
            await safety.copy_from_cache("/etc/passwd", destination, status)
        self.assertFalse(list(config.DIR_TELEGRAM_SAVED.glob("*.part")))

    async def test_get_file_can_return_original_local_upload_path(self):
        source = config.VAULT_DIR / "original.txt"
        source.write_bytes(b"local upload")
        destination = config.DIR_TELEGRAM_SAVED / "copy.txt"
        await safety.copy_from_cache(str(source), destination, message(self.bot))
        self.assertEqual(destination.read_bytes(), b"local upload")

    async def test_receiving_file_is_not_limited_by_telegram_upload_limit(self):
        destination = config.DIR_TELEGRAM_SAVED / "received.bin"
        with patch.object(config, "MAX_UPLOAD_BYTES", 2):
            await safety.copy_from_cache(str(self.bot.file_path), destination, message(self.bot))
        self.assertEqual(destination.read_bytes(), self.bot.file_path.read_bytes())

    async def test_worker_survives_job_and_notification_failure(self):
        await asyncio.to_thread(self.manager.store.initialize)
        fail_status = AsyncMock()
        fail_status.edit_text.side_effect = RuntimeError("notification unavailable")
        first = worker.TaskItem("media_dl", "first", message(self.bot), message(self.bot))
        second = worker.TaskItem("media_dl", "second", message(self.bot), message(self.bot))
        for item in [first, second]:
            await asyncio.to_thread(self.manager.store.put, item)
        first.status_msg = fail_status
        seen = []
        async def process(item):
            seen.append(item.payload)
            if item.payload == "first":
                raise RuntimeError("download failed")
        self.manager._process_media = process
        self.manager.workers = [asyncio.create_task(self.manager.worker(1))]
        await self.manager.queue.put(first)
        await self.manager.queue.put(second)
        await asyncio.wait_for(self.manager.queue.join(), 3)
        self.assertEqual(seen, ["first", "second"])
        self.assertFalse(self.manager.workers[0].done())
        self.assertEqual(self.manager.active_tasks, 0)
        self.assertEqual(await asyncio.to_thread(self.manager.store.pending), [])

    async def test_persistent_job_is_recovered(self):
        await asyncio.to_thread(self.manager.store.initialize)
        item = worker.TaskItem("media_dl", "recover", message(self.bot), message(self.bot))
        await asyncio.to_thread(self.manager.store.put, item)
        recovered = worker.DownloadManager()
        recovered._process_media = AsyncMock()
        try:
            await recovered.start(self.bot)
            await asyncio.wait_for(recovered.queue.join(), 3)
            recovered._process_media.assert_awaited_once()
            self.assertEqual(recovered._process_media.call_args.args[0].job_id, item.job_id)
            self.assertEqual(await asyncio.to_thread(recovered.store.pending), [])
        finally:
            await recovered.stop()

    async def test_full_queue_does_not_persist_an_unaccepted_job(self):
        await asyncio.to_thread(self.manager.store.initialize)
        self.manager.queue = asyncio.Queue(maxsize=1)
        self.manager.queue.put_nowait("already full")
        accepted = await self.manager.add_task(worker.TaskItem("media_dl", "url", message(self.bot), message(self.bot)))
        self.assertFalse(accepted)
        self.assertEqual(await asyncio.to_thread(self.manager.store.pending), [])

    async def test_media_jobs_send_only_their_own_files_and_all_outputs(self):
        unrelated = config.DIR_MEDIA_RRSS / "unrelated.mp4"
        unrelated.write_bytes(b"unrelated")
        class Process:
            returncode = 0
            def __init__(self, paths):
                self.stdout = asyncio.StreamReader()
                for path in paths:
                    self.stdout.feed_data(("__PI_FILE__" + str(path) + "\n").encode())
                self.stdout.feed_eof()
            async def wait(self):
                return 0
        async def create(*command, **kwargs):
            home = next(value for value in command if value.startswith("home:"))
            directory = Path(home.removeprefix("home:"))
            paths = [directory / "one.mp4", directory / "two.mp4"]
            for path in paths:
                path.write_bytes(directory.name.encode())
            return Process(paths)
        other = FakeBot()
        jobs = [worker.TaskItem("media_dl", "https://x.com/test/status/1", message(b), message(b))
                for b in [self.bot, other]]
        async def prepare(source, destination):
            return MediaOutput(source, True, 320, 240, 1)
        with patch.object(worker.asyncio, "create_subprocess_exec", side_effect=create), \
                patch.object(worker, "prepare_media", side_effect=prepare):
            await asyncio.gather(*(self.manager._process_media(item) for item in jobs))
        for bot, item in zip([self.bot, other], jobs):
            videos = [call.video for call in bot.calls if call.__api_method__ == "sendVideo"]
            self.assertEqual(len(videos), 2)
            self.assertTrue(all(item.job_id in path for path in videos))
            self.assertFalse(any("unrelated" in path for path in videos))

    def test_archive_listing_rejects_traversal_links_and_bombs(self):
        for record in ["Path = ../outside\nSize = 1", "Path = safe\nSize = 1\nSymbolic Link = /etc",
                       f"Path = huge\nSize = {config.MAX_EXTRACT_BYTES + 1}"]:
            with self.assertRaises(ValueError):
                safety.check_archive_listing("header\n----------\n" + record + "\n")

    def test_interrupted_extraction_copy_never_publishes_partial_destination(self):
        source = config.DIR_TMP_DECOMPRESS / "prepared"
        source.mkdir()
        (source / "complete.txt").write_bytes(b"complete extraction")
        destination = config.DIR_MEDIA_EXTRACTED / "fixture-job"
        partial = destination.with_name("." + destination.name + ".part")
        def interrupted_copy(origin, target):
            target.mkdir()
            (target / "complete.txt").write_bytes(b"partial")
            raise OSError("simulated interrupted cross-device copy")
        with patch.object(safety.shutil, "copytree", side_effect=interrupted_copy):
            with self.assertRaises(OSError):
                safety.publish_directory(source, destination)
        self.assertFalse(destination.exists())
        self.assertFalse(partial.exists())
        # A process killed during a copy can leave this temporary directory behind.
        partial.mkdir()
        (partial / "complete.txt").write_bytes(b"stale partial")
        safety.publish_directory(source, destination)
        self.assertEqual((destination / "complete.txt").read_bytes(), b"complete extraction")
        self.assertFalse(partial.exists())

    @unittest.skipUnless(shutil.which("7zz") or shutil.which("7z"), "7zip is not installed")
    async def test_small_zip_is_extracted(self):
        source = config.DIR_TELEGRAM_SAVED / "fixture.zip"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("nested/check.txt", "functional extraction")
        item = worker.TaskItem("decompress", str(source), message(self.bot), message(self.bot))
        await self.manager._process_decompress(item)
        target = config.DIR_MEDIA_EXTRACTED / f"fixture-{item.job_id[:12]}" / "nested/check.txt"
        self.assertEqual(target.read_text(), "functional extraction")

    @unittest.skipUnless(shutil.which("7zz") or shutil.which("7z"), "7zip is not installed")
    async def test_tar_gz_is_fully_extracted(self):
        fixture = self.root / "check.txt"
        fixture.write_text("nested tar check")
        source = config.DIR_TELEGRAM_SAVED / "fixture.tar.gz"
        with tarfile.open(source, "w:gz") as archive:
            archive.add(fixture, arcname="nested/check.txt")
        item = worker.TaskItem("decompress", str(source), message(self.bot), message(self.bot))
        await self.manager._process_decompress(item)
        target = config.DIR_MEDIA_EXTRACTED / f"fixture.tar-{item.job_id[:12]}" / "nested/check.txt"
        self.assertEqual(target.read_text(), "nested tar check")

    async def test_tool_timeout_kills_process(self):
        started = time.monotonic()
        with self.assertRaises(asyncio.TimeoutError):
            await safety.tool_output([sys.executable, "-c", "import time; time.sleep(20)"], timeout=0.1)
        self.assertLess(time.monotonic() - started, 3)

    async def test_qbit_calls_do_not_block_event_loop(self):
        class Client:
            def auth_log_in(self):
                time.sleep(0.15)
            def transfer_info(self):
                return {"ok": True}
        ticks = []
        async def ticker():
            for _ in range(4):
                await asyncio.sleep(0.02)
                ticks.append(time.monotonic())
        with patch.object(main, "qbit", Client()):
            result, _ = await asyncio.gather(main.qbit_call("transfer_info"), ticker())
        self.assertEqual(result, {"ok": True})
        self.assertLess(ticks[-1] - ticks[0], 0.13)

    def test_health_requires_fresh_marker_and_live_workers(self):
        marker = config.STATE_DIR / "health.json"
        for age, workers, expected in [(0, 2, True), (60, 2, False), (0, 1, False)]:
            marker.write_text(json.dumps({"timestamp": time.time() - age, "workers": workers}))
            self.assertEqual(healthcheck.healthy(), expected)


if __name__ == "__main__":
    unittest.main()
