"""Evolved Prompt Defaults and Illustration Logic for 'Curious Little Minds: Lily and Robo Adventures'.

Evolved to maximize narrative quality, viral factors, brand consistency,
and strict visual asset invariants across the series.
"""

import re

# ==============================================================================
# 1. SERIES BRANDING & IDENTITY
# ==============================================================================
SERIES_BRAND = "Curious Little Minds™"
SERIES_NAME = "Lily & Robo Adventures"
SERIES_NAME_ZH = "好奇小脑瓜·莉莉与乐乐大冒险"
SERIES_AUTHOR = "Enrique Chan"

# ==============================================================================
# 2. STEM METAPHOR ROSETTA STONE (Playful Child-Centric Concept Translation)
# ==============================================================================
STEM_METAPHOR_ROSETTA_STONE = {
    "long running agents": (
        "The Night-Light Helpers: Friendly digital lighthouses that gently tend the "
        "digital flowers and keep night watches while the world sleeps."
    ),
    "agent sandbox": (
        "The Magic Sandcastle Playground: A soft digital playroom where building blocks "
        "never hurt when they tumble and can be rebuilt with a giggle."
    ),
    "memory bank": (
        "The Glowing Star Jars: Luminous crystal jars on Robo's shelf holding favorite "
        "memories, friendly smiles, and bedtime lullabies."
    ),
    "skills": (
        "The Superhero Toolbelt: Colorful badges Robo earns for drawing rainbows, "
        "speaking dolphin language, and folding origami stars."
    ),
    "computer use": (
        "The Gentle Piano-Tap Dance: How Robo taps keyboard keys like musical piano "
        "notes to help friends find lost pictures and stories."
    ),
    "observability": (
        "The Illustrated Doodle Diary: A colorful diary with smiling stickers and "
        "footprints showing every helpful step Robo took today."
    ),
    "evaluation": (
        "The Golden Star High-Five: Celebration stickers and cheerful claps when a "
        "helper robot finishes their gentle task."
    ),
    "vibe coding": (
        "The Magic Paintbrush of Wishes: Whispering dreams to your computer helper "
        "and watching colorful castles appear without complicated spells."
    ),
}

# ==============================================================================
# 3. CHARACTER INVARIANTS & ARTISTIC STYLE
# ==============================================================================
SERIES_CHARACTER = (
    "Lily is a cheerful 6-year-old girl with fair skin, expressive bright brown eyes, "
    "and shoulder-length warm-brown hair styled in two neat pigtails secured with red hairbands. "
    "She wears a vibrant lemon-yellow A-line short-sleeved dress covered in clean white polka dots, "
    "and carries a small blue backpack. Her companion is Robo, a small, round, bipedal light-blue metallic robot, "
    "about waist-high to Lily (Lily is twice the height of Robo), featuring large, glowing, expressive circular "
    "white digital eyes and a warm, friendly smile."
)

CHARACTER_INVARIANTS = (
    "STRICT CHARACTER INVARIANTS: "
    "Lily is ALWAYS a 6-year-old girl with fair skin, expressive bright brown eyes, shoulder-length warm-brown hair in twin pigtails with red hairbands, wearing a vibrant lemon-yellow dress with clean white polka dots and a small blue backpack. "
    "Robo is ALWAYS a small spherical light-blue metallic robot, half of Lily's height, with large glowing white circular eye screen, gentle smile, and stubby magnetic feet. NO wheels, NO tracks, NO antennas, NO gears."
)

SERIES_STYLE = (
    "Premium children's picture book illustration in a whimsical watercolor style with soft pencil outlines. "
    "Warm pastel palette, gentle luminous gradients, delicate translucent watercolor washes, airy dreamlike backgrounds "
    "casting a soft warm ambient glow on the characters. Strictly textless, with no signatures, logos, or words."
)

NEGATIVE_STYLE_PROMPT = (
    "ugly, deformed, extra limbs, missing limbs, wheels, tracks, antennas, gears, boxy torso robot, "
    "photorealistic, 3D CGI render, dark moody lighting, harsh shadows, text, words, letters, titles, "
    "banners, ribbons with text, book covers inside illustration, signatures, watermarks, logo, typography."
)


def get_stem_metaphor_context(topic: str) -> str:
    """Finds the child-friendly metaphor for a given STEM topic."""
    topic_lower = topic.lower()
    for key, metaphor in STEM_METAPHOR_ROSETTA_STONE.items():
        if key in topic_lower:
            return f"Pedagogical Metaphor: '{metaphor}'"
    return (
        "Pedagogical Metaphor: Explain this concept through playful everyday analogies "
        "(like sharing toys, building blocks, or friendly helpers)."
    )


def get_story_generation_prompt(topic: str, age_group: str, language: str = "English") -> str:
    """Returns the prompt for generating the structured 20-page storybook JSON."""
    metaphor_guide = get_stem_metaphor_context(topic)

    return f"""
    You are an award-winning children's picture book author writing for "{SERIES_BRAND}: {SERIES_NAME}".
    Write an exceptionally engaging, heartwarming, and imaginative 20-page picture book about "{topic}" for children aged {age_group}.

    {metaphor_guide}

    PUBLISHING ARCHITECTURE (Exactly 20 Pages):
    - Page 1 [Front Cover]: `text` has the book title and "Written by {SERIES_AUTHOR}". `scene_description` MUST describe a 100% textless illustration with open pastel sky at top and bottom for typography overlay (DO NOT mention banners, titles, letters, or books in `scene_description`).
    - Page 2 [Publisher Colophon]: Copyright © 2026 {SERIES_AUTHOR}. Curious Little Minds Publishing. First Edition. (Max 25 words).
    - Page 3 [Dedication]: A warm, one-sentence dedication to curious little explorers (Max 25 words).
    - Pages 4–18 [15-Page Core Story Arc]:
        * Pacing: 20 to 35 words per page.
        * Tone: Warm, musical, soothing bedtime tone.
        * Interactive Lap-Reading Hook: Weave in onomatopoeia sound cues (*BEEP-BEEP!*, *SWOOSH!*), physical gestures (*clapping hands*, *stretching tall*), or visual search prompts.
        * Arc: Gentle inciting curiosity (Pages 4-6) -> Playful sensory exploration (Pages 7-11) -> Cooperative emotional climax (Pages 12-15) -> Cozy resolution & wind-down (Pages 16-18).
    - Page 19 [STEM Exploration Corner]: 3 short parent-child questions and 1 simple living-room activity explaining the concept in real life. STRICT WORD BUDGET: 35 to 50 words total so the text card stays compact.
    - Page 20 [Back Cover & Closing]: `text` contains a warm 20-30 word series teaser blurb ending with "The End". `scene_description` MUST be a 100% textless illustration of Lily and Robo waving goodnight under starry skies (DO NOT ask for "The End" or any words in `scene_description`).

    Language Requirement:
    The book `text` MUST be written in {language}. All `scene_description` fields MUST be in English.

    Guidelines for Character Consistency and Style:
    1. {CHARACTER_INVARIANTS}
    2. Scene descriptions must be detailed, action-oriented, in English, describing visual composition, camera angles, emotions, and lighting.
    3. Scene descriptions must be purely visual—NEVER include quoted phrases, book titles, banners, signs, labels, author credits, watermarks, or any written words.

    Format output as valid JSON:
    {{
        "title": "Title of the storybook in {language}",
        "series": "{SERIES_BRAND}: {SERIES_NAME}",
        "author": "{SERIES_AUTHOR}",
        "main_character_description": "{SERIES_CHARACTER}",
        "artistic_style": "{SERIES_STYLE}",
        "pages": [
            {{
                "page_number": 1,
                "text": "[Title]\\nWritten by {SERIES_AUTHOR}",
                "features_main_character": true,
                "scene_description": "Lily and Robo waving happily in a sunlit meadow with open pastel sky above and soft grass below, inviting the reader into their adventure."
            }},
            {{
                "page_number": 2,
                "text": "Curious Little Minds Publishing\\nText and illustrations copyright © 2026 Enrique Chan. All rights reserved.",
                "features_main_character": false,
                "scene_description": "A charming minimalist watercolor vignette of a tiny glowing star jar beside a wooden bookshelf."
            }},
            {{
                "page_number": 3,
                "text": "Dedicated to curious little dreamers everywhere who love to ask 'Why?' and 'How?'",
                "features_main_character": true,
                "scene_description": "Lily sitting on a soft grassy hill looking up at gentle watercolor stars with spherical Robo sitting contentedly beside her."
            }},
            {{
                "page_number": 4,
                "text": "First page of the core story in {language} (20-35 words, with gentle sound effect)",
                "features_main_character": true,
                "scene_description": "Detailed visual description of page 4 scene"
            }}
        ]
    }}

    Generate exactly 20 pages. Ensure JSON is valid, fully escaped, with NO markdown code block wrappers.
    """


def sanitize_scene_description(page_scene: str) -> str:
    """Sanitizes untrusted LLM scene descriptions so no text/titles/banners leak into image prompts."""
    if not page_scene:
        return ""

    cleaned = page_scene.strip()

    # 1. Strip quoted phrases (e.g. 'Curious Little Minds', "The Night-Light Helpers", "The End")
    cleaned = re.sub(r'"[^"\n]{2,120}"', "", cleaned)
    cleaned = re.sub(r"(?<![a-zA-Z])'[^'\n]{2,120}'(?![a-zA-Z])", "", cleaned)

    # 2. Strip clauses asking for banners, signs, titles, or text overlays
    clause_patterns = [
        r"\b(with\s+)?(a\s+|an\s+|the\s+)?(whimsical\s+|glowing\s+|colorful\s+|top\s+|bottom\s+|wooden\s+|floating\s+)*(series\s+)?(banner|ribbon|plaque|sign|signage|title|header|footer|label|text|words|lettering|calligraphy|inscription)\b[^.;,]*",
        r"\b(reading|saying|titled|entitled|inscribed\s+with|displaying\s+the\s+words|written\s+in)\b[^.;,]*",
        r"\b(by|written\s+by|illustrated\s+by|escrito\s+por|creador|author|credits|art\s+by|drawings?\s+by|draw\s+by|illustration\s+by|illustrations\s+by|artist)\b[:\s\-]*[a-zA-Z0-9\s\(\)\'\"]*",
        r"\b(enrique\s+chan|enrique|chan|curious\s+little\s+minds|lily\s+&\s+robo\s+adventures)\b",
        r"\b(page\s+\d+|chapter\s+\d+|front\s+cover|back\s+cover|book\s+cover|cover\s+illustration|title\s+page)\b",
        r"\b(signature|watermark|copyright|text|words|letters|signage|label|branding|typography|logo)\b",
    ]
    for pattern in clause_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

    # 3. Clean up dangling prepositions/conjunctions and whitespace
    cleaned = re.sub(r"\b(with|and|featuring|showing|displaying|under|above)\s*(?=[.,;]|$)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s+([.,;])", r"\1", cleaned)
    cleaned = re.sub(r"[.,;]{2,}", ".", cleaned)
    return cleaned.strip(".,;: ")


def construct_image_prompt(
    page_scene: str,
    main_char_desc: str = SERIES_CHARACTER,
    style_desc: str = SERIES_STYLE,
    features_char: bool = True,
    include_negative: bool = True,
) -> str:
    """Constructs and sanitizes the prompt for the image generation model for a specific page.

    Only untrusted `page_scene` and custom overrides are sanitized; canonical constants
    (`SERIES_STYLE`, `CHARACTER_INVARIANTS`) remain intact so phrases like
    'Strictly textless, with no signatures, logos, or words.' are never corrupted.
    """
    parts = []
    if features_char and main_char_desc:
        char_clean = (
            main_char_desc.strip()
            if main_char_desc.strip() == SERIES_CHARACTER
            else sanitize_scene_description(main_char_desc)
        )
        if char_clean:
            parts.append(char_clean.rstrip("."))
        parts.append(CHARACTER_INVARIANTS.strip().rstrip("."))

    sanitized_scene = sanitize_scene_description(page_scene)
    if sanitized_scene:
        parts.append(sanitized_scene.rstrip("."))

    if style_desc:
        style_clean = (
            style_desc.strip()
            if style_desc.strip() == SERIES_STYLE
            else sanitize_scene_description(style_desc)
        )
        if style_clean:
            parts.append(style_clean.rstrip("."))

    if include_negative and NEGATIVE_STYLE_PROMPT:
        parts.append(f"Avoid: {NEGATIVE_STYLE_PROMPT.strip().rstrip('.')}")

    prompt = ". ".join(p for p in parts if p)
    prompt = re.sub(r"\s+", " ", prompt)
    prompt = re.sub(r"\.+", ".", prompt)
    prompt = re.sub(r"\s+\.", ".", prompt)
    return prompt.strip(".,;: ") + "."
