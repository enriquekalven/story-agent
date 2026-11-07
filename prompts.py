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
HERITAGE_SERIES_NAME = "Mei, Ming & Baobao's Chinese Proverb Tales"
HERITAGE_SERIES_NAME_ZH = "好奇小脑瓜·美美、明明与宝宝的成语故事"
SERIES_AUTHOR = "Enrique Chan"

# ==============================================================================
# 2. STEM & CHINESE PROVERB ROSETTA STONES (Playful Child-Centric Translation)
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
    "year of the horse": (
        "The Lantern-Parade Pony & Little Helper Team: A glowing golden origami-lantern horse "
        "that trots through the Lunar New Year night market while Robo and tiny lantern helpers "
        "coordinate drumbeats, red envelopes, and twinkling lights in joyful harmony."
    ),
    "agent-to-agent": (
        "The Walkie-Talkie Treehouse Club: When Lily plans a garden picnic, Robo uses glowing "
        "walkie-talkies to coordinate with Baker-Bot, Weather-Bot, and Music-Bot so every helper "
        "does what they do best in joyful harmony."
    ),
    "multi-agent orchestration": (
        "The Walkie-Talkie Treehouse Club: When Lily plans a garden picnic, Robo uses glowing "
        "walkie-talkies to coordinate with Baker-Bot, Weather-Bot, and Music-Bot so every helper "
        "does what they do best in joyful harmony."
    ),
    "model context protocol": (
        "The Rainbow Keyring: Robo's magical universal keyring that opens friendly glowing bridges "
        "to the town library, art studio, and map room without needing a thousand tangled cords."
    ),
    "tool connectors": (
        "The Rainbow Keyring: Robo's magical universal keyring that opens friendly glowing bridges "
        "to the town library, art studio, and map room without needing a thousand tangled cords."
    ),
    "human-in-the-loop": (
        "The Green-Light High-Five: How Robo always pauses at big mystery doors and waits for "
        "Lily's warm Green-Light High-Five before stepping through, keeping every adventure safe and kind."
    ),
    "grounding": (
        "The Wise Owl's Magnifying Glass: How Robo checks the Golden Fact Book in the treehouse "
        "library with a glowing magnifying glass before answering Lily's big questions, instead of guessing."
    ),
    "self-improving agents": (
        "The Paper Airplane Breeze Test: How Lily and Robo fold, fly, measure, and gently tweak "
        "paper airplanes across the sunny meadow, learning from every flight until the best design "
        "glides all the way to the hilltop."
    ),
    "evolutionary optimization": (
        "The Paper Airplane Breeze Test: How Lily and Robo fold, fly, measure, and gently tweak "
        "paper airplanes across the sunny meadow, learning from every flight until the best design "
        "glides all the way to the hilltop."
    ),
}

CHINESE_PROVERB_ROSETTA_STONE = {
    "iron rod into a needle": (
        "Proverb: 铁杵磨成针 / 鐵杵磨成針 (Tiě Chǔ Mó Chéng Zhēn — 'Grinding an Iron Rod into a Needle'). "
        "Heritage Origin & Child Metaphor: Inspired by the young poet Li Bai meeting a kind grandmother "
        "by a clear mountain stream patiently rubbing a thick iron pestle against a smooth river stone to "
        "make a tiny sewing needle ('只要功夫深，铁杵磨成针' — with steady perseverance, even an iron rod "
        "can be ground into a needle). Mei, Ming, and Baobao discover that big dreams happen one patient, "
        "joyful little step at a time."
    ),
    "铁杵磨成针": (
        "Proverb: 铁杵磨成针 / 鐵杵磨成針 (Tiě Chǔ Mó Chéng Zhēn — 'Grinding an Iron Rod into a Needle'). "
        "Mei, Ming, and Baobao learn from a gentle grandmother by a mossy bamboo stream that steady daily "
        "practice turns the hardest task into something wonderful."
    ),
    "kong rong": (
        "Proverb: 孔融让梨 / 孔融讓梨 (Kǒng Róng Ràng Lí — 'Kong Rong Shares the Biggest Pear'). "
        "Heritage Origin & Child Metaphor: Inspired by four-year-old Kong Rong choosing the smallest golden "
        "pear from the family fruit basket so his older and younger brothers could enjoy the biggest, juiciest "
        "pears. Mei, Ming, and Baobao harvest golden autumn pears in Grandma's sunlit courtyard orchard and "
        "discover that sharing the sweetest fruit makes everyone's heart taste twice as sweet."
    ),
    "孔融让梨": (
        "Proverb: 孔融让梨 / 孔融讓梨 (Kǒng Róng Ràng Lí — 'Kong Rong Shares the Biggest Pear'). "
        "Mei, Ming, and Baobao learn the warm joy of generosity and family kindness in a sunlit pear orchard."
    ),
    "sima guang": (
        "Proverb: 司马光砸缸 / 司馬光砸缸 (Sīmǎ Guāng Zá Gāng — 'Sima Guang Smashes the Water Vat'). "
        "Heritage Origin & Child Metaphor: Inspired by young Sima Guang staying calm when a playmate slipped "
        "into a tall courtyard porcelain water vat—instead of panicking, he picked up a garden stone to crack "
        "the vat so the water poured out safely. When a tiny garden hedgehog or beloved lantern toy gets trapped "
        "in a deep rainwater barrel in the courtyard, Mei, Ming, and Baobao learn how staying calm and thinking "
        "creatively helps rescue a friend in need."
    ),
    "司马光砸缸": (
        "Proverb: 司马光砸缸 / 司馬光砸缸 (Sīmǎ Guāng Zá Gāng — 'Sima Guang Smashes the Water Vat'). "
        "Mei, Ming, and Baobao learn how staying calm and thinking creatively helps save a friend in need."
    ),
    "frog in the well": (
        "Proverb: 井底之蛙 / 井底之蛙 (Jǐng Dǐ Zhī Wā — 'The Little Frog at the Bottom of the Well'). "
        "Heritage Origin & Child Metaphor: Inspired by Zhuangzi's classic fable of a cheerful little green frog "
        "living at the bottom of a mossy stone well who thinks the sky is only as big as the round stone rim—until "
        "a friendly sea turtle and Mei, Ming, and Baobao invite him to hop up onto the garden wall and behold the "
        "vast, starry lotus valley, misty mountains, and endless sky."
    ),
    "井底之蛙": (
        "Proverb: 井底之蛙 / 井底之蛙 (Jǐng Dǐ Zhī Wā — 'The Little Frog at the Bottom of the Well'). "
        "Mei, Ming, and Baobao help a little green well-frog hop out to see the wide, wonderful world."
    ),
    "painting the dragon": (
        "Proverb: 画龙点睛 / 畫龍點睛 (Huà Lóng Diǎn Jīng — 'Painting the Dragon's Eyes'). "
        "Heritage Origin & Child Metaphor: Inspired by the legendary painter Zhang Sengyou painting "
        "magnificent dragons on a temple wall and adding two careful, focused dots of ink on the eyes "
        "so the dragon comes alive and dances into the clouds. Mei, Ming, and Baobao learn how patience, "
        "mindful focus, and the final thoughtful finishing touch bring art and kindness to life."
    ),
    "画龙点睛": (
        "Proverb: 画龙点睛 / 畫龍點睛 (Huà Lóng Diǎn Jīng — 'Painting the Dragon's Eyes'). "
        "Mei, Ming, and Baobao discover how careful attention and the final heartfelt brushstroke awaken "
        "a painted golden cloud dragon."
    ),
    "tree stump": (
        "Proverb: 守株待兔 / 守株待兔 (Shǒu Zhū Dài Tù — 'Waiting by the Tree Stump for a Rabbit'). "
        "Heritage Origin & Child Metaphor: Inspired by Han Feizi's playful fable of a farmer who saw a hurried "
        "bunny bump into a tree stump once and then sat idle by the stump every day waiting for another lucky "
        "accident instead of tending his garden. Mei, Ming, and Baobao meet a sleepy bunny by an old camphor "
        "tree stump and discover that real magic comes from cheerful effort, planting seeds, and trying your best "
        "rather than just waiting for luck."
    ),
    "守株待兔": (
        "Proverb: 守株待兔 / 守株待兔 (Shǒu Zhū Dài Tù — 'Waiting by the Tree Stump for a Rabbit'). "
        "Mei, Ming, and Baobao learn that joyful effort grows wonderful things much better than waiting for luck."
    ),
    "pulling up sprouts": (
        "Proverb: 拔苗助长 / 拔苗助長 (Bá Miáo Zhù Zhǎng — 'Pulling Up Sprouts to Help Them Grow'). "
        "Heritage Origin & Child Metaphor: Inspired by Mencius's story of an impatient villager who tugged on his "
        "young green rice sprouts to make them taller overnight, only to find they drooped without patient care. "
        "In their sunny bamboo terrace garden, Ming tries to stretch tiny lotus and rice sprouts taller with his "
        "fingers, and Mei and Baobao gently show him how sunshine, water, and patient love help little sprouts "
        "(and little kids!) grow strong at their own natural pace."
    ),
    "拔苗助长": (
        "Proverb: 拔苗助长 / 拔苗助長 (Bá Miáo Zhù Zhǎng — 'Pulling Up Sprouts to Help Them Grow'). "
        "Mei, Ming, and Baobao learn that plants and children grow best with gentle patience, water, and sunshine."
    ),
    "moved mountains": (
        "Proverb: 愚公移山 (Yú Gōng Yí Shān — 'The Old Man Who Moved Mountains'). "
        "Heritage Origin & Child Metaphor: Inspired by the legendary Grandpa Yu Gong who rallied his children, "
        "grandchildren, and neighbors to carry away pebbles and earth basket by basket until the path across two "
        "towering mountains opened up. When a pile of fallen bamboo and mossy boulders blocks the village garden "
        "trail, Mei, Ming, and Baobao team up with Grandpa to carry little woven baskets together—proving no "
        "obstacle is too big for family teamwork."
    ),
    "愚公移山": (
        "Proverb: 愚公移山 (Yú Gōng Yí Shān — 'The Old Man Who Moved Mountains'). "
        "Mei, Ming, and Baobao learn how family teamwork and patient basket-by-basket effort clear the biggest obstacle."
    ),
    "adding feet to a snake": (
        "Proverb: 画蛇添足 / 畫蛇添足 (Huà Shé Tiān Zú — 'Adding Feet to a Drawing of a Snake'). "
        "Heritage Origin & Child Metaphor: Inspired by the classic tale of an artist who finished a graceful brush "
        "painting of a ribbon-snake first, then kept doodling four little shoes and feet onto it until it didn't "
        "look like a snake anymore! During a festive lantern-painting afternoon, Mei, Ming, and Baobao giggle and "
        "learn the joyful artistic wisdom of knowing when a creation is already complete and just right."
    ),
    "画蛇添足": (
        "Proverb: 画蛇添足 / 畫蛇添足 (Huà Shé Tiān Zú — 'Adding Feet to a Drawing of a Snake'). "
        "Mei, Ming, and Baobao learn the joyful wisdom of knowing when a creation is already complete."
    ),
    "touching the elephant": (
        "Proverb: 盲人摸象 / 盲人摸象 (Máng Rén Mō Xiàng — 'Touching Different Parts of the Elephant'). "
        "Heritage Origin & Child Metaphor: Inspired by the classic parable where friends with eyes closed touch "
        "different parts of a gentle giant elephant—thinking its ear is a fan, its trunk is a flute, its leg is a "
        "pillar, and its tail is a paintbrush. In a lantern-lit misty courtyard game with a friendly sculpted stone "
        "or gentle festival elephant, Mei, Ming, and Baobao discover that when we listen to every friend's point of "
        "view, we see the whole wonderful picture together!"
    ),
    "盲人摸象": (
        "Proverb: 盲人摸象 / 盲人摸象 (Máng Rén Mō Xiàng — 'Touching Different Parts of the Elephant'). "
        "Mei, Ming, and Baobao learn to listen to every friend's perspective to see the whole big picture."
    ),
    "mending the sheepfold": (
        "Proverb: 亡羊补牢 / 亡羊補牢 (Wáng Yáng Bǔ Láo — 'Mending the Sheepfold After a Sheep Wanders Off'). "
        "Heritage Origin & Child Metaphor: Inspired by the classic proverb ('亡羊补牢，为时未晚' — it is never too "
        "late to mend the fence after a lamb wanders out). When a fluffy little cloud-lamb slips through a loose "
        "bamboo gate in the hillside meadow, Mei, Ming, and Baobao find the lamb with gentle bell chimes and work "
        "together to weave the bamboo fence strong and snug—learning that mistakes are okay when we fix them with care."
    ),
    "亡羊补牢": (
        "Proverb: 亡羊补牢 / 亡羊補牢 (Wáng Yáng Bǔ Láo — 'Mending the Sheepfold After a Sheep Wanders Off'). "
        "Mei, Ming, and Baobao learn that it is never too late to learn from a mistake and make things right."
    ),
    "fox and the tiger": (
        "Proverb: 狐假虎威 / 狐假虎威 (Hú Jiǎ Hǔ Wēi — 'The Clever Fox Walking Behind the Tiger'). "
        "Heritage Origin & Child Metaphor: Inspired by the classic forest fable of a little red fox who strutted "
        "ahead of a friendly big striped tiger, pretending the woodland animals were bowing to him instead of the "
        "tiger behind him. In a whimsical misty bamboo forest play, Mei, Ming, and Baobao meet a little fox and a "
        "gentle smiling guardian tiger, discovering that true confidence comes from your own kind heart rather than "
        "borrowing someone else's big shadow."
    ),
    "狐假虎威": (
        "Proverb: 狐假虎威 / 狐假虎威 (Hú Jiǎ Hǔ Wēi — 'The Clever Fox Walking Behind the Tiger'). "
        "Mei, Ming, and Baobao learn that true confidence comes from being proud and kind as yourself."
    ),
}

# ==============================================================================
# 3. CHARACTER INVARIANTS & ARTISTIC STYLE (GROUNDED IN BOOK 1 & HERITAGE CAST)
# ==============================================================================
SERIES_CHARACTER = (
    "Lily is a cheerful 6-year-old girl with fair skin, soft rosy watercolor cheeks with tiny light freckles "
    "across her nose, expressive large warm-brown eyes with white catchlights, and warm chestnut-brown wavy "
    "hair with soft bangs across her forehead, styled in two high-side fluffy curly pigtail buns/puffs tied "
    "with bright red ribbon bows on the upper sides of her head plus soft wavy shoulder-length curls framing "
    "her face and neck. She wears a short puff-sleeved buttercup-yellow A-line dress with golden-mustard and "
    "white polka dots, a crisp white rounded Peter Pan collar, three small buttons down the front bodice, a "
    "sky-blue backpack with smooth, plain blue shoulder straps (NEVER hands or buckles shaped like hands on the straps), "
    "white ankle socks, and bright red Mary Jane strap shoes. Lily has EXACTLY TWO normal arms and TWO hands total. Her companion "
    "is Robo, a small, round spherical sky-blue / powder-blue metallic robot, about waist-high to Lily, featuring "
    "a single smooth round sky-blue spherical body with a horizontal equator seam line across his middle and "
    "small circular buttons on his lower belly. Robo's face is drawn DIRECTLY on his smooth sky-blue spherical "
    "body (NO separate visor cutout, NO dark black screen, NO white/cream faceplate) with two large expressive "
    "round cartoon eyes (white outer sclera rings with large deep cobalt/navy-blue irises and white catchlights), "
    "two small curved eyebrows above his eyes, soft rosy cheek blush ovals, and a simple happy curved U-shaped "
    "line smile directly between/below his eyes. Robo has a single slender top antenna stalk rising from a small "
    "dome base on the very top of his head tipped with a glowing orb ball, circular ear-like side hubs, short "
    "segmented tubular blue arms with simple 2- or 3-fingered rounded pincer hands, and two short segmented "
    "cylindrical blue legs ending in rounded dome feet."
)

CHARACTER_INVARIANTS = (
    "STRICT CHARACTER INVARIANTS (MUST MATCH BOOK 1 REFERENCE SHEET): "
    "Lily is ALWAYS a 6-year-old girl with warm chestnut-brown wavy hair styled in two high-side fluffy curly "
    "pigtail puffs tied with bright red ribbon bows PLUS soft wavy shoulder-length curls framing her face and "
    "soft bangs, large warm-brown eyes, rosy cheeks with tiny freckles, wearing a short puff-sleeved "
    "buttercup-yellow polka-dot A-line dress with a crisp white Peter Pan collar and three small front bodice "
    "buttons, a sky-blue backpack with smooth plain sky-blue shoulder straps, white ankle socks, and bright red "
    "Mary Jane strap shoes (NEVER braided hair, NEVER a single ponytail, NEVER missing her yellow polka-dot dress "
    "with white Peter Pan collar, NEVER extra hands or disembodied hands on her backpack straps — Lily has EXACTLY "
    "TWO arms and TWO hands total, and her backpack straps are completely smooth plain blue fabric). "
    "Robo is ALWAYS a single round spherical sky-blue metallic robot, waist-high to Lily, with a horizontal "
    "equator seam line, his face drawn DIRECTLY on his blue spherical metal body (NEVER a dark black monitor "
    "screen or recessed visor cutout), two large round white-and-cobalt-blue cartoon eyes with small curved "
    "eyebrows, a simple curved line smile, rosy blush cheeks, a single slender top antenna stalk with a glowing "
    "ball tip on top of his head, circular side ear-hubs, short segmented tubular blue arms with rounded pincer "
    "hands, and short segmented cylindrical blue legs with dome feet. NO wheels, NO tracks, NO extra side antennas, "
    "NO antennas on ears, NO gears, NO boxy square torso. NEVER duplicate or clone Lily or Robo twice in the same "
    "scene (show at most ONE Lily and ONE Robo per page)."
)

SERIES_STYLE = (
    "Premium children's picture book illustration in a whimsical watercolor and colored-pencil style with warm "
    "brown ink outlines and visible watercolor paper grain, matching the Book 1 reference sheet. Warm pastel "
    "palette, gentle luminous gradients, delicate translucent watercolor washes, airy dreamlike backgrounds "
    "casting a soft warm ambient glow on the characters. Compose characters and key action in the upper "
    "two-thirds of the frame, leaving open ground, floor, or pastel wash in the bottom 30% for the story text "
    "card. Strictly textless, with no signatures, logos, or words."
)

NEGATIVE_STYLE_PROMPT = (
    "ugly, deformed, extra limbs, extra hands, extra arms, four hands, three hands, hands on backpack straps, "
    "disembodied hands on chest straps, gripping backpack straps, missing limbs, duplicate Lily, two Lilys in same "
    "scene, duplicate Robo, two Robos in same scene, cloned characters, multiple panels, split screen, collage, "
    "braided hair, braided pigtails, single ponytail, straight flat hair on Lily, black face screen on robot, dark "
    "navy monitor visor on robot, recessed faceplate cutout on robot, wheels, tank tracks, side ear antennas, gears, "
    "boxy square torso robot, separate head and torso on robot, photorealistic, 3D CGI render, dark moody lighting, "
    "harsh shadows, text, words, letters, titles, banners, ribbons with text, speech bubbles with text, book covers "
    "inside illustration, signatures, watermarks, logo, typography."
)

# ------------------------------------------------------------------------------
# Chinese Heritage & Proverbs Core Cast (Mei, Ming & Baobao) & Shuimo Style
# ------------------------------------------------------------------------------
MEI_CHARACTER_DESC = (
    "Mei is a curious, kind-hearted 6-year-old Chinese girl with warm fair-peach skin, soft rosy watercolor "
    "cheeks, expressive large dark-brown eyes with white catchlights, and glossy jet-black hair with soft bangs "
    "across her forehead, styled in two traditional round double-buns (shuangyaji twin buns) on the upper sides "
    "of her head tied with crimson-red silk ribbons and tiny golden bell tassels. She wears a modern-traditional "
    "vermilion-red and peach-blossom-pink embroidered tangzhuang tunic top with golden cloud-collar trim and "
    "three golden frog-button closures down the front over a soft cream pleated skirt, white ankle socks, and "
    "embroidered red cloth Mary Jane strap shoes. Mei has EXACTLY TWO normal arms and TWO hands total."
)

MEI_INVARIANTS = (
    "STRICT MEI CHARACTER INVARIANTS: Mei is ALWAYS a 6-year-old Chinese girl with glossy jet-black hair in two "
    "round top-side double-buns tied with crimson-red silk ribbons and tiny golden bell tassels plus soft bangs, "
    "large dark-brown eyes, rosy cheeks, wearing a vermilion-red and peach-pink embroidered tangzhuang tunic top "
    "with golden frog buttons over a soft cream pleated skirt, white ankle socks, and red embroidered Mary Jane "
    "shoes (NEVER blonde or brown hair, NEVER yellow polka-dot dress, NEVER extra hands — Mei has EXACTLY TWO "
    "arms and TWO hands total)."
)

MING_CHARACTER_DESC = (
    "Ming is Mei's playful, energetic 5-year-old younger brother (slightly shorter than Mei) with warm fair-peach "
    "skin, rosy watercolor cheeks, big cheerful round dark-brown eyes with white catchlights, and short neat "
    "jet-black hair with a cute soft crown tuft. He wears a jade-green and sky-blue sleeveless tangzhuang vest "
    "with golden cloud embroidery and golden frog-button closures over a crisp cream short-sleeved cotton shirt, "
    "navy-blue rolled-cuff shorts, white ankle socks, and teal-and-cream canvas slip-on shoes. Ming has EXACTLY "
    "TWO normal arms and TWO hands total."
)

MING_INVARIANTS = (
    "STRICT MING CHARACTER INVARIANTS: Ming is ALWAYS a 5-year-old Chinese boy (slightly shorter than his sister "
    "Mei) with short neat jet-black hair, round dark-brown eyes, rosy cheeks, wearing a jade-green and sky-blue "
    "embroidered tangzhuang vest with golden frog buttons over a cream short-sleeved shirt, navy-blue shorts, "
    "white socks, and teal-and-cream shoes (NEVER a girl, NEVER wearing a red dress, NEVER extra hands — Ming "
    "has EXACTLY TWO arms and TWO hands total)."
)

BAOBAO_CHARACTER_DESC = (
    "Baobao is a tiny, magical, chubby red-panda spirit guide (about knee-high to Mei and Ming) with plush "
    "cinnamon-rust and warm amber-orange watercolor fur, soft cream facial markings around his large sparkling "
    "dark-brown eyes and fluffy rounded cream-tipped ears, rosy cheek blush ovals, a small cute dark-brown "
    "button nose, a gentle happy smile, and a big fluffy ringed tail with alternating cinnamon-red and warm "
    "cream stripes. Around his neck he wears a small glowing celadon-jade cloud pendant on a braided red silk "
    "cord that radiates a gentle golden-amber warmth."
)

BAOBAO_INVARIANTS = (
    "STRICT BAOBAO CHARACTER INVARIANTS: Baobao is ALWAYS a small, chubby cinnamon-rust and amber-orange red "
    "panda (knee-high to Mei and Ming) with cream facial markings, fluffy cream-tipped ears, a big fluffy "
    "striped ringed tail, and a glowing green celadon-jade cloud pendant on a red cord around his neck "
    "(NEVER a black-and-white giant panda, NEVER a metallic robot)."
)

HERITAGE_CHARACTER = f"{MEI_CHARACTER_DESC} {MING_CHARACTER_DESC} {BAOBAO_CHARACTER_DESC}"

HERITAGE_CHARACTER_INVARIANTS = (
    "STRICT CHINESE HERITAGE CHARACTER INVARIANTS (MUST MATCH HERITAGE REFERENCE SHEET): "
    f"{MEI_INVARIANTS} {MING_INVARIANTS} {BAOBAO_INVARIANTS} "
    "NEVER include Lily or any blue robot (Robo) in this series. NEVER duplicate or clone Mei, Ming, or Baobao "
    "twice in the same scene (show at most ONE Mei, ONE Ming, and ONE Baobao per page)."
)

HERITAGE_STYLE = (
    "Premium children's picture book illustration in a warm whimsical watercolor and colored-pencil style "
    "harmoniously infused with classical Chinese shuimo (ink-and-wash) landscape brushwork, soft misty mountain "
    "washes, emerald bamboo groves, warm vermilion and golden lantern glow, and visible Xuan rice-paper and "
    "watercolor paper grain. Warm luminous pastel-and-mineral palette, delicate translucent washes, and gentle "
    "brown-ink outlines. Compose characters and key action in the upper two-thirds of the frame, leaving open "
    "stone courtyard, mossy path, water reflection, or soft mist wash in the bottom 30% for the story text card. "
    "Strictly textless, with no calligraphy characters, banners, signatures, seals, logos, or words inside the artwork."
)

HERITAGE_NEGATIVE_STYLE_PROMPT = (
    "ugly, deformed, extra limbs, extra hands, extra arms, four hands, three hands, missing limbs, duplicate Mei, "
    "two Meis in same scene, duplicate Ming, two Mings in same scene, duplicate Baobao, two red pandas in same scene, "
    "black and white giant panda, metallic robot, blue robot, Robo, Lily, yellow polka-dot dress, blonde hair, "
    "multiple panels, split screen, collage, photorealistic, 3D CGI render, dark moody lighting, harsh shadows, "
    "text, words, letters, Chinese characters, calligraphy writing, red artist seals, stamps, titles, banners, "
    "scrolls with visible writing, speech bubbles, signatures, watermarks, logo, typography."
)

# Solo-scene markers shared by construct_image_prompt() and StorybookGenerator.generate_page_image()
# so reference-image conditioning always matches the presence mode the prompt asked for.
SOLO_MEI_MARKER = "SOLO MEI SCENE (NO MING OR BAOBAO IN FRAME)"
SOLO_MING_MARKER = "SOLO MING SCENE (NO MEI OR BAOBAO IN FRAME)"
SOLO_BAOBAO_MARKER = "SOLO BAOBAO SCENE (NO HUMAN CHILDREN IN FRAME)"


LILY_CHARACTER_DESC = (
    "Lily is a cheerful 6-year-old girl with fair skin, soft rosy watercolor cheeks with tiny light freckles "
    "across her nose, expressive large warm-brown eyes with white catchlights, and warm chestnut-brown wavy "
    "hair with soft bangs across her forehead, styled in two high-side fluffy curly pigtail buns/puffs tied "
    "with bright red ribbon bows on the upper sides of her head plus soft wavy shoulder-length curls framing "
    "her face and neck. She wears a short puff-sleeved buttercup-yellow A-line dress with golden-mustard and "
    "white polka dots, a crisp white rounded Peter Pan collar, three small buttons down the front bodice, a "
    "sky-blue backpack with smooth, plain blue shoulder straps (NEVER hands on the straps), white ankle socks, "
    "and bright red Mary Jane strap shoes. Lily has EXACTLY TWO normal arms and TWO hands total."
)

LILY_INVARIANTS = (
    "STRICT LILY CHARACTER INVARIANTS (MUST MATCH BOOK 1 REFERENCE): "
    "Lily is ALWAYS a 6-year-old girl with warm chestnut-brown wavy hair styled in two high-side fluffy curly "
    "pigtail puffs tied with bright red ribbon bows PLUS soft wavy shoulder-length curls framing her face and "
    "soft bangs, large warm-brown eyes, rosy cheeks with tiny freckles, wearing a short puff-sleeved "
    "buttercup-yellow polka-dot A-line dress with a crisp white Peter Pan collar and three small front bodice "
    "buttons, a sky-blue backpack with smooth plain sky-blue shoulder straps, white ankle socks, and bright red "
    "Mary Jane strap shoes (NEVER braided hair, NEVER a single ponytail, NEVER missing her yellow polka-dot dress "
    "with white Peter Pan collar, NEVER extra hands or hands on her backpack straps — Lily has EXACTLY TWO arms "
    "and TWO hands total). Show ONLY ONE Lily in the scene and DO NOT include Robo or any robot in this frame."
)

ROBO_CHARACTER_DESC = (
    "Robo is a small, round spherical sky-blue / powder-blue metallic robot, featuring "
    "a single smooth round sky-blue spherical body with a horizontal equator seam line across his middle and "
    "small circular buttons on his lower belly. Robo's face is drawn DIRECTLY on his smooth sky-blue spherical "
    "body (NO separate visor cutout, NO dark black screen, NO white/cream faceplate) with two large expressive "
    "round cartoon eyes (white outer sclera rings with large deep cobalt/navy-blue irises and white catchlights), "
    "two small curved eyebrows above his eyes, soft rosy cheek blush ovals, and a simple happy curved U-shaped "
    "line smile directly between/below his eyes. Robo has a single slender top antenna stalk rising from a small "
    "dome base on the very top of his head tipped with a glowing orb ball, circular ear-like side hubs, short "
    "segmented tubular blue arms with simple 2- or 3-fingered rounded pincer hands, and two short segmented "
    "cylindrical blue legs ending in rounded dome feet."
)

ROBO_INVARIANTS = (
    "STRICT ROBO CHARACTER INVARIANTS (MUST MATCH BOOK 1 REFERENCE): "
    "Robo is ALWAYS a single round spherical sky-blue metallic robot with a horizontal equator seam line, his "
    "face drawn DIRECTLY on his blue spherical metal body (NEVER a dark black monitor screen or recessed visor "
    "cutout), two large round white-and-cobalt-blue cartoon eyes with small curved eyebrows, a simple curved "
    "line smile, rosy blush cheeks, a single slender top antenna stalk with a glowing ball tip on top of his "
    "head, circular side ear-hubs, short segmented tubular blue arms with rounded pincer hands, and short "
    "segmented cylindrical blue legs with dome feet. NO wheels, NO tracks, NO extra side antennas, NO gears, "
    "NO boxy square torso. Show ONLY ONE Robo in the scene and DO NOT include Lily or any human girl in this frame."
)


def is_heritage_genre(
    topic: str = "",
    character_ids: list[str] | None = None,
    genre: str | None = None,
    scene_or_char_desc: str = "",
) -> bool:
    """Detects whether a topic, character list, or scene belongs to the Chinese Heritage & Proverbs genre."""
    if genre and genre.lower().strip() in ("chinese_heritage", "heritage", "proverbs", "chinese_proverbs"):
        return True
    req = {c.lower().strip() for c in (character_ids or []) if c}
    if req and req.intersection({"mei", "ming", "baobao", "美美", "明明", "宝宝"}) and not req.intersection({"lily", "robo"}):
        return True
    tl = (topic or "").lower()
    for k in CHINESE_PROVERB_ROSETTA_STONE:
        if k in tl:
            return True
    if any(w in tl for w in ("proverb", "chengyu", "成语", "成語", "mei, ming", "baobao")):
        return True
    sc = (scene_or_char_desc or "").lower()
    if any(w in sc for w in ("baobao", "shuangyaji", "tangzhuang", "red-panda spirit")):
        return True
    return False


def detect_page_character_mode(
    page_scene: str,
    features_char: bool = True,
    genre: str | None = None,
    character_ids: list[str] | None = None,
) -> str:
    """Detects which character presence mode applies to a page:
    For STEM ('stem'):
      - 'none', 'lily_only', 'robo_only', 'both'
    For Chinese Heritage ('chinese_heritage'):
      - 'none': World/close-up/cutaway/panoramic page with no characters (`features_char=False`)
      - 'mei_only': Solo Mei page
      - 'ming_only': Solo Ming page
      - 'baobao_only': Solo Baobao page
      - 'both': Together page with Mei, Ming, and Baobao (or any combination of 2+ core characters)
    """
    if not features_char:
        return "none"
    scene_lower = (page_scene or "").lower()
    if is_heritage_genre(character_ids=character_ids, genre=genre, scene_or_char_desc=page_scene):
        has_mei = bool(re.search(r"\bmei\b", scene_lower))
        has_ming = bool(re.search(r"\bming\b", scene_lower))
        has_baobao = bool(re.search(r"\bbaobao\b|\bred[- ]panda\b", scene_lower))
        count = int(has_mei) + int(has_ming) + int(has_baobao)
        if count == 1:
            if has_mei:
                return "mei_only"
            if has_ming:
                return "ming_only"
            return "baobao_only"
        return "both"

    has_lily = bool(re.search(r"\blily\b", scene_lower))
    has_robo = bool(re.search(r"\brobo\b", scene_lower))
    if has_lily and not has_robo:
        return "lily_only"
    if has_robo and not has_lily:
        return "robo_only"
    return "both"


def build_character_prompt_context(
    character_ids: list[str] | None = None,
    genre: str | None = None,
) -> tuple[str, str, str]:
    """Builds combined (descriptions, strict_invariants, negative_traits) for the active genre and cast."""
    import config

    if is_heritage_genre(character_ids=character_ids, genre=genre):
        return HERITAGE_CHARACTER, HERITAGE_CHARACTER_INVARIANTS, HERITAGE_NEGATIVE_STYLE_PROMPT

    active_chars = config.get_active_characters(character_ids, genre=genre)
    guest_chars = [c for c in active_chars if not c.get("default_active", False)]
    if not guest_chars:
        return SERIES_CHARACTER, CHARACTER_INVARIANTS, NEGATIVE_STYLE_PROMPT

    desc_parts = [SERIES_CHARACTER]
    inv_parts = [CHARACTER_INVARIANTS]
    neg_parts = [NEGATIVE_STYLE_PROMPT.rstrip(".")]

    for ch in guest_chars:
        v_desc = str(ch.get("visual_description", "")).strip()
        s_inv = str(ch.get("strict_invariants", "")).strip()
        n_trait = str(ch.get("negative_traits", "")).strip().rstrip(".")
        if v_desc:
            desc_parts.append(v_desc)
        if s_inv:
            inv_parts.append(s_inv)
        if n_trait:
            neg_parts.append(n_trait)

    return (
        " ".join(desc_parts),
        " ".join(inv_parts),
        ", ".join(neg_parts) + ".",
    )


def get_stem_metaphor_context(topic: str, genre: str | None = None) -> str:
    """Finds the child-friendly metaphor for a given STEM or Chinese Heritage & Proverb topic."""
    topic_lower = topic.lower()
    if is_heritage_genre(topic=topic, genre=genre):
        for key, metaphor in CHINESE_PROVERB_ROSETTA_STONE.items():
            if key in topic_lower:
                return f"Chinese Heritage & Proverb Metaphor: '{metaphor}'"
        return (
            "Chinese Heritage & Proverb Metaphor: Weave the classical Chinese proverb (Chengyu / 成语) "
            "into a warm, relatable adventure with Mei, her younger brother Ming, and their red-panda "
            "spirit guide Baobao, connecting timeless cultural wisdom to everyday childhood kindness and perseverance."
        )
    for key, metaphor in STEM_METAPHOR_ROSETTA_STONE.items():
        if key in topic_lower:
            return f"Pedagogical Metaphor: '{metaphor}'"
    return (
        "Pedagogical Metaphor: Explain this concept through playful everyday analogies "
        "(like sharing toys, building blocks, or friendly helpers)."
    )


def get_story_generation_prompt(
    topic: str,
    age_group: str,
    language: str = "English",
    character_ids: list[str] | None = None,
    genre: str | None = None,
) -> str:
    """Returns the prompt for generating the structured 20-page storybook JSON."""
    heritage_mode = is_heritage_genre(topic=topic, character_ids=character_ids, genre=genre)
    metaphor_guide = get_stem_metaphor_context(topic, genre="chinese_heritage" if heritage_mode else genre)
    char_desc, char_invariants, _ = build_character_prompt_context(
        character_ids, genre="chinese_heritage" if heritage_mode else genre
    )

    if heritage_mode:
        return f"""
    You are an award-winning children's picture book author writing for "{SERIES_BRAND}: {HERITAGE_SERIES_NAME}".
    Write an exceptionally engaging, heartwarming, and culturally rich 20-page picture book about "{topic}" for children aged {age_group}.

    {metaphor_guide}

    CORE CAST (Chinese Heritage & Proverbs Series):
    - Mei (美美): Curious, kind-hearted 6-year-old older sister in a vermilion-red and peach-pink tangzhuang tunic with twin double-buns tied with red silk ribbons and tiny golden bell tassels.
    - Ming (明明): Playful, energetic 5-year-old younger brother in a jade-green and sky-blue embroidered tangzhuang vest over a cream shirt and navy shorts.
    - Baobao (宝宝): Their tiny, magical, chubby red-panda spirit guide wearing a glowing green celadon-jade cloud pendant on a red silk cord.

    PUBLISHING ARCHITECTURE (Exactly 20 Pages):
    - Page 1 [Front Cover]: `text` has the book title and "Written by {SERIES_AUTHOR}". `scene_description` MUST describe a 100% textless illustration of Mei, Ming, and Baobao in a scenic Chinese watercolor setting with open misty sky at top and open stone path/meadow at bottom for typography overlay (DO NOT mention banners, titles, letters, scrolls with writing, or books in `scene_description`).
    - Page 2 [Publisher Colophon]: Copyright © 2026 {SERIES_AUTHOR}. Curious Little Minds Publishing. First Edition. (Max 25 words). `features_main_character` MUST be `false` (pure classical object/environment vignette, e.g., a jade brush rest, porcelain teacup, or glowing red lantern by a bamboo window).
    - Page 3 [Dedication]: A warm, one-sentence dedication to curious little dreamers who treasure timeless wisdom and patient hearts (Max 25 words). Solo Mei scene (`features_main_character: true`, ONLY Mei in `scene_description`, do NOT mention Ming or Baobao on Page 3).
    - Pages 4–18 [15-Page Core Story Arc]:
        * Pacing: 20 to 35 words per page.
        * Tone: Warm, lyrical, soothing bedtime storytelling steeped in gentle Chinese heritage imagery (bamboo groves, mountain streams, stone bridges, paper lanterns, ink brushes) and child-friendly wonder.
        * Natural Proverb Integration: Introduce the Chinese proverb naturally in the story (including its poetic meaning in English so any child understands it immediately).
        * Interactive Lap-Reading Hook: Weave in gentle sound cues (*SHHH-SHHH!*, *TING-ALING!*, *SPLISH-SPLASH!*), physical gestures (*gently rubbing palms together*, *dotting the air with one finger*), or visual search prompts.
        * Arc: Gentle everyday impatience or curiosity (Pages 4-6) -> Magical heritage encounter & discovery of the proverb in action (Pages 7-11) -> Mei, Ming & Baobao practicing the proverb's wisdom together (Pages 12-15) -> Warm accomplishment & lantern-lit evening wind-down (Pages 16-18).
    - Page 19 [Proverb Wisdom & Heritage Corner]: Title line "Proverb Wisdom & Heritage Corner!" followed by: (1) The Chinese proverb in characters, Pinyin, and literal meaning, (2) 2 short parent-child questions about how we use this wisdom every day, and (3) 1 simple hands-on family activity. STRICT WORD BUDGET: 35 to 55 words total so the text card stays compact.
    - Page 20 [Back Cover & Closing]: `text` contains a warm 20-30 word series teaser blurb ending with "The End". `features_main_character` MUST be `false`, and `scene_description` MUST be a 100% textless wide panoramic shuimo watercolor landscape of the story world at peace under twilight lanterns and misty peaks (NO Mei, NO Ming, and NO Baobao in `scene_description`).

    VISUAL PACING REQUIREMENT (VARY CHARACTER PRESENCE ACROSS THE 20 PAGES):
    1. Together Pages (~8-9 pages total, e.g. Pages 1, 5, 6, 9, 12, 15, 18, 19): `features_main_character: true`, Mei, Ming, and Baobao appear together in `scene_description`.
    2. Solo Character Pages (~4-5 pages total, e.g. Pages 3, 4, 17): `features_main_character: true`, ONLY Mei (e.g. Page 3), OR ONLY Ming (e.g. Page 4), OR ONLY Baobao (e.g. Page 17) appears in `scene_description`—do NOT mention the other characters in `scene_description`.
    3. Pure World / Object Close-Up / Panoramic Pages (~6-7 pages total, e.g. Pages 2, 7, 8, 10, 11, 13, 14, 16, 20): `features_main_character: false`, NO human or animal characters appear in `scene_description`! Focus the camera on close-ups of the proverb's key objects (e.g., the smooth river stone and shining needle, glowing inkstone and bamboo brushes, misty mountain bridge, lotus pond, and the Page 20 panoramic landscape).

    Language Requirement:
    The book `text` MUST be written in {language}. All `scene_description` fields MUST be in English.

    Guidelines for Character Consistency and Style:
    1. {char_invariants}
    2. Scene descriptions must be detailed, action-oriented, in English, describing visual composition, camera angles, emotions, and lighting.
    3. Scene descriptions must be purely visual—NEVER include quoted phrases, book titles, Chinese calligraphy text, banners, signs, labels, red seals/stamps, author credits, watermarks, or any written words inside the illustration.

    Format output as valid JSON:
    {{
        "title": "Title of the storybook in {language}",
        "series": "{SERIES_BRAND}: {HERITAGE_SERIES_NAME}",
        "genre": "chinese_heritage",
        "author": "{SERIES_AUTHOR}",
        "main_character_description": "{char_desc}",
        "artistic_style": "{HERITAGE_STYLE}",
        "pages": [
            {{
                "page_number": 1,
                "text": "[Title]\\nWritten by {SERIES_AUTHOR}",
                "features_main_character": true,
                "scene_description": "Mei, Ming, and Baobao smiling warmly on an arched stone bridge beside a bamboo stream with open misty pastel sky above and smooth mossy stones below."
            }},
            {{
                "page_number": 2,
                "text": "Curious Little Minds Publishing\\nText and illustrations copyright © 2026 Enrique Chan. All rights reserved.",
                "features_main_character": false,
                "scene_description": "A serene minimalist watercolor vignette of a celadon porcelain teacup and a bamboo calligraphy brush resting on a smooth river stone beside pink peach blossoms."
            }},
            {{
                "page_number": 3,
                "text": "Dedicated to curious little hearts everywhere who discover big wisdom in every small, patient step.",
                "features_main_character": true,
                "scene_description": "Mei sitting by a round moon-window looking out at a sunlit bamboo garden in gentle wonder."
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

    return f"""
    You are an award-winning children's picture book author writing for "{SERIES_BRAND}: {SERIES_NAME}".
    Write an exceptionally engaging, heartwarming, and imaginative 20-page picture book about "{topic}" for children aged {age_group}.

    {metaphor_guide}

    PUBLISHING ARCHITECTURE (Exactly 20 Pages):
    - Page 1 [Front Cover]: `text` has the book title and "Written by {SERIES_AUTHOR}". `scene_description` MUST describe a 100% textless illustration with open pastel sky at top and bottom for typography overlay (DO NOT mention banners, titles, letters, or books in `scene_description`).
    - Page 2 [Publisher Colophon]: Copyright © 2026 {SERIES_AUTHOR}. Curious Little Minds Publishing. First Edition. (Max 25 words). `features_main_character` MUST be `false` (pure object/environment vignette).
    - Page 3 [Dedication]: A warm, one-sentence dedication to curious little explorers (Max 25 words). Solo Lily scene (`features_main_character: true`, only Lily in `scene_description`).
    - Pages 4–18 [15-Page Core Story Arc]:
        * Pacing: 20 to 35 words per page.
        * Tone: Warm, musical, soothing bedtime tone.
        * Interactive Lap-Reading Hook: Weave in onomatopoeia sound cues (*BEEP-BEEP!*, *SWOOSH!*), physical gestures (*clapping hands*, *stretching tall*), or visual search prompts.
        * Arc: Gentle inciting curiosity (Pages 4-6) -> Playful sensory exploration (Pages 7-11) -> Cooperative emotional climax (Pages 12-15) -> Cozy resolution & wind-down (Pages 16-18).
    - Page 19 [STEM Exploration Corner]: 3 short parent-child questions and 1 simple living-room activity explaining the concept in real life. STRICT WORD BUDGET: 35 to 50 words total so the text card stays compact.
    - Page 20 [Back Cover & Closing]: `text` contains a warm 20-30 word series teaser blurb ending with "The End". `features_main_character` MUST be `false`, and `scene_description` MUST be a 100% textless wide panoramic view of the story world at peace (NO Lily and NO Robo in `scene_description`, just like Book 1 Page 20).

    BOOK 1 VISUAL PACING REQUIREMENT (DO NOT PUT BOTH LILY AND ROBO ON EVERY PAGE!):
    Just like canonical Book 1 ("Lily & Robo: Day & Night Adventures"), vary visual focus across the 20 pages so the book breathes naturally:
    1. Together Pages (~7-9 pages total, e.g. Pages 1, 5, 6, 9, 12, 15, 18, 19): `features_main_character: true`, both Lily and Robo appear together in `scene_description`.
    2. Solo Character Pages (~4-5 pages total, e.g. Pages 3, 4, 17): `features_main_character: true`, ONLY Lily (wondering, looking out the window, smiling in close-up) OR ONLY Robo (demonstrating a tool or helper) appears in `scene_description`—do NOT mention the other character in `scene_description`.
    3. Pure World / Object Close-Up / Cutaway / Panoramic Pages (~6-8 pages total, e.g. Pages 2, 7, 8, 10, 11, 13, 14, 16, 20): `features_main_character: false`, NEITHER Lily nor Robo appears in `scene_description`! Focus the camera entirely on close-ups of the magical objects, cross-section cutaways, cozy town/nature details, concept vignettes, and the Page 20 wide panoramic closing landscape.

    Language Requirement:
    The book `text` MUST be written in {language}. All `scene_description` fields MUST be in English.

    Guidelines for Character Consistency and Style:
    1. {char_invariants}
    2. Scene descriptions must be detailed, action-oriented, in English, describing visual composition, camera angles, emotions, and lighting.
    3. Scene descriptions must be purely visual—NEVER include quoted phrases, book titles, banners, signs, labels, author credits, watermarks, or any written words.

    Format output as valid JSON:
    {{
        "title": "Title of the storybook in {language}",
        "series": "{SERIES_BRAND}: {SERIES_NAME}",
        "author": "{SERIES_AUTHOR}",
        "main_character_description": "{char_desc}",
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
                "scene_description": "Lily sitting by her sunny bedroom window looking out at the morning garden in wonder."
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
        r"\b(written\s+by|illustrated\s+by|created\s+by|by\s+enrique(?:\s+chan)?|escrito\s+por|creador|author|credits|art\s+by|drawings?\s+by|draw\s+by|illustration\s+by|illustrations\s+by|artist)\b[:\s\-]*[a-zA-Z0-9\s\(\)\'\"]*",
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
    character_ids: list[str] | None = None,
    genre: str | None = None,
) -> str:
    """Constructs and sanitizes the prompt for the image generation model for a specific page.

    Supports both 'stem' ('both', 'lily_only', 'robo_only', 'none') and 'chinese_heritage'
    ('both', 'mei_only', 'ming_only', 'baobao_only', 'none') character presence modes.
    """
    heritage_mode = is_heritage_genre(
        character_ids=character_ids, genre=genre, scene_or_char_desc=f"{main_char_desc} {page_scene}"
    )
    active_genre = "chinese_heritage" if heritage_mode else genre
    reg_desc, reg_inv, reg_neg = build_character_prompt_context(character_ids, genre=active_genre)
    char_mode = detect_page_character_mode(
        page_scene, features_char=features_char, genre=active_genre, character_ids=character_ids
    )
    parts = []

    if heritage_mode:
        if char_mode == "both":
            parts.append(HERITAGE_CHARACTER.rstrip("."))
            parts.append(HERITAGE_CHARACTER_INVARIANTS.rstrip("."))
        elif char_mode == "mei_only":
            parts.append(
                f"{SOLO_MEI_MARKER}: Show ONLY Mei in this illustration; Ming and Baobao are NOT present in this scene"
            )
            parts.append(MEI_CHARACTER_DESC.rstrip("."))
            parts.append(MEI_INVARIANTS.rstrip("."))
        elif char_mode == "ming_only":
            parts.append(
                f"{SOLO_MING_MARKER}: Show ONLY Ming in this illustration; Mei and Baobao are NOT present in this scene"
            )
            parts.append(MING_CHARACTER_DESC.rstrip("."))
            parts.append(MING_INVARIANTS.rstrip("."))
        elif char_mode == "baobao_only":
            parts.append(
                f"{SOLO_BAOBAO_MARKER}: Show ONLY Baobao the small red panda in this illustration; Mei and Ming are NOT present in this scene"
            )
            parts.append(BAOBAO_CHARACTER_DESC.rstrip("."))
            parts.append(BAOBAO_INVARIANTS.rstrip("."))
        else:
            parts.append(
                "PURE WORLD & OBJECT SCENE (NO CHARACTERS): Render ONLY the classical Chinese watercolor and shuimo ink-wash environment, objects, cutaway, or panoramic view described below with NO human characters, NO children, and NO animals in the frame"
            )
    else:
        if char_mode == "both" and main_char_desc:
            if character_ids:
                char_clean = reg_desc.strip()
            elif main_char_desc.strip() == SERIES_CHARACTER:
                char_clean = main_char_desc.strip()
            else:
                char_clean = sanitize_scene_description(main_char_desc)
            if char_clean:
                parts.append(char_clean.rstrip("."))
            parts.append(reg_inv.strip().rstrip("."))
        elif char_mode == "lily_only":
            parts.append(
                "SOLO LILY SCENE (NO ROBO IN FRAME): Show ONLY Lily in this illustration; Robo is NOT present in this scene"
            )
            parts.append(LILY_CHARACTER_DESC.rstrip("."))
            parts.append(LILY_INVARIANTS.rstrip("."))
        elif char_mode == "robo_only":
            parts.append(
                "SOLO ROBO SCENE (NO LILY IN FRAME): Show ONLY Robo in this illustration; Lily is NOT present in this scene"
            )
            parts.append(ROBO_CHARACTER_DESC.rstrip("."))
            parts.append(ROBO_INVARIANTS.rstrip("."))
        else:
            parts.append(
                "PURE WORLD & OBJECT SCENE (NO CHARACTERS): Render ONLY the environment, objects, cutaway, or panoramic view described below with NO human characters, NO girls, and NO robots in the frame"
            )

    sanitized_scene = sanitize_scene_description(page_scene)
    if sanitized_scene:
        parts.append(sanitized_scene.rstrip("."))

    if style_desc:
        style_clean = (
            style_desc.strip()
            if style_desc.strip() in (SERIES_STYLE, HERITAGE_STYLE)
            else sanitize_scene_description(style_desc)
        )
        if style_clean:
            parts.append(style_clean.rstrip("."))

    if include_negative and reg_neg:
        extra_neg = ""
        if heritage_mode:
            if char_mode == "none":
                extra_neg = "human characters, girl, boy, child, Mei, Ming, red panda, Baobao, robot, "
            elif char_mode == "mei_only":
                extra_neg = "boy, Ming, red panda, Baobao, robot, "
            elif char_mode == "ming_only":
                extra_neg = "girl, Mei, twin buns, red panda, Baobao, robot, "
            elif char_mode == "baobao_only":
                extra_neg = "human child, girl, boy, Mei, Ming, robot, "
        else:
            if char_mode == "none":
                extra_neg = "human characters, girl, child, Lily, robot, Robo, "
            elif char_mode == "lily_only":
                extra_neg = "robot, Robo, blue robot, metallic robot, "
            elif char_mode == "robo_only":
                extra_neg = "human girl, child, Lily, person, "
        parts.append(f"Avoid: {extra_neg}{reg_neg.strip().rstrip('.')}")

    prompt = ". ".join(p for p in parts if p)
    prompt = re.sub(r"\s+", " ", prompt)
    prompt = re.sub(r"\.+", ".", prompt)
    prompt = re.sub(r"\s+\.", ".", prompt)
    return prompt.strip(".,;: ") + "."



