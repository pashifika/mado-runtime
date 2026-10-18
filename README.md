# mado-runtime

Build and distribute native AI runtimes for Mado with a repeatable source-to-release workflow.

Mado applications need shared libraries that match their runtime bindings. This repository keeps the source revision, build options, compatibility checks and release assets together.

## Features

- **Consumer compatibility:** ONNX Runtime 1.29.0 with C API 17, matching Mado Pilot
- **Native builds:** Windows x64 CPU built with MSVC 2022, Windows 11 SDK 10.0.26100.0 and mimalloc; macOS arm64 CPU with the default allocator
- **Real inference checks:** Load the newly built shared library and run a small ONNX model, including Windows UCRT64 GCC and Mado’s pinned Rust `ort` consumers
- **Auditable packages:** Public headers, shared libraries, Windows import library, upstream licenses, SHA-256 checksums and build provenance

## Getting started

Python 3.12+ is enough to inspect the build plan and run the automation tests:

```bash
git clone https://github.com/pashifika/mado-runtime.git
cd mado-runtime
python scripts/runtime.py plan
python -m unittest discover -s tests -v
```

Download published binaries from [Releases](https://github.com/pashifika/mado-runtime/releases). CI build archives are available on successful [workflow runs](https://github.com/pashifika/mado-runtime/actions/workflows/build.yml).

## Usage

Print the configured release tag:

```bash
python scripts/runtime.py tag
```

Build on an Apple Silicon Mac with Xcode command line tools, Git and Python 3.12 installed:

```bash
python -m pip install -r requirements-build.txt
python scripts/runtime.py build --target macos-arm64-cpu
```

Verify a downloaded complete set of release ZIPs and their checksum files in `dist/`:

```bash
python scripts/runtime.py verify
```

Run `python scripts/runtime.py --help` for all commands and options. See [building and releasing](docs/building.md) for Windows setup, package layout and release recovery.

Windows builds also require Rust 1.98.1 with the `x86_64-pc-windows-msvc` target. The Rust smoke checks Mado’s exact binding initialization and a small inference; full OCR/model qualification remains a separate consumer task.

## Supported runtimes

| Runtime | Version | Target | Allocator |
| --- | --- | --- | --- |
| ONNX Runtime | 1.29.0 | Windows x64 CPU | mimalloc |
| ONNX Runtime | 1.29.0 | macOS arm64 CPU | Default |

The version is intentionally pinned to the consuming application's requirements. ONNX Runtime's upstream mimalloc build option is supported on Windows; it is not enabled on macOS. UCRT64 tests interoperability with the MSVC-built DLL rather than building ONNX Runtime with MinGW.

Mado Mata also pins official archive and library hashes. These custom builds need a separate consumer catalog/checksum update and qualification before adoption, even though the upstream version is unchanged. This repository does not update consumer repositories.

## Development

The standard-library Python CLI reads `runtime.json` and delegates build details to `recipes/onnxruntime.py`. Add another concrete recipe when a second runtime is needed; see [extending runtimes](docs/extending.md).

Pull requests build and test every configured target with read-only repository permissions. Publishing requires a manual workflow run with `publish` enabled on the default branch, and succeeds only after all targets pass.

## License

Repository code is licensed under [Apache-2.0](LICENSE). Distributed ONNX Runtime binaries retain their upstream MIT license and bundled third-party notices.
