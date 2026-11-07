"""Google Vids Storyboard & Speaker-Notes Deck Exporter for Story-Agent.

Packages 20-page storybooks into 16:9 widescreen PowerPoint (.pptx) decks and
Google Slides presentations specifically engineered for **Google Vids (vids.google.com)**:

1. **16:9 AI-Avatar-Ready Stage Layout (1920x1080)**:
   - Places the square 1:1 storybook page (`page_01.jpg`..`page_20.jpg`) on the left-center
     of a 16:9 canvas over a soft watercolor-matched ambient backdrop.
   - Reserves the right third of the 16:9 frame as an uncluttered **AI Avatar / Presenter Zone**
     so Google Vids AI Avatars never cover Lily, Robo, or the page typography.
2. **Automatic Scene Script Population via Speaker Notes**:
   - Extracts and cleans each page's narration text from `story.json` (converting cover,
     colophon, STEM corner, and "The End" pages into natural spoken voiceover lines).
   - Embeds the spoken narration into each slide's **Speaker Notes** (`notesSlideN.xml`),
     which Google Vids automatically imports as each scene's **Script / Voiceover** prompt.
3. **Markdown & JSON Voiceover Script Sheets**:
   - Writes `vids_script_en.md` / `vids_script_zh.md` and `vids_scenes.json` alongside the
     `.pptx` deck for quick inspection or copy-paste into Google Vids.
"""

import argparse
import io
import json
import logging
import os
import re
import zipfile
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape as xml_escape

from PIL import Image, ImageDraw, ImageFilter

import config
import drive_sync

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("vids_exporter")

# 16:9 Widescreen dimensions in pixels (for stage composition) and EMUs (for OpenXML .pptx)
STAGE_WIDTH_PX = 1920
STAGE_HEIGHT_PX = 1080
SLIDE_WIDTH_EMU = 12192000   # 13.333 inches * 914400 EMU/inch
SLIDE_HEIGHT_EMU = 6858000   # 7.5 inches * 914400 EMU/inch

DEFAULT_BOOKS = [
    "Long Running Agents",
    "Year of the Horse",
    "Agent Sandbox",
    "Agent Memory Bank",
    "Agent Vibe Coding",
    "Agent Skills",
]


def clean_narration_line(raw_text: str) -> str:
    """Strips only markdown emphasis asterisks so Speaker Notes match the rendered storybook text verbatim."""
    text = raw_text.replace("\r\n", "\n").strip()
    text = re.sub(r"\*+([^*]+)\*+", r"\1", text)
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    return "\n".join(lines)


SUPPORTED_LANG_LABELS: Dict[str, str] = {
    "en": "English",
    "es": "Spanish",
    "zh": "Chinese Simplified",
    "yue": "Cantonese",
    "pt": "Portuguese",
    "ja": "Japanese",
    "ko": "Korean",
    "fr": "French",
    "it": "Italian",
    "de": "German",
}

SUPPORTED_XML_LANGS: Dict[str, str] = {
    "en": "en-US",
    "es": "es-ES",
    "zh": "zh-CN",
    "yue": "zh-HK",
    "pt": "pt-BR",
    "ja": "ja-JP",
    "ko": "ko-KR",
    "fr": "fr-FR",
    "it": "it-IT",
    "de": "de-DE",
}


def extract_page_voiceover(
    story_json: Dict[str, Any],
    page: Dict[str, Any],
    total_pages: int,
    lang: str = "en",
) -> str:
    """Returns warm presenter intros/outros for the first 2 and last 2 pages, and 100% verbatim text for story content (Pages 3-18)."""
    p_num = int(page.get("page_number", 1))
    is_heritage = (
        story_json.get("genre") == "chinese_heritage"
        or "Mei (" in str(story_json.get("main_character_description", ""))
        or "Proverb Tales" in str(story_json.get("series", ""))
    )
    default_title = "Mei, Ming and Baobao's Proverb Adventure" if is_heritage else "Lily and Robo's Adventure"
    title_en = story_json.get("title", default_title)
    loc_title = (
        title_en
        if lang == "en"
        else (story_json.get(f"title_{lang}") or title_en)
    )
    author = story_json.get("author", "Enrique Chan")

    if lang == "en":
        raw_text = page.get("text", "")
    else:
        raw_text = page.get(f"text_{lang}") or page.get("text", "")

    cleaned = clean_narration_line(raw_text)

    # Page 1: Cover -> warm spoken title introduction
    if p_num == 1:
        if is_heritage:
            heritage_intro_map = {
                "zh": (
                    f"欢迎来到《好奇小脑瓜·成语故事》。今天我们要跟随美美、弟弟明明和小红熊猫宝宝，"
                    f"一起走进温馨的中华成语故事《{loc_title}》，作者是 {author}。让我们开始吧！"
                ),
                "yue": (
                    f"歡迎嚟到《好奇小腦瓜·成語故事》！今日我哋會同美美、細佬明明同小紅熊貓寶寶一齊，"
                    f"走入溫馨嘅中華成語故事《{loc_title}》，作者係 {author}。我哋開始啦！"
                ),
                "es": (
                    f"Bienvenidos a Pequeñas Mentes Curiosas. Hoy acompañaremos a Mei, a su hermanito Ming y al pequeño panda rojo Baobao "
                    f"en un tierno cuento de proverbios chinos titulado «{loc_title}», escrito por {author}. ¡Comencemos!"
                ),
                "pt": (
                    f"Bem-vindos ao Pequenas Mentes Curiosas. Hoje vamos acompanhar a Mei, o seu irmãozinho Ming e o pequeno panda-vermelho Baobao "
                    f"numa encantadora história de provérbios chineses chamada «{loc_title}», escrita por {author}. Vamos começar!"
                ),
                "ja": (
                    f"『キュリアス・リトル・マインド』へようこそ。今日はメイと弟のミン、そしてレッサーパンダのバオバオと一緒に、"
                    f"{author} 作の心温まる中国の故事成語のお話『{loc_title}』を楽しみましょう。それでは、はじまりはじまり！"
                ),
                "ko": (
                    f"호기심 가득한 작은 마음에 오신 것을 환영합니다. 오늘은 메이와 동생 밍, 그리고 아기 레서판다 바오바오와 함께 "
                    f"{author} 작가님의 따뜻한 고사성어 이야기 『{loc_title}』 속으로 떠나 볼까요? 자, 시작해 봐요!"
                ),
                "fr": (
                    f"Bienvenue dans Petits Esprits Curieux. Aujourd’hui, nous suivons Mei, son petit frère Ming et le petit panda roux Baobao "
                    f"dans un tendre conte de proverbes chinois intitulé « {loc_title} », écrit par {author}. Commençons !"
                ),
                "it": (
                    f"Benvenuti a Piccole Menti Curiose. Oggi seguiremo Mei, il suo fratellino Ming e il piccolo panda rosso Baobao "
                    f"in una dolce storia di proverbi cinesi intitolata «{loc_title}», scritta da {author}. Iniziamo!"
                ),
                "de": (
                    f"Willkommen bei Neugierige Kleine Köpfe. Heute begleiten wir Mei, ihren kleinen Bruder Ming und den kleinen Roten Panda Baobao "
                    f"in einer herzerwärmenden Geschichte über chinesische Sprichwörter mit dem Titel „{loc_title}“, geschrieben von {author}. Fangen wir an!"
                ),
            }
            if lang in heritage_intro_map:
                return heritage_intro_map[lang]
            return (
                f"Welcome to Curious Little Minds. Today, we’re following Mei, her little brother Ming, and their red-panda friend Baobao "
                f"in a heartwarming Chinese proverb tale titled, '{title_en},' written by {author}. Let’s begin."
            )
        intro_map = {
            "zh": (
                f"欢迎来到《好奇小脑瓜》。今天我们要跟随莉莉和她的机器人好朋友乐乐，"
                f"一起走进温馨的故事《{loc_title}》，作者是 {author}。让我们开始吧！"
            ),
            "yue": (
                f"歡迎嚟到《好奇小腦瓜》！今日我哋會同莉莉同佢嘅機械人好朋友樂樂一齊，"
                f"走入溫馨嘅故事《{loc_title}》，作者係 {author}。我哋開始啦！"
            ),
            "es": (
                f"Bienvenidos a Pequeñas Mentes Curiosas. Hoy acompañaremos a Lily y a su amigo robot, Robo, "
                f"en un tierno cuento titulado «{loc_title}», escrito por {author}. ¡Comencemos!"
            ),
            "pt": (
                f"Bem-vindos ao Pequenas Mentes Curiosas. Hoje vamos acompanhar a Lily e o seu amigo robô, Robo, "
                f"numa história encantadora chamada «{loc_title}», escrita por {author}. Vamos começar!"
            ),
            "ja": (
                f"『キュリアス・リトル・マインド』へようこそ。今日はリリーとロボットの親友ロボと一緒に、"
                f"{author} 作の心温まるお話『{loc_title}』を楽しみましょう。それでは、はじまりはじまり！"
            ),
            "ko": (
                f"호기심 가득한 작은 마음에 오신 것을 환영합니다. 오늘은 릴리와 로봇 친구 로보와 함께 "
                f"{author} 작가님의 따뜻한 이야기 『{loc_title}』 속으로 떠나 볼까요? 자, 시작해 봐요!"
            ),
            "fr": (
                f"Bienvenue dans Petits Esprits Curieux. Aujourd’hui, nous suivons Lily et son ami robot, Robo, "
                f"dans une tendre histoire intitulée « {loc_title} », écrite par {author}. Commençons !"
            ),
            "it": (
                f"Benvenuti a Piccole Menti Curiose. Oggi seguiremo Lily e il suo amico robot, Robo, "
                f"in una dolce storia intitolata «{loc_title}», scritta da {author}. Iniziamo!"
            ),
            "de": (
                f"Willkommen bei Neugierige Kleine Köpfe. Heute begleiten wir Lily und ihren Roboterfreund Robo "
                f"in einer herzerwärmenden Geschichte mit dem Titel „{loc_title}“, geschrieben von {author}. Fangen wir an!"
            ),
        }
        if lang in intro_map:
            return intro_map[lang]
        return (
            f"Welcome to Curious Little Minds. Today, we’re following Lily and her robot friend, Robo, "
            f"in a heartwarming story titled, '{title_en},' written by {author}. Let’s begin."
        )

    # Page 2: Copyright / Colophon -> cozy world introduction instead of reading legal copyright notice
    if p_num == 2 and (
        "copyright" in cleaned.lower()
        or "©" in cleaned
        or "all rights reserved" in cleaned.lower()
        or "curious little minds" in cleaned.lower()
        or "derechos reservados" in cleaned.lower()
        or "direitos reservados" in cleaned.lower()
        or "droits réservés" in cleaned.lower()
        or "diritti riservati" in cleaned.lower()
        or "rechte vorbehalten" in cleaned.lower()
        or "版权所有" in cleaned
        or "版權所有" in cleaned
    ):
        if is_heritage:
            heritage_colophon_map = {
                "zh": "在这个充满水墨诗意的温馨世界里，流传千年的中华成语智慧化作温柔的睡前故事，陪伴着六岁的美美、五岁的明明和奇妙的小红熊猫宝宝。",
                "yue": "喺呢個充滿水墨詩意嘅溫馨世界入面，流傳咗幾千年嘅中華成語智慧，會變成溫柔嘅睡前故事，陪住六歲嘅美美、五歲嘅明明，同埋神奇嘅小紅熊貓寶寶。",
                "es": (
                    "En este mundo lleno de jardines de bambú y linternas brillantes, la sabiduría de los antiguos proverbios chinos "
                    "se transforma en tiernas aventuras para antes de dormir junto a Mei, Ming y el pequeño panda rojo Baobao."
                ),
                "pt": (
                    "Neste mundo acolhedor de jardins de bambu e lanternas brilhantes, a sabedoria dos antigos provérbios chineses "
                    "transforma-se em doces aventuras de ninar com a Mei, o Ming e o pequeno panda-vermelho Baobao."
                ),
                "ja": (
                    "竹林や提灯が輝くこのあたたかな世界では、古くから伝わる中国の故事成語の知恵が、"
                    "6歳のメイ、5歳のミン、そしてレッサーパンダのバオバオが繰り広げるやさしいおやすみ前の物語へと変わります。"
                ),
                "ko": (
                    "대나무 정원과 등불이 빛나는 이 포근한 세상에서는 옛 고사성어의 깊은 지혜가 "
                    "여섯 살 메이와 다섯 살 밍, 그리고 아기 레서판다 바오바오가 함께하는 다정한 잠자리 동화로 펼쳐집니다."
                ),
                "fr": (
                    "Dans cet univers poétique de jardins de bambous et de lanternes dorées, la sagesse des proverbes chinois anciens "
                    "se transforme en tendres aventures du soir aux côtés de Mei, Ming et du petit panda roux Baobao."
                ),
                "it": (
                    "In questo mondo poetico di giardini di bambù e lanterne dorate, la saggezza degli antichi proverbi cinesi "
                    "si trasforma in dolci avventure della buonanotte insieme a Mei, Ming e al piccolo panda rosso Baobao."
                ),
                "de": (
                    "In dieser poetischen Welt voller Bambusgärten und leuchtender Laternen verwandelt sich die Weisheit alter chinesischer Sprichwörter "
                    "in sanfte Gute-Nacht-Abenteuer mit Mei, Ming und dem kleinen Roten Panda Baobao."
                ),
            }
            if lang in heritage_colophon_map:
                return heritage_colophon_map[lang]
            return (
                "In this poetic world of bamboo courtyards and glowing lanterns, timeless Chinese proverb wisdom is transformed "
                "into gentle bedtime adventures featuring six-year-old Mei, five-year-old Ming, and their red-panda guide, Baobao."
            )
        colophon_map = {
            "zh": "在这个温馨奇妙的世界里，深奥的科技概念变成了温柔的睡前大冒险，陪伴着六岁的莉莉和她忠实的机器人伙伴乐乐。",
            "yue": "喺呢個溫馨又奇妙嘅世界入面，好深奧嘅科技概念都會變成溫柔嘅睡前大冒險，陪住六歲嘅莉莉同佢忠心嘅機械人拍檔樂樂。",
            "es": (
                "En este mundo tan acogedor, las grandes ideas de la tecnología se transforman en tiernas aventuras "
                "para antes de dormir, junto a Lily, de seis años, y su leal compañero robot, Robo."
            ),
            "pt": (
                "Neste mundo acolhedor, grandes ideias da tecnologia transformam-se em doces aventuras de ninar, "
                "com a pequena Lily, de seis anos, e o seu fiel companheiro robô, Robo."
            ),
            "ja": (
                "このあたたかな世界では、むずかしいテクノロジーの仕組みが、6歳のリリーと仲良しのロボット・ロボが繰り広げる"
                "やさしいおやすみ前の冒険物語へと変わります。"
            ),
            "ko": (
                "이 포근한 세상에서는 여섯 살 소녀 릴리와 든든한 로봇 단짝 로보가 함께하며, "
                "어려운 과학 기술 이야기가 다정한 잠자리 모험 동화로 펼쳐집니다."
            ),
            "fr": (
                "Dans cet univers tout doux, les grandes idées technologiques se transforment en tendres aventures "
                "du soir, aux côtés de Lily, six ans, et de son fidèle compagnon robot, Robo."
            ),
            "it": (
                "In questo mondo accogliente, le grandi idee della tecnologia si trasformano in dolci avventure "
                "della buonanotte, insieme alla piccola Lily di sei anni e al suo fedele amico robot, Robo."
            ),
            "de": (
                "In dieser gemütlichen Welt verwandeln sich große Technologie-Ideen in sanfte Gute-Nacht-Abenteuer "
                "mit der sechsjährigen Lily und ihrem treuen Roboterbegleiter Robo."
            ),
        }
        if lang in colophon_map:
            return colophon_map[lang]
        return (
            "In this cozy world, complex technology concepts are transformed into gentle bedtime adventures, "
            "featuring six-year-old Lily and her loyal robotic companion, Robo."
        )

    # Page 19: STEM Exploration Corner or Proverb Wisdom & Heritage Corner -> conversational prompt for young viewers
    if p_num == 19 and (
        "stem" in cleaned.lower()
        or "proverb" in cleaned.lower()
        or "heritage" in cleaned.lower()
        or "成语" in cleaned
        or "成語" in cleaned
        or "科学探索" in cleaned
        or "科學探索" in cleaned
        or "探求コーナー" in cleaned
        or "탐구 코너" in cleaned
        or "exploración" in cleaned.lower()
        or "exploração" in cleaned.lower()
        or "exploration" in cleaned.lower()
        or "esplorazione" in cleaned.lower()
        or "entdeckerecke" in cleaned.lower()
        or "1." in cleaned
    ):
        body_lines = [
            re.sub(r"^[1-3]\.\s*", "", ln.strip())
            for ln in cleaned.split("\n")
            if ln.strip()
            and not re.match(
                r"^(?:¡?rincón de exploración stem!?|stem exploration corner!?|proverb wisdom & heritage corner!?|"
                r"成语智慧小角落[！!]?|科学探索小角落[！!]?|成語智慧小角落[！!]?|科學探索小角落[！!]?|"
                r"cantinho de exploração stem!?|stem探求コーナー[！!]?|stem 탐구 코너[！!]?|"
                r"coin d[’']exploration stem\s*!?|angolo di esplorazione stem!?|mint-entdeckerecke!?)$",
                ln.strip(),
                flags=re.I,
            )
        ]
        joined_body = " ".join(body_lines)
        if is_heritage:
            proverb_prefix_map = {
                "zh": f"现在来到我们的成语智慧小角落！想一想：{joined_body}",
                "yue": f"而家嚟到我哋嘅成語智慧小角落！一齊諗吓：{joined_body}",
                "es": f"En nuestro rincón de sabiduría de los proverbios de hoy, pensemos juntos: {joined_body}",
                "pt": f"No nosso cantinho de sabedoria dos provérbios de hoje, vamos pensar juntos: {joined_body}",
                "ja": f"今日の故事成語知恵コーナーです。一緒に考えてみましょう！ {joined_body}",
                "ko": f"오늘의 고사성어 지혜 코너예요! 함께 생각해 볼까요? {joined_body}",
                "fr": f"Dans notre coin sagesse des proverbes d’aujourd’hui, réfléchissons ensemble : {joined_body}",
                "it": f"Nel nostro angolo della saggezza dei proverbi di oggi, pensiamo insieme: {joined_body}",
                "de": f"In unserer heutigen Sprichwort-Weisheitsecke überlegen wir gemeinsam: {joined_body}",
            }
            if lang in proverb_prefix_map:
                return proverb_prefix_map[lang]
            return f"In our Proverb Wisdom Corner today, let's think together: {joined_body}"
        stem_prefix_map = {
            "zh": f"现在来到我们的科学探索小角落！想一想：{joined_body}",
            "yue": f"而家嚟到我哋嘅科學探索小角落！一齊諗吓：{joined_body}",
            "es": f"En nuestro rincón STEM de hoy, pensemos juntos: {joined_body}",
            "pt": f"No nosso cantinho STEM de hoje, vamos pensar juntos: {joined_body}",
            "ja": f"今日のSTEM探求コーナーです。一緒に考えてみましょう！ {joined_body}",
            "ko": f"오늘의 STEM 탐구 코너예요! 함께 생각해 볼까요? {joined_body}",
            "fr": f"Dans notre coin STEM d’aujourd’hui, réfléchissons ensemble : {joined_body}",
            "it": f"Nel nostro angolo STEM di oggi, pensiamo insieme: {joined_body}",
            "de": f"In unserer heutigen MINT-Entdeckerecke überlegen wir gemeinsam: {joined_body}",
        }
        if lang in stem_prefix_map:
            return stem_prefix_map[lang]
        return f"In our STEM corner today, let's think together: {joined_body}"

    # Page 20: Back cover / The End -> warm bedtime sign-off
    if p_num == total_pages and (
        cleaned.lower() in (
            "the end",
            "el fin",
            "fin",
            "fim",
            "fine",
            "ende",
            "おしまい",
            "끝",
            "全书完",
            "完",
            "剧终",
            "终",
        )
        or len(cleaned) <= 12
    ):
        if is_heritage:
            heritage_outro_map = {
                "zh": (
                    f"感谢你陪伴美美、明明和小红熊猫宝宝一起读完《{loc_title}》。"
                    "晚安，小小智慧家，祝你做个甜美的好梦，我们下次成语故事再见！"
                ),
                "yue": (
                    f"多謝你陪美美、明明同小紅熊貓寶寶一齊睇完《{loc_title}》。"
                    "早唞啦，小小智慧家，祝你發個甜甜嘅好夢，我哋下次成語故事再見！"
                ),
                "es": (
                    f"Gracias por acompañar a Mei, Ming y Baobao en «{loc_title}». "
                    "Que duermas bien, pequeño explorador, y nos vemos en nuestra próxima aventura."
                ),
                "pt": (
                    f"Obrigado por acompanhar a Mei, o Ming e o Baobao em «{loc_title}». "
                    "Durma bem, pequeno explorador, e até à nossa próxima aventura!"
                ),
                "ja": (
                    f"メイとミン、そしてバオバオの『{loc_title}』を一緒に楽しんでくれてありがとう。"
                    "おやすみなさい、小さな探検家さん。また次の素敵なお話でお会いしましょう！"
                ),
                "ko": (
                    f"메이와 밍, 그리고 바오바오의 『{loc_title}』 이야기를 함께해 주어서 고마워요. "
                    "잘 자요, 꼬마 친구들! 다음 멋진 이야기에서 또 만나요!"
                ),
                "fr": (
                    f"Merci d’avoir suivi Mei, Ming et Baobao dans « {loc_title} ». "
                    "Dors bien, petit explorateur, et à très bientôt pour notre prochaine aventure !"
                ),
                "it": (
                    f"Grazie per aver seguito Mei, Ming e Baobao in «{loc_title}». "
                    "Sogni d’oro, piccolo esploratore, e arrivederci alla nostra prossima avventura!"
                ),
                "de": (
                    f"Danke, dass du Mei, Ming und Baobao in „{loc_title}“ begleitet hast. "
                    "Schlaf gut, kleiner Entdecker, und bis zu unserem nächsten Abenteuer!"
                ),
            }
            if lang in heritage_outro_map:
                return heritage_outro_map[lang]
            return (
                f"Thank you for joining Mei, Ming, and Baobao in '{title_en}.' "
                "Sleep well, little explorer, and we will see you on our next proverb adventure."
            )
        outro_map = {
            "zh": (
                f"感谢你陪伴莉莉和机器人乐乐一起读完《{loc_title}》。"
                "晚安，小小探险家，祝你做个甜美的好梦，我们下次大冒险再见！"
            ),
            "yue": (
                f"多謝你陪莉莉同機械人樂樂一齊睇完《{loc_title}》。"
                "早唞啦，小小探險家，祝你發個甜甜嘅好夢，我哋下次大冒險再見！"
            ),
            "es": (
                f"Gracias por acompañar a Lily y Robo en «{loc_title}». "
                "Que duermas bien, pequeño explorador, y nos vemos en nuestra próxima gran aventura."
            ),
            "pt": (
                f"Obrigado por acompanhar a Lily e o Robo em «{loc_title}». "
                "Durma bem, pequeno explorador, e até à nossa próxima grande aventura!"
            ),
            "ja": (
                f"リリーとロボの『{loc_title}』を一緒に楽しんでくれてありがとう。"
                "おやすみなさい、小さな探検家さん。また次の素敵な冒険でお会いしましょう！"
            ),
            "ko": (
                f"릴리와 로보의 『{loc_title}』 이야기를 함께해 주어서 고마워요. "
                "잘 자요, 꼬마 탐험가 친구들! 다음 멋진 모험에서 또 만나요!"
            ),
            "fr": (
                f"Merci d’avoir suivi Lily et Robo dans « {loc_title} ». "
                "Dors bien, petit explorateur, et à très bientôt pour notre prochaine grande aventure !"
            ),
            "it": (
                f"Grazie per aver seguito Lily e Robo in «{loc_title}». "
                "Sogni d’oro, piccolo esploratore, e arrivederci alla nostra prossima grande avventura!"
            ),
            "de": (
                f"Danke, dass du Lily und Robo in „{loc_title}“ begleitet hast. "
                "Schlaf gut, kleiner Entdecker, und bis zu unserem nächsten großen Abenteuer!"
            ),
        }
        if lang in outro_map:
            return outro_map[lang]
        return (
            f"Thank you for joining Lily and Robo in '{title_en}.' "
            "Sleep well, little explorer, and we will see you on our next grand adventure."
        )

    # Pages 3-18: Actual story content -> 100% verbatim storybook text
    return cleaned


def render_vids_stage_frame(
    page_img_path: Optional[str],
    page_number: int,
    book_title: str,
    page_img_bytes: Optional[bytes] = None,
) -> bytes:
    """Composes a 1920x1080 (16:9) Google Vids scene image from a 1:1 storybook page.

    - Left/center (`x=72, y=24, 1032x1032`): crisp square storybook page with subtle shadow.
    - Right side (`x=1144..1860`): clean, softly lit ambient stage reserved for the
      Google Vids AI Avatar so the avatar never overlaps the page illustration or text.
    """
    if page_img_bytes is not None:
        with Image.open(io.BytesIO(page_img_bytes)) as raw_im:
            src = raw_im.convert("RGB")
    elif page_img_path is not None:
        with Image.open(page_img_path) as raw_im:
            src = raw_im.convert("RGB")
    else:
        raise ValueError("Either page_img_path or page_img_bytes must be provided")

    # 1. Create soft ambient 1920x1080 background from the page's own palette
    bg = src.resize((STAGE_WIDTH_PX, STAGE_HEIGHT_PX), Image.Resampling.BICUBIC)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=48))

    # Warm cream wash over the blurred background so it stays bright and gentle
    cream_overlay = Image.new("RGB", (STAGE_WIDTH_PX, STAGE_HEIGHT_PX), (252, 248, 240))
    bg = Image.blend(bg, cream_overlay, alpha=0.42)

    # 2. Draw a soft translucent stage card on the right for the AI Avatar zone
    canvas = bg.convert("RGBA")
    overlay = Image.new("RGBA", (STAGE_WIDTH_PX, STAGE_HEIGHT_PX), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Subtle shadow behind the storybook page
    page_size = 1024
    page_x = 76
    page_y = (STAGE_HEIGHT_PX - page_size) // 2  # 28
    draw.rounded_rectangle(
        [page_x + 8, page_y + 10, page_x + page_size + 8, page_y + page_size + 10],
        radius=20,
        fill=(30, 25, 20, 45),
    )

    # White mat frame around the storybook page
    draw.rounded_rectangle(
        [page_x - 6, page_y - 6, page_x + page_size + 6, page_y + page_size + 6],
        radius=20,
        fill=(255, 255, 255, 235),
    )

    # Subtle soft pedestal glow on the right third where the AI Avatar stands
    avatar_x1, avatar_y1, avatar_x2, avatar_y2 = 1160, 56, 1856, 1024
    draw.rounded_rectangle(
        [avatar_x1, avatar_y1, avatar_x2, avatar_y2],
        radius=28,
        fill=(255, 255, 255, 88),
        outline=(255, 255, 255, 150),
        width=2,
    )

    canvas = Image.alpha_composite(canvas, overlay)

    # 3. Paste crisp square storybook page on the left
    page_resized = src.resize((page_size, page_size), Image.Resampling.LANCZOS)
    canvas.paste(page_resized, (page_x, page_y))

    out_rgb = canvas.convert("RGB")
    buf = io.BytesIO()
    out_rgb.save(buf, format="JPEG", quality=92, optimize=True)
    return buf.getvalue()


def build_pptx_with_speaker_notes(
    slides_data: List[Dict[str, Any]],
    output_pptx_path: str,
    deck_title: str,
    xml_lang: str = "en-US",
) -> str:
    """Builds a standards-compliant 16:9 OpenXML .pptx with embedded images and Speaker Notes.

    When imported into Google Slides / Google Vids (`vids.google.com`), each slide becomes a
    video scene and each slide's Speaker Notes automatically populate the scene's voiceover script.
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_pptx_path)), exist_ok=True)
    num_slides = len(slides_data)

    with zipfile.ZipFile(output_pptx_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 1. [Content_Types].xml
        ct_overrides = [
            '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
            '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
            '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
            '<Override PartName="/ppt/notesMasters/notesMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesMaster+xml"/>',
            '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
            '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>',
        ]
        for i in range(1, num_slides + 1):
            ct_overrides.append(
                f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            )
            ct_overrides.append(
                f'<Override PartName="/ppt/notesSlides/notesSlide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>'
            )

        content_types_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="jpg" ContentType="image/jpeg"/>'
            + "".join(ct_overrides)
            + "</Types>"
        )
        zf.writestr("[Content_Types].xml", content_types_xml)

        # 2. _rels/.rels
        root_rels_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
            "</Relationships>"
        )
        zf.writestr("_rels/.rels", root_rels_xml)

        # 3. docProps/core.xml & docProps/app.xml
        escaped_title = xml_escape(deck_title)
        core_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f"<dc:title>{escaped_title}</dc:title>"
            "<dc:creator>Enrique Chan</dc:creator>"
            "</cp:coreProperties>"
        )
        zf.writestr("docProps/core.xml", core_xml)

        app_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
            'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
            "<Application>Story-Agent Google Vids Exporter</Application>"
            f"<Slides>{num_slides}</Slides>"
            f"<Notes>{num_slides}</Notes>"
            "</Properties>"
        )
        zf.writestr("docProps/app.xml", app_xml)

        # 4. ppt/presentation.xml and ppt/_rels/presentation.xml.rels
        sld_id_entries = []
        pres_rels = [
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>',
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesMaster" Target="notesMasters/notesMaster1.xml"/>',
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="theme/theme1.xml"/>',
        ]
        for i in range(1, num_slides + 1):
            r_id = f"rId{i + 3}"
            sld_id = 255 + i
            sld_id_entries.append(f'<p:sldId id="{sld_id}" r:id="{r_id}"/>')
            pres_rels.append(
                f'<Relationship Id="{r_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>'
            )

        presentation_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
            '<p:notesMasterIdLst><p:notesMasterId r:id="rId2"/></p:notesMasterIdLst>'
            f'<p:sldIdLst>{"".join(sld_id_entries)}</p:sldIdLst>'
            f'<p:sldSz cx="{SLIDE_WIDTH_EMU}" cy="{SLIDE_HEIGHT_EMU}"/>'
            '<p:notesSz cx="6858000" cy="9144000"/>'
            "</p:presentation>"
        )
        zf.writestr("ppt/presentation.xml", presentation_xml)

        pres_rels_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(pres_rels)
            + "</Relationships>"
        )
        zf.writestr("ppt/_rels/presentation.xml.rels", pres_rels_xml)

        # 5. Minimal slideMaster1, slideLayout1, notesMaster1, theme1
        theme_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="StorybookTheme">'
            '<a:themeElements>'
            '<a:clrScheme name="Storybook">'
            '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
            '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
            '<a:dk2><a:srgbClr val="1F2937"/></a:dk2>'
            '<a:lt2><a:srgbClr val="FCF8F0"/></a:lt2>'
            '<a:accent1><a:srgbClr val="F59E0B"/></a:accent1>'
            '<a:accent2><a:srgbClr val="3B82F6"/></a:accent2>'
            '<a:accent3><a:srgbClr val="10B981"/></a:accent3>'
            '<a:accent4><a:srgbClr val="EC4899"/></a:accent4>'
            '<a:accent5><a:srgbClr val="8B5CF6"/></a:accent5>'
            '<a:accent6><a:srgbClr val="F97316"/></a:accent6>'
            '<a:hlink><a:srgbClr val="2563EB"/></a:hlink>'
            '<a:folHlink><a:srgbClr val="7C3AED"/></a:folHlink>'
            '</a:clrScheme>'
            '<a:fontScheme name="StorybookFonts">'
            '<a:majorFont><a:latin typeface="Arial"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
            '<a:minorFont><a:latin typeface="Arial"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont>'
            '</a:fontScheme>'
            '<a:fmtScheme name="StorybookFmt">'
            '<a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
            '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
            '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>'
            '<a:lnStyleLst><a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
            '<a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
            '<a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>'
            '<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle>'
            '<a:effectStyle><a:effectLst/></a:effectStyle>'
            '<a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>'
            '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
            '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
            '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>'
            '</a:fmtScheme>'
            '</a:themeElements>'
            '</a:theme>'
        )
        zf.writestr("ppt/theme/theme1.xml", theme_xml)

        slide_layout_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="blank" preserve="1">'
            '<p:cSld name="Blank"><p:spTree>'
            '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
            '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
            '</p:spTree></p:cSld>'
            '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>'
            '</p:sldLayout>'
        )
        zf.writestr("ppt/slideLayouts/slideLayout1.xml", slide_layout_xml)
        zf.writestr(
            "ppt/slideLayouts/_rels/slideLayout1.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>'
            "</Relationships>",
        )

        slide_master_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            '<p:cSld><p:spTree>'
            '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
            '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
            '</p:spTree></p:cSld>'
            '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
            'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
            '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
            '</p:sldMaster>'
        )
        zf.writestr("ppt/slideMasters/slideMaster1.xml", slide_master_xml)
        zf.writestr(
            "ppt/slideMasters/_rels/slideMaster1.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>'
            "</Relationships>",
        )

        notes_master_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<p:notesMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            '<p:cSld><p:spTree>'
            '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
            '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
            '</p:spTree></p:cSld>'
            '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
            'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
            '</p:notesMaster>'
        )
        zf.writestr("ppt/notesMasters/notesMaster1.xml", notes_master_xml)
        zf.writestr(
            "ppt/notesMasters/_rels/notesMaster1.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>'
            "</Relationships>",
        )

        # 6. Each slide + media image + notesSlide (Speaker Notes for Google Vids voiceover)
        for idx, item in enumerate(slides_data, 1):
            img_bytes: bytes = item["image_bytes"]
            script_text: str = item["script"]
            media_name = f"page_{idx:02d}_stage.jpg"
            zf.writestr(f"ppt/media/{media_name}", img_bytes)

            slide_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
                'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
                '<p:cSld><p:spTree>'
                '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
                '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
                '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
                '<p:pic>'
                '<p:nvPicPr>'
                f'<p:cNvPr id="2" name="Page {idx}"/>'
                '<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr>'
                '<p:nvPr/>'
                '</p:nvPicPr>'
                '<p:blipFill>'
                '<a:blip r:embed="rId2"/>'
                '<a:stretch><a:fillRect/></a:stretch>'
                '</p:blipFill>'
                '<p:spPr>'
                f'<a:xfrm><a:off x="0" y="0"/><a:ext cx="{SLIDE_WIDTH_EMU}" cy="{SLIDE_HEIGHT_EMU}"/></a:xfrm>'
                '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
                '</p:spPr>'
                '</p:pic>'
                '</p:spTree></p:cSld>'
                '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>'
                '</p:sld>'
            )
            zf.writestr(f"ppt/slides/slide{idx}.xml", slide_xml)

            slide_rels_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
                f'<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/{media_name}"/>'
                f'<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide{idx}.xml"/>'
                "</Relationships>"
            )
            zf.writestr(f"ppt/slides/_rels/slide{idx}.xml.rels", slide_rels_xml)

            note_lines = [ln for ln in script_text.split("\n") if ln] or [""]
            note_paragraphs_xml = "".join(
                f'<a:p><a:r><a:rPr lang="{xml_lang}" dirty="0"/><a:t>{xml_escape(ln)}</a:t></a:r></a:p>'
                for ln in note_lines
            )
            notes_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<p:notes xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
                'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
                '<p:cSld><p:spTree>'
                '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
                '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
                '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
                '<p:sp>'
                '<p:nvSpPr><p:cNvPr id="2" name="Slide Image Placeholder 1"/>'
                '<p:cNvSpPr><a:spLocks noGrp="1" noRot="1" noChangeAspect="1"/></p:cNvSpPr>'
                '<p:nvPr><p:ph type="sldImg"/></p:nvPr></p:nvSpPr>'
                '<p:spPr/>'
                '</p:sp>'
                '<p:sp>'
                '<p:nvSpPr><p:cNvPr id="3" name="Notes Placeholder 2"/>'
                '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
                '<p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr>'
                '<p:spPr/>'
                '<p:txBody><a:bodyPr/><a:lstStyle/>'
                f'{note_paragraphs_xml}'
                '</p:txBody>'
                '</p:sp>'
                '</p:spTree></p:cSld>'
                '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>'
                '</p:notes>'
            )
            zf.writestr(f"ppt/notesSlides/notesSlide{idx}.xml", notes_xml)

            notes_rels_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesMaster" Target="../notesMasters/notesMaster1.xml"/>'
                f'<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="../slides/slide{idx}.xml"/>'
                "</Relationships>"
            )
            zf.writestr(f"ppt/notesSlides/_rels/notesSlide{idx}.xml.rels", notes_rels_xml)

    return output_pptx_path


def _render_localized_page_bytes(
    book_dir: str,
    page: Dict[str, Any],
    total_pages: int,
    lang: str,
    generator: Any,
    raw_cache_bytes: Optional[bytes] = None,
) -> bytes:
    """Renders localized page typography onto cached raw illustration if available, or loads page_XX.jpg."""
    p_num = int(page.get("page_number", 1))
    raw_cache_path = os.path.join(book_dir, f".cache_page_{p_num:02d}_raw.jpg")
    page_img_path = os.path.join(book_dir, f"page_{p_num:02d}.jpg")

    lang_label = SUPPORTED_LANG_LABELS.get(lang, "English")

    if lang == "en":
        page_text = page.get("text", "")
    else:
        page_text = page.get(f"text_{lang}") or page.get("text", "")

    raw_bytes = raw_cache_bytes
    if raw_bytes is None and os.path.exists(raw_cache_path):
        with open(raw_cache_path, "rb") as rf:
            raw_bytes = rf.read()

    if raw_bytes is not None:
        rendered = generator.overlay_text_on_image(
            raw_bytes,
            page_text,
            p_num,
            total_pages,
            language=lang_label,
        )
        # Keep English page_XX.jpg on disk synced with clean asterisk-free typography
        if lang == "en":
            with open(page_img_path, "wb") as pf:
                pf.write(rendered)
        return rendered

    if not os.path.exists(page_img_path):
        raise FileNotFoundError(f"Missing page image: {page_img_path}")
    with open(page_img_path, "rb") as pf:
        return pf.read()


def export_book_for_google_vids(
    book_dir: str,
    languages: Optional[List[str]] = None,
    drive_service=None,
    drive_root_id: Optional[str] = None,
    auto_translate: bool = False,
    compile_localized_books: bool = True,
    drive_book_folder_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Exports a storybook directory into Google Vids 16:9 .pptx decks + extracted script sheets."""
    from concurrent.futures import ThreadPoolExecutor

    from storybook import StorybookGenerator

    story_path = os.path.join(book_dir, "story.json")
    if not os.path.exists(story_path):
        raise FileNotFoundError(f"story.json not found in {book_dir}")

    with open(story_path, "r", encoding="utf-8") as f:
        story_json = json.load(f)

    generator = StorybookGenerator(drive_service=drive_service)

    if auto_translate:
        target_langs = [
            lang_code
            for lang_code in (languages or ["en", "es", "zh"])
            if lang_code != "en" and lang_code in SUPPORTED_LANG_LABELS
        ]
        story_json = generator.ensure_multilingual_translations(
            story_json, local_dir=book_dir, target_langs=target_langs
        )

    title = story_json.get("title", os.path.basename(book_dir))
    pages = story_json.get("pages", [])
    total_pages = len(pages)

    vids_dir = os.path.join(book_dir, "google_vids")
    os.makedirs(vids_dir, exist_ok=True)

    # Determine available languages in story.json if not explicitly specified
    if not languages:
        languages = ["en"]
        for cand_lang in ("es", "zh", "yue", "pt", "ja", "ko", "fr", "it", "de"):
            if any(p.get(f"text_{cand_lang}") for p in pages):
                languages.append(cand_lang)

    # Preload raw illustrations once across all target languages to avoid 10x disk reads per page
    raw_page_cache: Dict[int, bytes] = {}
    for page in pages:
        p_num = int(page.get("page_number", 1))
        raw_cache_path = os.path.join(book_dir, f".cache_page_{p_num:02d}_raw.jpg")
        if os.path.exists(raw_cache_path):
            with open(raw_cache_path, "rb") as rf:
                raw_page_cache[p_num] = rf.read()

    safe_title = re.sub(r"[^A-Za-z0-9 _-]+", "", title).strip()
    exported_decks: Dict[str, str] = {}
    exported_scripts: Dict[str, str] = {}
    localized_pdfs: Dict[str, str] = {}
    localized_epubs: Dict[str, str] = {}
    drive_slides_urls: Dict[str, str] = {}

    def _build_single_lang(lang: str) -> Dict[str, Optional[str]]:
        lang_upper = lang.upper()
        slides_data = []
        localized_pil_images: List[Image.Image] = []
        localized_pages_data: List[tuple[str, bytes]] = []

        md_lines = [
            f"# Google Vids AI Avatar & Voiceover Script — {title} ({lang_upper})",
            "",
            "> **How to use in Google Vids (`https://vids.google.com`)**:",
            f"> 1. Upload `{safe_title}_Google_Vids_{lang_upper}.pptx` to Google Drive and open as **Google Slides** (or run `vids_exporter.py --sync-drive`).",
            "> 2. In **Google Vids**, click **File → Import slides** and select the presentation.",
            "> 3. Every slide's **Speaker Notes** will automatically populate that scene's **Script / Voiceover** tab!",
            "> 4. Click **Scripts / AI Avatar** in the right sidebar, select your AI Avatar, and position the avatar in the reserved right-hand stage zone.",
            "",
            "---",
            "",
        ]

        for page in pages:
            p_num = int(page.get("page_number", 1))
            localized_page_bytes = _render_localized_page_bytes(
                book_dir=book_dir,
                page=page,
                total_pages=total_pages,
                lang=lang,
                generator=generator,
                raw_cache_bytes=raw_page_cache.get(p_num),
            )
            if compile_localized_books and lang != "en":
                with Image.open(io.BytesIO(localized_page_bytes)) as src_im:
                    localized_pil_images.append(src_im.convert("RGB"))
                localized_pages_data.append((f"page_{p_num:02d}.jpg", localized_page_bytes))

            stage_frame_bytes = render_vids_stage_frame(
                page_img_path=None,
                page_number=p_num,
                book_title=title,
                page_img_bytes=localized_page_bytes,
            )
            script_line = extract_page_voiceover(story_json, page, total_pages, lang=lang)
            slides_data.append(
                {
                    "page_number": p_num,
                    "image_bytes": stage_frame_bytes,
                    "script": script_line,
                }
            )
            md_lines.append(f"### Scene {p_num:02d} (Page {p_num})")
            md_lines.append(f"```text\n{script_line}\n```")
            md_lines.append("")

        pptx_filename = f"{safe_title}_Google_Vids_{lang_upper}.pptx"
        pptx_path = os.path.join(vids_dir, pptx_filename)
        build_pptx_with_speaker_notes(
            slides_data=slides_data,
            output_pptx_path=pptx_path,
            deck_title=f"{title} ({lang_upper}) - Google Vids Storyboard",
            xml_lang=SUPPORTED_XML_LANGS.get(lang, "en-US"),
        )

        md_filename = f"{safe_title}_Google_Vids_Script_{lang_upper}.md"
        md_path = os.path.join(vids_dir, md_filename)
        with open(md_path, "w", encoding="utf-8") as mf:
            mf.write("\n".join(md_lines))

        logger.info(f"[{title} | {lang_upper}] Built 16:9 Google Vids deck: {pptx_path}")

        pdf_out: Optional[str] = None
        epub_out: Optional[str] = None
        if compile_localized_books and lang != "en" and localized_pil_images:
            loc_title = story_json.get(f"title_{lang}") or title
            pdf_out = os.path.join(book_dir, f"{safe_title}_{lang_upper}.pdf")
            pdf_bytes = generator.compile_pdf_bytes(localized_pil_images, dpi=config.PDF_DPI)
            with open(pdf_out, "wb") as pf:
                pf.write(pdf_bytes)

            epub_out = os.path.join(book_dir, f"{safe_title}_{lang_upper} (eBook).epub")
            epub_bytes = generator.create_epub(
                loc_title,
                localized_pages_data,
                language=SUPPORTED_LANG_LABELS.get(lang, "English"),
            )
            with open(epub_out, "wb") as ef:
                ef.write(epub_bytes)
            logger.info(f"[{title} | {lang_upper}] Compiled localized PDF & EPUB: {pdf_out}, {epub_out}")

        return {"lang": lang, "pptx": pptx_path, "md": md_path, "pdf": pdf_out, "epub": epub_out}

    max_workers = max(1, min(len(languages), 4))
    if max_workers == 1:
        lang_results = [_build_single_lang(languages[0])] if languages else []
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            lang_results = list(pool.map(_build_single_lang, languages))

    book_folder_id: Optional[str] = None
    vids_sub_id: Optional[str] = None
    book_index: Optional[Dict[str, Dict[str, Any]]] = None
    vids_index: Optional[Dict[str, Dict[str, Any]]] = None
    if drive_service and (drive_book_folder_id or drive_root_id):
        book_folder_id = drive_book_folder_id
        if not book_folder_id and drive_root_id:
            book_folder_name = os.path.basename(os.path.normpath(book_dir))
            book_folder_id = drive_sync.get_or_create_folder(
                drive_service, book_folder_name, parent_id=drive_root_id
            )
        if book_folder_id:
            vids_sub_id = drive_sync.get_or_create_folder(
                drive_service, "google_vids", parent_id=book_folder_id
            )
            book_index = drive_sync.list_folder_index(drive_service, book_folder_id)
            if vids_sub_id:
                vids_index = drive_sync.list_folder_index(drive_service, vids_sub_id)

    for item in lang_results:
        lang = item["lang"] or "en"
        lang_upper = lang.upper()
        pptx_path = item["pptx"] or ""
        md_path = item["md"] or ""
        exported_decks[lang] = pptx_path
        exported_scripts[lang] = md_path
        if item["pdf"]:
            localized_pdfs[lang] = item["pdf"]
        if item["epub"]:
            localized_epubs[lang] = item["epub"]

        if drive_service and book_folder_id:
            if lang in localized_pdfs and os.path.exists(localized_pdfs[lang]):
                drive_sync.upsert_local_file(
                    drive_service, localized_pdfs[lang], book_folder_id, folder_index=book_index
                )
            if lang in localized_epubs and os.path.exists(localized_epubs[lang]):
                drive_sync.upsert_local_file(
                    drive_service, localized_epubs[lang], book_folder_id, folder_index=book_index
                )

            with open(pptx_path, "rb") as pf:
                pptx_bytes = pf.read()
            slides_name = f"{title} ({lang_upper}) — Google Vids Import Deck"
            file_id = drive_sync.upsert_bytes_to_drive(
                drive_service=drive_service,
                data_bytes=pptx_bytes,
                filename=slides_name,
                folder_id=book_folder_id,
                mimetype=drive_sync.PPTX_MIME,
                target_mime_type=drive_sync.SLIDES_MIME,
                folder_index=book_index,
            )
            slides_url = f"https://docs.google.com/presentation/d/{file_id}/edit"
            drive_slides_urls[lang] = slides_url
            logger.info(f"[{title} | {lang_upper}] Converted to Google Slides: {slides_url}")

            if vids_sub_id:
                drive_sync.upsert_local_file(drive_service, pptx_path, vids_sub_id, folder_index=vids_index)
                drive_sync.upsert_local_file(drive_service, md_path, vids_sub_id, folder_index=vids_index)

    return {
        "title": title,
        "title_es": story_json.get("title_es"),
        "title_zh": story_json.get("title_zh"),
        "title_yue": story_json.get("title_yue"),
        "title_pt": story_json.get("title_pt"),
        "title_ja": story_json.get("title_ja"),
        "title_ko": story_json.get("title_ko"),
        "title_fr": story_json.get("title_fr"),
        "title_it": story_json.get("title_it"),
        "title_de": story_json.get("title_de"),
        "book_dir": book_dir,
        "vids_dir": vids_dir,
        "decks": exported_decks,
        "scripts": exported_scripts,
        "localized_pdfs": localized_pdfs,
        "localized_epubs": localized_epubs,
        "drive_slides_urls": drive_slides_urls,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export Story-Agent books into 16:9 Google Vids decks with Speaker Notes voiceover scripts."
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Base output directory containing storybook folders (default: output).",
    )
    parser.add_argument(
        "--books",
        nargs="*",
        default=DEFAULT_BOOKS,
        help="Book folder names to export (default: Book 2 + all 4 Wave 1 books).",
    )
    parser.add_argument(
        "--languages",
        nargs="*",
        default=["en", "es", "zh"],
        help="Language codes to export (default: en es zh).",
    )
    parser.add_argument(
        "--auto-translate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Automatically translate missing Spanish/Chinese page texts using Gemini (default: True).",
    )
    parser.add_argument(
        "--sync-drive",
        action="store_true",
        help="Upload and convert .pptx decks into native Google Slides in Google Drive.",
    )
    args = parser.parse_args(argv)

    drive_service = None
    drive_root_id = None
    if args.sync_drive:
        drive_service = drive_sync.init_drive_service(no_drive=False)
        if drive_service:
            drive_root_id = drive_sync.get_or_create_folder(
                drive_service, config.DRIVE_ROOT_FOLDER_NAME
            )

    summary = []
    for book_folder in args.books:
        book_dir = os.path.join(args.output_dir, book_folder)
        if not os.path.isdir(book_dir):
            logger.warning(f"Skipping missing book folder: {book_dir}")
            continue
        res = export_book_for_google_vids(
            book_dir=book_dir,
            languages=args.languages,
            drive_service=drive_service,
            drive_root_id=drive_root_id,
            auto_translate=args.auto_translate,
            compile_localized_books=True,
        )
        summary.append(res)

    manifest_path = os.path.join(args.output_dir, "google_vids_manifest.json")
    existing: List[Dict[str, Any]] = []
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            existing = loaded if isinstance(loaded, list) else []
        except Exception as e:
            logger.warning(f"Could not read existing {manifest_path}; starting fresh: {e}")
    by_dir = {e.get("book_dir"): e for e in existing if isinstance(e, dict)}
    for res in summary:
        prev = by_dir.get(res.get("book_dir"), {})
        merged = {**prev, **res}  # keep fields only set elsewhere (e.g. google_vids_urls)
        if prev.get("drive_slides_urls") and not res.get("drive_slides_urls"):
            merged["drive_slides_urls"] = prev["drive_slides_urls"]
        by_dir[res.get("book_dir")] = merged
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(list(by_dir.values()), f, ensure_ascii=False, indent=2)
    logger.info(f"Merged Google Vids export manifest into {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
