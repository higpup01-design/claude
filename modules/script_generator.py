import anthropic
import json
import os

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

def generate_script(topic: str, num_scenes: int = 12) -> dict:
    """Generate a video script with narration and dynamic entity-matched image searches per scene."""

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=16000,
        messages=[{
            "role": "user",
            "content": f"""Create an engaging investigative conspiracy video script about: {topic}

Return ONLY valid JSON in this exact format:
{{
    "title": "Compelling YouTube title (max 100 chars)",
    "description": "YouTube video description (2-3 paragraphs, include keywords)",
    "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
    "outro": {{
        "narration": "Compelling 3-4 sentence like-and-subscribe call to action. Reference what was just revealed. Urge viewers to hit like, subscribe, and turn on notifications. E.g. 'The truth about [topic] has been hidden for decades. If this opened your eyes, hit that like button — it helps more people find the truth. Subscribe and ring the bell so you never miss an investigation. The next revelation might change everything you thought you knew.'",
        "outro_search_query": "Real photo search for the outro background image. Pick something atmospheric and thematic — NOT a person's name. E.g. for dead scientists: 'police crime scene tape investigation night' — for UFOs: 'night sky stars mysterious light' — for government cover-up: 'government building night dramatic'",
        "ai_image_prompt": "Cinematic dramatic scene representing the core mystery of the video topic — powerful, atmospheric, high detail, wide shot, no text, suitable as a video thumbnail"
    }},
    "scenes": [
        {{
            "narration": "Spoken narration text for this scene (5-8 sentences)",
            "image_searches": [
                {{
                    "type": "person OR place OR event",
                    "subject": "Exact name as mentioned in narration",
                    "label": "Display text for on-screen chyron",
                    "search_query": "Targeted image search query",
                    "ai_image_prompt": "Cinematic fallback image description, no text"
                }}
            ],
            "clip_searches": [
                {{
                    "archive_query": "VISUAL B-ROLL atmosphere description",
                    "pexels_query": "Short visual description"
                }}
            ]
        }}
    ]
}}

IMAGE SEARCH RULES — read carefully:
- Read each scene's narration and identify EVERY named entity: every person mentioned by name, every specific location or institution named, every specific event or incident described
- Create ONE image_search entry for EACH entity found
- Do NOT invent entities not in the narration
- REPEAT SUBJECT RULE: If a scene is long and focuses heavily on a single person (they are the main subject throughout with minimal other entities), you MAY include 2-3 entries for that same person using DIFFERENT search queries — e.g. a portrait, then a photo at their workplace, then a news article about them. Use different search angles so the viewer sees variety. Each repeat entry must have a distinct search_query targeting a different type of image.
- type "person": subject = full name EXACTLY as referred to in the narration — if the narration says "Dr. Don C. Wiley" use that; if it says "Robert Schwartz" with no title, use that. label = "Full Name | Role" under 50 chars. search_query = MUST include the full name as written in the narration (with title only if narration uses it) + first name + last name + role + year (e.g. "Dr. Don C. Wiley microbiologist Harvard 2001 portrait photograph" or "Robert Schwartz Virginia microbiologist 2001 portrait") — NEVER use last name only
- IF a person died or was killed: one of their image_search entries MUST use search_query = "death of [subject name as written] [year]" — e.g. "death of Dr. Don Wiley 2001" or "death of Robert Schwartz 2001" (use Dr. only if the narration uses Dr.)
- For repeat entries of same person, vary the angle: first entry = portrait, second = workplace/lab, third = death/news coverage
- type "place": subject = exact location name (e.g. "Fort Detrick, Maryland"), label = "Location Name" (e.g. "Fort Detrick, Maryland"), search_query = location name + relevant year + "aerial photograph" or "exterior photograph" (e.g. "Fort Detrick Maryland 2001 military base aerial photograph")
- type "event": subject = event name (e.g. "2001 Anthrax Attacks"), label = "Event Name" (e.g. "2001 Anthrax Attacks"), search_query = event name + year + "newspaper headline" OR "news photo" OR "declassified document" (e.g. "2001 anthrax attacks FBI investigation newspaper headline")
- ai_image_prompt: cinematic, dramatic, NO text or words in image
- Never mix people — if narrating about Einstein, search Einstein. If Tesla, search Tesla.

CLIP SEARCH RULES:
- Each scene must have 2-3 clip_searches describing VISUAL B-ROLL atmosphere — what a documentary camera would show during the narration (action, mood, setting)
- NEVER use a person's name or specific conspiracy title in clip_searches — describe the visual atmosphere
- Think: scientist in lab, bridge at night, government building, crowd of reporters, classified folders, city skyline — visuals that FEEL like the narration
- archive_query: broad descriptive terms that match public domain documentary footage
- pexels_query: short simple visual description for stock footage

GENERAL REQUIREMENTS:
- Write exactly {num_scenes} scenes — no more, no fewer
- Each scene covers ONE narrative subject (a key person, event, or development in the story)
- Pick the {num_scenes} most important subjects from the story and devote one scene to each
- Each scene narration 5-8 sentences, approximately 90-130 words
- Scene length varies based on importance — a pivotal figure may get more sentences; a brief event fewer
- Build suspense throughout, end with thought-provoking conclusion
- The outro narration must reference the specific topic and be a genuine emotional CTA
- The outro ai_image_prompt must describe a dramatic scene related to this specific topic"""
        }]
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    return json.loads(raw.strip())
