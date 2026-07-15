"""Evolved Prompt Defaults and Illustration Logic for 'Lily and Robo Adventures'.

Evolved by AlphaEvolve to maximize originality, genuineness, viral factors,
and strict visual consistency across assets.
"""
import re

SERIES_CHARACTER = (
    "Lily is a cheerful 6-year-old girl with fair skin, expressive bright brown eyes, "
    "and shoulder-length warm-brown hair styled in two neat pigtails secured with red hairbands. "
    "She wears a vibrant lemon-yellow A-line short-sleeved dress covered in clean white polka dots, "
    "and carries a small blue backpack. Her companion is Robo, a small, round, bipedal light-blue metallic robot, "
    "about waist-high to Lily (Lily is twice the height of Robo), featuring large, glowing, expressive circular "
    "white digital eyes and a warm, friendly smile."
)

SERIES_STYLE = (
    "Premium children's book illustration in a whimsical watercolor style with soft pencil outlines. "
    "The palette consists of warm pastel colors and gentle luminous gradients, with delicate visible "
    "watercolor washes. The backgrounds are simplified, airy, and dreamlike environments painted in "
    "translucent washes, casting a gentle warm ambient glow on the characters. Strictly textless, "
    "with no signatures, logos, or words."
)

def get_story_generation_prompt(topic: str, age_group: str, language: str = "English") -> str:
    """Returns the prompt for generating the structured storybook JSON using Gemini 3.5 Flash."""
    return f"""
    You are a world-class, award-winning children's book author. Write an exceptionally engaging, highly imaginative, original, and heartwarming storybook about "{topic}" tailored for children aged {age_group}.

    Narrative Architecture & Engagement Guide:
    - Craft a captivating and original story arc over exactly 20 pages: introduction, a gentle inciting curiosity, playful exploration with sensory engagement, a cooperative emotional climax, and a comforting resolution.
    - Weave in a gentle, interactive rhythm or playful physical gestures for the child reading aloud with their parent (e.g. tracing a path, making a sound). Avoid repetitive tropes.
    
    The book text (specifically the "title" and the "text" field of each page) MUST be written in the following language: {language}.
    
    The book must start with Page 1 as the Cover Page. 
    The cover page text (the "text" field of page 1) MUST be exactly:
    "[Title of the book in {language}]\nWritten by Enrique Chan"
    
    Guidelines for Character Consistency and Style:
    1. The main character is Lily: "Lily, a cheerful 6-year-old girl with fair skin, expressive bright brown eyes, and shoulder-length warm-brown hair styled in two neat pigtails, wearing a vibrant yellow short-sleeved dress with white polka dots".
    2. Her companion is Robo: "Robo, a small, round, bipedal light-blue metallic robot, half the height of Lily, featuring large expressive glowing white digital eyes and a gentle smile".
    3. All page "scene_description" fields MUST be detailed, action-oriented, written in English, and explicitly describe the visual layout, character emotions, dynamic physical placements, and scenery.
    4. In the page scene descriptions, do NOT include any author credits, names, signatures, watermarks, quotes, or text of any kind. Keep the scene purely visual and artistic.
    
    Format the output as a valid JSON object with the following structure:
    {{
        "title": "Title of the storybook in {language}",
        "main_character_description": "Detailed visual description of Lily and Robo in English",
        "artistic_style": "Detailed description of the unified artistic style in English",
        "pages": [
            {{
                "page_number": 1,
                "text": "[Title of the book in {language}]\\nWritten by Enrique Chan",
                "features_main_character": true,
                "scene_description": "Lily and her robot companion Robo waving happily on the colorful cover of their adventure book, inviting the reader in. Whimsical playground background."
            }},
            {{
                "page_number": 2,
                "text": "First page story text in {language}",
                "features_main_character": true,
                "scene_description": "Detailed visual description of page 2 scene"
            }}
        ]
    }}
    
    Generate exactly 20 pages (including the cover page as page 1).
    Ensure the JSON is perfectly valid, with correct escaping and NO trailing commas or formatting flaws.
    """

def construct_image_prompt(page_scene: str, main_char_desc: str = SERIES_CHARACTER, style_desc: str = SERIES_STYLE, features_char: bool = True) -> str:
    """Constructs and sanitizes the prompt for the image generation model for a specific page."""
    parts = []
    if features_char and main_char_desc:
        parts.append(main_char_desc.strip())
    if page_scene:
        parts.append(page_scene.strip())
    if style_desc:
        parts.append(style_desc.strip())
        
    prompt = ". ".join(parts) + "."
    
    # Clean up and strip unwanted words, names, credits, watermarks, text, or signatures
    unauthorized_patterns = [
        rf"\b(by|written\s+by|illustrated\s+by|escrito por|creador|author|credits|art\s+by|drawings?\s+by|draw\s+by|illustration\s+by|illustrations\s+by|artist|enrique\s+chan|enrique|chan)\b[:\s\-]*[a-zA-Z0-9\s\(\)\'\"]*",
        rf"\b(by|written|illustrated|author|credits|enrique|chan|page\s+\d+|chapter\s+\d+|book cover|title|cover)\b",
        rf"\b(signature|watermark|copyright|text|words|letters|signage|label|sign|branding)\b"
    ]
    for pattern in unauthorized_patterns:
        prompt = re.sub(pattern, "", prompt, flags=re.IGNORECASE)
        
    # Standardize spaces and clean up redundant punctuation
    prompt = re.sub(r'\s+', ' ', prompt)
    prompt = re.sub(r'\.+', '.', prompt)
    prompt = re.sub(r'\s+\.', '.', prompt)
    # Ensure no leading/trailing punctuation debris
    return prompt.strip('.,;: ') + "."
