"""Run fixture checks without starting HTTP, MCP, Codex or WhatsApp servers."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/"whatsapp-mcp-server"))

if __name__ == "__main__":
    from control.test_control import ControllerTests
    from control.test_outreach import OutreachTests
    from scripts.test_sharing import SharingTests

    loader = unittest.TestLoader()
    suite = unittest.TestSuite(ControllerTests(name) for name in loader.getTestCaseNames(ControllerTests)
                               if name != "test_http_auth_csrf_and_host_validation")
    suite.addTests(loader.loadTestsFromTestCase(OutreachTests))
    suite.addTests(loader.loadTestsFromTestCase(SharingTests))
    suite.addTests(loader.discover(str(ROOT/"whatsapp-mcp-server"), pattern="test_local.py"))
    with patch("socket.socket.bind", side_effect=AssertionError("Server listeners are forbidden in offline checks")), \
         patch("socket.socket.connect", side_effect=AssertionError("Network connections are forbidden in offline checks")):
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
