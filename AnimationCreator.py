"""Offline 2D storybook animator. This is not an AI text-to-video model.

Install: python -m pip install Pillow imageio-ffmpeg
Try: python AnimationCreator.py --demo -o tourist.mp4 --duration 20
Story: --story "Your story here" or --story-file story.txt
Artwork: --images scene1.png scene2.png (one image per scene, or one for all)
Voice: --narrate uses installed Windows voices; --audio accepts a recording.
Paragraphs define scenes. Long paragraphs are split into short caption scenes.
Without artwork, keyword-based city, restaurant, nature, and room templates
provide simple animated illustrations, not arbitrary story-specific actions.
"""

import argparse
import bisect
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import wave

try:
    import imageio_ffmpeg
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except ImportError as error:
    raise SystemExit("Install dependencies: python -m pip install Pillow imageio-ffmpeg") from error


DEMO_STORY = (
    "Alex arrives in Berlin, carrying a suitcase and a restaurant address on his phone.\n\n"
    "He asks a passerby: Excuse me, how do I get to Linden Street?\n\n"
    "She smiles and points: Go straight ahead, then turn right. The restaurant is on your left.\n\n"
    "Alex finds the restaurant, says thank you, and sits beside the window with a menu."
)


def split_story(story):
    paragraphs = re.split(r"\n\s*\n", story.strip())
    scenes = []
    for paragraph in paragraphs:
        paragraph = re.sub(r"^\s*\d+(?:\.\d+)?\s*[-\u2013\u2014]\s*\d+(?:\.\d+)?s?\s*:\s*", "", paragraph)
        words = paragraph.split()
        for offset in range(0, len(words), 35):
            scenes.append(" ".join(words[offset:offset + 35]))
    if not scenes:
        raise ValueError("The story is empty")
    return scenes


def font_at(size):
    for name in ("C:/Windows/Fonts/segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def wrap_text(draw, text, font, width):
    lines = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = ""
        for character in word:
            if current and draw.textlength(current + character, font=font) > width:
                lines.append(current)
                current = ""
            current += character
    if current:
        lines.append(current)
    return lines


def caption_layer(text, size):
    width, height = size
    layer = Image.new("RGBA", size)
    draw = ImageDraw.Draw(layer)
    margin = max(12, width // 22)
    for font_size in range(max(12, min(width // 26, height // 18)), 7, -1):
        font = font_at(font_size)
        lines = wrap_text(draw, text, font, width - margin * 4)
        line_height = font_size + max(3, font_size // 4)
        box_height = len(lines) * line_height + margin
        if box_height < height * 0.34:
            break
    top = height - box_height - margin
    draw.rounded_rectangle((margin, top, width - margin, height - margin),
                           radius=8, fill=(15, 25, 29, 225))
    for index, line in enumerate(lines):
        draw.text((width / 2, top + margin / 2 + index * line_height), line,
                  font=font, anchor="mt", fill="white")
    return layer


def setting_for(text):
    text = text.lower()
    if any(word in text for word in ("station", "arriv", "airport", "train")):
        return "station"
    if any(word in text for word in ("menu", "meal", "sits", "dinner", "eat", "cafe")):
        return "restaurant"
    if any(word in text for word in ("forest", "park", "garden", "mountain", "river")):
        return "nature"
    if any(word in text for word in ("home", "bedroom", "house", "room")):
        return "room"
    return "street"


def illustrated_background(setting):
    canvas = Image.new("RGB", (1280, 720))
    draw = ImageDraw.Draw(canvas)
    for row in range(720):
        fraction = row / 720
        draw.line((0, row, 1280, row), fill=(int(154 + fraction * 69),
                                          int(210 + fraction * 23),
                                          int(224 + fraction * 12)))
    draw.ellipse((1030, 55, 1140, 165), fill="#fff0ac")
    draw.rounded_rectangle((140, 100, 340, 145), radius=22, fill="#f6faf5")
    draw.rounded_rectangle((530, 65, 770, 110), radius=22, fill="#f6faf5")
    if setting == "nature":
        draw.ellipse((-220, 260, 800, 920), fill="#648d6c")
        draw.ellipse((490, 240, 1510, 940), fill="#80a76b")
        draw.polygon(((560, 720), (650, 400), (720, 400), (1030, 720)), fill="#d8cfa9")
        for position in (100, 290, 1030, 1180):
            draw.rectangle((position, 300, position + 24, 530), fill="#676454")
            draw.ellipse((position - 85, 165, position + 105, 380), fill="#3c795f")
    elif setting in ("room", "restaurant"):
        draw.rectangle((0, 0, 1280, 530), fill="#e4ece3")
        draw.rectangle((0, 530, 1280, 720), fill="#939d99")
        for position in (120, 850):
            draw.rounded_rectangle((position, 90, position + 280, 390), radius=8, fill="#6b8784")
            draw.rectangle((position + 15, 105, position + 265, 375), fill="#9cd6df")
            draw.line((position + 140, 105, position + 140, 375), fill="#f8fcf6", width=9)
        draw.rectangle((560, 110, 720, 235), fill="#c36852")
        draw.ellipse((600, 135, 680, 210), fill="#f8e5ab")
        if setting == "restaurant":
            for position in (180, 880):
                draw.rectangle((position + 100, 470, position + 125, 610), fill="#536365")
                draw.ellipse((position, 430, position + 240, 490), fill="#f7f5e9")
    else:
        colors = ("#e6bfa6", "#91aaa3", "#dfd1a9", "#c7b3aa")
        for index, position in enumerate(range(-60, 1280, 290)):
            rooftop = 165 + (index % 3) * 30
            draw.rectangle((position, rooftop, position + 270, 530), fill=colors[index % 4])
            draw.rectangle((position - 5, rooftop - 12, position + 275, rooftop), fill="#657373")
            for window_x in range(position + 30, position + 245, 75):
                for window_y in range(rooftop + 40, 450, 95):
                    draw.rectangle((window_x, window_y, window_x + 40, window_y + 60), fill="#537d88")
                    draw.line((window_x + 20, window_y, window_x + 20, window_y + 60), fill="#e7e8d8", width=3)
        draw.rectangle((0, 530, 1280, 720), fill="#859295")
        draw.rectangle((0, 530, 1280, 550), fill="#d8ddd6")
        if setting == "station":
            draw.rounded_rectangle((385, 260, 890, 440), radius=60, fill="#f8f5e8")
            draw.rectangle((410, 300, 865, 400), fill="#547d85")
            draw.rectangle((380, 440, 900, 470), fill="#c35e52")
            draw.text((640, 200), "BERLIN", font=font_at(38), anchor="mm", fill="#2f4d53")
        else:
            draw.rectangle((920, 350, 1210, 400), fill="#39766b")
            draw.text((1065, 375), "RESTAURANT", font=font_at(25), anchor="mm", fill="white")
    return canvas


def draw_person(canvas, position, phase, companion=False):
    draw = ImageDraw.Draw(canvas)
    center, ground = position
    stride = math.sin(phase) * 18
    jacket = "#c45c4d" if not companion else "#397a76"
    draw.ellipse((center - 45, ground - 9, center + 55, ground + 12), fill="#647b7c")
    for offset, swing in ((-15, stride), (15, -stride)):
        draw.line((center + offset, ground - 96, center + offset + swing, ground - 10), fill="#344d5b", width=18)
        draw.line((center + offset + swing - 7, ground - 8, center + offset + swing + 17, ground - 8), fill="#263a42", width=12)
    draw.rounded_rectangle((center - 34, ground - 190, center + 34, ground - 85), radius=17, fill=jacket)
    draw.line((center - 27, ground - 170, center - 48 - stride / 2, ground - 115), fill=jacket, width=17)
    draw.line((center + 27, ground - 170, center + 50 + stride / 2, ground - 115), fill=jacket, width=17)
    draw.ellipse((center - 27, ground - 247, center + 27, ground - 187), fill="#e9b98d")
    draw.pieslice((center - 29, ground - 253, center + 29, ground - 196), 180, 355, fill="#3c4140")
    draw.ellipse((center + 9, ground - 225, center + 14, ground - 220), fill="#263a42")
    draw.arc((center + 1, ground - 219, center + 20, ground - 203), 15, 100, fill="#985b52", width=2)
    if not companion:
        draw.rounded_rectangle((center - 70, ground - 84, center - 34, ground - 10), radius=5, fill="#dda64d")
        draw.arc((center - 62, ground - 98, center - 43, ground - 73), 180, 360, fill="#695d46", width=4)


def scene_frame(background, setting, progress, size, index, supplied):
    frame = background.copy()
    if not supplied:
        walk = setting not in ("restaurant", "room")
        center = 380 + 180 * progress if walk else 540
        draw_person(frame, (center, 570), progress * math.tau * 3 if walk else 0)
        if setting == "street":
            draw_person(frame, (800, 565), 0, companion=True)
    width, height = frame.size
    zoom = 1.025 + 0.055 * progress
    crop_width, crop_height = width / zoom, height / zoom
    horizontal = progress if index % 2 == 0 else 1 - progress
    left = (width - crop_width) * horizontal
    top = (height - crop_height) * 0.4
    return ImageOps.fit(frame.crop((left, top, left + crop_width, top + crop_height)),
                        size, method=Image.Resampling.LANCZOS)


def synthesize_voice(text, output, folder):
    powershell = shutil.which("powershell")
    if os.name != "nt" or not powershell:
        raise ValueError("--narrate needs Windows PowerShell and an installed speech voice; use --audio instead")
    source = folder / "narration.txt"
    source.write_text(text, encoding="utf-8")
    environment = dict(os.environ, ANIMATION_TEXT=str(source), ANIMATION_WAV=str(output))
    script = (
        "$ErrorActionPreference='Stop'; Add-Type -AssemblyName System.Speech; "
        "$voice=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "try { $voice.SetOutputToWaveFile($env:ANIMATION_WAV); "
        "$voice.Speak([IO.File]::ReadAllText($env:ANIMATION_TEXT, [Text.Encoding]::UTF8)) } "
        "finally { $voice.Dispose() }"
    )
    result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, env=environment)
    if result.returncode or not output.is_file():
        raise RuntimeError(f"Windows narration failed: {result.stderr[-2000:]}")


def create_video(args, scenes, folder):
    audio = Path(args.audio).resolve() if args.audio else None
    duration = args.duration or 20.0
    if args.narrate:
        audio = folder / "narration.wav"
        synthesize_voice(" ".join(scenes), audio, folder)
        with wave.open(str(audio), "rb") as recording:
            spoken_duration = recording.getnframes() / recording.getframerate()
        if args.duration and spoken_duration > duration:
            raise ValueError(f"Narration needs {spoken_duration:.1f}s. Increase --duration or omit it for automatic timing")
        duration = args.duration or max(20.0, spoken_duration + 0.5)
    size = tuple(map(int, args.size.split("x")))
    total_frames = max(1, round(duration * args.fps))
    if total_frames < len(scenes) * args.fps:
        raise ValueError("Allow at least one second per scene; increase --duration or shorten the story")
    weights = [max(1, len(scene.split())) for scene in scenes]
    boundaries = [0]
    cumulative = 0
    for weight in weights:
        cumulative += weight
        boundaries.append(round(total_frames * cumulative / sum(weights)))
    backgrounds, settings, captions = [], [], []
    for index, scene in enumerate(scenes):
        setting = setting_for(scene)
        settings.append(setting)
        if args.images:
            path = args.images[0] if len(args.images) == 1 else args.images[index]
            with Image.open(path) as image:
                backgrounds.append(ImageOps.exif_transpose(image).convert("RGB"))
        else:
            backgrounds.append(illustrated_background(setting))
        captions.append(caption_layer(scene, size))
    silent = folder / "animation.mp4"
    writer = imageio_ffmpeg.write_frames(
        str(silent), size, fps=args.fps, codec="libx264", macro_block_size=1,
        ffmpeg_log_level="error", output_params=["-crf", "18", "-preset", "fast", "-movflags", "+faststart"],
    )
    writer.send(None)
    last_scene = -1
    outgoing = None
    try:
        for frame_number in range(total_frames):
            index = min(len(scenes) - 1, bisect.bisect_right(boundaries, frame_number) - 1)
            span = boundaries[index + 1] - boundaries[index]
            progress = (frame_number - boundaries[index]) / max(1, span - 1)
            frame = scene_frame(backgrounds[index], settings[index], progress, size, index, bool(args.images))
            if index != last_scene:
                print(f"Rendering scene {index + 1}/{len(scenes)}: {scenes[index][:70]}", flush=True)
                if index:
                    outgoing = scene_frame(backgrounds[index - 1], settings[index - 1], 1, size, index - 1, bool(args.images))
                last_scene = index
            fade_frames = min(round(args.fps * 0.5), span // 3)
            local_frame = frame_number - boundaries[index]
            if outgoing is not None and fade_frames and local_frame < fade_frames:
                frame = Image.blend(outgoing, frame, (local_frame + 1) / fade_frames)
            if not args.no_captions:
                frame = Image.alpha_composite(frame.convert("RGBA"), captions[index]).convert("RGB")
            writer.send(frame.tobytes())
    finally:
        writer.close()
    if not silent.is_file() or silent.stat().st_size < 100:
        raise RuntimeError("FFmpeg did not produce a video")
    if audio:
        voiced = folder / "voiced.mp4"
        command = [imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error",
                   "-y", "-i", str(silent), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
                   "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-af", "apad",
                   "-t", str(total_frames / args.fps), "-movflags", "+faststart", str(voiced)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f"Audio encoding failed: {result.stderr[-2000:]}")
        silent = voiced
    silent.replace(args.output)
    print(f"Saved {total_frames / args.fps:.2f}s video: {args.output}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    story_input = parser.add_mutually_exclusive_group(required=True)
    story_input.add_argument("--story", help="Story text; separate scenes with blank lines")
    story_input.add_argument("--story-file", type=Path, help="UTF-8 text file; blank line between scenes")
    story_input.add_argument("--demo", action="store_true", help="Use the Berlin tourist story")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output MP4")
    parser.add_argument("--images", nargs="+", type=Path, help="One image per scene, or one reused image")
    parser.add_argument("--duration", type=float, help="Total seconds; default 20, extended for generated narration")
    parser.add_argument("--size", default="1280x720", help="Even WIDTHxHEIGHT; default 1280x720")
    parser.add_argument("--fps", type=int, default=24)
    sound = parser.add_mutually_exclusive_group()
    sound.add_argument("--narrate", action="store_true", help="Offline narration with the Windows default voice")
    sound.add_argument("--audio", type=Path, help="Existing narration/music; padded or trimmed to video length")
    parser.add_argument("--no-captions", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9]+x[0-9]+", args.size):
        parser.error("--size must look like 1280x720")
    dimensions = tuple(map(int, args.size.split("x")))
    if any(value < 160 or value > 3840 or value % 2 for value in dimensions):
        parser.error("Dimensions must be even numbers between 160 and 3840")
    if not 1 <= args.fps <= 60:
        parser.error("--fps must be between 1 and 60")
    if args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0):
        parser.error("--duration must be a positive finite number")
    args.output = args.output.resolve()
    if args.output.suffix.lower() != ".mp4" or not args.output.parent.is_dir():
        parser.error("Output must be an MP4 in an existing directory")
    sources = [path.resolve() for path in [args.story_file, args.audio, *(args.images or [])] if path]
    if args.output in sources:
        parser.error("Output cannot replace an input file")
    for path in sources:
        if not path.is_file():
            parser.error(f"Input not found: {path}")
    if args.output.exists() and not args.overwrite:
        parser.error("Output exists; add --overwrite to replace it")
    story = DEMO_STORY if args.demo else args.story_file.read_text(encoding="utf-8-sig") if args.story_file else args.story
    scenes = split_story(story)
    if args.images and len(args.images) not in (1, len(scenes)):
        parser.error(f"Story has {len(scenes)} scenes; provide one image or exactly {len(scenes)} images")
    print(f"Local storybook animation: {len(scenes)} scenes. No AI model or network connection.")
    with tempfile.TemporaryDirectory(prefix="animation-", dir=str(args.output.parent)) as folder:
        create_video(args, scenes, Path(folder))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)