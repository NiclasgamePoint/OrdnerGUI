from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from app.core.index_layout import IndexLayout
from app.gui.workers.content_job_controller import ContentJobController
from app.gui.workers.index_job_controller import IndexJobController
from tests.base.test_case import PapaGuiTestCase


class JobControllerTests(PapaGuiTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        self.layout = IndexLayout(self.temp_path / "index")
        self.layout.ensure_directories()

    def test_content_controller_start_adopt_guards_and_platform_options(self):
        controller = ContentJobController(self.layout, self.temp_path / "customers.db")
        pause = controller.state_dir / "index_job.paused"
        pause.parent.mkdir(parents=True)
        pause.touch()
        self.assertFalse(controller.start_or_adopt())
        pause.unlink()
        with (
            patch("app.gui.workers.content_job_controller.read_state", return_value={"status": "running", "pid": 4}),
            patch("app.gui.workers.content_job_controller.process_is_alive", return_value=True),
        ):
            self.assertTrue(controller.start_or_adopt())
        controller._timer.stop()
        self.assertFalse(controller.start_or_adopt())

        self.layout.catalog_path.parent.mkdir(parents=True, exist_ok=True)
        self.layout.catalog_path.touch()
        stream = Mock()
        process = Mock()
        for platform_name in ("linux", "win32"):
            with (
                patch("app.gui.workers.content_job_controller.read_state", return_value={}),
                patch("app.gui.workers.content_job_controller.sys.platform", platform_name),
                patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", 1, create=True),
                patch.object(subprocess, "CREATE_NO_WINDOW", 2, create=True),
                patch("app.gui.workers.content_job_controller.open_content_process_log", return_value=stream),
                patch("app.gui.workers.content_job_controller.subprocess.Popen", return_value=process) as popen,
            ):
                self.assertTrue(controller.start_or_adopt())
            options = popen.call_args.kwargs
            self.assertIn("creationflags" if platform_name == "win32" else "start_new_session", options)
        controller._timer.stop()
        self.assertEqual(stream.close.call_count, 2)

    def test_content_controller_poll_lifecycle_cancel_pause_resume_and_activity(self):
        controller = ContentJobController(self.layout, self.temp_path / "customers.db")
        progress, finished = [], []
        controller.progress.connect(progress.append)
        controller.finished.connect(finished.append)
        running = {"status": "running", "pid": 3, "updated_at": "one"}
        with (
            patch("app.gui.workers.content_job_controller.read_state", return_value=running),
            patch("app.gui.workers.content_job_controller.process_is_alive", return_value=False),
            patch("app.gui.workers.content_job_controller.write_state") as write,
        ):
            controller.poll()
        self.assertEqual(progress[-1]["status"], "error")
        write.assert_called_once()
        self.assertEqual(finished[-1]["status"], "error")

        completed = {"status": "completed", "updated_at": "two"}
        with patch("app.gui.workers.content_job_controller.read_state", return_value=completed):
            controller.poll()
            controller.poll()
        self.assertEqual(len([item for item in progress if item.get("updated_at") == "two"]), 1)
        with (
            patch("app.gui.workers.content_job_controller.read_state", return_value={"status": "running", "pid": 4}),
            patch("app.gui.workers.content_job_controller.process_is_alive", return_value=True),
        ):
            controller.poll()

        with (
            patch("app.gui.workers.content_job_controller.read_state", return_value={"status": "running", "pid": 4}),
            patch("app.gui.workers.content_job_controller.process_is_alive", return_value=True),
        ):
            self.assertTrue(controller.is_active())
            controller.cancel()
        self.assertTrue((controller.state_dir / "index_job.cancel").exists())
        (controller.state_dir / "index_job.cancel").unlink()
        with patch("app.gui.workers.content_job_controller.read_state", return_value={}):
            controller.cancel()
            self.assertFalse(controller.is_active())
        with patch.object(controller, "cancel") as cancel:
            controller.pause()
        cancel.assert_called_once_with()
        self.assertTrue((controller.state_dir / "index_job.paused").exists())
        with patch.object(controller, "start_or_adopt", return_value=True) as start:
            self.assertTrue(controller.resume())
        start.assert_called_once_with()

    def test_index_controller_adopt_start_errors_and_both_platforms(self):
        controller = IndexJobController(self.temp_path / "active.db", self.temp_path / "state")
        with patch("app.gui.workers.index_job_controller.read_state", return_value={}):
            self.assertFalse(controller.adopt_running_job())
        dead = {"status": "running", "pid": 1}
        with (
            patch("app.gui.workers.index_job_controller.read_state", return_value=dead),
            patch("app.gui.workers.index_job_controller.process_is_alive", return_value=False),
            patch("app.gui.workers.index_job_controller.write_state") as write,
        ):
            self.assertFalse(controller.adopt_running_job())
        self.assertEqual(write.call_args.args[1]["status"], "error")
        live = {"status": "running", "pid": 2, "job_id": "job"}
        with (
            patch("app.gui.workers.index_job_controller.read_state", return_value=live),
            patch("app.gui.workers.index_job_controller.process_is_alive", return_value=True),
            patch("app.gui.workers.index_job_controller.write_owner"),
        ):
            self.assertTrue(controller.adopt_running_job())
        controller._timer.stop()

        with patch.object(controller, "is_active", return_value=True):
            self.assertFalse(controller.start(Path("source"), False))
        for platform_name in ("linux", "win32"):
            process = Mock(pid=42)
            with (
                patch.object(controller, "is_active", return_value=False),
                patch("app.gui.workers.index_job_controller.sys.platform", platform_name),
                patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", 1, create=True),
                patch.object(subprocess, "CREATE_NO_WINDOW", 2, create=True),
                patch("app.gui.workers.index_job_controller.subprocess.Popen", return_value=process) as popen,
                patch("app.gui.workers.index_job_controller.read_state", return_value={}),
                patch("app.gui.workers.index_job_controller.write_state") as write,
                patch("app.gui.workers.index_job_controller.clear_control_files"),
                patch("app.gui.workers.index_job_controller.write_owner"),
            ):
                self.assertTrue(controller.start(Path("source"), True))
            self.assertIn("--full-rebuild", popen.call_args.args[0])
            self.assertIn("creationflags" if platform_name == "win32" else "start_new_session", popen.call_args.kwargs)
            write.assert_called_once()
            controller._timer.stop()

        with (
            patch.object(controller, "is_active", return_value=False),
            patch("app.gui.workers.index_job_controller.subprocess.Popen", side_effect=OSError("bad")),
            patch("app.gui.workers.index_job_controller.release_owner") as release,
            self.assertRaises(OSError),
        ):
            controller.start(Path("source"), False)
        release.assert_called_once()

        matching_process = Mock(pid=99)
        with (
            patch.object(controller, "is_active", return_value=False),
            patch("app.gui.workers.index_job_controller.uuid.uuid4", return_value=Mock(hex="fixed")),
            patch("app.gui.workers.index_job_controller.subprocess.Popen", return_value=matching_process),
            patch("app.gui.workers.index_job_controller.read_state", return_value={"job_id": "fixed"}),
            patch("app.gui.workers.index_job_controller.write_state") as write,
        ):
            self.assertTrue(controller.start(Path("source"), False))
        write.assert_not_called()
        controller._timer.stop()

    def test_index_controller_poll_all_states_and_public_actions(self):
        controller = IndexJobController(self.temp_path / "active.db", self.temp_path / "state")
        controller._job_id = "job"
        progress, ready, finished = [], [], []
        controller.progress.connect(lambda *args: progress.append(args))
        controller.ready.connect(ready.append)
        controller.finished.connect(finished.append)
        for state in ({}, {"job_id": "other"}):
            with patch("app.gui.workers.index_job_controller.read_state", return_value=state):
                controller.poll()
        running = {"job_id": "job", "status": "running", "pid": 3, "processed_count": 2, "current_path": "x"}
        with (
            patch("app.gui.workers.index_job_controller.read_state", return_value=running),
            patch("app.gui.workers.index_job_controller.process_is_alive", return_value=True),
        ):
            controller.poll()
            controller.poll()
        self.assertEqual(progress, [(2, "x")])
        with (
            patch("app.gui.workers.index_job_controller.read_state", return_value=running),
            patch("app.gui.workers.index_job_controller.process_is_alive", return_value=False),
            patch("app.gui.workers.index_job_controller.write_state") as write,
        ):
            controller.poll()
        self.assertEqual(write.call_args.args[1]["status"], "error")

        state = {"job_id": "job", "status": "ready"}
        with patch("app.gui.workers.index_job_controller.read_state", return_value=state):
            controller.poll()
            controller.poll()
        self.assertEqual(ready, [state])

        process = Mock()
        process.wait.side_effect = subprocess.TimeoutExpired([], 0.2)
        controller._process = process
        state = {"job_id": "job", "status": "completed"}
        with patch("app.gui.workers.index_job_controller.read_state", return_value=state):
            controller.poll()
            controller.poll()
        self.assertEqual(finished, [state])

        with patch("app.gui.workers.index_job_controller.activated_path", return_value=self.temp_path / "activated"):
            controller.acknowledge_activation()
        self.assertTrue((self.temp_path / "activated").exists())
        with (
            patch.object(controller, "is_active", return_value=True),
            patch("app.gui.workers.index_job_controller.cancel_path", return_value=self.temp_path / "cancel"),
        ):
            controller.cancel()
        self.assertTrue((self.temp_path / "cancel").exists())
        with patch.object(controller, "is_active", return_value=False):
            controller.cancel()
        with patch("app.gui.workers.index_job_controller.read_state", return_value={"status": "running"}) as read:
            self.assertEqual(controller.current_state(), {"status": "running"})
        read.assert_called_once()
        with patch("app.gui.workers.index_job_controller.release_owner") as release:
            controller.release_owner()
        release.assert_called_once()
