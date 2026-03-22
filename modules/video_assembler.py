import os
import subprocess
from pathlib import Path

FFMPEG = r"C:\Users\higpu\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
FFPROBE = FFMPEG.replace("ffmpeg.exe", "ffprobe.exe")

def get_audio_duration(audio_path: str) -> float:
    """Get duration of audio file in seconds using ffprobe."""
    result = subprocess.run([
        FFPROBE, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        audio_path
    ], capture_output=True, text=True)
    return float(result.stdout.strip())

def make_scene_video(images: list, audio_path: str, output_path: str) -> str:
    """
    Create a scene video: slideshow of images synced to audio.
    Each image shows for equal duration across the full audio.
    """
    duration = get_audio_duration(audio_path)
    n = len(images)
    per_image = duration / n

    # Build input args: each image looped for its duration
    inputs = []
    for img in images:
        inputs += ["-loop", "1", "-t", str(per_image), "-i", img]
    inputs += ["-i", audio_path]

    # Scale each image to 1920x1080 first, then concat (images can have different sizes)
    scale = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1"
    scale_parts = "".join(f"[{i}:v]{scale}[v{i}];" for i in range(n))
    concat_inputs = "".join(f"[v{i}]" for i in range(n))
    filter_complex = f"{scale_parts}{concat_inputs}concat=n={n}:v=1:a=0[v]"

    # Use absolute paths
    abs_images = [str(Path(img).resolve()) for img in images]
    abs_audio = str(Path(audio_path).resolve())
    abs_output = str(Path(output_path).resolve())
    Path(abs_output).parent.mkdir(parents=True, exist_ok=True)

    # Rebuild inputs with absolute paths
    abs_inputs = []
    for img in abs_images:
        abs_inputs += ["-loop", "1", "-t", str(per_image), "-i", img]
    abs_inputs += ["-i", abs_audio]

    result = subprocess.run([
        FFMPEG, "-y",
        *abs_inputs,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", f"{n}:a",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        abs_output
    ], capture_output=True, text=True)

    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, FFMPEG, result.stderr)

    return output_path

def get_video_duration(video_path: str) -> float:
    """Get duration of a video file in seconds using ffprobe."""
    result = subprocess.run([
        FFPROBE, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path
    ], capture_output=True, text=True)
    return float(result.stdout.strip())


def make_scene_video_from_mixed(media_items: list, audio_path: str, output_path: str) -> str:
    """
    Create a scene video from a mix of video clips and still images, synced to audio.
    media_items: list of {"path": str, "is_video": bool}
    Each item gets an equal share of the total audio duration.
    Original audio stripped from clips; narration overlaid on final output.
    """
    audio_duration = get_audio_duration(audio_path)
    n = len(media_items)
    per_item = audio_duration / n

    abs_audio = str(Path(audio_path).resolve())
    abs_output = str(Path(output_path).resolve())
    Path(abs_output).parent.mkdir(parents=True, exist_ok=True)

    # Use a unique temp dir per scene (avoids WinError 183 file-exists collision)
    temp_dir = Path(abs_output).parent / f"mix_temp_{Path(abs_output).stem}"
    if temp_dir.exists():
        import shutil as _shutil
        _shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    scale = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1"
    font_bold = r"C\:/Windows/Fonts/arialbd.ttf"
    processed = []

    for i, item in enumerate(media_items):
        abs_path = str(Path(item["path"]).resolve())
        raw = str(temp_dir / f"item_{i:02d}_raw.mp4")

        if item["is_video"]:
            try:
                clip_dur = get_video_duration(abs_path)
            except Exception:
                clip_dur = 0

            if clip_dur >= per_item:
                result = subprocess.run([
                    FFMPEG, "-y",
                    "-ss", "0", "-t", str(per_item), "-i", abs_path,
                    "-an", "-vf", scale,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25",
                    raw
                ], capture_output=True, text=True)
            else:
                result = subprocess.run([
                    FFMPEG, "-y",
                    "-stream_loop", "-1", "-i", abs_path,
                    "-t", str(per_item),
                    "-an", "-vf", scale,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25",
                    raw
                ], capture_output=True, text=True)
        else:
            result = subprocess.run([
                FFMPEG, "-y",
                "-loop", "1", "-t", str(per_item), "-i", abs_path,
                "-vf", scale,
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25",
                raw
            ], capture_output=True, text=True)

        if result.returncode != 0 or not Path(raw).exists():
            raise subprocess.CalledProcessError(result.returncode, FFMPEG, result.stderr)

        # Apply chyron label overlay if present
        label = item.get("label", "").strip()
        out = str(temp_dir / f"item_{i:02d}.mp4")
        if label:
            # Escape special chars for FFmpeg drawtext
            esc = label.replace("\\", "\\\\").replace("'", "\u2019").replace(":", "\\:").replace("%", "%%")
            chyron = (
                f"drawbox=x=0:y=h-75:w=iw:h=75:color=black@0.65:t=fill,"
                f"drawtext=fontfile='{font_bold}':text='{esc}':"
                f"fontsize=42:fontcolor=white:x=28:y=h-55:"
                f"shadowcolor=black@0.9:shadowx=2:shadowy=2"
            )
            res = subprocess.run([
                FFMPEG, "-y", "-i", raw,
                "-vf", chyron,
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                out
            ], capture_output=True, text=True)
            Path(raw).unlink(missing_ok=True)
            if res.returncode != 0:
                # Label failed — use the unlabeled version
                import shutil
                shutil.copy2(raw if Path(raw).exists() else out, out)
        else:
            Path(raw).replace(out)  # replace() overwrites on Windows, rename() does not

        processed.append(out)

    # Concat all processed items
    if n == 1:
        concat_video = processed[0]
    else:
        concat_inputs = []
        for p in processed:
            concat_inputs += ["-i", p]
        filter_complex = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
        concat_video = str(temp_dir / "concat.mp4")
        result = subprocess.run([
            FFMPEG, "-y",
            *concat_inputs,
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            concat_video
        ], capture_output=True, text=True)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, FFMPEG, result.stderr)

    # Overlay narration
    result = subprocess.run([
        FFMPEG, "-y",
        "-i", concat_video,
        "-i", abs_audio,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        abs_output
    ], capture_output=True, text=True)

    for p in processed:
        try:
            Path(p).unlink()
        except Exception:
            pass
    try:
        Path(temp_dir / "concat.mp4").unlink(missing_ok=True)
        temp_dir.rmdir()
    except Exception:
        pass

    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, FFMPEG, result.stderr)

    return output_path


def make_scene_video_from_clips(clips: list, audio_path: str, output_path: str) -> str:
    """
    Create a scene video from video clips synced to audio narration.
    Trims or loops each clip to fill its share of audio duration.
    Strips original clip audio and overlays narration.
    """
    audio_duration = get_audio_duration(audio_path)
    n = len(clips)
    per_clip = audio_duration / n

    abs_audio = str(Path(audio_path).resolve())
    abs_output = str(Path(output_path).resolve())
    Path(abs_output).parent.mkdir(parents=True, exist_ok=True)

    temp_dir = Path(abs_output).parent / "clip_temp"
    temp_dir.mkdir(exist_ok=True)

    scale = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1"
    trimmed_clips = []

    for i, clip_path in enumerate(clips):
        abs_clip = str(Path(clip_path).resolve())
        trimmed = str(temp_dir / f"clip_{i:02d}.mp4")

        try:
            clip_dur = get_video_duration(abs_clip)
        except Exception:
            clip_dur = 0

        if clip_dur >= per_clip:
            # Trim to per_clip seconds
            result = subprocess.run([
                FFMPEG, "-y",
                "-ss", "0", "-t", str(per_clip), "-i", abs_clip,
                "-an",
                "-vf", scale,
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-r", "25",
                trimmed
            ], capture_output=True, text=True)
        else:
            # Loop to fill per_clip seconds
            result = subprocess.run([
                FFMPEG, "-y",
                "-stream_loop", "-1", "-i", abs_clip,
                "-t", str(per_clip),
                "-an",
                "-vf", scale,
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-r", "25",
                trimmed
            ], capture_output=True, text=True)

        if result.returncode != 0 or not Path(trimmed).exists():
            raise subprocess.CalledProcessError(result.returncode, FFMPEG, result.stderr)

        trimmed_clips.append(trimmed)

    # Concat all trimmed clips into one silent video
    if n == 1:
        concat_video = trimmed_clips[0]
    else:
        concat_inputs = []
        for tc in trimmed_clips:
            concat_inputs += ["-i", tc]

        filter_complex = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
        concat_video = str(temp_dir / "concat.mp4")

        result = subprocess.run([
            FFMPEG, "-y",
            *concat_inputs,
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            concat_video
        ], capture_output=True, text=True)

        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, FFMPEG, result.stderr)

    # Overlay narration audio
    result = subprocess.run([
        FFMPEG, "-y",
        "-i", concat_video,
        "-i", abs_audio,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        abs_output
    ], capture_output=True, text=True)

    # Clean up temp clips
    for tc in trimmed_clips:
        try:
            Path(tc).unlink()
        except Exception:
            pass
    concat_merged = temp_dir / "concat.mp4"
    if concat_merged.exists():
        concat_merged.unlink()
    try:
        temp_dir.rmdir()
    except Exception:
        pass

    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, FFMPEG, result.stderr)

    return output_path


def make_outro_scene(image_path: str, audio_path: str, output_path: str) -> str:
    """
    Create the final like/subscribe outro scene.
    Displays the AI image with a bold LIKE & SUBSCRIBE overlay for the duration of the audio.
    """
    duration = get_audio_duration(audio_path)
    abs_image = str(Path(image_path).resolve())
    abs_audio = str(Path(audio_path).resolve())
    abs_output = str(Path(output_path).resolve())
    Path(abs_output).parent.mkdir(parents=True, exist_ok=True)

    scale = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1"
    font = r"C\:/Windows/Fonts/arialbd.ttf"

    # Overlay: semi-transparent dark bar at bottom, then two lines of text
    overlay = (
        f"{scale},"
        # Dark translucent bar behind the text
        "drawbox=x=0:y=830:w=1920:h=250:color=black@0.6:t=fill,"
        # Top line: LIKE & SUBSCRIBE
        f"drawtext=fontfile='{font}':text='👍  LIKE   &   SUBSCRIBE  🔔':"
        "fontsize=90:fontcolor=white:x=(w-text_w)/2:y=860:"
        "shadowcolor=black@0.8:shadowx=4:shadowy=4,"
        # Bottom line: channel reminder
        f"drawtext=fontfile='{font}':text='Hit the bell so you never miss an investigation':"
        "fontsize=38:fontcolor=white@0.85:x=(w-text_w)/2:y=970:"
        "shadowcolor=black@0.8:shadowx=2:shadowy=2"
    )

    result = subprocess.run([
        FFMPEG, "-y",
        "-loop", "1", "-t", str(duration), "-i", abs_image,
        "-i", abs_audio,
        "-vf", overlay,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        abs_output
    ], capture_output=True, text=True)

    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, FFMPEG, result.stderr)

    return output_path


def assemble_video(scenes: list, output_path: str, title: str, outro_video: str = None) -> str:
    """
    Assemble final video from scene clips, with optional outro appended at the end.
    scenes: list of {"images": [path...], "audio": path, "media": [...] (optional)}
    outro_video: path to pre-built outro scene mp4 (like/subscribe)
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(output_path).parent / "temp"
    temp_dir.mkdir(exist_ok=True)

    scene_videos = []
    print("  Assembling scenes...")
    for i, scene in enumerate(scenes):
        scene_video = str(temp_dir / f"scene_{i:02d}.mp4")
        media = scene.get("media")
        if media:
            n_clips = sum(1 for m in media if m["is_video"])
            n_imgs = len(media) - n_clips
            try:
                make_scene_video_from_mixed(media, scene["audio"], scene_video)
                print(f"    Scene {i+1}/{len(scenes)} done ({n_clips} clips + {n_imgs} images)")
            except Exception as e:
                print(f"    Scene {i+1}/{len(scenes)} mixed assembly failed ({e}), falling back to images")
                make_scene_video(scene["images"], scene["audio"], scene_video)
                print(f"    Scene {i+1}/{len(scenes)} done ({len(scene['images'])} images [fallback])")
        else:
            make_scene_video(scene["images"], scene["audio"], scene_video)
            print(f"    Scene {i+1}/{len(scenes)} done ({len(scene['images'])} images)")
        scene_videos.append(scene_video)

    # Write concat list — include outro as last entry if provided
    all_videos = scene_videos[:]
    outro_abs = None
    if outro_video and Path(outro_video).exists():
        outro_abs = str(Path(outro_video).resolve())
        # Copy outro into temp dir so concat can use relative filenames
        outro_temp = str(temp_dir / "scene_outro.mp4")
        import shutil
        shutil.copy2(outro_abs, outro_temp)
        all_videos.append(outro_temp)
        print("  Appending outro (like & subscribe)...")

    concat_file = str((temp_dir / "concat.txt").resolve())
    with open(concat_file, "w") as f:
        for v in all_videos:
            f.write(f"file '{Path(v).name}'\n")

    print("  Concatenating scenes...")
    abs_output = str(Path(output_path).resolve())
    subprocess.run([
        FFMPEG, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", "concat.txt",
        "-c", "copy",
        abs_output
    ], check=True, capture_output=True, cwd=str(temp_dir))

    for v in all_videos:
        try:
            os.remove(v)
        except Exception:
            pass
    os.remove(concat_file)
    try:
        temp_dir.rmdir()
    except Exception:
        pass

    print(f"  Video assembled: {output_path}")
    return output_path
