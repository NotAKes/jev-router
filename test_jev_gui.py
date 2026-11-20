"""Offline, headless tests for the desktop launcher's core behavior."""
import io
import json
import os
from pathlib import Path
import queue
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

import jev_gui as gui


class Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class GuiTests(unittest.TestCase):
    def launcher(self):
        obj = gui.Launcher.__new__(gui.Launcher)
        obj.process = None
        obj.levels = {name: Var(name in ('mini', 'flash')) for name in gui.ORDER}
        obj.port = Var('8787')
        obj.key = Var('test-key')
        obj.status = Var('')
        obj.setup_status = Var('')
        obj.auto_setup = Var(True)
        obj.make_default = Var(False)
        obj.update_controls = MagicMock()
        return obj

    def test_preferences_save_only_tier_names(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'gui.json'
            gui.save_selection(path, ['mini', 'flash'])
            self.assertEqual(json.loads(path.read_text()), {'enabled_tiers': ['mini', 'flash']})
            self.assertEqual(gui.load_selection(path), {'mini', 'flash'})
            path.write_text('{invalid')
            self.assertIsNone(gui.load_selection(path))

    def test_router_command_for_packaged_and_source_modes(self):
        with patch.object(sys, 'frozen', True, create=True):
            self.assertEqual(gui.router_command(['mini']), [sys.executable, gui.WORKER_FLAG, '--disable', 'mini'])
        with patch.object(sys, 'frozen', False, create=True):
            command = gui.router_command(['mini'])
            self.assertEqual(Path(command[2]).name, 'jev_router.py')
            self.assertEqual(command[-2:], ['--disable', 'mini'])

    def test_start_passes_only_selected_levels_and_does_not_configure_early(self):
        obj = self.launcher()
        process = MagicMock()
        process.stdout = io.StringIO('')
        with patch.dict(os.environ, {'JEV_DISABLE_TIERS': 'mini', 'POLZA_API_KEY': 'other'}, clear=True), \
                patch.object(gui.subprocess, 'Popen', return_value=process) as popen, \
                patch.object(gui, 'Thread') as thread, patch.object(gui, 'configure') as configure:
            obj.start()
        args, kwargs = popen.call_args
        self.assertNotIn('JEV_DISABLE_TIERS', kwargs['env'])
        self.assertEqual(kwargs['env']['POLZA_API_KEY'], 'test-key')
        self.assertNotIn('test-key', args[0])
        disabled = args[0][-1].split(',')
        self.assertNotIn('mini', disabled)
        self.assertNotIn('flash', disabled)
        self.assertIn('opus', disabled)
        self.assertEqual(obj.state, 'starting')
        thread.return_value.start.assert_called_once()
        configure.assert_not_called()

    def test_missing_key_and_empty_selection_do_not_spawn(self):
        obj = self.launcher()
        obj.key.set('')
        with patch.dict(os.environ, {}, clear=True), patch.object(gui.subprocess, 'Popen') as popen:
            obj.start()
            popen.assert_not_called()
            obj.levels = {name: Var(False) for name in gui.ORDER}
            obj.key.set('test-key')
            obj.start()
            popen.assert_not_called()

    def test_stop_terminates_only_owned_process(self):
        obj = self.launcher()
        obj.process = MagicMock()
        obj.state = 'running'
        obj.stop()
        obj.process.terminate.assert_called_once_with()
        obj.process.kill.assert_not_called()
        self.assertEqual(obj.state, 'stopping')

    def test_ready_event_configures_opencode_after_start(self):
        obj = self.launcher()
        obj.process = MagicMock()
        obj.process.poll.return_value = None
        obj.state = 'starting'
        obj.address = 'http://127.0.0.1:8787'
        obj.events = queue.SimpleQueue()
        obj.events.put('ready')
        obj.closing = False
        obj.root = MagicMock()
        obj.setup_opencode = MagicMock()
        obj.tick()
        self.assertEqual(obj.state, 'running')
        obj.setup_opencode.assert_called_once_with()

    def test_setup_keeps_default_model_unless_selected(self):
        obj = self.launcher()
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': '/tmp/gui-test'}, clear=True), \
                patch.object(gui, 'configure', return_value=(Path('/tmp/gui-test/opencode/opencode.json'), None)) as configure:
            obj.setup_opencode()
        configure.assert_called_once_with(Path('/tmp/gui-test/opencode'), 8787, make_default=False)


    def test_update_status_and_prompt_on_tick(self):
        obj = self.launcher()
        obj.update_events = queue.SimpleQueue()
        obj.update_checking = True
        obj.update_button = MagicMock()
        obj.update_status = Var('')
        obj.closing = False
        obj.root = MagicMock()
        res = gui.jev_updates.UpdateResult('update_available', 'Доступно обновление', 'v0.2.0', 'v0.3.0',
                                           'https://github.com/uprosti-tech/uprosti-vibe/releases/tag/v0.3.0')
        obj.update_events.put(res)
        with patch.object(gui.messagebox, 'askyesno', return_value=True), patch.object(gui.webbrowser, 'open') as web_open:
            obj.tick()
            web_open.assert_called_once_with('https://github.com/uprosti-tech/uprosti-vibe/releases/tag/v0.3.0')
        self.assertFalse(obj.update_checking)
        self.assertIn('v0.3.0', obj.update_status.get())


if __name__ == '__main__':
    unittest.main()
