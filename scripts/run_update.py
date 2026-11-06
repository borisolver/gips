"""Validate an update and package the public site; CI may also commit and push it."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
DATA_PATHS = (
    "proposals.json", "sync-state.json", "feed.json", "gips.json", "index.html",
    "evidence",
)
PUBLIC_FILES = (
    "index.html", "styles.css", "site.js", "logo.png", "logo.svg", "gips.json",
    "feed.json", "sync-state.json", ".nojekyll",
)


def run(*args, capture=False):
    return subprocess.run(
        args, cwd=ROOT, check=True, text=True,
        stdout=subprocess.PIPE if capture else None,
    )


def prepare_artifact(destination):
    """Copy only public assets, rejecting symlinks and non-regular files."""
    paths = [ROOT / name for name in PUBLIC_FILES]
    for name in ("evidence", "concepts"):
        directory = ROOT / name
        if name == "concepts" and not directory.exists():
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError(f"Expected a regular directory: {name}")
        paths.extend(directory.rglob("*"))

    # Check everything before copying or creating the artifact directory.
    for source in paths:
        if source.is_symlink() or not (source.is_file() or source.is_dir()):
            raise ValueError(f"Invalid public asset: {source.relative_to(ROOT)}")
    for name in PUBLIC_FILES:
        if not (ROOT / name).is_file():
            raise ValueError(f"Missing public file: {name}")

    destination.mkdir(parents=True, exist_ok=False)
    for source in paths:
        target = destination / source.relative_to(ROOT)
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


def validate_ci_checkout():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise ValueError("--push is limited to GitHub Actions; omit it for a local run")
    if run("git", "branch", "--show-current", capture=True).stdout.strip() != "main":
        raise ValueError("Automatic updates must start on main")
    if run("git", "status", "--porcelain", capture=True).stdout.strip():
        raise ValueError("Automatic updates require a clean checkout")


def commit_and_push():
    # Explicit paths prevent source code, credentials, or scratch files entering
    # the bot commit. Build and tests have already completed successfully.
    run("git", "add", "--all", "--", *DATA_PATHS)
    staged = run("git", "diff", "--cached", "--name-only", "-z", capture=True).stdout
    for path in filter(None, staged.split("\0")):
        if path not in DATA_PATHS and not path.startswith("evidence/"):
            raise ValueError(f"Unexpected staged path: {path}")
    if staged:
        run("git", "config", "user.name", "github-actions[bot]")
        run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
        run("git", "commit", "-m", "Update GIPS from public reports")
    else:
        print("Public data is unchanged.", flush=True)

    # Even a no-op push detects a concurrent main update. Never force-push or
    # deploy after a rejected push; the next run starts from fresh main.
    run("git", "push", "origin", "HEAD:refs/heads/main")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--push", action="store_true", help="Commit and push validated data in CI")
    args = parser.parse_args()
    destination = args.artifact_dir.resolve()
    if destination == ROOT or ROOT in destination.parents or destination in ROOT.parents:
        raise ValueError("The artifact directory must be outside the repository")
    if destination.exists():
        raise ValueError("The artifact directory must be new")
    if args.push:
        validate_ci_checkout()

    run(sys.executable, "scripts/sync_reports.py")
    run(sys.executable, "build.py")
    run(sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v")
    prepare_artifact(destination)
    if args.push:
        commit_and_push()
    print(f"Public site is ready in {destination}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Update failed: {error}", file=sys.stderr)
        raise SystemExit(1)
