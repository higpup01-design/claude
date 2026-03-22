"""
Auto-assembly script — reads scene/image numbers directly from filenames.
Naming convention: scene##_img#_description.ext
Run: python assemble_custom.py
"""
import re
from pathlib import Path
from collections import defaultdict
from dotenv import load_dotenv
load_dotenv()

from modules.video_assembler import assemble_video

IMAGES_DIR = Path("output/images")
AUDIO_DIR  = Path("output/audio")
OUTPUT     = "output/videos/The_Philadelphia_Experiment_v3.mp4"
AUDIO_PREFIX = "The_Philadelphia_Experiment_scene_"

# --- Scan and group images by scene number ---
scene_map = defaultdict(list)

for f in IMAGES_DIR.iterdir():
    m = re.match(r"scene(\d+)_img(\d+)([a-z]?)", f.name, re.IGNORECASE)
    if m:
        scene_num = int(m.group(1))
        img_num   = int(m.group(2))
        img_sub   = m.group(3).lower()  # '', 'a', 'b', 'c' etc.
        scene_map[scene_num].append((img_num, img_sub, f))

# Sort scenes and images within each scene
sorted_scenes = sorted(scene_map.items())

print("\nImage mapping detected:")
for scene_num, imgs in sorted_scenes:
    imgs_sorted = sorted(imgs, key=lambda x: (x[0], x[1]))  # sort by num then letter
    print(f"  Scene {scene_num:02d}: {[f.name for _, _, f in imgs_sorted]}")

# --- Map scene numbers to audio files (scene01 → scene_00.mp3, etc.) ---
scenes_data = []
for scene_num, imgs in sorted_scenes:
    audio_index = scene_num - 1
    audio_path  = AUDIO_DIR / f"{AUDIO_PREFIX}{audio_index:02d}.mp3"

    if not audio_path.exists():
        print(f"  WARNING: Audio not found for scene {scene_num}: {audio_path}")
        continue

    imgs_sorted = sorted(imgs, key=lambda x: (x[0], x[1]))
    scenes_data.append({
        "images": [str(f.resolve()) for _, _, f in imgs_sorted],
        "audio":  str(audio_path.resolve())
    })

print(f"\nAssembling {len(scenes_data)} scenes...\n")
assemble_video(scenes_data, OUTPUT, "The Philadelphia Experiment")
print(f"\nDone! Video saved to: {str(Path(OUTPUT).resolve())}")
