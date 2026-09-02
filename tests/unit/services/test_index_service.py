from __future__ import annotations

from unittest.mock import Mock, call, patch

from app.services.index_service import IndexService, build_parser, main
from tests.base.test_case import PapaGuiTestCase


class IndexServiceTests(PapaGuiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.source = self.temp_path / "source"
        self.source.mkdir()
        self.data = self.temp_path / "data"

    def test_run_once_coordinates_catalog_then_content(self):
        catalog = Mock()
        catalog.run.return_value = 0
        content = Mock()
        content.run.return_value = 0
        service = IndexService(self.source, self.data, interval_seconds=10)

        with (
            patch("app.services.index_service.IndexJobRunner", return_value=catalog) as catalog_type,
            patch("app.services.index_service.ContentIndexJobRunner", return_value=content) as content_type,
            patch("app.services.index_service.write_state") as write,
        ):
            result = service.run_once()

        self.assertEqual(result, 0)
        self.assertTrue(service.layout.catalog_dir.is_dir())
        self.assertTrue(catalog_type.call_args.kwargs["launch_content_job"] is False)
        catalog.run.assert_called_once_with()
        content_type.assert_called_once_with(
            service.layout,
            service.content_state_dir,
            service.customer_database_path,
        )
        content.run.assert_called_once_with()
        self.assertEqual(write.call_args.args[1]["status"], "completed")

    def test_catalog_failure_stops_before_content(self):
        catalog = Mock()
        catalog.run.return_value = 1
        service = IndexService(self.source, self.data)
        with (
            patch("app.services.index_service.IndexJobRunner", return_value=catalog),
            patch("app.services.index_service.ContentIndexJobRunner") as content_type,
        ):
            self.assertEqual(service.run_once(), 1)
        content_type.assert_not_called()

    def test_stop_after_catalog_skips_content(self):
        service = IndexService(self.source, self.data)
        catalog = Mock()
        catalog.run.side_effect = lambda: (service.stop() or 0)
        with (
            patch("app.services.index_service.IndexJobRunner", return_value=catalog),
            patch("app.services.index_service.ContentIndexJobRunner") as content_type,
        ):
            self.assertEqual(service.run_once(), 2)
        content_type.assert_not_called()

    def test_content_failure_is_returned(self):
        catalog = Mock()
        catalog.run.return_value = 0
        content = Mock()
        content.run.return_value = 1
        service = IndexService(self.source, self.data)
        with (
            patch("app.services.index_service.IndexJobRunner", return_value=catalog),
            patch("app.services.index_service.ContentIndexJobRunner", return_value=content),
            patch("app.services.index_service.write_state") as write,
        ):
            self.assertEqual(service.run_once(), 1)
        self.assertEqual(write.call_args.args[1]["status"], "error")

    def test_missing_source_and_invalid_interval_are_rejected(self):
        with self.assertRaises(ValueError):
            IndexService(self.source, self.data, interval_seconds=0)
        with self.assertRaisesRegex(ValueError, "Datenquelle"):
            IndexService(self.temp_path / "missing", self.data).run_once()
        invalid_data = self.temp_path / "file"
        invalid_data.write_text("not a directory", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Ausgabeverzeichnis"):
            IndexService(self.source, invalid_data).run_once()

    def test_stop_ends_service_loop_after_current_iteration(self):
        service = IndexService(self.source, self.data, interval_seconds=0.01)
        service.run_once = Mock(side_effect=lambda **_kwargs: (service.stop() or 0))
        self.assertEqual(service.serve(full_rebuild=True), 0)
        service.run_once.assert_called_once_with(full_rebuild=True)

    def test_already_stopped_service_does_not_start_a_run(self):
        service = IndexService(self.source, self.data)
        service.run_once = Mock()
        service.stop()
        self.assertEqual(service.serve(), 0)
        service.run_once.assert_not_called()

    def test_service_logs_failure_and_runs_incrementally_after_first_pass(self):
        service = IndexService(self.source, self.data, interval_seconds=1)
        service.run_once = Mock(side_effect=[1, 0])
        service._stopped.wait = Mock(side_effect=[False, True])
        with patch("app.services.index_service.logger.error") as error:
            self.assertEqual(service.serve(full_rebuild=True), 0)
        self.assertEqual(
            service.run_once.call_args_list,
            [
                call(full_rebuild=True),
                call(full_rebuild=False),
            ],
        )
        error.assert_called_once()

    def test_parser_supports_container_modes(self):
        arguments = build_parser().parse_args([
            "--source", str(self.source),
            "--data", str(self.data),
            "--once",
            "--full-rebuild",
            "--interval-seconds", "60",
        ])
        self.assertTrue(arguments.once)
        self.assertTrue(arguments.full_rebuild)
        self.assertEqual(arguments.interval_seconds, 60)

    def test_server_status_and_control_requests_are_coordinated(self):
        service = IndexService(self.source, self.data, interval_seconds=120)
        service.layout.ensure_directories()
        status = service.status()
        self.assertEqual(status["server"]["status"], "online")
        self.assertEqual(status["backups"], 0)

        self.assertTrue(service.control("run")["accepted"])
        self.assertTrue(service._requested_run)
        service.control("rebuild")
        self.assertTrue(service._requested_full_rebuild)
        service.control("delete")
        self.assertTrue(service._requested_delete)
        service.control("interval:300")
        self.assertEqual(service.interval_seconds, 300)
        with self.assertRaisesRegex(ValueError, "mindestens"):
            service.control("interval:10")
        with self.assertRaisesRegex(ValueError, "Unbekannte"):
            service.control("unknown")

    def test_delete_index_keeps_authoritative_customer_database(self):
        service = IndexService(self.source, self.data)
        (self.data / "index").mkdir(parents=True)
        (self.data / "index" / "old.db").write_text("old", encoding="utf-8")
        (self.data / "publications").mkdir()
        (self.data / "publications" / "current.json").write_text("{}", encoding="utf-8")
        customer_database = self.data / "customers.db"
        customer_database.write_text("customers", encoding="utf-8")

        service._delete_index_data()

        self.assertTrue(customer_database.exists())
        self.assertFalse((self.data / "publications").exists())
        self.assertTrue(service.layout.root.is_dir())

    def test_main_routes_once_and_service_modes_and_installs_signal_handlers(self):
        service = Mock()
        service.run_once.return_value = 3
        service.serve.return_value = 4
        once_argv = [
            "index_service", "--source", str(self.source), "--data", str(self.data),
            "--once", "--full-rebuild",
        ]
        with (
            patch("app.services.index_service.IndexService", return_value=service),
            patch("app.services.index_service.configure_logging"),
            patch("app.services.index_service.signal.signal") as install_signal,
            patch("sys.argv", once_argv),
        ):
            self.assertEqual(main(), 3)
            install_signal.call_args_list[0].args[1]()
        service.stop.assert_called_once_with()
        service.run_once.assert_called_once_with(full_rebuild=True)

        service.reset_mock()
        api = Mock()
        with (
            patch("app.services.index_service.IndexService", return_value=service),
            patch("app.services.index_service.IndexApiServer", return_value=api),
            patch("app.services.index_service.configure_logging"),
            patch("app.services.index_service.signal.signal"),
            patch("sys.argv", [
                "index_service", "--source", str(self.source), "--data", str(self.data),
            ]),
        ):
            self.assertEqual(main(), 4)
        service.serve.assert_called_once_with(full_rebuild=False)
        api.start.assert_called_once_with()
        api.stop.assert_called_once_with()
