import argparse
import os
import re
import subprocess
import imageio_ffmpeg


def srt_time_to_ass(srt_time):
    """Converts SRT timestamp (00:00:01,200) to ASS format (0:00:01.20)."""
    parts = srt_time.strip().replace(",", ".").split(":")
    h = int(parts[0])
    m = parts[1]
    s_ms = float(parts[2])
    return f"{h}:{m}:{s_ms:05.2f}"


def create_styled_ass(srt_path, ass_path, mode="pad"):
    """Converts a 2-line bilingual SRT into a dual-styled ASS subtitle file."""
    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    # In 'pad' mode, the 16:9 video sits in the center (Y: 371 to 979) of the 1080x1350 canvas.
    # Setting MarginV to 220 places the subtitles neatly in the bottom cream area below the video.
    margin_v = 220 if mode == "pad" else 120

    # ASS Header configured for 1080x1350 Instagram 4:5 Portrait
    # German = Bold White (Size 46), English = Yellow (Size 36) with dark outline
    ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1350

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,46,&H00FFFFFF,&H000000FF,&H001E2816,&H80000000,-1,0,0,0,100,100,0,0,1,3.5,1.5,2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    blocks = re.split(r"\n\s*\n", content)
    events = []

    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if len(lines) >= 3 and "-->" in lines[1]:
            times = lines[1].split("-->")
            start = srt_time_to_ass(times[0])
            end = srt_time_to_ass(times[1])

            german_line = lines[2]
            english_line = " ".join(lines[3:]) if len(lines) > 3 else ""

            if english_line:
                # Top line: Bold White (46pt) | Bottom line: Yellow (36pt)
                styled_text = (
                    f"{german_line}\\N{{\\fs36\\c&H0000FFFF&\\b0}}{english_line}"
                )
            else:
                styled_text = german_line

            events.append(
                f"Dialogue: 0,{start},{end},Default,,0,0,0,,{styled_text}"
            )

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_header + "\n".join(events))


def burn_subtitles(video_path, srt_path, output_path, mode="pad"):
    """Formats the video to 1080x1350 (4:5) and burns the styled subtitles."""
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    temp_ass = "temp_bilingual_subs.ass"

    create_styled_ass(srt_path, temp_ass, mode=mode)
    escaped_ass = temp_ass.replace("\\", "/").replace(":", "\\:")

    if mode == "pad":
        # Scales video to 1080 wide, pads height to 1350 with your slide's #FDF3E1 cream color
        vf_filter = (
            f"scale=1080:1350:force_original_aspect_ratio=decrease,"
            f"pad=1080:1350:(ow-iw)/2:(oh-ih)/2:color=#FDF3E1,"
            f"ass='{escaped_ass}'"
        )
    elif mode == "blur":
        # Fills top/bottom with a blurred background of the video itself
        vf_filter = (
            f"split[original][copy];"
            f"[copy]scale=1080:1350:force_original_aspect_ratio=increase,crop=1080:1350,boxblur=25:5[blurred];"
            f"[original]scale=1080:1350:force_original_aspect_ratio=decrease[scaled];"
            f"[blurred][scaled]overlay=(W-w)/2:(H-h)/2,"
            f"ass='{escaped_ass}'"
        )
    else:
        # Center-crops the video to fill 1080x1350 (cuts off left/right edges)
        vf_filter = (
            f"scale=1080:1350:force_original_aspect_ratio=increase,"
            f"crop=1080:1350,"
            f"ass='{escaped_ass}'"
        )

    cmd = [
        ffmpeg_exe,
        "-y",
        "-i",
        video_path,
        "-vf",
        vf_filter,
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-c:a",
        "copy",
        output_path,
    ]

    print(f"Creating 1080x1350 Instagram Portrait video ({mode} mode): {output_path}...")
    subprocess.run(cmd, check=True)

    if os.path.exists(temp_ass):
        os.remove(temp_ass)
    print(f"Done! Saved as: {output_path}")


def merge_videos(video_list, output_path):
    """Stitches multiple MP4 clips into a single video before subtitling."""
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    list_file = "temp_concat_list.txt"

    with open(list_file, "w", encoding="utf-8") as f:
        for v in video_list:
            abs_path = os.path.abspath(v).replace("\\", "/")
            f.write(f"file '{abs_path}'\n")

    cmd = [
        ffmpeg_exe,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        list_file,
        "-c",
        "copy",
        output_path,
    ]
    print("Merging clips...")
    subprocess.run(cmd, check=True)

    if os.path.exists(list_file):
        os.remove(list_file)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert MP4 to 1080x1350 Instagram Portrait and burn bilingual SRT subtitles."
    )
    parser.add_argument("-v", "--video", help="Input MP4 file (for single clip)")
    parser.add_argument(
        "-m",
        "--merge",
        nargs="+",
        help="Merge multiple MP4 files in order (e.g. 1.mp4 2.mp4 3.mp4 4.mp4)",
    )
    parser.add_argument("-s", "--srt", required=True, help="Input SRT file")
    parser.add_argument("-o", "--output", required=True, help="Output MP4 file")
    parser.add_argument(
        "--mode",
        choices=["pad", "blur", "crop"],
        default="pad",
        help="'pad' (cream background matching slide1.png), 'blur' (blurred video background), or 'crop' (zoom to fill)",
    )

    args = parser.parse_args()

    if args.merge:
        temp_merged = "temp_merged_video.mp4"
        merge_videos(args.merge, temp_merged)
        burn_subtitles(temp_merged, args.srt, args.output, mode=args.mode)
        if os.path.exists(temp_merged):
            os.remove(temp_merged)
    elif args.video:
        burn_subtitles(args.video, args.srt, args.output, mode=args.mode)
    else:
        print("Error: Please provide either --video or --merge.")