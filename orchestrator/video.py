"""Full-length MP4 output suitable for sharing through WhatsApp."""
import asyncio
import json
import math
import uuid
from dataclasses import dataclass
from pathlib import Path

import config
from safety import tool_output


@dataclass(frozen=True)
class MediaOutput:
    path: Path
    is_video: bool
    width: int = 0
    height: int = 0
    duration: float = 0


async def probe(path):
    data = json.loads(await tool_output([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path),
    ], timeout=60))
    if not isinstance(data.get("streams"), list):
        raise ValueError("No se pudo identificar el contenido multimedia")
    return data


def duration(data):
    value = float(data.get("format", {}).get("duration", 0))
    if not math.isfinite(value) or value <= 0:
        raise ValueError("El video no tiene una duración válida")
    return value


def validate_video(data, original_duration):
    videos = [s for s in data["streams"] if s.get("codec_type") == "video"]
    audios = [s for s in data["streams"] if s.get("codec_type") == "audio"]
    if len(videos) != 1 or len(audios) > 1:
        raise ValueError("La salida debe tener un video y como máximo una pista de audio")
    video = videos[0]
    numerator, denominator = str(video.get("r_frame_rate", "0/1")).split("/")
    fps = float(numerator) / float(denominator)
    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    if (video.get("codec_name") != "h264" or video.get("pix_fmt") != "yuv420p"
            or video.get("profile") not in {"Baseline", "Constrained Baseline"}
            or video.get("level", 999) > 31 or width <= 0 or height <= 0
            or width > 1280 or height > 720 or width % 2 or height % 2
            or not 0 < fps <= 30):
        raise ValueError("La salida de video no cumple el formato compatible")
    if audios and (audios[0].get("codec_name") != "aac"
                   or audios[0].get("channels", 0) > 2):
        raise ValueError("La salida de audio no es compatible")
    output_duration = duration(data)
    if abs(output_duration - original_duration) > 1:
        raise ValueError("La conversión no conservó la duración completa del video")
    return width, height, output_duration


async def prepare_media(source, destination):
    source, destination = Path(source), Path(destination)
    original = await probe(source)
    if not any(s.get("codec_type") == "video" for s in original["streams"]):
        return MediaOutput(source, False)
    original_duration = duration(original)
    if destination.exists():
        width, height, output_duration = validate_video(await probe(destination), original_duration)
        if destination.stat().st_size > config.MAX_UPLOAD_BYTES:
            raise ValueError("El video completo supera el límite de envío configurado")
        return MediaOutput(destination, True, width, height, output_duration)
    partial = destination.with_name("." + destination.name + "." + uuid.uuid4().hex + ".part")
    try:
        await tool_output([
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning", "-y",
            "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
            "-vf", "scale=w='max(2,trunc(min(iw*if(gt(sar,0),sar,1),min(1280,720*dar))/2)*2)':h='max(2,trunc(min(ih,min(720,1280/dar))/2)*2)',setsar=1,fps=30",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-profile:v", "baseline", "-level:v", "3.1", "-pix_fmt", "yuv420p",
            "-maxrate", "2500k", "-bufsize", "5000k", "-threads", "2",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
            "-map_metadata", "-1", "-map_chapters", "-1", "-movflags", "+faststart",
            "-f", "mp4", str(partial),
        ], timeout=config.VIDEO_TIMEOUT)
        width, height, output_duration = validate_video(await probe(partial), original_duration)
        if partial.stat().st_size > config.MAX_UPLOAD_BYTES:
            raise ValueError("El video completo supera el límite de envío configurado")
        await asyncio.to_thread(partial.rename, destination)
        return MediaOutput(destination, True, width, height, output_duration)
    finally:
        partial.unlink(missing_ok=True)
