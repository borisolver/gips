import fcntl
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

import watchdog as w

NOW = 1793973600.0
OLD = '2026-11-06T13:00:00Z'


class Checks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.responses = {w.FEED: {'checked_at': OLD},
                          w.SNAPSHOT: {'last_successful_sync': OLD},
                          w.RUNS: {'workflow_runs': [{'status': 'completed'}]}}

    def test_stale_idle_requests_once_then_cools_down(self):
        with patch.object(w, 'fetch', side_effect=self.responses.__getitem__) as fetch, \
                patch.object(w, 'trigger') as trigger:
            self.assertEqual(w.check(self.state, NOW), 'refresh requested')
            self.assertEqual(w.check(self.state, NOW + 299), 'cooldown')
            trigger.assert_called_once_with(self.state)
            self.assertEqual(fetch.call_count, 3)
            self.assertEqual(w.check(self.state, NOW + 300), 'refresh requested')
            self.assertEqual(trigger.call_count, 2)

    def test_either_fresh_snapshot_prevents_trigger(self):
        for url, field in ((w.FEED, 'checked_at'), (w.SNAPSHOT, 'last_successful_sync')):
            with self.subTest(url=url):
                self.responses[url][field] = '2026-11-06T13:59:00Z'
                with patch.object(w, 'fetch', side_effect=self.responses.__getitem__), \
                        patch.object(w, 'trigger') as trigger:
                    w.check(self.state, NOW)
                    trigger.assert_not_called()
                self.responses[url][field] = OLD

    def test_active_workflow_prevents_trigger(self):
        self.responses[w.RUNS]['workflow_runs'] = [{'status': 'queued'}]
        with patch.object(w, 'fetch', side_effect=self.responses.__getitem__), \
                patch.object(w, 'trigger') as trigger:
            self.assertEqual(w.check(self.state, NOW), 'workflow active')
            trigger.assert_not_called()
        self.assertFalse((self.state / 'last-attempt').exists())

    def test_invalid_future_time_and_fetch_failure_do_not_trigger(self):
        with self.assertRaises(ValueError):
            w.age('2099-01-01T00:00:00Z', NOW)
        with self.assertRaises(ValueError):
            w.age('2026-11-06T13:00:00', NOW)
        with patch.object(w, 'fetch', side_effect=ValueError('unavailable')), \
                patch.object(w, 'trigger') as trigger:
            with self.assertRaises(ValueError):
                w.check(self.state, NOW)
            trigger.assert_not_called()

    def test_failed_trigger_also_cools_down(self):
        with patch.object(w, 'fetch', side_effect=self.responses.__getitem__), \
                patch.object(w, 'trigger', side_effect=RuntimeError('push rejected')):
            with self.assertRaises(RuntimeError):
                w.check(self.state, NOW)
        with patch.object(w, 'fetch') as fetch:
            self.assertEqual(w.check(self.state, NOW + 299), 'cooldown')
            fetch.assert_not_called()

    def test_duplicate_process_lock_skips_all_checks(self):
        with (self.state / 'lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(w, 'check') as check, redirect_stdout(io.StringIO()):
                self.assertEqual(w.main(self.state), 0)
                check.assert_not_called()

    def test_external_urls_and_redirects_refused(self):
        with self.assertRaises(ValueError):
            w.fetch('https://attacker.invalid/data')
        with self.assertRaises(ValueError):
            w.NoRedirect().redirect_request(None, None, 302, '', {}, 'http://127.0.0.1/')


class BareGit(unittest.TestCase):
    def test_empty_commit_preserves_tree_and_conflicting_push_is_rejected(self):
        def command(*args, input_text=None):
            return subprocess.run(['git', '-c', 'user.name=Fixture', '-c',
                                   'user.email=fixture@example.invalid', *args],
                                  input=input_text, text=True, capture_output=True,
                                  check=True).stdout.strip()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            remote, working, state = base / 'remote.git', base / 'working', base / 'state'
            state.mkdir()
            command('init', '--bare', str(remote))
            command('init', '-b', 'main', str(working))
            (working / 'index.html').write_text('unchanged public site')
            command('-C', str(working), 'add', 'index.html')
            command('-C', str(working), 'commit', '-m', 'initial')
            command('-C', str(working), 'push', str(remote), 'main')
            tree = command('--git-dir', str(remote), 'rev-parse', 'main^{tree}')
            with patch.object(w, 'REMOTE', str(remote)):
                w.trigger(state)
            self.assertEqual(command('--git-dir', str(remote), 'rev-parse', 'main^{tree}'), tree)
            self.assertFalse((state / 'index.html').exists())
            original_git, competing = w.git, []
            def racing_git(state_path, *args, **kwargs):
                if args[0] == 'push':
                    parent = command('--git-dir', str(remote), 'rev-parse', 'main')
                    other = command('--git-dir', str(remote), 'commit-tree', tree,
                                    '-p', parent, input_text='Concurrent writer\n')
                    command('--git-dir', str(remote), 'update-ref', 'refs/heads/main', other)
                    competing.append(other)
                return original_git(state_path, *args, **kwargs)
            with patch.object(w, 'REMOTE', str(remote)), patch.object(w, 'git', racing_git):
                with self.assertRaises(subprocess.CalledProcessError):
                    w.trigger(state)
            self.assertEqual(command('--git-dir', str(remote), 'rev-parse', 'main'), competing[0])


if __name__ == '__main__':
    unittest.main()
