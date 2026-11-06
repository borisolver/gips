#!/usr/bin/env python3
"""Trigger the existing GitHub workflow only when both published snapshots stall."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
from datetime import datetime, timezone

STATE = Path('/var/lib/gips-watchdog')
FEED = 'https://borisolver.github.io/gips/feed.json'
SNAPSHOT = 'https://raw.githubusercontent.com/borisolver/gips/main/sync-state.json'
RUNS = 'https://api.github.com/repos/borisolver/gips/actions/workflows/update-gips.yml/runs?per_page=100'
REMOTE = 'git@github.com:borisolver/gips.git'
STALE_SECONDS, COOLDOWN_SECONDS = 300, 300


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Redirect refused')


def fetch(url):
    if url not in (FEED, SNAPSHOT, RUNS):
        raise ValueError('URL refused')
    request = urllib.request.Request(url, headers={
        'User-Agent': 'gips-watchdog/1.0', 'Accept': 'application/json',
        'Cache-Control': 'no-cache',
    })
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
        data = response.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError('Response too large')
    parsed = json.loads(data)
    if not isinstance(parsed, dict):
        raise ValueError('Invalid response')
    return parsed


def age(value, now):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None or stamp.timestamp() > now + 60:
        raise ValueError('Invalid timestamp')
    return max(0, now - stamp.timestamp())


def git(state, *args, input_text=None):
    env = {
        'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0',
        'GIT_SSH_COMMAND': 'ssh -F /etc/gips-watchdog/ssh_config',
        'GIT_AUTHOR_NAME': 'GIPS watchdog',
        'GIT_AUTHOR_EMAIL': 'gips-watchdog@users.noreply.github.com',
        'GIT_COMMITTER_NAME': 'GIPS watchdog',
        'GIT_COMMITTER_EMAIL': 'gips-watchdog@users.noreply.github.com',
    }
    result = subprocess.run(
        ['git', '-c', 'core.hooksPath=/dev/null', '--git-dir', str(state / 'repo.git'), *args],
        input=input_text, text=True, capture_output=True, timeout=45, check=True, env=env,
    )
    return result.stdout.strip()


def trigger(state):
    if not (state / 'repo.git').exists():
        git(state, 'init', '--bare')
    git(state, 'fetch', '--no-tags', '--depth=1', REMOTE, 'refs/heads/main')
    parent = git(state, 'rev-parse', 'FETCH_HEAD')
    tree = git(state, 'rev-parse', 'FETCH_HEAD^{tree}')
    commit = git(state, 'commit-tree', tree, '-p', parent,
                 input_text='GIPS watchdog: refresh stale public feed\n')
    # An unchanged tree triggers the existing workflow without executing site code.
    # A concurrent main update causes rejection; never force or retry over it.
    git(state, 'push', REMOTE, commit + ':refs/heads/main')


def check(state, now):
    attempt = state / 'last-attempt'
    if attempt.exists() and age(attempt.read_text().strip(), now) < COOLDOWN_SECONDS:
        return 'cooldown'
    if age(fetch(FEED)['checked_at'], now) < STALE_SECONDS:
        return 'published feed fresh'
    if age(fetch(SNAPSHOT)['last_successful_sync'], now) < STALE_SECONDS:
        return 'repository sync fresh'
    runs = fetch(RUNS).get('workflow_runs')
    if not isinstance(runs, list) or any(not isinstance(run, dict) for run in runs):
        raise ValueError('Invalid workflow response')
    if any(run.get('status') != 'completed' for run in runs):
        return 'workflow active'
    temporary = state / '.last-attempt.tmp'
    temporary.write_text(datetime.fromtimestamp(now, timezone.utc).isoformat() + '\n')
    temporary.replace(attempt)  # Cool down failed pushes as well as successful ones.
    trigger(state)
    return 'refresh requested'


def main(state=STATE):
    os.umask(0o077)
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('gips-watchdog: another check is running')
            return 0
        try:
            print('gips-watchdog: ' + check(state, time.time()))
            return 0
        except Exception as error:
            # Never print source text, subprocess output, URLs with credentials,
            # or private key material to the journal.
            print('gips-watchdog: check failed (' + type(error).__name__ + ')')
            return 1


if __name__ == '__main__':
    raise SystemExit(main())
