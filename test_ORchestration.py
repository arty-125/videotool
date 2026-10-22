from contextlib import redirect_stdout
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import ORchestration


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="video orchestration test ")
        self.addCleanup(self.folder.cleanup)
        self.source = Path(self.folder.name)
        self.media = self.source / "Output"
        self.media.mkdir()
        for name in ("Starter.png", "Finisher.png"):
            (self.media / name).touch()
        for name in (
            "SF10.MP4", "sf2.mp4", "sf01.mp4", "SG10.mp4", "sg2.MP4",
            "MASTERF.SRT", "masterg.srt", "FR10.PNG", "fr2.png",
            "DE10.png", "de2.PNG",
            "fr_00.mp4", "unrelated.mp4", "unrelated.png",
            "f01.mp4", "g01.mp4", "other_sf01.mp4", "sf01.png",
        ):
            (self.media / name).touch()
        for name in (
            "sf0.mp4", "sg0.mp4", "fr0.png", "de0.png", "masterf.srt",
            "Starter.png", "Finisher.png",
        ):
            (self.source / name).touch()
        self.stdout = io.StringIO()
        self.redirect = redirect_stdout(self.stdout)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    @patch("ORchestration.subprocess.run")
    def test_pipeline_commands_and_order(self, run):
        ORchestration.orchestrate(self.source)
        self.assertTrue((self.source / "Output").is_dir())
        self.assertEqual(run.call_count, 4)
        commands = [call.args[0] for call in run.call_args_list]
        for command, clips, subtitle, video in (
            (commands[0], ["sf01.mp4", "sf2.mp4", "SF10.MP4"], "MASTERF.SRT", "fr_00.mp4"),
            (commands[1], ["sg2.MP4", "SG10.mp4"], "masterg.srt", "de_00.mp4"),
        ):
            self.assertEqual(command, [
                ORchestration.sys.executable,
                str(Path(ORchestration.__file__).parent / "subtitler.py"), "-m",
                *(str(self.media / name) for name in clips),
                "-s", str(self.media / subtitle),
                "-o", str(self.source / "Output" / video), "--mode", "crop",
            ])
        for command, video, slides, final in (
            (commands[2], "fr_00.mp4", ["fr2.png", "FR10.PNG"], "mergerF.mp4"),
            (commands[3], "de_00.mp4", ["de2.PNG", "DE10.png"], "MergeG.mp4"),
        ):
            self.assertEqual(command, [
                ORchestration.sys.executable,
                str(Path(ORchestration.__file__).parent / "merger.py"), "-m",
                str(self.media / "Starter.png"), str(self.source / "Output" / video),
                *(str(self.media / name) for name in slides),
                str(self.media / "Finisher.png"),
                "-o", str(self.source / "Output" / final), "--overwrite",
            ])
        for call in run.call_args_list:
            self.assertTrue(call.kwargs["check"])
            workdir = Path(call.kwargs["cwd"])
            self.assertEqual(workdir.parent, self.source / "Output")
            self.assertFalse(workdir.exists())

    @patch("ORchestration.subprocess.run")
    def test_each_language_can_run_alone(self, run):
        for language, prefixes, subtitle, final, expected_video in (
            ("fr", ("sg", "de"), "masterg.srt", "MergeG.mp4", "fr_00.mp4"),
            ("de", ("sf", "fr"), "masterf.srt", "mergerF.mp4", "de_00.mp4"),
        ):
            with self.subTest(language=language):
                removed = []
                for path in self.media.iterdir():
                    name = path.name.casefold()
                    if name.startswith(prefixes) or name == subtitle:
                        removed.append(path)
                        path.unlink()
                existing_final = self.media / final
                existing_final.touch()
                run.reset_mock()
                ORchestration.orchestrate(self.source)
                self.assertEqual(run.call_count, 2)
                self.assertEqual(
                    Path(run.call_args_list[0].args[0][-3]).name, expected_video,
                )
                self.assertIn(f"Skipping {prefixes[1]}", self.stdout.getvalue())
                existing_final.unlink()
                for path in removed:
                    path.touch()

    @patch("ORchestration.subprocess.run")
    def test_final_outputs_alone_do_not_trigger_processing(self, run):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)
            output = source / "Output"
            output.mkdir()
            for name in ("MergedF.mp4", "MergedG.mp4", "mergerF.mp4", "MergeG.mp4"):
                (output / name).touch()
            with self.assertRaisesRegex(ValueError, "No French .* source inputs found"):
                ORchestration.orchestrate(source)
            run.assert_not_called()

    @patch("ORchestration.subprocess.run")
    def test_existing_output_and_rerun(self, run):
        output = self.source / "Output"
        for name in ("fr_00.mp4", "de_00.mp4", "mergerF.mp4", "MergeG.mp4"):
            (output / name).touch()
        ORchestration.orchestrate(self.source)
        ORchestration.orchestrate(self.source)
        self.assertEqual(run.call_count, 8)
        self.assertEqual(
            [Path(value).name for value in run.call_args_list[0].args[0][3:6]],
            ["sf01.mp4", "sf2.mp4", "SF10.MP4"],
        )

    @patch("ORchestration.subprocess.run")
    def test_all_inputs_validated_before_rendering(self, run):
        for name in ("masterg.srt", "Finisher.png", "sg2.MP4", "de2.PNG"):
            with self.subTest(name=name):
                path = self.media / name
                path.unlink()
                if name == "sg2.MP4":
                    (self.media / "SG10.mp4").unlink()
                if name == "de2.PNG":
                    (self.media / "DE10.png").unlink()
                with self.assertRaises(ValueError):
                    ORchestration.orchestrate(self.source)
                run.assert_not_called()
                path.touch()
                if name == "sg2.MP4":
                    (self.media / "SG10.mp4").touch()
                if name == "de2.PNG":
                    (self.media / "DE10.png").touch()

    @patch("ORchestration.subprocess.run")
    def test_missing_output_is_created_without_using_root_inputs(self, run):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)
            for name in ("Starter.png", "Finisher.png", "sf01.mp4"):
                (source / name).touch()
            with self.assertRaises(ValueError) as error:
                ORchestration.orchestrate(source)
            self.assertEqual(
                str(error.exception),
                f"No French (sf*) or German (sg*) source inputs found in "
                f"{source.resolve() / 'Output'}",
            )
            self.assertTrue((source / "Output").is_dir())
            run.assert_not_called()

    @patch("ORchestration.subprocess.run")
    def test_render_failure_stops_pipeline_and_cleans_temporary_files(self, run):
        for failure_index in range(4):
            with self.subTest(failure_index=failure_index):
                run.reset_mock()
                run.side_effect = [
                    *([None] * failure_index),
                    subprocess.CalledProcessError(1, "render"),
                ]
                with self.assertRaises(subprocess.CalledProcessError):
                    ORchestration.orchestrate(self.source)
                self.assertEqual(run.call_count, failure_index + 1)
                self.assertFalse(Path(run.call_args.kwargs["cwd"]).exists())

    @patch("ORchestration.orchestrate", side_effect=ValueError("Missing inputs"))
    def test_main_reports_failure(self, orchestrate):
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            self.assertEqual(ORchestration.main(), 1)
        self.assertIn("Error: Missing inputs", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
