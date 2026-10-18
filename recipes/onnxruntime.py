"""Native CPU ONNX Runtime builds for Mado's C API 17 consumers."""
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET


def validate(config):
    if config["source"].get("repository") != "https://github.com/microsoft/onnxruntime.git":
        raise ValueError("Unexpected ONNX Runtime source repository")
    if config.get("c_api_version") != 17:
        raise ValueError("Mado requires C API 17")
    targets = config.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("At least one target is required")
    supported = {
        "windows-x64-cpu": ("windows-2022", "Windows", "AMD64", "mimalloc"),
        "macos-arm64-cpu": ("macos-15", "Darwin", "arm64", "default"),
    }
    seen = set()
    for target in targets:
        name = target.get("id")
        if name not in supported or name in seen:
            raise ValueError("Unknown or duplicate target")
        seen.add(name)
        if target["system"] == "Windows" and target.get("windows_sdk") != "10.0.26100.0":
            raise ValueError("Windows builds require Windows 11 SDK 10.0.26100.0")
        if tuple(target.get(k) for k in ("runner", "system", "machine", "allocator")) != supported[name]:
            raise ValueError(f"Invalid configuration for {name}")


def required_files(config, target):
    shared = {"bin/onnxruntime.dll", "lib/onnxruntime.lib"} if target["system"] == "Windows" else {f"lib/libonnxruntime.{config['version']}.dylib"}
    return shared | {"include/onnxruntime/onnxruntime_c_api.h", "LICENSE", "ThirdPartyNotices.txt", "build-provenance.json"}


def verify_windows_toolchain(project, expected_sdk):
    """Read CMake's generated MSBuild project, not just requested options."""
    tree = ET.parse(project)
    sdks = {node.text for node in tree.iter() if node.tag.rsplit("}", 1)[-1] == "WindowsTargetPlatformVersion"}
    toolsets = {node.text for node in tree.iter() if node.tag.rsplit("}", 1)[-1] == "PlatformToolset"}
    if sdks != {expected_sdk}:
        raise ValueError(f"Configured Windows SDK mismatch: {sdks}")
    if toolsets != {"v143"}:
        raise ValueError(f"Expected Visual Studio 2022 MSVC v143 toolset, found {toolsets}")
    return {"windows_sdk": expected_sdk, "platform_toolset": "v143"}


def allocator_enabled(cache):
    """Dependent CMake options can be INTERNAL or absent off-platform."""
    match = re.search(r"^onnxruntime_USE_MIMALLOC:[^=]+=(.*)$", cache, re.MULTILINE)
    return bool(match and match.group(1).strip().upper() in {"ON", "TRUE", "YES", "1"})


def capture(command, cwd=None):
    return subprocess.check_output(command, cwd=cwd, text=True, stderr=subprocess.STDOUT).strip()


def build(config, target, work, root, run):
    source, build_dir, install, stage = (work / name for name in ("source", "build", "install", "stage"))
    run(["git", "init", source])
    run(["git", "remote", "add", "origin", config["source"]["repository"]], cwd=source)
    run(["git", "fetch", "--depth=1", "origin", config["source"]["commit"]], cwd=source)
    run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=source)
    actual = capture(["git", "rev-parse", "HEAD"], source)
    if actual != config["source"]["commit"]:
        raise ValueError("Source checkout did not match pinned commit")
    run(["git", "submodule", "update", "--init", "--recursive", "--depth=1"], cwd=source)
    if (source / "VERSION_NUMBER").read_text().strip() != config["version"]:
        raise ValueError("Pinned source version does not match manifest")
    command = [sys.executable, source / "tools/ci_build/build.py", "--build_dir", build_dir,
               "--config", "Release", "--update", "--build", "--parallel", "2",
               "--build_shared_lib", "--skip_tests", "--compile_no_warning_as_error", "--skip_submodule_sync", "--no_telemetry"]
    if target["allocator"] == "mimalloc":
        command.append("--use_mimalloc")
    if target["system"] == "Windows":
        command += ["--cmake_generator", "Visual Studio 17 2022"]
    else:
        command += ["--cmake_generator", "Unix Makefiles"]
    command += ["--cmake_extra_defines", "onnxruntime_BUILD_UNIT_TESTS=OFF"]
    if target["system"] == "Windows":
        command += [f"CMAKE_SYSTEM_VERSION={target['windows_sdk']}"]
    if target["system"] == "Darwin":
        command += ["CMAKE_OSX_DEPLOYMENT_TARGET=13.3", "CMAKE_OSX_ARCHITECTURES=arm64"]
    run(command, cwd=source)
    cache = (build_dir / "Release/CMakeCache.txt").read_text(encoding="utf-8")
    windows_toolchain = {}
    if target["system"] == "Windows":
        windows_toolchain = verify_windows_toolchain(build_dir / "Release/onnxruntime.vcxproj", target["windows_sdk"])
        windows_toolchain.update(vc_tools_version=os.environ.get("VCToolsVersion"),
                                 developer_prompt_sdk=os.environ.get("WindowsSDKVersion"),
                                 visual_studio_version=os.environ.get("VisualStudioVersion"))
    allocator_evidence = {"requested": target["allocator"]}
    if target["allocator"] == "mimalloc":
        if not allocator_enabled(cache):
            raise ValueError("CMake did not enable mimalloc")
        projects = list((build_dir / "Release").rglob("onnxruntime*.vcxproj"))
        project_text = "\n".join(p.read_text(encoding="utf-8") for p in projects)
        libraries = list((build_dir / "Release").rglob("*mimalloc*.lib"))
        if "USE_MIMALLOC" not in project_text or "mimalloc" not in project_text or not libraries:
            raise ValueError("Generated build/link evidence for mimalloc is missing")
        allocator_evidence.update(compile_definition="USE_MIMALLOC", libraries=[str(p.relative_to(build_dir)) for p in libraries])
    else:
        # CMake dependent options may be omitted or INTERNAL when WIN32 is false.
        if allocator_enabled(cache):
            raise ValueError("Unexpected mimalloc configuration on macOS")
        allocator_evidence["upstream_platform_gate"] = "WIN32 is false; mimalloc option disabled"
    run(["cmake", "--install", build_dir / "Release", "--config", "Release", "--prefix", install])
    # Keep the public runtime surface, not dependency SDKs/CMake exports.
    # Dereference dylib aliases so ZIP extraction needs no symlink support.
    shutil.copytree(install / "include/onnxruntime", stage / "include/onnxruntime", symlinks=False)
    patterns = {"bin": ("onnxruntime*.dll",), "lib": ("onnxruntime*.lib", "libonnxruntime*.dylib")}
    for directory, globs in patterns.items():
        for pattern in globs:
            for library in (install / directory).glob(pattern):
                destination = stage / directory / library.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(library, destination)
    for name in ("LICENSE", "ThirdPartyNotices.txt"):
        shutil.copy2(source / name, stage / name)
    binary_evidence = {}
    if target["system"] == "Darwin":
        library = stage / f"lib/libonnxruntime.{config['version']}.dylib"
        architectures = capture(["lipo", "-archs", str(library)])
        if architectures != "arm64":
            raise ValueError(f"Unexpected Mach-O architecture: {architectures}")
        dependencies = capture(["otool", "-L", str(library)])
        for line in dependencies.splitlines()[2:]:
            dependency = line.strip().split(" (", 1)[0]
            if dependency.startswith(("/usr/lib/", "/System/Library/")):
                continue
            if not dependency.startswith(("@rpath/", "@loader_path/")) or not (stage / "lib" / Path(dependency).name).is_file():
                raise ValueError(f"Unpackaged dynamic dependency: {dependency}")
        binary_evidence = {"architectures": architectures, "dynamic_dependencies": dependencies,
                           "deployment_target": "13.3"}
    smoke_results = smoke(config, target, stage, work, root, run)
    compiler = subprocess.run(["cl"] if target["system"] == "Windows" else ["clang", "--version"],
                              capture_output=True, text=True)
    compiler_version = compiler.stdout + compiler.stderr
    if target["system"] == "Windows":
        compiler_version += capture([str(ucrt_compiler()), "--version"])
    provenance = {
        "repository_commit": os.environ.get("GITHUB_SHA") or capture(["git", "rev-parse", "HEAD"], root),
        "source_commit": actual, "source_submodules": capture(["git", "submodule", "status", "--recursive"], source),
        "windows_toolchain": windows_toolchain,
        "allocator_evidence": allocator_evidence, "binary_evidence": binary_evidence,
        "target": target, "build_command": list(map(str, command)), "python": sys.version,
        "cmake": capture(["cmake", "--version"]), "platform": platform.platform(),
        "runner_image": os.environ.get("ImageVersion", "local"),
        "compiler": compiler_version,
        "workflow_run": os.environ.get("GITHUB_RUN_ID"), "workflow_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "smoke": {"c_api_version": 17, "model": "identity.onnx", "passed": True, "compilers": smoke_results},
    }
    (stage / "build-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    return stage, provenance


def ucrt_compiler():
    return Path(os.environ.get("MSYS2_LOCATION") or "C:/msys64") / "ucrt64/bin/gcc.exe"


def smoke(config, target, stage, work, root, run):
    library = stage / ("bin/onnxruntime.dll" if target["system"] == "Windows" else f"lib/libonnxruntime.{config['version']}.dylib")
    smoke = work / ("smoke.exe" if target["system"] == "Windows" else "smoke")
    include = stage / "include/onnxruntime"
    if target["system"] == "Windows":
        compiler = ["cl", "/nologo", "/std:c11", "/W4", "/WX", "/TC", f"/I{include}", root / "tests/smoke.c", f"/Fe:{smoke}"]
    else:
        compiler = ["clang", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", include, root / "tests/smoke.c", "-o", smoke]
    run(compiler, cwd=work)
    run([smoke, library, root / "tests/identity.onnx", config["version"]])
    smoke_results = {"native": True}
    if target["system"] == "Windows":
        gcc = ucrt_compiler()
        if not gcc.is_file():
            raise FileNotFoundError("Install MSYS2 UCRT64 GCC before building")
        ucrt_smoke = work / "smoke-ucrt64.exe"
        run([gcc, "-municode", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", include,
             root / "tests/smoke.c", "-o", ucrt_smoke], cwd=work)
        environment = os.environ.copy()
        environment["PATH"] = str(gcc.parent) + os.pathsep + environment["PATH"]
        run([ucrt_smoke, library, root / "tests/identity.onnx", config["version"]], env=environment)
        smoke_results["ucrt64_gcc_consumer"] = True
    return smoke_results
