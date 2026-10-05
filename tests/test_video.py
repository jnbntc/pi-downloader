import json
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
import config
from safety import tool_output
from video import prepare_media, probe, validate_video


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class VideoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def fixture(self, path, *, audio=True, portrait=False):
        size = "641x1025" if portrait else "241x427"
        command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                   "-f", "lavfi", "-i", f"testsrc=size={size}:rate=60"]
        if audio:
            command += ["-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000",
                        "-c:a", "libopus"]
        command += ["-t", "0.5", "-c:v", "libvpx-vp9", "-deadline", "realtime",
                    "-cpu-used", "8", "-threads", "2", "-pix_fmt", "yuv444p",
                    "-metadata", "artist=Private fixture author", str(path)]
        await tool_output(command, timeout=60)

    async def test_webm_vp9_opus_becomes_full_mp4_baseline_aac(self):
        source, destination = self.root / "input.webm", self.root / "output.mp4"
        await self.fixture(source)
        original = await probe(source)
        output = await prepare_media(source, destination)
        data = await probe(destination)
        self.assertTrue(output.is_video)
        self.assertEqual(output.path, destination)
        self.assertTrue(source.exists())
        validate_video(data, float(original["format"]["duration"]))
        self.assertEqual(data["streams"][0]["codec_name"], "h264")
        self.assertEqual(data["streams"][0]["pix_fmt"], "yuv420p")
        self.assertEqual(data["streams"][1]["codec_name"], "aac")
        self.assertEqual(data["streams"][1]["channels"], 2)
        self.assertEqual(data["streams"][0]["r_frame_rate"], "30/1")
        self.assertNotIn("Private fixture author", json.dumps(data))
        atoms = []
        with destination.open("rb") as stream:
            while header := stream.read(8):
                length, kind = struct.unpack(">I4s", header)
                self.assertGreaterEqual(length, 8)
                atoms.append(kind)
                stream.seek(length - 8, 1)
        self.assertLess(atoms.index(b"moov"), atoms.index(b"mdat"))
        # A recovered job can reuse its completed conversion without overwriting it.
        with patch("video.tool_output", wraps=tool_output) as calls:
            recovered = await prepare_media(source, destination)
        self.assertEqual(recovered.path, destination)
        self.assertFalse(any(call.args[0][0] == "ffmpeg" for call in calls.call_args_list))

    async def test_portrait_video_without_audio_keeps_orientation_and_duration(self):
        source, destination = self.root / "portrait.webm", self.root / "portrait.mp4"
        await self.fixture(source, audio=False, portrait=True)
        output = await prepare_media(source, destination)
        data = await probe(destination)
        self.assertGreater(output.height, output.width)
        self.assertLessEqual(output.height, 720)
        self.assertEqual([s["codec_type"] for s in data["streams"]], ["video"])
        self.assertLess(abs(output.duration - float((await probe(source))["format"]["duration"])), 0.2)

    async def test_failed_conversion_does_not_publish_partial_video(self):
        source, destination = self.root / "source.webm", self.root / "out.mp4"
        await self.fixture(source)
        with patch("video.tool_output", wraps=tool_output) as command:
            async def fail(args, **kwargs):
                if args[0] == "ffmpeg":
                    Path(args[-1]).write_bytes(b"incomplete")
                    raise RuntimeError("simulated converter failure")
                return await tool_output(args, **kwargs)
            command.side_effect = fail
            with self.assertRaises(RuntimeError):
                await prepare_media(source, destination)
        self.assertFalse(destination.exists())
        self.assertFalse(list(self.root.glob("*.part")))

    def test_validation_rejects_truncation_and_incompatible_codecs(self):
        data = {"format": {"duration": "20"}, "streams": [{"codec_type": "video",
                "codec_name": "h264", "pix_fmt": "yuv420p", "profile": "Constrained Baseline",
                "level": 31, "width": 320, "height": 240, "r_frame_rate": "30/1"}]}
        with self.assertRaises(ValueError):
            validate_video(data, 60)
        data["streams"][0]["codec_name"] = "hevc"
        with self.assertRaises(ValueError):
            validate_video(data, 20)

    async def test_audio_only_is_preserved_and_oversized_video_is_rejected(self):
        audio = self.root / "audio.ogg"
        await tool_output(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                           "sine=duration=0.2", "-c:a", "libopus", str(audio)])
        result = await prepare_media(audio, self.root / "unused.mp4")
        self.assertFalse(result.is_video)
        self.assertEqual(result.path, audio)
        source, destination = self.root / "large.webm", self.root / "large.mp4"
        await self.fixture(source)
        with patch.object(config, "MAX_UPLOAD_BYTES", 1):
            with self.assertRaises(ValueError):
                await prepare_media(source, destination)
        self.assertFalse(destination.exists())
        self.assertFalse(list(self.root.glob("*.part")))

    async def test_anamorphic_video_preserves_display_aspect_ratio(self):
        source, destination = self.root / "anamorphic.mp4", self.root / "square-pixels.mp4"
        await tool_output(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                           "color=size=320x240:rate=30:duration=0.2", "-vf", "setsar=2",
                           "-c:v", "libx264", "-threads", "2", str(source)])
        await prepare_media(source, destination)
        output = (await probe(destination))["streams"][0]
        self.assertEqual(output["sample_aspect_ratio"], "1:1")
        self.assertAlmostEqual(output["width"] / output["height"], 8 / 3, places=2)
