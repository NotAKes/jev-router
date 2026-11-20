"""Offline tests for the shared CLI / packaged worker entrypoint."""
import unittest
from unittest.mock import MagicMock, patch

import jev_router as router


class RouterEntrypointTests(unittest.TestCase):
    def test_main_serves_only_after_explicit_call(self):
        server = MagicMock()
        with patch.dict(router.os.environ, {'JEV_ROUTER_PORT': '9876'}, clear=True), \
                patch.object(router, 'KEY', 'unit-test-key'), \
                patch.object(router, 'ThreadingHTTPServer', return_value=server) as factory, \
                patch('builtins.print'):
            router.main([])
        factory.assert_called_once_with(('127.0.0.1', 9876), router.Handler)
        server.serve_forever.assert_called_once_with()

    def test_missing_key_never_binds(self):
        with patch.object(router, 'KEY', ''), \
                patch.dict(router.os.environ, {}, clear=True), \
                patch.object(router, 'ThreadingHTTPServer') as factory:
            with self.assertRaisesRegex(SystemExit, 'POLZA_API_KEY'):
                router.main([])
        factory.assert_not_called()

    def test_bind_failure_is_reported(self):
        with patch.object(router, 'KEY', 'unit-test-key'), \
                patch.dict(router.os.environ, {}, clear=True), \
                patch.object(router, 'ThreadingHTTPServer', side_effect=OSError('address in use')):
            with self.assertRaisesRegex(SystemExit, 'Cannot start Jev Router'):
                router.main([])


if __name__ == '__main__':
    unittest.main()
