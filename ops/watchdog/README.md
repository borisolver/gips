# GIPS cloud watchdog

Fallback timer only. This package does not ingest reports or run site code on the server. Every five minutes it checks the public Pages feed and the latest `main` sync-state. If both are at least five minutes old and the existing GitHub workflow has no active run, it pushes an empty commit with the exact current `main` tree. The existing GitHub workflow performs ingestion, tests, and publication.

The check uses one unauthenticated GitHub Actions API request at most per invocation (normally at most 12/hour); raw GitHub and Pages requests do not use that API quota. Network errors, invalid/future timestamps, redirects, and malformed workflow responses fail closed. A process lock prevents duplicate checks; every push attempt starts a five-minute cooldown, including failed attempts. Pushes never force an update.

## Install on the existing Linux host

Use a new `gips-watchdog` service user. Do not change existing CleanApp jobs, users, SSH keys, or service definitions. Requires systemd, Python 3.11+, Git, and OpenSSH; no pip packages or GitHub token.

As root, after copying this package into a temporary staging directory:

```sh
useradd --system --home-dir /var/lib/gips-watchdog --shell /usr/sbin/nologin gips-watchdog
install -d -o root -g root -m 0755 /opt/gips-watchdog
install -d -o root -g gips-watchdog -m 0750 /etc/gips-watchdog
install -d -o gips-watchdog -g gips-watchdog -m 0700 /var/lib/gips-watchdog
install -o root -g root -m 0644 watchdog.py /opt/gips-watchdog/watchdog.py
install -o root -g root -m 0644 ssh_config /etc/gips-watchdog/ssh_config
ssh-keygen -t ed25519 -N '' -C gips-watchdog -f /etc/gips-watchdog/deploy_key
chown gips-watchdog:gips-watchdog /etc/gips-watchdog/deploy_key
chmod 0600 /etc/gips-watchdog/deploy_key
```

Fetch GitHub's SSH host keys from its official HTTPS metadata endpoint as root. Do not trust an unauthenticated `ssh-keyscan` result:

```sh
python3 - <<'PY'
import json, urllib.request
from pathlib import Path
with urllib.request.urlopen('https://api.github.com/meta', timeout=20) as response:
    keys = json.load(response)['ssh_keys']
assert keys and all(isinstance(key, str) and '\n' not in key for key in keys)
Path('/etc/gips-watchdog/known_hosts').write_text(''.join('github.com ' + key + '\n' for key in keys))
PY
chmod 0644 /etc/gips-watchdog/known_hosts
```

Register **only** `/etc/gips-watchdog/deploy_key.pub` as a write-enabled deploy key on **borisolver/gips** through GitHub's settings/API. Keep the private key on this server; do not copy an existing PAT or user SSH key into this service. Root-owned SSH configuration enforces the pinned host keys and this dedicated identity. The service can read its private key but its systemd filesystem sandbox makes `/etc/gips-watchdog` read-only; writable state contains only the ledger and bare repository.

```sh
install -o root -g root -m 0644 gips-watchdog.service gips-watchdog.timer /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/gips-watchdog.service /etc/systemd/system/gips-watchdog.timer
systemctl daemon-reload
systemctl enable --now gips-watchdog.timer
systemctl list-timers gips-watchdog.timer
journalctl -u gips-watchdog.service --no-pager -n 20
```

`OnActiveSec=1min` supplies the first natural check one minute after the timer starts; subsequent checks use `OnUnitActiveSec=5min`. The service is restricted to its own state directory, with read-only system paths, no privilege escalation, and CPU/memory limits. Git hooks are disabled and the dedicated bare repository never checks out or executes repository files.

## Verify and remove

Before installing, run `python3 -m unittest -v` in this directory. Tests use synthetic responses and disposable local bare Git repositories; no cloud or production writes occur.

Confirm a natural timer activation requests a refresh only when stale and idle; then confirm the resulting GitHub push run publishes a newer `checked_at`. A successful empty commit proves triggering, not completed ingestion. No secrets or source text are written to the service journal.

To disable: `systemctl disable --now gips-watchdog.timer`. Remove its repository deploy key when retiring the fallback. Leave unrelated services unchanged.
