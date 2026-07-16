import sys
import time
import unittest

from app.services.document_converter import DocumentConverter


class DocumentConverterTests(unittest.TestCase):
    def test_external_process_can_be_cancelled_promptly(self):
        converter = DocumentConverter()
        started = time.monotonic()

        with self.assertRaises(InterruptedError):
            converter._run_command(
                [
                    sys.executable,
                    "-c",
                    "import time; time.sleep(5)",
                ],
                timeout=10,
                should_cancel=lambda: time.monotonic() - started > 0.05,
            )

        self.assertLess(time.monotonic() - started, 2)


if __name__ == "__main__":
    unittest.main()
