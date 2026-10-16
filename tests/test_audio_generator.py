"""Unit tests for Chinese Audio Generator module."""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import audio_generator


class TestAudioGenerator(unittest.TestCase):

    def test_is_chinese_text(self):
        self.assertTrue(audio_generator.is_chinese_text("你好世界"))
        self.assertTrue(audio_generator.is_chinese_text("Lily and Robo 玩耍"))
        self.assertFalse(audio_generator.is_chinese_text("Hello World"))
        self.assertFalse(audio_generator.is_chinese_text("12345!@#$"))

    def test_build_chinese_narration_script_structure(self):
        story = {
            "title": "智能体沙盒大冒险",
            "pages": [
                {"page_number": 1, "text": "智能体沙盒大冒险\\nWritten by Enrique Chan"},
                {"page_number": 2, "text": "莉莉和机器人乐乐走进了沙盒。"},
                {"page_number": 3, "text": "他们在数字沙滩上堆城堡。"},
                {"page_number": 4, "text": "终"}
            ]
        }
        script = audio_generator.build_chinese_narration_script(story)

        # Check series sonic branding, title & author credit
        self.assertIn("欢迎收听《好奇小脑瓜·莉莉与乐乐大冒险》睡前故事", script)
        self.assertIn("《智能体沙盒大冒险》", script)
        self.assertIn("著者：Enrique Chan", script)

        # Check story content
        self.assertIn("莉莉和机器人乐乐走进了沙盒。", script)
        self.assertIn("他们在数字沙滩上堆城堡。", script)

        # Check pauses
        self.assertIn("[[slnc 900]]", script)
        self.assertIn("[[slnc 600]]", script)

        # Check bedtime closing
        self.assertIn("这是《好奇小脑瓜》陪伴你的又一个奇妙夜晚", script)
        self.assertIn("祝你做个甜美的好梦，晚安，明天见！", script)

        # Check that page 1 title/author duplicate and final page '终' are not repeated in body
        lines = script.split("\n")
        self.assertNotIn("Written by Enrique Chan", lines)
        self.assertNotIn("终", lines)

    def test_build_chinese_narration_script_with_title_zh(self):
        story = {
            "title": "Agent Sandbox (English Title)",
            "title_zh": "沙盒小助手",
            "pages": [
                {"page_number": 1, "text": "Page 1 text", "text_zh": "第一页故事。"}
            ]
        }
        script = audio_generator.build_chinese_narration_script(story)
        self.assertIn("《沙盒小助手》", script)
        self.assertIn("第一页故事。", script)

    @patch("subprocess.run")
    def test_synthesize_speech_say_success(self, mock_run):
        mock_run.return_value = MagicMock(stdout="Tingting zh_CN", returncode=0)
        
        with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as tmp:
            tmp.write(b"fake audio bytes")
            tmp_path = tmp.name

        try:
            success = audio_generator.synthesize_speech_say("你好", tmp_path, voice="Tingting", rate_wpm=175)
            self.assertTrue(success)
            self.assertTrue(mock_run.called)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_mix_voice_and_bgm(self, mock_run, mock_which):
        mock_which.return_value = "/mock/ffmpeg"
        mock_run.return_value = MagicMock(returncode=0)

        with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as v_tmp:
            v_tmp.write(b"voice bytes")
            voice_path = v_tmp.name

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as b_tmp:
            b_tmp.write(b"bgm bytes" * 200)
            bgm_path = b_tmp.name

        output_mp3 = os.path.join(tempfile.gettempdir(), "test_mix_output.mp3")

        try:
            # Simulate output creation by ffmpeg
            def side_effect(*args, **kwargs):
                with open(output_mp3, "wb") as f:
                    f.write(b"mp3 content")
                return MagicMock(returncode=0)

            mock_run.side_effect = side_effect

            result = audio_generator.mix_voice_and_bgm(
                voice_aiff_path=voice_path,
                output_mp3_path=output_mp3,
                bgm_path=bgm_path,
                bgm_volume=0.12
            )
            self.assertEqual(result, output_mp3)
            self.assertTrue(os.path.exists(output_mp3))
        finally:
            for p in [voice_path, bgm_path, output_mp3]:
                if os.path.exists(p):
                    os.remove(p)

    @patch("audio_generator.synthesize_speech_say")
    @patch("audio_generator.mix_voice_and_bgm")
    def test_generate_chinese_audio_file(self, mock_mix, mock_synth):
        mock_synth.return_value = True
        mock_mix.return_value = "/output/test.mp3"

        story = {
            "title": "测试故事",
            "pages": [
                {"page_number": 1, "text": "测试故事\\n作者：Enrique Chan"},
                {"page_number": 2, "text": "莉莉开始微笑。"}
            ]
        }
        res = audio_generator.generate_chinese_audio_file(story, "/output/test.mp3")
        self.assertEqual(res, "/output/test.mp3")
        self.assertTrue(mock_synth.called)
        self.assertTrue(mock_mix.called)

if __name__ == '__main__':
    unittest.main()
