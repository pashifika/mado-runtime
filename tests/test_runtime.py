import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("runtime", ROOT / "scripts/runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.config = runtime.load_config()

    def validate(self, config):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            path.write_text(json.dumps(config))
            return runtime.load_config(path)

    def test_matrix_and_names(self):
        self.assertEqual(len(runtime.matrix(self.config)["include"]), 2)
        self.assertEqual(runtime.release_tag(self.config), "onnxruntime-v1.29.0-r1")
        self.assertEqual(runtime.archive_name(self.config, self.config["targets"][0]),
                         "onnxruntime-v1.29.0-r1-windows-x64-cpu-mimalloc.zip")

    def test_generated_windows_toolchain(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "onnxruntime.vcxproj"
            for sdk, toolset, valid in (("10.0.26100.0", "v143", True),
                                        ("10.0.19041.0", "v143", False),
                                        ("10.0.26100.0", "ClangCL", False)):
                project.write_text(f'<Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003"><PropertyGroup><WindowsTargetPlatformVersion>{sdk}</WindowsTargetPlatformVersion><PlatformToolset>{toolset}</PlatformToolset></PropertyGroup></Project>')
                if valid:
                    result = runtime.onnxruntime.verify_windows_toolchain(project, "10.0.26100.0")
                    self.assertEqual(result["platform_toolset"], "v143")
                else:
                    with self.assertRaises(ValueError):
                        runtime.onnxruntime.verify_windows_toolchain(project, "10.0.26100.0")

    def test_dependent_allocator_cache(self):
        for cache in ("", "onnxruntime_USE_MIMALLOC:BOOL=OFF", "onnxruntime_USE_MIMALLOC:INTERNAL=OFF"):
            self.assertFalse(runtime.onnxruntime.allocator_enabled(cache))
        for kind in ("BOOL", "INTERNAL", "UNINITIALIZED"):
            self.assertTrue(runtime.onnxruntime.allocator_enabled(f"onnxruntime_USE_MIMALLOC:{kind}=ON"))

    def test_reject_invalid_configurations(self):
        mutations = [
            lambda c: c.update(schema_version=2),
            lambda c: c.update(runtime="../unsupported"),
            lambda c: c.update(version="../1.29.0"),
            lambda c: c.update(revision=True),
            lambda c: c["source"].update(commit="main"),
            lambda c: c["source"].update(commit="0" * 40),
            lambda c: c["source"].update(repository="https://untrusted.invalid/runtime"),
            lambda c: c.update(c_api_version=29),
            lambda c: c.update(targets=[]),
            lambda c: c["targets"].append(c["targets"][0]),
            lambda c: c["targets"][1].update(allocator="mimalloc"),
            lambda c: c["targets"][0].update(id="../../escape"),
            lambda c: c["targets"][0].update(windows_sdk="10.0.19041.0"),
        ]
        for mutation in mutations:
            config = copy.deepcopy(self.config)
            mutation(config)
            with self.subTest(config=config), self.assertRaises(ValueError):
                self.validate(config)


class SmokeOrchestrationTests(unittest.TestCase):
    def test_preflight_compiles_both_windows_harnesses_with_strict_warnings(self):
        from unittest.mock import Mock, patch
        config = runtime.load_config()
        runner = Mock()
        include = Path("source/include/onnxruntime/core/session")
        with patch.object(Path, "is_file", return_value=True):
            runtime.onnxruntime.compile_smoke(config["targets"][0], include, Path("work"), ROOT, runner)
        commands = [call.args[0] for call in runner.call_args_list]
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0][0], "cl")
        self.assertIn("/WX", commands[0])
        self.assertIn(f"/I{include}", commands[0])
        self.assertIn("-Werror", commands[1])
        self.assertIn("-v", commands[1])
        self.assertNotIn("env", runner.call_args_list[0].kwargs)
        self.assertEqual(runner.call_args_list[1].kwargs["env"],
                         runtime.onnxruntime.ucrt_environment(runtime.onnxruntime.ucrt_compiler()))
        self.assertIn(include, commands[1])

    def test_windows_runs_pinned_msvc_rust_consumer(self):
        from unittest.mock import Mock, patch
        config = runtime.load_config()
        runner = Mock()
        with patch.object(Path, "is_file", return_value=True):
            result = runtime.onnxruntime.smoke(config, config["targets"][0], Path("stage"), Path("work"), ROOT, runner)
        command, = runner.call_args.args
        self.assertEqual(command[:4], ["cargo", "+1.98.1", "run", "--locked"])
        self.assertIn("x86_64-pc-windows-msvc", command)
        self.assertIn(Path("stage/bin/onnxruntime.dll"), command)
        self.assertEqual(runner.call_args.kwargs["env"]["ORT_DISABLE_TELEMETRY"], "1")
        self.assertTrue(result["mado_rust_ort_api17"])

    def test_ucrt_environment_is_shared_without_mutating_msvc_path(self):
        import os
        from unittest.mock import Mock, patch
        config = runtime.load_config()
        runner = Mock()
        gcc = Path("msys/ucrt64/bin/gcc.exe")
        with patch.dict(os.environ, {"PATH": "msvc-tools", "MSYS2_LOCATION": "msys"}, clear=True), patch.object(Path, "is_file", return_value=True):
            runtime.onnxruntime.smoke(config, config["targets"][0], Path("stage"), Path("work"), ROOT, runner)
            self.assertEqual(os.environ["PATH"], "msvc-tools")
            gcc_call = next(call for call in runner.call_args_list if call.args[0][0] == gcc)
            executable_call = next(call for call in runner.call_args_list if call.args[0][0] == Path("work/smoke-ucrt64.exe"))
            self.assertEqual(gcc_call.kwargs["env"], executable_call.kwargs["env"])
            self.assertEqual(gcc_call.kwargs["env"]["PATH"], str(gcc.parent) + os.pathsep + "msvc-tools")
            self.assertEqual(runner.call_args.kwargs["env"]["PATH"], "msvc-tools")

    def test_rust_failure_propagates(self):
        from unittest.mock import patch
        def runner(command, **kwargs):
            if command[0] == "cargo":
                raise RuntimeError("consumer init failed")
        config = runtime.load_config()
        with patch.object(Path, "is_file", return_value=True), self.assertRaisesRegex(RuntimeError, "consumer init failed"):
            runtime.onnxruntime.smoke(config, config["targets"][0], Path("stage"), Path("work"), ROOT, runner)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = runtime.load_config()
        self.target = self.config["targets"][0]
        self.stage = self.root / "stage"
        self.stage.mkdir()
        for name in runtime.onnxruntime.required_files(self.config, self.target):
            path = self.stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture contents")
        self.provenance = {"repository_commit": "a" * 40}

    def package(self):
        return runtime.package(self.stage, self.root / "dist", self.config, self.target, self.provenance)

    def rewrite(self, archive, transform):
        with zipfile.ZipFile(archive) as bundle:
            contents = {name: bundle.read(name) for name in bundle.namelist()}
        transform(contents)
        with zipfile.ZipFile(archive, "w") as bundle:
            for name, data in contents.items():
                bundle.writestr(name, data)
        archive.with_suffix(".zip.sha256").write_text(f"{runtime.sha256(archive)}  {archive.name}\n")

    def test_roundtrip(self):
        archive = self.package()
        manifest = runtime.verify_archive(archive, self.config, self.target)
        self.assertEqual(manifest["source"], self.config["source"])
        self.assertIn("bin/onnxruntime.dll", manifest["files"])

    def test_missing_required_library(self):
        (self.stage / "bin/onnxruntime.dll").unlink()
        with self.assertRaises(ValueError):
            self.package()

    def test_checksum_tampering(self):
        archive = self.package()
        with archive.open("ab") as stream:
            stream.write(b"tamper")
        with self.assertRaises(ValueError):
            runtime.verify_archive(archive, self.config, self.target)

    def test_inventory_tampering(self):
        archive = self.package()
        self.rewrite(archive, lambda files: files.update({"bin/onnxruntime.dll": b"changed"}))
        with self.assertRaises(ValueError):
            runtime.verify_archive(archive, self.config, self.target)

    def test_unsafe_paths(self):
        for name in ("../escape", "/absolute", "C:/escape", "lib\\escape"):
            with self.subTest(name=name):
                stage = self.stage / "manifest.json"
                stage.unlink(missing_ok=True)
                output = self.root / "dist"
                if output.exists():
                    import shutil
                    shutil.rmtree(output)
                archive = self.package()
                self.rewrite(archive, lambda files: files.update({name: b"unsafe"}))
                with self.assertRaises(ValueError):
                    runtime.verify_archive(archive, self.config, self.target)

    def test_partial_release_rejected(self):
        self.package()
        with self.assertRaises(ValueError):
            runtime.verify_release(self.root / "dist", self.config)

    def test_complete_release_and_mixed_commit_rejected(self):
        self.package()
        target = self.config["targets"][1]
        stage = self.root / "stage-mac"
        for name in runtime.onnxruntime.required_files(self.config, target):
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture contents")
        archive = runtime.package(stage, self.root / "dist", self.config, target, self.provenance)
        from unittest.mock import patch
        with patch.dict("os.environ", {}, clear=True):
            runtime.verify_release(self.root / "dist", self.config)
        def change_commit(files):
            manifest = json.loads(files["manifest.json"])
            manifest["provenance"]["repository_commit"] = "b" * 40
            files["manifest.json"] = json.dumps(manifest).encode()
        self.rewrite(archive, change_commit)
        with self.assertRaises(ValueError):
            runtime.verify_release(self.root / "dist", self.config)

    def test_existing_archive_refused(self):
        self.package()
        (self.stage / "manifest.json").unlink()
        with self.assertRaises(FileExistsError):
            self.package()

    def test_extra_asset_rejected(self):
        self.package()
        (self.root / "dist/unexpected.txt").write_text("unexpected")
        with self.assertRaises(ValueError):
            runtime.verify_release(self.root / "dist", self.config)

    def test_wrong_target_rejected(self):
        archive = self.package()
        with self.assertRaises(ValueError):
            runtime.verify_archive(archive, self.config, self.config["targets"][1])


if __name__ == "__main__":
    unittest.main()
