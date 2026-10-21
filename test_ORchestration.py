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
        for name in (
            "SF10.MP4", "sf2.mp4", "sf01.mp4", "SG10.mp4", "sg2.MP4",
            "MASTERF.SRT", "masterg.srt", "FR10.PNG", "fr2.png",
            "DE10.png", "de2.PNG", "Starter.png", "Finisher.png",
            "fr_00.mp4", "unrelated.mp4", "unrelated.png",
            "f01.mp4", "g01.mp4", "other_sf01.mp4", "sf01.png",
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
                *(str(self.source / name) for name in clips),
                "-s", str(self.source / subtitle),
                "-o", str(self.source / "Output" / video), "--mode", "crop",
            ])
        for command, video, slides, final in (
            (commands[2], "fr_00.mp4", ["fr2.png", "FR10.PNG"], "mergerF.mp4"),
            (commands[3], "de_00.mp4", ["de2.PNG", "DE10.png"], "MergeG.mp4"),
        ):
            self.assertEqual(command, [
                ORchestration.sys.executable,
                str(Path(ORchestration.__file__).parent / "merger.py"), "-m",
                str(self.source / "Starter.png"), str(self.source / "Output" / video),
                *(str(self.source / name) for name in slides),
                str(self.source / "Finisher.png"),
                "-o", str(self.source / "Output" / final), "--overwrite",
            ])
        for call in run.call_args_list:
            self.assertTrue(call.kwargs["check"])
            workdir = Path(call.kwargs["cwd"])
            self.assertEqual(workdir.parent, self.source / "Output")
            self.assertFalse(workdir.exists())

    @patch("ORchestration.subprocess.run")
    def test_existing_output_and_rerun(self, run):
        output = self.source / "Output"
        output.mkdir()
        for name in ("fr_00.mp4", "de_00.mp4", "mergerF.mp4", "MergeG.mp4"):
            (output / name).touch()
        ORchestration.orchestrate(self.source)
        ORchestration.orchestrate(self.source)
        self.assertEqual(run.call_count, 8)
        self.assertNotIn("Output", " ".join(run.call_args_list[0].args[0][3:6]))

    @patch("ORchestration.subprocess.run")
    def test_all_inputs_validated_before_rendering(self, run):
        for name in ("masterg.srt", "Finisher.png", "sg2.MP4", "de2.PNG"):
            with self.subTest(name=name):
                path = self.source / name
                path.unlink()
                if name == "sg2.MP4":
                    (self.source / "SG10.mp4").unlink()
                if name == "de2.PNG":
                    (self.source / "DE10.png").unlink()
                with self.assertRaises(ValueError):
                    ORchestration.orchestrate(self.source)
                run.assert_not_called()
                path.touch()
                if name == "sg2.MP4":
                    (self.source / "SG10.mp4").touch()
                if name == "de2.PNG":
                    (self.source / "DE10.png").touch()

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
