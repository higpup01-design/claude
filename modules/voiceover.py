import os
import requests
from pathlib import Path

ELEVENLABS_API_KEY = os.environ["ELEVENLABS_API_KEY"]
VOICE_ID = "pNInz6obpgDQGcFmaJgB"  # Default: "Adam" — change to your preferred voice ID

def generate_voiceover(text: str, output_path: str) -> str:
    """Generate voiceover audio from text using ElevenLabs API."""

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"

    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.3,
            "use_speaker_boost": True
        }
    }

    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(response.content)

    print(f"  Voiceover saved: {output_path}")
    return output_path

def list_voices() -> list:
    """List available ElevenLabs voices."""
    url = "https://api.elevenlabs.io/v1/voices"
    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()["voices"]
