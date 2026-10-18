"""Merge videos with detected-scene transitions, followed by a photo ending.

Install: python -m pip install imageio-ffmpeg
Example: python merger.py -m clip1.mp4 clip2.mp4 photo1.jpg photo2.png -o merged.mp4
"""

import argparse
from dataclasses import dataclass
import math
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile

import imageio_ffmpeg


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
TRANSITIONS = ("fade", "dissolve", "smoothleft", "smoothright")
IMAGE_SECONDS = 3.0


@dataclass
class Media:
    path: Path
    duration: float
    size: tuple[int, int]
    has_audio: bool
    is_image: bool


@dataclass
class Segment:
    media: Media
    start: float
    duration: float


def run_ffmpeg(ffmpeg, arguments):
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostdin", *arguments],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode:
        raise RuntimeError(f"FFmpeg failed:\n{result.stderr[-6000:]}")
    return result


def inspect_media(ffmpeg, path):
    is_image = path.suffix.lower() in IMAGE_EXTENSIONS
    reader = imageio_ffmpeg.read_frames(str(path), pix_fmt="rgb24")
    try:
        metadata = next(reader)
    finally:
        reader.close()
    duration = IMAGE_SECONDS if is_image else float(metadata.get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"Cannot determine a positive duration for {path}")
    audio = False
    if not is_image:
        probe = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostdin", "-i", str(path),
             "-map", "0:a:0", "-t", "0", "-f", "null", "-"],
            capture_output=True,
        )
        audio = probe.returncode == 0
    return Media(path, duration, tuple(metadata["size"]), audio, is_image)


def detect_scenes(ffmpeg, media, threshold, minimum):
    result = run_ffmpeg(ffmpeg, [
        "-loglevel", "error", "-i", str(media.path), "-an",
        "-vf", f"setpts=PTS-STARTPTS,scale=320:-2,scdet=t={threshold},"
        "metadata=mode=print:key=lavfi.scd.time:file=-",
        "-f", "null", "-",
    ])
    boundaries = [0.0]
    for match in re.finditer(r"lavfi\.scd\.time=([0-9.]+)", result.stdout):
        timestamp = float(match.group(1))
        if timestamp - boundaries[-1] >= minimum and media.duration - timestamp >= minimum:
            boundaries.append(timestamp)
    boundaries.append(media.duration)
    return [
        Segment(media, start, end - start)
        for start, end in zip(boundaries, boundaries[1:])
    ]


def normalize_segment(ffmpeg, segment, destination, size, fps, mode, tail):
    width, height = size
    if mode == "crop":
        framing = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}"
        )
    else:
        framing = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
    inputs = []
    if segment.media.is_image:
        inputs.extend(["-loop", "1", "-framerate", str(fps)])
    else:
        inputs.extend(["-ss", f"{segment.start:.9f}"])
    inputs.extend(["-i", str(segment.media.path)])
    if not segment.media.has_audio:
        inputs.extend(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"])
    video_filter = (
        f"[0:v:0]setpts=PTS-STARTPTS,{framing},setsar=1,fps={fps},"
        f"format=yuv420p,trim=duration={segment.duration:.9f},"
        f"tpad=stop_mode=clone:stop_duration={tail:.9f},"
        f"settb=AVTB[v];"
    )
    audio_input = "0:a:0" if segment.media.has_audio else "1:a:0"
    audio_filter = (
        f"[{audio_input}]asetpts=PTS-STARTPTS,aresample=48000:async=1:first_pts=0,"
        f"aformat=sample_fmts=fltp:channel_layouts=stereo,apad,"
        f"atrim=duration={segment.duration:.9f}[a]"
    )
    run_ffmpeg(ffmpeg, [
        "-y", "-loglevel", "error", *inputs,
        "-filter_complex_threads", "1", "-filter_complex", video_filter + audio_filter,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "pcm_s16le", str(destination),
    ])


def assemble(ffmpeg, paths, segments, destination, transition, fps, seed, workdir):
    arguments = []
    for path in paths:
        arguments.extend(["-i", str(path)])
    filters = []
    for index in range(len(paths)):
        filters.append(f"[{index}:v:0]setpts=PTS-STARTPTS,fps={fps},settb=AVTB[v{index}]")
        filters.append(
            f"[{index}:a:0]atrim=duration={segments[index].duration:.9f},"
            f"asetpts=PTS-STARTPTS[a{index}]"
        )
    current = "v0"
    elapsed = segments[0].duration
    generator = random.Random(seed)
    previous = None
    for index in range(1, len(paths)):
        style = generator.choice([style for style in TRANSITIONS if style != previous])
        previous = style
        output = f"mix{index}"
        filters.append(
            f"[{current}][v{index}]xfade=transition={style}:"
            f"duration={transition:.9f}:offset={elapsed:.9f}[{output}]"
        )
        print(f"  Transition {index}: {style} at {elapsed:.2f}s", flush=True)
        current = output
        elapsed += segments[index].duration
    filters.append(
        "".join(f"[a{index}]" for index in range(len(paths)))
        + f"concat=n={len(paths)}:v=0:a=1[aout]"
    )
    graph = workdir / "transitions.txt"
    graph.write_text(";\n".join(filters), encoding="utf-8")
    run_ffmpeg(ffmpeg, [
        "-y", "-loglevel", "error", *arguments,
        "-filter_complex_threads", "1", "-filter_complex_script", str(graph),
        "-map", f"[{current}]", "-map", "[aout]", "-t", f"{elapsed:.9f}",
        "-r", str(fps), "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(destination),
    ])


def build_parser():
    parser = argparse.ArgumentParser(
        description="Detect video scenes, add smooth random transitions, and append images.",
        epilog="Required starter.png and finisher.png are found beside the first regular input "
        "unless explicitly supplied. Order: starter, videos, other images, finisher. "
        "Each image lasts 3 seconds (including fades). "
        "Video timing and narration are preserved using frozen outgoing transition handles. "
        "Temporary files require free disk space; processing re-encodes the videos.",
    )
    parser.add_argument("files", nargs="*", help="Video and image paths; quote paths containing spaces")
    parser.add_argument("-m", "--merge", nargs="+", help="Alternative way to supply input paths")
    parser.add_argument("-o", "--output", required=True, help="Output MP4 path")
    parser.add_argument("--transition", type=float, default=0.5, help="Transition seconds (default: 0.5)")
    parser.add_argument("--scene-threshold", type=float, default=10.0,
                        help="Scene change threshold, 0-100; lower detects more cuts (default: 10)")
    parser.add_argument("--min-scene", type=float, default=1.5, help="Minimum detected scene seconds (default: 1.5)")
    parser.add_argument("--no-scene-detection", action="store_true", help="Only transition between input files")
    parser.add_argument("--size", help="Output WIDTHxHEIGHT, e.g. 1080x1350; default: first video/image size")
    parser.add_argument("--fps", type=int, default=30, help="Output frame rate (default: 30)")
    parser.add_argument("--mode", choices=("pad", "crop"), default="pad", help="Fit with black bars or center-crop")
    parser.add_argument("--seed", type=int, help="Seed for repeatable random transition choices")
    parser.add_argument("--overwrite", action="store_true", help="Allow replacing an existing output")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.files and args.merge:
        parser.error("Use positional inputs or --merge, not both")
    inputs = [Path(value).resolve() for value in (args.merge or args.files)]
    output = Path(args.output).resolve()
    if not inputs:
        parser.error("Provide at least one video or image")
    bookends = {}
    content = []
    for path in inputs:
        name = path.name.lower()
        if name in ("starter.png", "finisher.png"):
            if name in bookends:
                parser.error(f"Provide {name} only once")
            bookends[name] = path
        else:
            content.append(path)
    if not content:
        parser.error("Provide at least one video or image besides starter.png and finisher.png")
    starter = bookends.get("starter.png", content[0].parent / "starter.png")
    finisher = bookends.get("finisher.png", content[0].parent / "finisher.png")
    inputs = [starter, *content, finisher]
    for path in inputs:
        if not path.is_file():
            parser.error(f"Input does not exist: {path}")
    if output in inputs:
        parser.error("Output must not be an input file")
    if output.suffix.lower() != ".mp4":
        parser.error("Output must have an .mp4 extension")
    if output.exists() and not args.overwrite:
        parser.error("Output exists; choose another path or add --overwrite")
    if not output.parent.is_dir():
        parser.error("Output directory does not exist")
    if args.fps < 1 or args.fps > 120:
        parser.error("--fps must be between 1 and 120")
    if not math.isfinite(args.transition) or not 0 < args.transition <= 1:
        parser.error("--transition must be greater than 0 and at most 1 second")
    if not math.isfinite(args.scene_threshold) or not 0 <= args.scene_threshold <= 100:
        parser.error("--scene-threshold must be between 0 and 100")
    if not math.isfinite(args.min_scene) or args.min_scene <= 0:
        parser.error("--min-scene must be positive")
    size = None
    if args.size:
        match = re.fullmatch(r"([0-9]+)x([0-9]+)", args.size)
        if not match:
            parser.error("--size must look like 1080x1350")
        size = tuple(int(value) for value in match.groups())
        if any(value < 2 or value % 2 for value in size):
            parser.error("Output dimensions must be positive even numbers")
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    media = [inspect_media(ffmpeg, path) for path in inputs]
    content_media = media[1:-1]
    videos = [item for item in content_media if not item.is_image]
    images = [item for item in content_media if item.is_image]
    if size is None:
        reference = (videos or images)[0]
        size = tuple(max(2, value - value % 2) for value in reference.size)
    media = [media[0], *videos, *images, media[-1]]
    segments = []
    for item in media:
        print(f"Analyzing: {item.path.name}", flush=True)
        if item.is_image or args.no_scene_detection:
            scenes = [Segment(item, 0.0, item.duration)]
        else:
            scenes = detect_scenes(ffmpeg, item, args.scene_threshold,
                                   max(args.min_scene, args.transition * 2))
        print(f"  {len(scenes)} {'image' if item.is_image else 'scene(s)'}", flush=True)
        segments.extend(scenes)
    for segment in segments:
        segment.duration = max(1, round(segment.duration * args.fps)) / args.fps
    transition = max(1, round(args.transition * args.fps)) / args.fps
    transition = min(transition, min(segment.duration for segment in segments))
    with tempfile.TemporaryDirectory(prefix="merger-", dir=str(output.parent)) as folder:
        workdir = Path(folder)
        paths = []
        for index, segment in enumerate(segments):
            print(f"Preparing {index + 1}/{len(segments)}: {segment.media.path.name}", flush=True)
            path = workdir / f"segment-{index:05d}.mkv"
            normalize_segment(ffmpeg, segment, path, size, args.fps, args.mode,
                              transition if index < len(segments) - 1 else 0)
            paths.append(path)
        print("Rendering transitions and merging audio...", flush=True)
        staged_output = workdir / "merged.mp4"
        assemble(ffmpeg, paths, segments, staged_output, transition, args.fps, args.seed, workdir)
        staged_output.replace(output)
    print(f"Done! Saved as: {output}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)