import asyncio
import logging
import os
import re
import shutil
import signal
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import parse_qs, urlsplit

import config

logger = logging.getLogger("pi")
MEDIA_HOSTS = {"youtube.com", "youtu.be", "instagram.com", "twitter.com", "x.com",
               "t.co", "tiktok.com", "reddit.com", "fb.watch", "facebook.com"}


def redact(value):
    value = str(value)
    for secret in [config.BOT_TOKEN, config.QBIT_PASSWORD]:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b", "[REDACTED]", value)


async def notify(status, text):
    """A delivery failure must never terminate a worker or mask a job failure."""
    try:
        await status.edit_text(redact(text)[:3900])
        return True
    except Exception as error:
        logger.warning("Could not update status: %s", redact(error))
        return False


def safe_filename(name, fallback):
    if not name:
        return fallback
    if name in {".", ".."} or "/" in name or "\\" in name or PureWindowsPath(name).drive:
        raise ValueError("El nombre del archivo contiene una ruta no permitida")
    clean = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return clean[:180] or fallback


def contained_path(path, roots):
    resolved = Path(path).resolve()
    if not any(resolved.is_relative_to(Path(root).resolve()) for root in roots):
        raise ValueError("La ruta está fuera de los directorios permitidos")
    return resolved


def media_url(text):
    for match in re.finditer(r"https?://[^\s<>]+", text, re.IGNORECASE):
        url = match.group().rstrip(".,;!?)\"'")
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if not parsed.username and not parsed.password and any(
            host == allowed or host.endswith("." + allowed) for allowed in MEDIA_HOSTS
        ):
            return url
    return None


def torrent_url(text):
    value = text.strip()
    parsed = urlsplit(value)
    if parsed.scheme.lower() == "magnet":
        hashes = parse_qs(parsed.query).get("xt", [])
        if any(re.fullmatch(r"urn:btih:(?:[a-fA-F0-9]{40}|[a-zA-Z2-7]{32})", h) for h in hashes):
            return value
    elif parsed.scheme.lower() in {"http", "https"} and parsed.hostname:
        if not parsed.username and not parsed.password and parsed.path.lower().endswith(".torrent"):
            return value
    return None


def archive_tool():
    executable = shutil.which("7zz") or shutil.which("7z")
    if not executable:
        raise RuntimeError("Falta 7zip (7zz o 7z) en el contenedor")
    return executable


def check_archive_listing(text):
    """Validate 7-Zip's technical listing before extracting an archive."""
    entries = text.split("----------", 1)
    if len(entries) != 2:
        raise ValueError("No se pudo validar el listado del archivo comprimido")
    total = 0
    count = 0
    for block in entries[1].strip().split("\n\n"):
        fields = dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
        name = fields.get("Path")
        if name is None:
            continue
        path = PurePosixPath(name.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or PureWindowsPath(name).drive:
            raise ValueError("El archivo comprimido contiene una ruta insegura")
        if fields.get("Symbolic Link") or fields.get("Hard Link") or re.search(r"\bl[rwx-]{9}", fields.get("Attributes", "")):
            raise ValueError("No se permiten enlaces en archivos comprimidos")
        if fields.get("Encrypted") == "+":
            raise ValueError("No se admiten archivos comprimidos con contraseña")
        size = int(fields.get("Size", "0"))
        if size < 0:
            raise ValueError("Tamaño de archivo inválido")
        total += size
        count += 1
    if not count:
        raise ValueError("El archivo comprimido está vacío o no se pudo validar")
    if count > config.MAX_EXTRACT_FILES or total > config.MAX_EXTRACT_BYTES:
        raise ValueError("El contenido extraído supera los límites configurados")


def check_extracted_tree(root):
    total = 0
    count = 0
    for path in Path(root).rglob("*"):
        if path.is_symlink():
            raise ValueError("No se permiten enlaces en el contenido extraído")
        contained_path(path, [root])
        if path.is_file():
            count += 1
            total += path.stat().st_size
        elif not path.is_dir():
            raise ValueError("Tipo de archivo extraído no permitido")
    if count > config.MAX_EXTRACT_FILES or total > config.MAX_EXTRACT_BYTES:
        raise ValueError("El contenido extraído supera los límites configurados")


async def terminate_process(process):
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), 5)
    except asyncio.TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


async def tool_output(command, timeout=None, limit=8_000_000):
    process = await asyncio.create_subprocess_exec(
        *command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, start_new_session=True,
    )

    async def collect():
        data = bytearray()
        while chunk := await process.stdout.read(8192):
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError("La salida de la herramienta supera el límite permitido")
        await process.wait()
        text = data.decode("utf-8", errors="replace")
        if process.returncode:
            raise RuntimeError(redact(text[-2000:]) or "La herramienta terminó con error")
        return text

    try:
        return await asyncio.wait_for(collect(), timeout or config.TASK_TIMEOUT)
    finally:
        await terminate_process(process)


async def copy_from_cache(file_path, destination, status):
    source = Path(file_path)
    if not source.is_absolute():
        source = config.TELEGRAM_CACHE_DIR / config.BOT_TOKEN / source
    # Local uploads can be returned as their original shared media path by getFile.
    source = contained_path(source, [config.TELEGRAM_CACHE_DIR, config.VAULT_DIR])
    if not source.is_file():
        raise FileNotFoundError("El archivo no está disponible en el caché local de Telegram")
    total = source.stat().st_size
    if total > config.MAX_DOWNLOAD_BYTES:
        raise ValueError("El archivo supera el tamaño máximo configurado")
    destination = contained_path(destination, [config.VAULT_DIR, config.WORKSPACE_DIR])
    if destination.exists():
        raise FileExistsError("El archivo de destino ya existe")
    partial = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".part")
    copy_task = asyncio.create_task(asyncio.to_thread(shutil.copyfile, source, partial))
    try:
        while not copy_task.done():
            await asyncio.wait([copy_task], timeout=2)
            if not copy_task.done() and total:
                copied = partial.stat().st_size if partial.exists() else 0
                await notify(status, f"Guardando archivo: {min(copied * 100 / total, 99):.1f}%")
        await asyncio.shield(copy_task)
        # Publish a complete file atomically, refusing to overwrite another file.
        await asyncio.to_thread(os.link, partial, destination)
    finally:
        if not copy_task.done():
            await asyncio.shield(copy_task)
        partial.unlink(missing_ok=True)


def publish_directory(source, destination):
    """Copy across filesystems, then expose the complete directory atomically."""
    source, destination = Path(source), Path(destination)
    partial = destination.with_name("." + destination.name + ".part")
    if destination.exists():
        raise FileExistsError("El destino de extracción ya existe")
    if partial.exists():
        if partial.is_symlink() or not partial.is_dir():
            raise ValueError("El destino temporal de extracción no es seguro")
        shutil.rmtree(partial)
    try:
        shutil.copytree(source, partial)
        check_extracted_tree(partial)
        if destination.exists():
            raise FileExistsError("El destino de extracción ya existe")
        partial.rename(destination)
    finally:
        if partial.exists():
            shutil.rmtree(partial)
