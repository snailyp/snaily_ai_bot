"""Bounded media I/O. No provider errors, file URLs or credentials leave this module."""
import asyncio
import json
import math
import re
import shutil
import tempfile
from pathlib import Path

import anyio
import httpx
import openai

from bot.services.text_generation import client_options

MAX_BYTES = 20 * 1024 * 1024
MAX_SECONDS = 300


class MediaError(ValueError):
    """A credential-safe, user-facing media failure."""


def speech_text(text):
    text = re.sub(r"```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)", "", text)
    text = "\n".join(line for line in text.splitlines() if "|" not in line)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[`*_#>~]", "", text)
    return re.sub(r"\s+", " ", text).strip()


async def bounded_body(response, limit=MAX_BYTES):
    if response.headers.get("content-encoding", "identity").lower() not in ("", "identity"):
        raise MediaError("媒体服务返回了不支持的压缩响应。")
    length = response.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > limit):
        raise MediaError("媒体文件超过大小限制。")
    result = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=65536):
        if len(result) + len(chunk) > limit:
            raise MediaError("媒体文件超过大小限制。")
        result.extend(chunk)
    if not result:
        raise MediaError("媒体文件为空。")
    return bytes(result)


async def download_telegram(media, bot, client_factory=httpx.AsyncClient):
    if media.file_size and media.file_size > MAX_BYTES:
        raise MediaError("媒体文件不能超过 20 MB。")
    try:
        with anyio.fail_after(65):
            file = await bot.get_file(media.file_id)
            # Never accept local paths or arbitrary URLs returned by a local Bot API.
            from urllib.parse import urlsplit
            url = urlsplit(file.file_path or "")
            if url.scheme != "https" or url.hostname != "api.telegram.org" or url.username or url.password:
                raise MediaError("暂不支持此 Telegram 文件下载地址。")
            async with client_factory(timeout=30, follow_redirects=False) as client:
                async with client.stream("GET", file.file_path, headers={"Accept-Encoding": "identity"}) as response:
                    if response.status_code != 200:
                        raise MediaError("Telegram 媒体下载失败，请重试。")
                    return await bounded_body(response)
    except MediaError:
        raise
    except Exception:
        raise MediaError("Telegram 媒体下载失败或超时。") from None


class AudioProcessor:
    def __init__(self):
        self._slots = asyncio.Semaphore(2)

    async def _run(self, *args):
        process = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        async def collect():
            output = bytearray()
            while True:
                chunk = await process.stdout.read(8192)
                if not chunk:
                    break
                if len(output) + len(chunk) > 65536:
                    raise MediaError("音频元数据超过处理限制。")
                output.extend(chunk)
            await process.wait()
            return bytes(output)

        try:
            output = await asyncio.wait_for(collect(), 35)
            if process.returncode:
                raise MediaError("音频格式无效或无法转换。")
            return output
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def convert(self, data, *, voice=False):
        if not data or len(data) > MAX_BYTES:
            raise MediaError("音频为空或超过 20 MB。")
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise MediaError("语音处理不可用：管理员需要安装 ffmpeg 和 ffprobe。")
        async with self._slots:
            with tempfile.TemporaryDirectory(prefix="snaily-audio-") as directory:
                source = Path(directory) / "input"
                target = Path(directory) / ("voice.ogg" if voice else "audio.wav")
                source.write_bytes(data)
                try:
                    raw = await self._run("ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-format_whitelist", "ogg,mp3,wav,flac,aac,mov,matroska,webm",
                                          "-select_streams", "a:0", "-show_entries", "format=duration:stream=codec_type,duration",
                                          "-of", "json", str(source))
                    info = json.loads(raw)
                    streams = info.get("streams", [])
                    duration = float(info.get("format", {}).get("duration", 0))
                    if not any(s.get("codec_type") == "audio" for s in streams):
                        raise MediaError("文件不包含有效音频。")
                    if not math.isfinite(duration) or duration <= 0 or duration > MAX_SECONDS:
                        raise MediaError("音频必须在 5 分钟以内且具有有效时长。")
                    await self._run("ffmpeg", "-v", "error", "-nostdin", "-protocol_whitelist", "file,pipe", "-format_whitelist", "ogg,mp3,wav,flac,aac,mov,matroska,webm",
                                    "-i", str(source), "-map", "0:a:0", "-vn", "-t", "301", "-ac", "1",
                                    "-ar", "24000" if voice else "16000", "-c:a", "libopus" if voice else "pcm_s16le",
                                    "-fs", str(MAX_BYTES), "-y", str(target))
                    verified = json.loads(await self._run(
                        "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
                        "-select_streams", "a:0", "-show_entries", "format=duration:stream=codec_name",
                        "-of", "json", str(target),
                    ))
                    actual_duration = float(verified.get("format", {}).get("duration", 0))
                    if not math.isfinite(actual_duration) or actual_duration <= 0 or actual_duration > MAX_SECONDS:
                        raise MediaError("实际音频时长超过 5 分钟或无效。")
                    if voice and not any(s.get("codec_name") == "opus" for s in verified.get("streams", [])):
                        raise MediaError("语音输出不是有效的 Opus 音频。")
                    output = target.read_bytes()
                    if not output or len(output) >= MAX_BYTES:
                        raise MediaError("转换后的音频超过大小限制。")
                    return output
                except MediaError:
                    raise
                except Exception:
                    raise MediaError("音频验证失败或处理超时。") from None


class SpeechService:
    def __init__(self, client_factory=None, processor=None):
        self.client_factory = client_factory or httpx.AsyncClient
        self.processor = processor or AudioProcessor()

    async def _request(self, provider, endpoint, *, limit, **kwargs):
        options = client_options(provider)
        headers = {k: v for k, v in options["default_headers"].items() if not isinstance(v, openai.Omit)}
        headers["Accept-Encoding"] = "identity"
        if provider.get("api_key") and "Authorization" not in headers:
            headers["Authorization"] = "Bearer " + provider["api_key"]
        # At most one retry, only for explicit 429/503 rejection; never switch providers.
        try:
            with anyio.fail_after(min(provider.get("timeout", 60), 300) + 5):
                async with self.client_factory(timeout=options["timeout"], follow_redirects=False) as client:
                    for attempt in range(2):
                        async with client.stream("POST", options["base_url"] + endpoint, headers=headers, **kwargs) as response:
                            if response.status_code in (429, 503) and attempt == 0:
                                await asyncio.sleep(0.25)
                                continue
                            if response.status_code != 200:
                                raise MediaError("语音服务请求失败，请检查模型、接口及密钥。")
                            return await bounded_body(response, limit)
        except MediaError:
            raise
        except Exception:
            raise MediaError("语音服务连接失败或超时，请稍后再试。") from None

    async def transcribe(self, provider, model, data):
        wav = await self.processor.convert(data)
        result = await self._request(provider, "audio/transcriptions", limit=256 * 1024,
                                     data={"model": model["model"], "response_format": "json"},
                                     files={"file": ("audio.wav", wav, "audio/wav")})
        try:
            text = json.loads(result).get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError()
            return text.strip()
        except (ValueError, AttributeError):
            raise MediaError("未识别到有效语音，请重新录制。") from None

    async def synthesize(self, provider, model, text):
        if not text or len(text) > 2000:
            raise MediaError("朗读内容为空或超过 2,000 字符，仅发送文字。")
        audio = await self._request(provider, "audio/speech", limit=MAX_BYTES,
                                    json={"model": model["model"], "voice": model["voice"],
                                          "input": text, "response_format": "opus"})
        return await self.processor.convert(audio, voice=True)
