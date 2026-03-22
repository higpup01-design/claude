import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from modules.script_generator import generate_script
from modules.voiceover import generate_voiceover
from modules.image_generator import generate_image, search_real_image, generate_ai_image
from modules.video_assembler import assemble_video, make_outro_scene
from modules.topic_suggester import suggest_and_select_topic
from modules.clip_fetcher import fetch_clip, fetch_clip_from_query

def _find_manual_image(manual_dir: Path, subject: str) -> str:
    """
    Check manual_dir for an image whose filename contains words from subject.
    Returns the file path if found, empty string otherwise.
    Supports .jpg, .jpeg, .png, .webp
    """
    if not subject or not manual_dir.exists():
        return ""
    # Build a set of meaningful words from the subject (ignore short words)
    words = [w.lower().strip(".,") for w in subject.split() if len(w) > 2]
    if not words:
        return ""
    for f in manual_dir.iterdir():
        if f.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            continue
        fname = f.stem.lower().replace("_", " ").replace("-", " ")
        # Match if any word from subject appears in filename
        if any(w in fname for w in words):
            return str(f)
    return ""


def run_pipeline(topic: str, num_scenes: int = 12):
    print(f"\n{'='*60}")
    print(f"PIPELINE: {topic}")
    print(f"{'='*60}\n")

    # Sanitize topic for filenames
    safe_name = "".join(c if c.isalnum() or c in " -_" else "" for c in topic).strip().replace(" ", "_")[:50]
    output_dir = Path("output")

    # --- Step 1: Generate Script ---
    print("[1/4] Generating script...")
    script_path = output_dir / "scripts" / f"{safe_name}.json"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    if script_path.exists():
        print(f"  Reusing existing script: {script_path}")
        with open(script_path) as f:
            script = json.load(f)
    else:
        script = generate_script(topic, num_scenes=num_scenes)
        with open(script_path, "w") as f:
            json.dump(script, f, indent=2)
        # Clear stale cached images for this topic so old scenes don't bleed into new script
        import glob as _glob
        stale = _glob.glob(str(output_dir / "images" / f"{safe_name}_scene_*.png"))
        for f_path in stale:
            Path(f_path).unlink(missing_ok=True)
        if stale:
            print(f"  Cleared {len(stale)} stale cached images (script regenerated)")
    print(f"  Title: {script['title']}")
    print(f"  Scenes: {len(script['scenes'])}")

    # Write YouTube metadata text file
    desc_dir = output_dir / "description"
    desc_dir.mkdir(parents=True, exist_ok=True)
    desc_path = desc_dir / f"{safe_name}.txt"
    tags_str = ", ".join(script.get("tags", []))
    with open(desc_path, "w", encoding="utf-8") as f:
        f.write(f"TITLE\n{'='*60}\n{script['title']}\n\n")
        f.write(f"DESCRIPTION\n{'='*60}\n{script['description']}\n\n")
        f.write(f"TAGS\n{'='*60}\n{tags_str}\n")
    print(f"  Metadata saved: {desc_path}")

    # --- Step 2: Generate Voiceovers ---
    print("\n[2/4] Generating voiceovers...")
    audio_files = []
    for i, scene in enumerate(script["scenes"]):
        audio_path = str(output_dir / "audio" / f"{safe_name}_scene_{i:02d}.mp3")
        if Path(audio_path).exists():
            print(f"  Skipping existing: {audio_path}")
        else:
            generate_voiceover(scene["narration"], audio_path)
        audio_files.append(audio_path)

    # --- Step 3: Fetch media — clips from clip_searches (B-roll), images from image_searches (labeled) ---
    print("\n[3/4] Fetching media...")
    images_dir = output_dir / "images"
    clips_dir = output_dir / "clips" / safe_name
    manual_dir = output_dir / "images" / "manual" / safe_name
    images_dir.mkdir(parents=True, exist_ok=True)
    clips_dir.mkdir(parents=True, exist_ok=True)
    manual_dir.mkdir(parents=True, exist_ok=True)

    scene_media_lists = []
    scene_image_files = []   # fallback list for assembler

    for i, scene in enumerate(script["scenes"]):
        print(f"  Scene {i+1}/{len(script['scenes'])}:")
        scene_clips = []
        scene_images = []
        scene_imgs_fallback = []

        # --- Fetch B-roll video clips from clip_searches ---
        clip_searches = scene.get("clip_searches", [])
        for j, clip_search in enumerate(clip_searches):
            clip_path = str(clips_dir / f"scene_{i:02d}_clip{j}.mp4")
            if Path(clip_path).exists() and Path(clip_path).stat().st_size > 10000:
                print(f"    clip{j+1} [B-roll]: reusing cached")
                scene_clips.append({"path": clip_path, "is_video": True, "label": ""})
            else:
                ok = fetch_clip(clip_search, clip_path)
                if ok:
                    scene_clips.append({"path": clip_path, "is_video": True, "label": ""})
                    pq = clip_search.get("pexels_query", clip_search.get("archive_query", ""))[:40]
                    print(f"    clip{j+1} [B-roll]: fetched ({pq})")
                else:
                    print(f"    clip{j+1} [B-roll]: not found")

        # --- Fetch labeled images from image_searches (person/place/event) ---
        image_searches = scene.get("image_searches", [])
        if not image_searches:
            image_searches = [{"search_query": scene.get("search_query", topic),
                                "ai_image_prompt": scene.get("ai_image_prompt", "")}]

        for j, search in enumerate(image_searches):
            query = search["search_query"]
            ai_prompt = search.get("ai_image_prompt", "")
            subject = search.get("subject", "")
            label = search.get("label", "")
            media_type = search.get("type", "")

            img_path = str(images_dir / f"{safe_name}_scene_{i:02d}_img{j}.png")
            got_img = False

            # 0. Check manual override folder first
            manual_match = _find_manual_image(manual_dir, subject)
            if manual_match:
                import shutil as _shutil
                _shutil.copy2(manual_match, img_path)
                print(f"    img{j+1} [{media_type}]: MANUAL override ({Path(manual_match).name})")
                scene_images.append({"path": img_path, "is_video": False, "label": label})
                scene_imgs_fallback.append(img_path)
                got_img = True

            elif Path(img_path).exists() and Path(img_path).stat().st_size > 5000:
                print(f"    img{j+1} [{media_type}]: reusing cached ({subject or query[:35]})")
                scene_images.append({"path": img_path, "is_video": False, "label": label})
                scene_imgs_fallback.append(img_path)
                got_img = True
            else:
                got_img = search_real_image(query, img_path, subject=subject)
                if got_img:
                    scene_images.append({"path": img_path, "is_video": False, "label": label})
                    scene_imgs_fallback.append(img_path)
                    print(f"    img{j+1} [{media_type}]: found ({subject or query[:35]})")

                # If person search failed, retry with "death of [subject]" phrasing
                if not got_img and media_type == "person" and subject:
                    import re as _re
                    year_match = _re.search(r'\b(19\d\d|20\d\d)\b', query)
                    year_str = f" {year_match.group(1)}" if year_match else ""
                    death_query = f"death of {subject}{year_str}"
                    got_img = search_real_image(death_query, img_path, subject=subject)
                    if got_img:
                        scene_images.append({"path": img_path, "is_video": False, "label": label})
                        scene_imgs_fallback.append(img_path)
                        print(f"    img{j+1} [{media_type}]: found via death search ({subject})")

            if not got_img:
                ai_path = str(images_dir / f"{safe_name}_scene_{i:02d}_img{j}_ai.png")
                if Path(ai_path).exists() and Path(ai_path).stat().st_size > 5000:
                    print(f"    img{j+1} [{media_type}]: reusing cached AI")
                    scene_images.append({"path": ai_path, "is_video": False, "label": label})
                    scene_imgs_fallback.append(ai_path)
                else:
                    ok = generate_ai_image(ai_prompt, query, ai_path)
                    if ok:
                        scene_images.append({"path": ai_path, "is_video": False, "label": label})
                        scene_imgs_fallback.append(ai_path)
                        print(f"    img{j+1} [{media_type}]: AI generated ({subject})")

        # Interleave: clip, image, clip, image... (documentary B-roll + cutaway style)
        scene_media = []
        for k in range(max(len(scene_clips), len(scene_images))):
            if k < len(scene_clips):
                scene_media.append(scene_clips[k])
            if k < len(scene_images):
                scene_media.append(scene_images[k])

        # If only clips or only images, use whichever we have
        if not scene_media:
            scene_media = scene_clips or scene_images

        scene_media_lists.append(scene_media)
        scene_image_files.append(scene_imgs_fallback)

    # --- Step 3.5: Generate Outro ---
    outro_data = script.get("outro", {})
    outro_narration = outro_data.get("narration", (
        f"The full truth about {topic} may never be officially confirmed. "
        "If this investigation opened your eyes, hit that like button — it helps others find the truth. "
        "Subscribe and ring the bell so you never miss our next revelation. "
        "The next investigation might change everything you thought you knew."
    ))
    outro_ai_prompt = outro_data.get("ai_image_prompt", (
        f"Dramatic cinematic wide shot representing the mystery of {topic}, "
        "dark atmosphere, moody lighting, high detail, no text"
    ))

    outro_audio_path = str(output_dir / "audio" / f"{safe_name}_outro.mp3")
    outro_image_path = str(output_dir / "images" / f"{safe_name}_outro.png")
    outro_video_path = str(output_dir / "videos" / "temp" / f"{safe_name}_outro.mp4")

    print("\n[3.5/4] Generating outro...")
    if not Path(outro_audio_path).exists():
        generate_voiceover(outro_narration, outro_audio_path)
        print("  Outro voiceover: generated")
    else:
        print("  Outro voiceover: reusing cached")

    # Check for manual outro image override first
    manual_outro = _find_manual_image(manual_dir, "outro")
    if not manual_outro:
        manual_outro = _find_manual_image(manual_dir, "subscribe")
    if not manual_outro:
        manual_outro = _find_manual_image(manual_dir, "end")

    if not Path(outro_image_path).exists() or Path(outro_image_path).stat().st_size < 5000:
        if manual_outro:
            import shutil as _shutil
            _shutil.copy2(manual_outro, outro_image_path)
            print(f"  Outro image: MANUAL override ({Path(manual_outro).name})")
        else:
            # Try real web search for atmospheric scene first, then AI fallback
            outro_search = outro_data.get("outro_search_query",
                f"{topic} police investigation crime scene atmospheric")
            if not search_real_image(outro_search, outro_image_path):
                from modules.image_generator import _try_pollinations
                if not _try_pollinations(outro_ai_prompt, outro_image_path):
                    generate_ai_image(outro_ai_prompt, topic, outro_image_path)
            print("  Outro image: generated")
    else:
        print("  Outro image: reusing cached")

    # --- Step 4: Assemble Video ---
    print("\n[4/4] Assembling video...")
    scenes_data = [
        {
            "media": scene_media_lists[i],
            "images": scene_image_files[i],
            "audio": audio_files[i],
        }
        for i in range(len(script["scenes"]))
    ]
    # Build outro scene video (like/subscribe overlay)
    Path(outro_video_path).parent.mkdir(parents=True, exist_ok=True)
    if not Path(outro_video_path).exists():
        make_outro_scene(outro_image_path, outro_audio_path, outro_video_path)

    video_path = str(output_dir / "videos" / f"{safe_name}.mp4")
    assemble_video(scenes_data, video_path, script["title"], outro_video=outro_video_path)

    abs_video_path = str(Path(video_path).resolve())
    print(f"\n{'='*60}")
    print(f"DONE!")
    print(f"Video ready: {abs_video_path}")
    print(f"Title: {script['title']}")
    print(f"{'='*60}\n")

    return abs_video_path

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("topic", nargs="*", help="Video topic")
    parser.add_argument("--scenes", type=int, default=12, help="Number of scenes (default: 12, ~8-10 min)")
    args = parser.parse_args()

    topic = " ".join(args.topic) if args.topic else suggest_and_select_topic()
    run_pipeline(topic, num_scenes=args.scenes)
