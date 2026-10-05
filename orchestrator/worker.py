import asyncio
import logging
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field

from aiogram import types

import config
from jobstore import JobStore
from video import prepare_media
from safety import (archive_tool, check_archive_listing, check_extracted_tree,
                    contained_path, notify, publish_directory, redact, terminate_process, tool_output)

logger = logging.getLogger("pi.worker")


@dataclass
class TaskItem:
    task_type: str
    payload: str
    message: types.Message
    status_msg: types.Message
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex)


class DownloadManager:
    def __init__(self):
        self.queue = asyncio.Queue(maxsize=config.MAX_QUEUE_SIZE)
        self.capacity = asyncio.Semaphore(config.MAX_CONCURRENT_TASKS)
        self.active_tasks = 0
        self.workers = []
        self.store = JobStore(config.STATE_DIR / "jobs.sqlite3")
        self.enqueue_lock = asyncio.Lock()

    async def start(self, bot):
        await asyncio.to_thread(self.store.initialize)
        pending = await asyncio.to_thread(self.store.pending)
        self.workers = [asyncio.create_task(self.worker(i), name=f"worker-{i}")
                        for i in range(1, config.MAX_CONCURRENT_TASKS + 1)]
        for job_id, data in pending:
            message = types.Message.model_validate(data["message"], context={"bot": bot})
            status = types.Message.model_validate(data["status_msg"], context={"bot": bot})
            user_id = message.from_user.id if message.from_user else None
            if user_id not in config.ALLOWED_USER_IDS or message.chat.id not in config.ALLOWED_CHAT_IDS:
                await asyncio.to_thread(self.store.remove, job_id)
                continue
            await self.queue.put(TaskItem(data["task_type"], data["payload"], message, status, job_id))
        logger.info("Recovered %s pending jobs", len(pending))

    async def stop(self):
        for task in self.workers:
            task.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)

    async def add_task(self, item):
        async with self.enqueue_lock:
            if self.queue.full():
                await notify(item.status_msg, "La cola está llena. Intentá de nuevo más tarde.")
                return False
            await asyncio.to_thread(self.store.put, item)
            self.queue.put_nowait(item)
        logger.info("Queued job %s (%s)", item.job_id, item.task_type)
        await notify(item.status_msg, f"Trabajo recibido. En cola: {self.queue.qsize()}.")
        return True

    async def worker(self, worker_id):
        logger.info("Worker %s started", worker_id)
        while True:
            item = await self.queue.get()
            finished = False
            try:
                async with self.capacity:
                    self.active_tasks += 1
                    try:
                        logger.info("Job %s started (%s)", item.job_id, item.task_type)
                        if item.task_type == "media_dl":
                            await self._process_media(item)
                        elif item.task_type == "decompress":
                            await self._process_decompress(item)
                        else:
                            raise ValueError("Tipo de trabajo desconocido")
                    finally:
                        self.active_tasks -= 1
                logger.info("Job %s completed", item.job_id)
                finished = True
            except asyncio.CancelledError:
                raise  # Leave the durable record pending for recovery.
            except Exception as error:
                logger.error("Job %s failed: %s", item.job_id, redact(error))
                await notify(item.status_msg, f"El trabajo falló: {redact(error)[:1500]}")
                finished = True
            finally:
                if finished:
                    try:
                        await asyncio.to_thread(self.store.remove, item.job_id)
                    except Exception as error:
                        logger.error("Could not finish job record %s: %s", item.job_id, redact(error))
                self.queue.task_done()

    async def _process_media(self, item):
        output_dir = config.DIR_MEDIA_RRSS / item.job_id
        temp_dir = config.DIR_TMP_MEDIA / item.job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        original_dir = output_dir / "originals"
        original_dir.mkdir(exist_ok=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        await notify(item.status_msg, "Descargando multimedia…")
        command = [
            "yt-dlp", "--no-playlist", "--no-cache-dir", "--no-simulate",
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--max-filesize", str(config.MAX_UPLOAD_BYTES),
            "--socket-timeout", "20", "--retries", "2", "--extractor-retries", "2",
            "--paths", f"home:{original_dir}", "--paths", f"temp:{temp_dir}",
            "-o", "%(title).100s [%(id)s].%(ext)s",
            "--print", "after_move:__PI_FILE__%(filepath)s", "--progress", "--newline", item.payload,
        ]
        process = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, start_new_session=True,
        )
        outputs = []
        tail = []

        async def consume():
            last_update = 0
            while line := await process.stdout.readline():
                text = line.decode("utf-8", errors="replace").strip()
                tail.append(redact(text)[:2000])
                del tail[:-12]
                if text.startswith("__PI_FILE__"):
                    path = contained_path(text[len("__PI_FILE__"):], [output_dir])
                    if path not in outputs:
                        outputs.append(path)
                    if len(outputs) > config.MAX_MEDIA_FILES:
                        raise ValueError("La publicación contiene demasiados archivos")
                elif "ERROR:" in text or "WARNING:" in text:
                    logger.warning("yt-dlp job %s: %s", item.job_id, redact(text))
                else:
                    match = re.search(r"\[download\]\s+(\d+(?:\.\d+)?)%", text)
                    if match and time.monotonic() - last_update > 3:
                        await notify(item.status_msg, f"Descargando multimedia: {match.group(1)}%")
                        last_update = time.monotonic()
            await process.wait()
            if process.returncode:
                raise RuntimeError("\n".join(tail)[-2000:] or "yt-dlp terminó con error")

        try:
            await asyncio.wait_for(consume(), config.TASK_TIMEOUT)
        finally:
            await terminate_process(process)
            await asyncio.to_thread(shutil.rmtree, temp_dir, True)
        if not outputs:
            raise RuntimeError("yt-dlp no produjo archivos descargables dentro del límite configurado")
        for index, path in enumerate(outputs, 1):
            if not path.is_file() or path.stat().st_size > config.MAX_UPLOAD_BYTES:
                raise ValueError("La salida no existe o supera el límite de envío")
            await notify(item.status_msg, f"Preparando video compatible: {index}/{len(outputs)}…")
            media = await prepare_media(path, output_dir / f"{index:02d}_{path.stem[:160]}.mp4")
            await notify(item.status_msg, f"Enviando archivo {index}/{len(outputs)}…")
            uri = "file://" + str(media.path)
            caption = media.path.name[:1000]
            if media.is_video:
                await item.message.answer_video(video=uri, caption=caption, supports_streaming=True,
                                                width=media.width, height=media.height,
                                                duration=max(1, round(media.duration)))
            else:
                await item.message.answer_document(document=uri, caption=caption)
        await notify(item.status_msg, f"Descarga completa: {len(outputs)} archivo(s). Guardado en {output_dir}.")

    async def _process_decompress(self, item):
        source = contained_path(item.payload, [config.VAULT_DIR, config.TORRENT_DIR])
        if not source.is_file():
            raise FileNotFoundError("El archivo comprimido no existe")
        tool = archive_tool()
        await notify(item.status_msg, "Validando archivo comprimido…")
        listing = await tool_output([tool, "l", "-slt", "-p", "--", str(source)])
        check_archive_listing(listing)
        scratch = config.DIR_TMP_DECOMPRESS / item.job_id
        destination = config.DIR_MEDIA_EXTRACTED / f"{source.stem[:120]}-{item.job_id[:12]}"
        if destination.is_dir():
            await asyncio.to_thread(check_extracted_tree, destination)
            await notify(item.status_msg, f"Extracción ya completada. Destino: {destination}")
            return
        if scratch.exists():
            await asyncio.to_thread(shutil.rmtree, scratch)
        scratch.mkdir(parents=True)
        try:
            await notify(item.status_msg, "Extrayendo en el disco de trabajo…")
            await tool_output([tool, "x", "-y", "-p", f"-o{scratch}", "--", str(source)])
            await asyncio.to_thread(check_extracted_tree, scratch)
            extracted_root = scratch
            if source.name.lower().endswith((".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")):
                files = list(scratch.iterdir())
                if len(files) == 1 and files[0].is_file() and files[0].suffix.lower() == ".tar":
                    listing = await tool_output([tool, "l", "-slt", "-p", "--", str(files[0])])
                    check_archive_listing(listing)
                    extracted_root = scratch / "unpacked"
                    extracted_root.mkdir()
                    await tool_output([tool, "x", "-y", "-p", f"-o{extracted_root}", "--", str(files[0])])
                    await asyncio.to_thread(check_extracted_tree, extracted_root)
            if destination.exists():
                raise FileExistsError("El destino de extracción ya existe")
            publication = asyncio.create_task(asyncio.to_thread(publish_directory, extracted_root, destination))
            try:
                await asyncio.shield(publication)
            finally:
                # A cancelled worker must not remove the source while its copy thread is active.
                if not publication.done():
                    await asyncio.shield(publication)
            await notify(item.status_msg, f"Extracción completa. Destino: {destination}")
        finally:
            if scratch.exists():
                await asyncio.to_thread(shutil.rmtree, scratch, True)


manager = DownloadManager()
