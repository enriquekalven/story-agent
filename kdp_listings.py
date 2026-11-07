"""Build Amazon KDP listing files (kdp/listings/*.json) for every ready book.

The listing template mirrors Book 1 ("Lily & Robo: Day & Night Adventures",
ASIN B0H55BJ36X) exactly as it is configured in the KDP Bookshelf
(see kdp/book1_reference.json): English, author Enrique Chan, reading age 3-7,
Amazon.com primary marketplace, 3 categories, up to 7 keywords, DRM on,
AI-content declaration, KDP Select, worldwide rights, 35% royalty, $0.99.

Usage:
    python kdp_listings.py prepare            # (re)build listing JSONs, generate missing descriptions
    python kdp_listings.py show <slug>        # print one listing
"""

from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from PIL import Image

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("kdp_listings")

REPO_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = REPO_ROOT / "output"
KDP_DIR = REPO_ROOT / "kdp"
LISTINGS_DIR = KDP_DIR / "listings"
COVERS_DIR = KDP_DIR / "covers"
KDP_EPUBS_DIR = KDP_DIR / "epubs"  # compressed EPUBs (<= 3 MB after conversion → $0.99 allowed, like Book 1)
REFERENCE = KDP_DIR / "book1_reference.json"

AUTHOR = ("Enrique", "Chan")
STEM_SERIES = "Little Agents at Work"
HERITAGE_SERIES = "Mei, Ming & Baobao's Chinese Proverb Tales"
STEM_TAGLINE = "A Magical Bedtime Story for Kids"
HERITAGE_TAGLINE = "A Chinese Proverb Bedtime Story for Kids"

STEM_CATEGORIES = [
    ["Children's eBooks", "Ages 3-5", "Basic Concepts", "General"],
    ["Children's eBooks", "Computers & Technology", "Software"],
    ["Children's eBooks", "Computers & Technology", "Programming"],
]
# Verified live in the KDP category picker before publishing the first heritage title.
HERITAGE_CATEGORIES = [
    ["Children's eBooks", "Fairy Tales, Folk Tales & Myths", "Asian"],
    ["Children's eBooks", "Geography & Cultures", "Explore the World", "Asia"],
    ["Children's eBooks", "Ages 3-5", "Basic Concepts", "General"],
]

# (slug, output folder, KDP title, hook subtitle, series, series number, proverb hanzi / pinyin, extra keywords)
BOOK_PLAN: list[dict[str, Any]] = [
    # KDP rule (and how Book 1 is set up): the Book Title must match the title printed on the cover.
    # Covers print story.json["title"]; titles containing ": " are split so KDP re-joins them as
    # "Title: Subtitle…". Character names live in the subtitle and the series metadata.
    # ---- Series 1: Little Agents at Work (Lily & Robo) — Book 1 is already live ----
    dict(slug="lily-robo-02-lantern-parade-horse", folder="Year of the Horse",
         title="The Lantern-Parade Horse",
         subtitle="A Lily & Robo Lunar New Year · Year of the Horse Adventure 🏮 | A Magical Bedtime Story for Kids",
         series=STEM_SERIES, number=2,
         kind="stem", extra_keywords=["Lunar New Year picture book", "Year of the Horse kids book"]),
    dict(slug="lily-robo-03-magic-sandcastle-playground", folder="Agent Sandbox",
         title="The Magic Sandcastle Playground",
         subtitle="Lily & Robo Discover Where Big Ideas Practice Safely ✨ | A Magical Bedtime Story for Kids",
         series=STEM_SERIES, number=3,
         kind="stem", extra_keywords=["agent sandbox", "safe practice space for kids"]),
    dict(slug="lily-robo-04-glowing-star-jars", folder="Agent Memory Bank",
         title="The Glowing Star Jars",
         subtitle="Lily & Robo's Cozy Treehouse Library of Forever Memories 🌟 | A Magical Bedtime Story for Kids",
         series=STEM_SERIES, number=4,
         kind="stem", extra_keywords=["agent memory", "memory bedtime story"]),
    dict(slug="lily-robo-05-magic-paintbrush-of-wishes", folder="Agent Vibe Coding",
         title="The Magic Paintbrush of Wishes",
         subtitle="Lily & Robo Paint Cozy Dreams to Life with Vibe Coding 🎨 | A Magical Bedtime Story for Kids",
         series=STEM_SERIES, number=5,
         kind="stem", extra_keywords=["vibe coding", "creative coding for kids"]),
    dict(slug="lily-robo-06-superhero-toolbelt", folder="Agent Skills",
         title="The Superhero Toolbelt",
         subtitle="Lily & Robo's Snap-On Star Badges for Every Helping Mission ⭐ | A Magical Bedtime Story for Kids",
         series=STEM_SERIES, number=6,
         kind="stem", extra_keywords=["agent skills", "helping others picture book"]),
    dict(slug="lily-robo-07-gentle-tap-dance", folder="Agent Computer Use",
         title="Lily & Robo and the Gentle Tap Dance",
         subtitle="How AI Agents Learn Computer Use with Careful Little Steps 🖱️ | A Magical Bedtime Story for Kids",
         series=STEM_SERIES, number=7,
         kind="stem", extra_keywords=["computer use AI agent", "digital skills for kids"]),
    dict(slug="lily-robo-08-happy-helper-diary", folder="Agent Observability",
         title="Robo's Happy Helper Diary",
         subtitle="Lily & Robo Learn Agent Observability with Glowing Footprint Logs 📓 | A Magical Bedtime Story for Kids",
         series=STEM_SERIES, number=8,
         kind="stem", extra_keywords=["agent observability", "keeping a journal picture book"]),
    dict(slug="lily-robo-09-golden-star-day", folder="Agent Evaluation",
         title="Lily & Robo's Golden Star Day!",
         subtitle="How AI Agents Practice, Check Their Work & Earn Gold Stars ⭐ | A Magical Bedtime Story for Kids",
         series=STEM_SERIES, number=9,
         kind="stem", extra_keywords=["agent evaluation", "growth mindset kids book"]),
    dict(slug="lily-robo-10-walkie-talkie-treehouse-club", folder="Agent To Agent",
         title="The Walkie-Talkie Treehouse Club",
         subtitle="Lily & Robo's Picnic Plan · Multi-Agent A2A Teamwork in Action 📻 | A Magical Bedtime Story for Kids",
         series=STEM_SERIES, number=10,
         kind="stem", extra_keywords=["multi-agent teamwork", "agent to agent A2A"]),
    dict(slug="lily-robo-11-rainbow-keyring-adventure", folder="Agent MCP Connectors",
         title="Robo's Rainbow Keyring Adventure",
         subtitle="Lily & Robo Unlock Helpful Tools with Model Context Protocol (MCP) 🌈 | A Magical Bedtime Story for Kids",
         series=STEM_SERIES, number=11,
         kind="stem", extra_keywords=["Model Context Protocol MCP", "AI tools for kids"]),
    dict(slug="lily-robo-12-green-light-high-five", folder="Agent Human In The Loop",
         title="The Green-Light High-Five",
         subtitle="Lily & Robo's Safe Adventures · Human-in-the-Loop AI Safety Guardrails 🖐️ | A Magical Bedtime Story for Kids",
         series=STEM_SERIES, number=12,
         kind="stem", extra_keywords=["human in the loop AI", "AI safety for kids"]),
    dict(slug="lily-robo-13-golden-fact-book", folder="Agent Grounding",
         title="Lily & Robo and the Golden Fact Book",
         subtitle="How AI Agents Use Grounding & Search to Find Real Truths 🔍 | A Magical Bedtime Story for Kids",
         series=STEM_SERIES, number=13,
         kind="stem", extra_keywords=["AI grounding and search", "fact checking for kids"]),
    dict(slug="lily-robo-14-paper-airplane-breeze-test", folder="Agent Evolution",
         title="The Paper Airplane Breeze Test",
         subtitle="Lily & Robo Discover Self-Improving Agents & Evolutionary Design ✈️ | A Magical Bedtime Story for Kids",
         series=STEM_SERIES, number=14,
         kind="stem", extra_keywords=["self-improving AI agents", "paper airplane STEM book"]),
    # ---- Series 2: Mei, Ming & Baobao's Chinese Proverb Tales (new series) ----
    dict(slug="mmb-01-iron-rod-and-needle", folder="Iron Rod Into A Needle",
         title="The Iron Rod and the Needle",
         subtitle="Mei, Ming & Baobao Learn 铁杵磨成针 · Big Dreams Grow One Patient Step at a Time 🪡 | A Chinese Proverb Bedtime Story for Kids",
         series=HERITAGE_SERIES, number=1,
         kind="heritage", proverb="铁杵磨成针 (Tiě Chǔ Mó Chéng Zhēn)",
         extra_keywords=["perseverance picture book", "Li Bai story for kids"]),
    dict(slug="mmb-02-kong-rong-shares-the-biggest-pear", folder="Kong Rong Shares The Pears",
         title="Kong Rong Shares the Biggest Pear",
         subtitle="Mei, Ming & Baobao Learn 孔融让梨 · The Sweetest Fruit Is the One We Share 🍐 | A Chinese Proverb Bedtime Story for Kids",
         series=HERITAGE_SERIES, number=2,
         kind="heritage", proverb="孔融让梨 (Kǒng Róng Ràng Lí)",
         extra_keywords=["sharing picture book", "kindness bedtime story"]),
    dict(slug="mmb-03-sima-guang-water-vat", folder="Sima Guang Smashes The Water Vat",
         title="The Clever Solution of the Water Vat",
         subtitle="Sima Guang's Wisdom · Mei, Ming & Baobao Learn 司马光砸缸 🏺 | A Chinese Proverb Bedtime Story for Kids",
         series=HERITAGE_SERIES, number=3,
         kind="heritage", proverb="司马光砸缸 (Sīmǎ Guāng Zá Gāng)",
         extra_keywords=["problem solving picture book", "staying calm kids book"]),
    dict(slug="mmb-04-little-frog-bottom-of-the-well", folder="The Frog In The Well",
         title="The Little Frog at the Bottom of the Well",
         subtitle="Mei, Ming & Baobao Learn 井底之蛙 · See How Big the Sky Really Is 🐸 | A Chinese Proverb Bedtime Story for Kids",
         series=HERITAGE_SERIES, number=4,
         kind="heritage", proverb="井底之蛙 (Jǐng Dǐ Zhī Wā)",
         extra_keywords=["curiosity picture book", "open mind kids story"]),
    dict(slug="mmb-05-painting-the-dragons-eyes", folder="Painting The Dragons Eyes",
         title="Painting the Dragon's Eyes",
         subtitle="Mei, Ming & Baobao Learn 画龙点睛 · Two Focused Brushstrokes Bring Magic to Life 🐉 | A Chinese Proverb Bedtime Story for Kids",
         series=HERITAGE_SERIES, number=5,
         kind="heritage", proverb="画龙点睛 (Huà Lóng Diǎn Jīng)",
         extra_keywords=["dragon picture book", "finishing touch kids story"]),
    dict(slug="mmb-06-waiting-by-the-tree-stump", folder="Waiting By The Tree Stump",
         title="Waiting by the Tree Stump for a Rabbit",
         subtitle="Mei, Ming & Baobao Learn 守株待兔 · Joyful Effort Beats Waiting for Luck 🐰 | A Chinese Proverb Bedtime Story for Kids",
         series=HERITAGE_SERIES, number=6,
         kind="heritage", proverb="守株待兔 (Shǒu Zhū Dài Tù)",
         extra_keywords=["hard work picture book", "rabbit bedtime story"]),
    dict(slug="mmb-07-pulling-up-sprouts", folder="Pulling Up Sprouts",
         title="Pulling Up Sprouts to Help Them Grow",
         subtitle="Mei, Ming & Baobao Learn 拔苗助长 · Gentle Patience Helps Little Sprouts Grow 🌱 | A Chinese Proverb Bedtime Story for Kids",
         series=HERITAGE_SERIES, number=7,
         kind="heritage", proverb="拔苗助长 (Bá Miáo Zhù Zhǎng)",
         extra_keywords=["patience picture book", "gardening kids story"]),
    dict(slug="mmb-08-old-man-who-moved-mountains", folder="The Old Man Who Moved Mountains",
         title="The Old Man Who Moved Mountains",
         subtitle="Mei, Ming & Baobao Learn 愚公移山 · No Obstacle Is Too Big When Family Works Together 🧺 | A Chinese Proverb Bedtime Story for Kids",
         series=HERITAGE_SERIES, number=8,
         kind="heritage", proverb="愚公移山 (Yú Gōng Yí Shān)",
         extra_keywords=["teamwork picture book", "determination kids story"]),
    dict(slug="mmb-09-lantern-snake-surprise", folder="Adding Feet To A Snake",
         title="Mei, Ming, and Baobao's Lantern Snake Surprise",
         subtitle="Adding Feet to a Drawing of a Snake · 画蛇添足 · Knowing When Your Art Is Just Right 🐍 | A Chinese Proverb Bedtime Story for Kids",
         series=HERITAGE_SERIES, number=9,
         kind="heritage", proverb="画蛇添足 (Huà Shé Tiān Zú)",
         extra_keywords=["Lantern Festival picture book", "knowing when to stop kids story"]),
    dict(slug="mmb-10-elephants-big-picture", folder="Touching The Elephant",
         title="The Elephant's Big Picture",
         subtitle="A Lantern Festival Tale · Mei, Ming & Baobao Learn 盲人摸象 · Listening to Every Friend 🐘 | A Chinese Proverb Bedtime Story for Kids",
         series=HERITAGE_SERIES, number=10,
         kind="heritage", proverb="盲人摸象 (Máng Rén Mō Xiàng)",
         extra_keywords=["listening picture book", "elephant bedtime story"]),
    dict(slug="mmb-11-wandering-lamb", folder="Mending The Sheepfold",
         title="Mei, Ming, and Baobao and the Wandering Lamb",
         subtitle="Mending the Sheepfold · 亡羊补牢 · It's Never Too Late to Make Things Right 🐑 | A Chinese Proverb Bedtime Story for Kids",
         series=HERITAGE_SERIES, number=11,
         kind="heritage", proverb="亡羊补牢 (Wáng Yáng Bǔ Láo)",
         extra_keywords=["learning from mistakes picture book", "lamb bedtime story"]),
    dict(slug="mmb-12-clever-fox-gentle-tiger", folder="The Fox And The Tiger",
         title="The Clever Fox and the Gentle Tiger",
         subtitle="Mei, Ming & Baobao Learn 狐假虎威 · True Confidence Comes from a Kind Heart 🦊 | A Chinese Proverb Bedtime Story for Kids",
         series=HERITAGE_SERIES, number=12,
         kind="heritage", proverb="狐假虎威 (Hú Jiǎ Hǔ Wēi)",
         extra_keywords=["confidence picture book", "fox and tiger fable"]),
]


@dataclass
class Listing:
    slug: str
    folder: str
    kind: str
    title: str
    subtitle: str
    series_title: str
    series_number: int
    author_first: str
    author_last: str
    language: str
    reading_age_min: int
    reading_age_max: int
    primary_marketplace: str
    categories: list[list[str]]
    keywords: list[str]
    description_html: str
    epub_path: str
    cover_path: str
    drm: bool
    ai_text: str
    ai_text_tool: str
    ai_images: str
    ai_images_tool: str
    ai_translations: str
    ai_translations_tool: str
    accessibility_images: str
    kdp_select: bool
    territories: str
    royalty_rate: str
    price_usd: str
    cover_title: str = ""
    proverb: str = ""
    # --- runtime state, updated by kdp_publisher.py ---
    kdp_title_id: Optional[str] = None
    status: str = "planned"  # planned -> details -> content -> pricing -> published
    asin: Optional[str] = None
    notes: list[str] = field(default_factory=list)


def _find_epub(folder: Path) -> Path:
    cands = [p for p in folder.glob("*(eBook).epub") if not re.search(r"_(ES|ZH|YUE|PT|JA|KO|FR|IT|DE) \(eBook\)\.epub$", p.name)]
    if not cands:
        raise FileNotFoundError(f"No English EPUB in {folder}")
    return sorted(cands, key=lambda p: len(p.name))[0]


def _kdp_epub(slug: str, folder: Path) -> Path:
    """The EPUB to upload: the compressed copy from kdp_epub_optimize.py if present, else the original."""
    optimized = KDP_EPUBS_DIR / f"{slug}.epub"
    return optimized if optimized.exists() else _find_epub(folder)


def _prepare_cover(folder: Path, slug: str) -> Path:
    """Upscale the 1024px cover page to 2048px JPG for KDP's cover uploader."""
    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    src = folder / "page_01.jpg"
    if not src.exists():
        src = folder / "page_01.png"
    dst = COVERS_DIR / f"{slug}-cover.jpg"
    if not dst.exists():
        img = Image.open(src).convert("RGB")
        if max(img.size) < 2000:
            img = img.resize((img.width * 2, img.height * 2), Image.Resampling.LANCZOS)
        img.save(dst, "JPEG", quality=92, optimize=True)
    return dst


def _keywords(plan: dict[str, Any], meta: dict[str, Any]) -> list[str]:
    base = list(meta.get("keywords", []))
    if plan["kind"] == "stem":
        kws = [
            "STEM picture book for kids ages 3-7",
            "AI and robotics bedtime story",
            "Curious Little Minds Lily and Robo",
            "coding and technology for preschoolers",
            "kindergarten computer science book",
        ] + plan.get("extra_keywords", [])
    else:
        kws = [
            "Chinese proverb picture book for kids",
            "chengyu stories for preschoolers",
            "Chinese heritage bedtime story",
            "Curious Little Minds Mei Ming and Baobao",
            "Chinese culture gift book for kids",
        ] + plan.get("extra_keywords", [])
    # Keep any topic-specific keyword from the generated metadata (last entry) if room remains
    for k in reversed(base):
        if len(kws) >= 7:
            break
        if k not in kws and len(k) <= 50:
            kws.append(k)
    return [k[:50] for k in kws[:7]]


def _validate_titles(plan: dict[str, Any], cover_title: str) -> None:
    """KDP requires the Book Title to match the cover; title + subtitle must be <= 200 characters."""
    title, subtitle = plan["title"], plan["subtitle"]
    joined = f"{title}: {subtitle}"
    if not (cover_title == title or joined.startswith(cover_title + " ")):
        raise ValueError(f"{plan['slug']}: KDP title {title!r} does not match cover title {cover_title!r}")
    if len(title) + len(subtitle) > 200:
        raise ValueError(f"{plan['slug']}: title+subtitle is {len(title) + len(subtitle)} chars (>200)")


def build_listing(plan: dict[str, Any], ref: dict[str, Any], existing: Optional[dict[str, Any]] = None) -> Listing:
    folder = OUTPUT_DIR / plan["folder"]
    story = json.loads((folder / "story.json").read_text(encoding="utf-8"))
    meta = json.loads((folder / "kdp_metadata.json").read_text(encoding="utf-8"))
    _validate_titles(plan, story.get("title", ""))
    ai = ref["content"]["ai_generated_content"]
    listing = Listing(
        slug=plan["slug"],
        folder=plan["folder"],
        kind=plan["kind"],
        title=plan["title"],
        subtitle=plan["subtitle"],
        series_title=plan["series"],
        series_number=plan["number"],
        author_first=AUTHOR[0],
        author_last=AUTHOR[1],
        language="English",
        reading_age_min=ref["details"]["reading_age"]["min"],
        reading_age_max=ref["details"]["reading_age"]["max"],
        primary_marketplace="Amazon.com",
        categories=STEM_CATEGORIES if plan["kind"] == "stem" else HERITAGE_CATEGORIES,
        keywords=_keywords(plan, meta),
        description_html=(existing or {}).get("description_html", ""),
        epub_path=str(_kdp_epub(plan["slug"], folder)),
        cover_path=str(_prepare_cover(folder, plan["slug"])),
        drm=True,
        ai_text=ai["texts"]["value"],
        ai_text_tool=ai["texts"]["tools"],
        ai_images=ai["images"]["value"],
        ai_images_tool=ai["images"]["tools"],
        ai_translations="NONE",  # English edition contains no translated text
        ai_translations_tool="",
        accessibility_images="not_readable",
        kdp_select=True,
        territories="all",
        royalty_rate="35_PERCENT",
        price_usd="0.99",
        cover_title=story.get("title", ""),
        proverb=plan.get("proverb", ""),
    )
    if existing:
        for k in ("kdp_title_id", "status", "asin", "notes"):
            if existing.get(k):
                setattr(listing, k, existing[k])
    return listing


# --------------------------------------------------------------------------- #
# Description generation (Gemini, styled after Book 1's live description)
# --------------------------------------------------------------------------- #
DESC_PROMPT = """You write Amazon KDP product descriptions for the children's picture-book brand "Curious Little Minds™" by Enrique Chan.

Here is the LIVE description of Book 1 in the same catalog. Match its structure, tone, length and HTML style EXACTLY:
---BOOK 1 DESCRIPTION (HTML)---
{reference}
---END---

Now write the description for this new book.
Title: {title}
Subtitle: {subtitle}
Series: {series} (Book {number})
{proverb_line}
Characters: {characters}
Story text (all pages, in order):
{story_text}
Back-cover blurb: {blurb}

Rules:
- Output ONLY a JSON object: {{"description_html": "..."}}.
- Use only these HTML tags: <p>, <b>, <i>, <br>, <ul>, <li>. No headings, no markdown, no links, no quotes around the whole thing.
- Structure (same as Book 1): (1) one-sentence hook introducing the main character(s) and a big question in <i>; (2) two short paragraphs summarizing the adventure and the lesson; (3) <p><b>Why parents and kids will love this book:</b></p> followed by a <ul> with exactly 3 <li><p>emoji <b>Label:</b> text</p></li> bullets {bullet_hint}; (4) a "Perfect for curious children ages 3 to 7, ..." paragraph; (5) a final <p> with two bold lines separated by <br>, the second starting with "Scroll up and click \\"Buy Now\\" to join ...".
- Total length 1,300–1,900 characters. Warm, concrete, no hype words like "revolutionary". Do not mention AI, Gemini, or page counts.
- {extra_rule}
"""


def generate_description(listing: Listing, regen: bool = False) -> str:
    from google.genai import types

    import config  # noqa: WPS433  (repo module)
    from storybook import StorybookGenerator

    folder = OUTPUT_DIR / listing.folder
    cached_desc_file = folder / "kdp_description.txt"
    if not regen and cached_desc_file.exists():
        cached_html = re.sub(r"\s+", " ", cached_desc_file.read_text(encoding="utf-8").strip())
        if 900 <= len(cached_html) <= 4000 and "<p>" in cached_html and "<b>" in cached_html:
            return cached_html

    ref = json.loads(REFERENCE.read_text(encoding="utf-8"))
    story = json.loads((folder / "story.json").read_text(encoding="utf-8"))
    pages = story.get("pages", [])
    story_text = "\n".join(f"Page {p.get('page_number', i + 1)}: {p.get('text', '').strip()}" for i, p in enumerate(pages))
    if listing.kind == "stem":
        characters = "Lily (curious girl) and Robo (her friendly round robot)"
        bullet_hint = "(🤖 Gentle STEM Introduction, ✨ Comforting Bedtime Story, 🎨 Beautiful Visuals)"
        extra_rule = "Explain the technology idea in the story in plain words a parent can repeat."
        proverb_line = ""
    else:
        characters = "Mei (6-year-old girl), her little brother Ming (5) and Baobao, their magical red-panda spirit guide"
        bullet_hint = "(🏮 Timeless Chinese Proverb, ✨ Comforting Bedtime Story, 🎨 Beautiful Shuimo Watercolor Art)"
        extra_rule = ("Name the proverb once in Chinese characters with pinyin and its literal meaning, "
                      "and mention it is a classic chengyu passed down for generations; keep it bilingual-family friendly.")
        proverb_line = f"Proverb: {listing.proverb}"
    prompt = DESC_PROMPT.format(
        reference=ref["details"]["description_html"],
        title=listing.title, subtitle=listing.subtitle, series=listing.series_title, number=listing.series_number,
        proverb_line=proverb_line, characters=characters, story_text=story_text[:9000],
        blurb=story.get("back_cover_blurb", ""), bullet_hint=bullet_hint, extra_rule=extra_rule,
    )
    client = StorybookGenerator()._get_client()
    resp = client.models.generate_content(
        model=config.TEXT_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.7),
    )
    data = json.loads(resp.text)
    html = data["description_html"].strip()
    html = re.sub(r"\s+", " ", html)
    if not (900 <= len(html) <= 4000):
        raise ValueError(f"Description length out of range: {len(html)}")
    cached_desc_file.write_text(html, encoding="utf-8")
    return html


def prepare(regen_descriptions: bool = False) -> list[Listing]:
    ref = json.loads(REFERENCE.read_text(encoding="utf-8"))
    LISTINGS_DIR.mkdir(parents=True, exist_ok=True)
    listings: list[Listing] = []
    for plan in BOOK_PLAN:
        path = LISTINGS_DIR / f"{plan['slug']}.json"
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        listing = build_listing(plan, ref, existing)
        if regen_descriptions or not listing.description_html:
            logger.info("Generating description for %s ...", listing.slug)
            listing.description_html = generate_description(listing, regen=regen_descriptions)
        path.write_text(json.dumps(asdict(listing), indent=2, ensure_ascii=False), encoding="utf-8")
        listings.append(listing)
        logger.info("Prepared %s (%d chars description)", listing.slug, len(listing.description_html))
    return listings


def load_listing(slug: str) -> Listing:
    data = json.loads((LISTINGS_DIR / f"{slug}.json").read_text(encoding="utf-8"))
    return Listing(**data)


def save_listing(listing: Listing) -> None:
    (LISTINGS_DIR / f"{listing.slug}.json").write_text(json.dumps(asdict(listing), indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "prepare"
    if cmd == "prepare":
        prepare(regen_descriptions="--regen" in sys.argv)
    elif cmd == "show":
        print(json.dumps(asdict(load_listing(sys.argv[2])), indent=2, ensure_ascii=False))
