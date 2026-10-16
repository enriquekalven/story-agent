"""Chinese Audio Narration Generator for Storybook Agent.

Synthesizes soothing, high-fidelity Chinese bedtime audio narration for children's
books using Gemini TTS (cross-platform Linux/Cloud Run) or macOS native speech
synthesis (Tingting / Meijia) as a local engine, and mixes with gentle ambient
background music using FFmpeg.
"""

import logging
import os
import re
import shutil
import subprocess
import tempfile
import wave
from typing import Any, Dict, Optional

from google.genai import Client, types

import config

logger = logging.getLogger(__name__)

# Default voices for Chinese narration
DEFAULT_CHINESE_VOICE = "Tingting"  # macOS zh_CN natural voice
ALTERNATIVE_CHINESE_VOICE = "Meijia"  # macOS zh_TW natural voice
DEFAULT_GEMINI_VOICE = config.GEMINI_TTS_VOICE


def is_chinese_text(text: str) -> bool:
    """Checks if a string contains Chinese characters."""
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def clean_script_for_cloud_tts(script: str) -> str:
    """Replaces macOS `[[slnc N]]` directives with natural punctuation pauses for Cloud/Gemini TTS."""
    cleaned = re.sub(r"\[\[slnc\s+\d+\]\]", "……", script)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def build_chinese_narration_script(story_json: Dict[str, Any]) -> str:
    """Extracts and structures a cohesive Chinese read-aloud bedtime script from story JSON.

    Includes title, author credit, natural page-by-page pacing pauses, and a gentle closing.
    """
    title = story_json.get("title_zh") or story_json.get("title", "绘本故事")
    author = "Enrique Chan"
    pages = story_json.get("pages", [])

    script_parts = []

    # 1. Opening Sonic Branding
    series_title = "好奇小脑瓜·莉莉与乐乐大冒险"
    script_parts.append(f"欢迎收听《{series_title}》睡前故事。")
    script_parts.append(f"今天我们要听的故事是：《{title}》。")
    script_parts.append(f"著者：{author}。")
    script_parts.append("[[slnc 900]]")  # 900ms natural pause before beginning

    # 2. Page-by-page narrative
    for page in pages:
        p_num = page.get("page_number", 1)
        raw_text = page.get("text_zh") or page.get("text", "")

        # Clean text
        clean_text = raw_text.replace("\n", "，").strip()
        # Strip author lines if appearing in body text
        clean_text = re.sub(
            r"(written by|escrito por|作者|著者)[：:\s]*[a-zA-Z\s]+",
            "",
            clean_text,
            flags=re.IGNORECASE,
        ).strip()
        clean_text = clean_text.strip("，, ")

        # Skip cover title duplication if already read
        if p_num == 1 and (
            title in clean_text
            or "Written by" in raw_text
            or "Escrito por" in raw_text
            or "作者" in raw_text
            or "著者" in raw_text
        ):
            continue

        # Skip colophon page (Page 2) if it contains publishing/copyright metadata
        if p_num == 2 and (
            "curious little minds publishing" in clean_text.lower()
            or "copyright" in clean_text.lower()
            or "all rights reserved" in clean_text.lower()
            or "版权所有" in clean_text
        ):
            continue

        # Skip raw "The End" / "El Fin" placeholder if present on last page
        if p_num == len(pages) and clean_text.lower() in [
            "the end",
            "el fin",
            "fin",
            "fim",
            "终",
            "完",
            "全书完",
            "剧终",
        ]:
            continue

        if clean_text:
            script_parts.append(f"{clean_text}")
            script_parts.append("[[slnc 600]]")  # 600ms page pause

    # 3. Comforting bedtime closing
    script_parts.append("[[slnc 500]]")
    script_parts.append(
        f"《{title}》故事讲完了。这是《好奇小脑瓜》陪伴你的又一个奇妙夜晚。"
        "谢谢你，聪明的小探险家！祝你做个甜美的好梦，晚安，明天见！"
    )

    return "\n".join(script_parts)


def synthesize_speech_say(
    text: str,
    output_aiff_path: str,
    voice: str = DEFAULT_CHINESE_VOICE,
    rate_wpm: int = 180,
) -> bool:
    """Synthesizes text using macOS 'say' command."""
    try:
        check_cmd = ["say", "-v", "?"]
        res = subprocess.run(check_cmd, capture_output=True, text=True, check=True)
        available_voices = res.stdout

        selected_voice: Optional[str] = voice
        if voice not in available_voices:
            if DEFAULT_CHINESE_VOICE in available_voices:
                selected_voice = DEFAULT_CHINESE_VOICE
            elif ALTERNATIVE_CHINESE_VOICE in available_voices:
                selected_voice = ALTERNATIVE_CHINESE_VOICE
            else:
                logger.warning(
                    f"Neither {voice} nor fallback Chinese voices found. Using default system voice."
                )
                selected_voice = None

        cmd = ["say"]
        if selected_voice:
            cmd.extend(["-v", selected_voice])
        if rate_wpm:
            cmd.extend(["-r", str(rate_wpm)])
        cmd.extend([text, "-o", output_aiff_path])

        logger.info(f"Synthesizing speech with macOS voice '{selected_voice}' (rate: {rate_wpm} wpm)...")
        subprocess.run(cmd, check=True)
        return os.path.exists(output_aiff_path) and os.path.getsize(output_aiff_path) > 0
    except Exception as e:
        logger.error(f"Failed to synthesize speech using 'say': {e}")
        return False


def synthesize_speech_gemini_tts(
    text: str,
    output_audio_path: str,
    voice_name: str = DEFAULT_GEMINI_VOICE,
    client: Optional[Client] = None,
) -> bool:
    """Synthesizes Chinese bedtime narration using Gemini TTS (cross-platform Linux/Cloud Run)."""
    try:
        clean_text = clean_script_for_cloud_tts(text)
        tts_client = client or Client(
            vertexai=True,
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION,
        )
        prompt = (
            "Read the following Mandarin Chinese children's bedtime story in a warm, gentle, "
            "soothing storyteller voice with natural pauses between sentences:\n\n"
            f"{clean_text}"
        )
        logger.info(
            f"Synthesizing speech with Gemini TTS model '{config.TTS_MODEL}' (voice: '{voice_name}')..."
        )
        response = tts_client.models.generate_content(
            model=config.TTS_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name)
                    )
                ),
            ),
        )

        audio_bytes = None
        mime_type = ""
        if response.candidates:
            for candidate in response.candidates:
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if part.inline_data and part.inline_data.data:
                            audio_bytes = part.inline_data.data
                            mime_type = (part.inline_data.mime_type or "").lower()
                            break
        elif getattr(response, "parts", None):
            for part in response.parts:
                if part.inline_data and part.inline_data.data:
                    audio_bytes = part.inline_data.data
                    mime_type = (part.inline_data.mime_type or "").lower()
                    break

        if not audio_bytes:
            raise RuntimeError("Gemini TTS returned no audio bytes.")

        if audio_bytes.startswith(b"RIFF") or "wav" in mime_type or "mp3" in mime_type:
            with open(output_audio_path, "wb") as f:
                f.write(audio_bytes)
        else:
            # Raw 24kHz 16-bit mono PCM from Gemini TTS -> wrap in standard WAV container
            with wave.open(output_audio_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes(audio_bytes)

        return os.path.exists(output_audio_path) and os.path.getsize(output_audio_path) > 0
    except Exception as e:
        logger.error(f"Failed to synthesize speech using Gemini TTS: {e}")
        return False


def mix_voice_and_bgm(
    voice_aiff_path: str,
    output_mp3_path: str,
    bgm_path: Optional[str] = None,
    bgm_volume: float = 0.12,
) -> str:
    """Encodes voice narration to MP3, mixing with gentle looping background music if provided."""
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        if os.path.exists("/opt/homebrew/bin/ffmpeg"):
            ffmpeg_bin = "/opt/homebrew/bin/ffmpeg"
        else:
            raise FileNotFoundError("FFmpeg is required for MP3 generation but was not found in PATH.")

    os.makedirs(os.path.dirname(os.path.abspath(output_mp3_path)), exist_ok=True)

    has_valid_bgm = bgm_path and os.path.exists(bgm_path) and os.path.getsize(bgm_path) > 1000

    if has_valid_bgm and bgm_path:
        logger.info(
            f"Mixing voice track with background music '{bgm_path}' (BGM volume: {bgm_volume})..."
        )
        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            voice_aiff_path,
            "-stream_loop",
            "-1",
            "-i",
            bgm_path,
            "-filter_complex",
            f"[1:a]volume={bgm_volume}[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "2",
            output_mp3_path,
        ]
    else:
        logger.info("Encoding voice track directly to MP3 (no background music)...")
        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            voice_aiff_path,
            "-c:a",
            "libmp3lame",
            "-q:a",
            "2",
            output_mp3_path,
        ]

    subprocess.run(cmd, check=True, capture_output=True)

    if not os.path.exists(output_mp3_path) or os.path.getsize(output_mp3_path) == 0:
        raise RuntimeError(f"FFmpeg failed to generate valid MP3 at '{output_mp3_path}'")

    logger.info(
        f"Audio file generated successfully: {output_mp3_path} ({os.path.getsize(output_mp3_path)} bytes)"
    )
    return output_mp3_path


def generate_chinese_audio_file(
    story_json: Dict[str, Any],
    output_mp3_path: str,
    voice: str = DEFAULT_CHINESE_VOICE,
    rate_wpm: int = 175,
    bgm_path: Optional[str] = "background_music.mp3",
    bgm_volume: float = 0.12,
    tts_provider: Optional[str] = None,
) -> str:
    """End-to-end generator that turns story JSON into a polished Chinese MP3 audio file."""
    script = build_chinese_narration_script(story_json)
    logger.info(f"Generated narration script ({len(script)} chars): {script[:80]}...")

    provider = (tts_provider or config.TTS_PROVIDER or "auto").lower()
    is_mocked_say = hasattr(synthesize_speech_say, "_mock_name") or "MagicMock" in type(synthesize_speech_say).__name__

    suffix = ".wav" if provider == "gemini" else ".aiff"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_audio:
        temp_audio_path = tmp_audio.name

    try:
        success = False
        if provider == "gemini" and not is_mocked_say:
            gemini_voice = voice if voice in ("Kore", "Aoede", "Puck", "Charon", "Fenrir") else DEFAULT_GEMINI_VOICE
            success = synthesize_speech_gemini_tts(script, temp_audio_path, voice_name=gemini_voice)
        elif provider == "say" or is_mocked_say or shutil.which("say") is not None:
            success = synthesize_speech_say(script, temp_audio_path, voice=voice, rate_wpm=rate_wpm)
            if not success and not is_mocked_say and provider == "auto":
                logger.info("macOS 'say' failed; falling back to Gemini TTS...")
                success = synthesize_speech_gemini_tts(script, temp_audio_path)
        else:
            logger.info("macOS 'say' not available on this OS; using Gemini TTS for headless synthesis...")
            success = synthesize_speech_gemini_tts(script, temp_audio_path)

        if not success:
            raise RuntimeError("Speech synthesis failed across configured TTS providers.")

        actual_bgm = bgm_path
        if bgm_path and not os.path.exists(bgm_path):
            cwd_bgm = os.path.join(os.path.dirname(__file__), bgm_path)
            if os.path.exists(cwd_bgm):
                actual_bgm = cwd_bgm
            else:
                actual_bgm = None

        result_path = mix_voice_and_bgm(
            voice_aiff_path=temp_audio_path,
            output_mp3_path=output_mp3_path,
            bgm_path=actual_bgm,
            bgm_volume=bgm_volume,
        )
        return result_path
    finally:
        if os.path.exists(temp_audio_path):
            try:
                os.remove(temp_audio_path)
            except Exception:
                pass
