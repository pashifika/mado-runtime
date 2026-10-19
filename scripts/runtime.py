#!/usr/bin/env python3
"""Build and verify release archives; runtime-specific details live in recipes."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from recipes import onnxruntime

RECIPES = {"onnxruntime": onnxruntime}


def load_config(path=ROOT / "runtime.json"):
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if config.get("schema_version") != 1 or config.get("runtime") not in RECIPES:
        raise ValueError("Unsupported schema or runtime recipe")
    if not re.fullmatch(r"\d+\.\d+\.\d+", config.get("version", "")):
        raise ValueError("Version must be major.minor.patch")
    if type(config.get("revision")) is not int or config["revision"] < 1:
        raise ValueError("Revision must be a positive integer")
    source = config.get("source", {})
    if not re.fullmatch(r"[0-9a-f]{40}", source.get("commit", "")) or set(source["commit"]) == {"0"}:
        raise ValueError("Source must be pinned to a real 40-character commit")
    RECIPES[config["runtime"]].validate(config)
    return config


def release_tag(config):
    return f"{config['runtime']}-v{config['version']}-r{config['revision']}"


def archive_name(config, target):
    return f"{release_tag(config)}-{target['id']}-{target['allocator']}.zip"


def matrix(config):
    return {"include": config["targets"]}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command, **kwargs):
    print("+", " ".join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), check=True, **kwargs)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def package(stage, output, config, target, provenance):
    """Package a flat-root ZIP with a complete, content-addressed inventory."""
    stage, output = Path(stage), Path(output)
    if (stage / "manifest.json").exists():
        raise ValueError("Stage already has a manifest")
    files = {}
    for path in sorted(stage.rglob("*")):
        if path.is_symlink():
            raise ValueError("Stage must contain regular files, not symlinks")
        if path.is_file():
            files[path.relative_to(stage).as_posix()] = sha256(path)
    required = RECIPES[config["runtime"]].required_files(config, target)
    if not required <= files.keys():
        raise ValueError(f"Missing required files: {sorted(required - files.keys())}")
    write_json(stage / "manifest.json", {
        "schema_version": 1, "runtime": config["runtime"], "version": config["version"],
        "revision": config["revision"], "target": target, "source": config["source"],
        "c_api_version": config["c_api_version"], "files": files, "provenance": provenance,
    })
    output.mkdir(parents=True, exist_ok=True)
    archive = output / archive_name(config, target)
    if archive.exists():
        raise FileExistsError(archive)
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                info = zipfile.ZipInfo(path.relative_to(stage).as_posix(), (2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                bundle.writestr(info, path.read_bytes())
    archive.with_suffix(".zip.sha256").write_text(f"{sha256(archive)}  {archive.name}\n", encoding="ascii")
    verify_archive(archive, config, target)
    return archive


def verify_archive(archive, config, target):
    archive = Path(archive)
    expected = archive.with_suffix(".zip.sha256").read_text(encoding="ascii")
    if expected != f"{sha256(archive)}  {archive.name}\n":
        raise ValueError("Archive checksum mismatch")
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if len(set(names)) != len(names):
            raise ValueError("Duplicate ZIP entries")
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name or name.endswith("/"):
                raise ValueError("Unsafe archive path")
        manifest = json.loads(bundle.read("manifest.json"))
        for key in ("runtime", "version", "revision", "source", "c_api_version"):
            if manifest.get(key) != config[key]:
                raise ValueError(f"Manifest mismatch: {key}")
        if manifest.get("target") != target:
            raise ValueError("Target mismatch")
        inventory = manifest["files"]
        if set(names) != set(inventory) | {"manifest.json"}:
            raise ValueError("Archive inventory mismatch")
        if not RECIPES[config["runtime"]].required_files(config, target) <= inventory.keys():
            raise ValueError("Required files absent")
        for name, digest in inventory.items():
            if hashlib.sha256(bundle.read(name)).hexdigest() != digest:
                raise ValueError(f"File checksum mismatch: {name}")
    return manifest


def verify_release(directory, config):
    directory = Path(directory)
    expected = {archive_name(config, t) + suffix for t in config["targets"] for suffix in ("", ".sha256")}
    actual = {p.name for p in directory.iterdir() if p.is_file()}
    if actual != expected or any(p.is_dir() for p in directory.iterdir()):
        raise ValueError(f"Release asset set mismatch: expected {sorted(expected)}, found {sorted(actual)}")
    manifests = [verify_archive(directory / archive_name(config, t), config, t) for t in config["targets"]]
    commits = {m["provenance"]["repository_commit"] for m in manifests}
    if len(commits) != 1:
        raise ValueError("Targets were built from different repository commits")
    if os.environ.get("GITHUB_SHA") and commits != {os.environ["GITHUB_SHA"]}:
        raise ValueError("Artifacts were not built from this workflow commit")


def build(config, target):
    if platform.system() != target["system"] or platform.machine().lower() != target["machine"].lower():
        raise ValueError("Build must run natively on the selected target")
    work = ROOT / ".work" / target["id"]
    if work.exists():
        raise FileExistsError(f"Use a clean build directory: {work}")
    work.mkdir(parents=True)
    recipe = RECIPES[config["runtime"]]
    stage, provenance = recipe.build(config, target, work, ROOT, run)
    archive = package(stage, ROOT / "dist", config, target, provenance)
    extracted = work / "extracted"
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(extracted)  # verify_archive already rejected unsafe paths.
    recipe.smoke(config, target, extracted, work, ROOT, run)
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "build", "verify", "tag"))
    parser.add_argument("--target", help="Native target ID (required for build)")
    parser.add_argument("--directory", type=Path, default=ROOT / "dist", help="Release assets to verify")
    args = parser.parse_args()
    config = load_config()
    if args.command == "plan":
        print(json.dumps(matrix(config), separators=(",", ":")))
    elif args.command == "tag":
        print(release_tag(config))
    elif args.command == "verify":
        verify_release(args.directory, config)
        print("All release assets verified")
    else:
        targets = [t for t in config["targets"] if t["id"] == args.target]
        if not targets:
            parser.error("build requires a configured --target")
        print(build(config, targets[0]))


if __name__ == "__main__":
    main()
