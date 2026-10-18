# Building and releasing

## Requirements

- Python 3.12, Git and CMake 3.31.6 (installed by `requirements-build.txt`)
- Windows: Visual Studio 2022 C++ tools and Windows SDK, an x64 developer command prompt, and MSYS2 at `C:\msys64` with `mingw-w64-ucrt-x86_64-gcc` installed
- macOS: Apple Silicon with Xcode command line tools; hosted CI uses `macos-15`
- Network access to the pinned upstream repository, submodules, CMake dependencies and Python package index

Windows builds run from an x64 Visual Studio developer prompt:

```powershell
python -m pip install -r requirements-build.txt
python scripts/runtime.py build --target windows-x64-cpu
```

Each build requires an unused `.work/<target>` directory. To rebuild locally, move or remove that target's work directory and its previous files in `dist/`. The tool refuses to overwrite an existing archive. Builds use two parallel jobs to limit memory pressure.

## Source and compatibility

`runtime.json` pins upstream 1.29.0 to commit `2e2543fbe9fae542f921d47a72d21d5a4ef0b710`. Source is fetched by commit, checked against `VERSION_NUMBER`, and its exact submodules are initialized. Builds enable the shared CPU library and disable telemetry. Windows enables mimalloc; macOS uses the default allocator. Windows CI checks the enabled CMake option, generated mimalloc compile/link references and built mimalloc library. macOS explicitly targets arm64 and macOS 13.3; CI checks Mach-O architecture and rejects unpackaged non-system dynamic dependencies.

The version follows [Mado Pilot's third-party dependency contract](https://github.com/pashifika/mado-pilot/blob/main/docs/third-party-dependencies.md). Updating upstream also requires checking the consumer's exact-version check and C API bindings. A newer upstream version alone is not sufficient justification. Mado Mata currently pins official distribution archive and library hashes. Custom binaries have different hashes even at the same upstream version; adoption requires a separately reviewed consumer catalog/checksum update and qualification against its models. Publishing here does not automatically integrate these assets into Mado applications.

`tests/smoke.c` opens the package's library by path, requests C API 17, checks the exact runtime version, creates a session and runs `tests/identity.onnx`. It verifies three float outputs. Both the installation tree and extracted ZIP are tested. Windows runs the same smoke program compiled by MSVC and UCRT64 GCC. No Python ONNX Runtime wheel participates.

This is an ABI/inference smoke test, not a model coverage suite or performance benchmark. Passing mimalloc configuration checks does not establish a performance benefit. CPU is the only execution provider configured here.

## Package contents

Archives have a flat root with:

- `include/onnxruntime/`: installed public headers
- `bin/onnxruntime.dll` and `lib/onnxruntime.lib` on Windows
- `lib/libonnxruntime.1.29.0.dylib` and installed aliases on macOS
- `LICENSE` and `ThirdPartyNotices.txt` from the pinned upstream source
- `build-provenance.json`: source/submodule revisions, build command, runner image, tool versions and native smoke results
- `manifest.json`: identity, target, allocator and SHA-256 for every other packaged file

Only the public header tree and ONNX Runtime shared/import libraries are selected from the CMake installation. Dependency SDKs and CMake exports are excluded. macOS library symlinks are stored as regular-file copies for portable ZIP extraction. The `.zip.sha256` sidecar checks the entire archive. These checksums detect corruption; they are not cryptographic publisher signatures.

Asset names include runtime, upstream version, packaging revision, target and allocator. The first release tag is `onnxruntime-v1.29.0-r1`. Windows consumers may need the Microsoft Visual C++ 2015–2022 x64 Redistributable; it is not redistributed here. Load the packaged shared library rather than a system or Python-installed copy.

## Release procedure

1. Review and merge the source/manifest changes into the repository's default branch
2. In Actions, select **Build runtimes**, choose the default branch and enable **publish**
3. Every configured target builds, packages and tests before the release job starts
4. The release job downloads this run's immutable Actions artifacts and validates the complete expected asset set and common commit
5. It refuses existing release tags, creates a new draft, verifies downloaded uploaded assets, then publishes

PRs and ordinary branch pushes cannot publish. No release credentials are available to build jobs. Actions are pinned to reviewed commit SHAs. Release concurrency prevents two release runs from racing. The workflow uses the repository's default branch for the publication gate; the push build trigger currently names `master` and should be adjusted if the default branch is renamed.

If upload or verification fails, a draft may remain. Inspect it manually; automation never overwrites, deletes or resumes an existing release. Resolve the failed draft explicitly or increase `revision` for a new release. Never reuse a published tag for different bytes.

Hosted toolchains and downloaded dependency packages can change. Source and action pins plus provenance improve traceability; this project does not claim bit-for-bit reproducible compilation. Before calling a change release-ready, require both native jobs and the extracted-archive smoke tests to pass on that exact commit.
