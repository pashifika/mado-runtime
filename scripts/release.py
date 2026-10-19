#!/usr/bin/env python3
"""Publish a complete verified set once, via a new GitHub draft release."""
import json
import os
from pathlib import Path
import subprocess
import sys

from runtime import ROOT, load_config, release_tag, verify_release


def gh(*args):
    return subprocess.check_output(["gh", *args], text=True).strip()


def main():
    config = load_config()
    assets = ROOT / "dist"
    verify_release(assets, config)
    repository = os.environ["GITHUB_REPOSITORY"]
    commit = os.environ["GITHUB_SHA"]
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    default_branch = event["repository"]["default_branch"]
    if os.environ["GITHUB_EVENT_NAME"] != "workflow_dispatch" or os.environ["GITHUB_REF"] != f"refs/heads/{default_branch}":
        raise ValueError("Publication requires manual dispatch from the default branch")
    tag = release_tag(config)
    # List calls must succeed; an authentication/network failure is not treated as absence.
    tags = json.loads(gh("api", "--paginate", "--slurp", f"repos/{repository}/tags?per_page=100"))
    releases = json.loads(gh("api", "--paginate", "--slurp", f"repos/{repository}/releases?per_page=100"))
    if any(t["name"] == tag for page in tags for t in page) or any(r["tag_name"] == tag for page in releases for r in page):
        raise ValueError("Release/tag already exists; use a new manifest revision. No assets were modified.")
    target_notes = "\n".join(f"- {t['id']}: {t['allocator']} allocator" for t in config["targets"])
    notes = (f"{config['runtime']} {config['version']}, packaging revision {config['revision']}.\n\n"
             f"{target_notes}\n\n"
             "Each archive contains the public interface, upstream licensing, checksums and build provenance.\n"
             "Every configured target passed native and extracted-archive smoke tests.\n\n"
             f"Source: {config['source']['repository'].removesuffix('.git')}/commit/{config['source']['commit']}\n"
             f"Packaging: https://github.com/{repository}/commit/{commit}\n")
    gh("release", "create", tag, "--repo", repository, "--target", commit,
       "--title", tag, "--notes", notes, "--draft", *map(str, sorted(assets.iterdir())))
    # A partial upload stays draft. Never overwrite/delete it automatically.
    published_assets = json.loads(gh("release", "view", tag, "--repo", repository, "--json", "assets"))["assets"]
    expected = {p.name: p.stat().st_size for p in assets.iterdir()}
    actual = {a["name"]: a["size"] for a in published_assets}
    if actual != expected:
        raise ValueError("Uploaded assets differ; release remains draft for inspection")
    # Download and hash actual uploaded bytes, not only the local archives.
    temporary = ROOT / ".work/release-download"
    temporary.mkdir(parents=True, exist_ok=False)
    gh("release", "download", tag, "--repo", repository, "--dir", str(temporary))
    verify_release(temporary, config)
    gh("release", "edit", tag, "--repo", repository, "--draft=false", "--latest=false")
    print(gh("release", "view", tag, "--repo", repository, "--json", "url", "--jq", ".url"))


if __name__ == "__main__":
    main()
