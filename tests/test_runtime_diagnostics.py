"""Privacy, isolation and failure semantics of the opt-in runtime probes."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import runtime_diagnostics as diagnostics


class RuntimeDiagnosticsTests(unittest.TestCase):
    def test_import_has_no_application_or_optional_dependency_side_effects(self):
        script = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location('isolated_diagnostic', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
for name in ('teamsub', 'translator', 'tkinter', 'uiautomation', 'win32crypt', 'PIL'):
    assert name not in sys.modules, name
"""
        result = subprocess.run([sys.executable, '-c', script, str(ROOT / 'runtime_diagnostics.py')],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fixture_directory_is_unicode_and_cleaned_and_report_is_private(self):
        visited = []

        def probe(directory):
            visited.append(directory)
            self.assertIn('中文 空格', directory.name)
            (directory / 'owned fixture.txt').write_text('test', encoding='utf-8')
            return True

        with tempfile.TemporaryDirectory() as owned:
            path = Path(owned) / '报告 with space.json'
            with patch.object(diagnostics, '_checks', return_value=(('isolation', probe),)):
                self.assertTrue(diagnostics.run_self_test(path))
            text = path.read_text(encoding='utf-8')
            report = json.loads(text)
            self.assertEqual(report['checks'], {'isolation': True})
            self.assertEqual(report['errors'], {})
            self.assertNotIn(str(visited[0]), text)
            self.assertNotIn(str(path), text)
            self.assertFalse(visited[0].exists())
            self.assertEqual(list(Path(owned).iterdir()), [path])

    def test_failed_probe_is_false_and_exception_message_is_never_logged(self):
        private = r'C:\Users\Private Person\secret-api-key-and-meeting'

        def fails(_directory):
            raise PermissionError(private)

        with tempfile.TemporaryDirectory() as owned:
            path = Path(owned) / 'failure.json'
            with patch.object(diagnostics, '_checks', return_value=(
                    ('failure', fails), ('continues', lambda _: True),
                    ('false_result', lambda _: False))):
                self.assertFalse(diagnostics.run_self_test(path))
            text = path.read_text(encoding='utf-8')
            report = json.loads(text)
            self.assertFalse(report['passed'])
            self.assertEqual(report['checks'], {'failure': False, 'continues': True, 'false_result': False})
            self.assertEqual(report['errors'], {'failure': 'PermissionError', 'false_result': 'CheckFailed'})
            self.assertNotIn(private, text)
            self.assertNotIn('secret-api-key', text)

    def test_requires_absolute_existing_parent_and_preserves_existing_report_on_write_failure(self):
        self.assertFalse(diagnostics.run_self_test(Path('relative-report.json')))
        with tempfile.TemporaryDirectory() as owned:
            path = Path(owned) / 'existing.json'
            path.write_text('previous report', encoding='utf-8')
            with patch.object(diagnostics, '_checks', return_value=(('ok', lambda _: True),)), \
                    patch.object(diagnostics.os, 'replace', side_effect=PermissionError('private path')):
                self.assertFalse(diagnostics.run_self_test(path))
            self.assertEqual(path.read_text(encoding='utf-8'), 'previous report')
            self.assertEqual(list(Path(owned).iterdir()), [path])

    def test_actual_store_edits_rename_and_all_export_modes_on_unicode_path(self):
        with tempfile.TemporaryDirectory() as owned:
            directory = Path(owned) / '中文 空格 兼容性'
            directory.mkdir()
            self.assertTrue(diagnostics._check_persistence(directory))
            self.assertTrue(diagnostics._check_exports(directory))
            self.assertTrue(any(directory.glob('*.sqlite3')))
            self.assertEqual(len(list(directory.glob('导出 *.*'))), 12)  # Nine exports and three SRT notes.


if __name__ == '__main__':
    unittest.main()
