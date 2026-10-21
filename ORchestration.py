"""Run both language pipelines using files in the current working directory.

Usage: python ORchestration.py
Requires sf*.mp4, sg*.mp4, masterf.srt, masterg.srt, fr*.png, de*.png,
Starter.png, and Finisher.png (case insensitive). Names are naturally sorted.
All generated videos are saved in Output and replaced on subsequent runs.
"""

from pathlib import Path
import re
import subprocess
import sys
import tempfile


def natural_key(path: Path) -> tuple:
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part)
        for part in re.split(r"([0-9]+)", path.name.casefold())
    ), path.name


def required_file(files: list[Path], name: str) -> Path:
    matches = [path for path in files if path.name.casefold() == name.casefold()]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {name}; found {len(matches)}")
    return matches[0]


def orchestrate(source: Path) -> None:
    source = source.resolve()
    scripts = Path(__file__).resolve().parent
    subtitler = scripts / "subtitler.py"
    merger = scripts / "merger.py"
    for script in (subtitler, merger):
        if not script.is_file():
            raise FileNotFoundError(f"Required script does not exist: {script}")

    files = [path for path in source.iterdir() if path.is_file()]
    starter = required_file(files, "Starter.png")
    finisher = required_file(files, "Finisher.png")
    output = source / "Output"
    jobs = []
    for prefix, language, subtitle_name, final_name in (
        ("sf", "fr", "masterf.srt", "mergerF.mp4"),
        ("sg", "de", "masterg.srt", "MergeG.mp4"),
    ):
        clips = sorted(
            (
                path for path in files
                if path.suffix.casefold() == ".mp4"
                and path.name.casefold().startswith(prefix)
            ),
            key=natural_key,
        )
        images = sorted(
            (
                path for path in files
                if path.suffix.casefold() == ".png"
                and path.name.casefold().startswith(language)
            ),
            key=natural_key,
        )
        if not clips:
            raise ValueError(f"No {prefix}*.mp4 clips found in {source}")
        if not images:
            raise ValueError(f"No {language}*.png slides found in {source}")
        subtitle = required_file(files, subtitle_name)
        jobs.append((clips, subtitle, images, output / f"{language}_00.mp4",
                     output / final_name))

    output.mkdir(exist_ok=True)
    # subtitler.py uses fixed temporary filenames; isolate them for this run.
    with tempfile.TemporaryDirectory(prefix="orchestration-", dir=output) as workdir:
        for clips, subtitle, images, video, final in jobs:
            command = [
                sys.executable, str(subtitler), "-m",
                *(str(path) for path in clips),
                "-s", str(subtitle), "-o", str(video), "--mode", "crop",
            ]
            print(f"Creating {video.name}...", flush=True)
            subprocess.run(command, cwd=workdir, check=True)

        for clips, subtitle, images, video, final in jobs:
            command = [
                sys.executable, str(merger), "-m", str(starter), str(video),
                *(str(path) for path in images), str(finisher),
                "-o", str(final), "--overwrite",
            ]
            print(f"Creating {final.name}...", flush=True)
            subprocess.run(command, cwd=workdir, check=True)

    print(f"Done! Videos saved in: {output}", flush=True)


def main() -> int:
    try:
        orchestrate(Path.cwd())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
