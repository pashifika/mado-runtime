# Windows Rust consumer smoke

This isolated executable reproduces the DLL loading and Rust `ort` API boundary
of MadoPilot commit `c1c6fd3f0d8400649c018ddef29075aee6e37fe4`, consumed by
MadoMata PR 85 head `670bebc9adf79f2747fb98f9e0ecfe96eabb9f7b`.
It is not a build of MadoMata and does not qualify OCR models or native games.

Run in Windows x64 with Rust 1.98.1, the MSVC target and build prerequisites:

```powershell
$env:ORT_DISABLE_TELEMETRY = '1'
cargo +1.98.1 run --locked --manifest-path tests/consumer/Cargo.toml --target x86_64-pc-windows-msvc -- C:\extracted\bin\onnxruntime.dll tests/identity.onnx
```

Run this against the extracted archive, not the build tree. The loader searches
only the DLL directory and System32. Required non-system DLL dependencies must
be available there; adding MSYS2 to PATH does not make them available to this
loader. No alternate runtime, downloaded ORT binary or accelerator is enabled.

The exact direct dependency versions/features match the production boundary.
Cargo.lock was generated with Rust 1.98.1. A Linux-hosted cross-target
`cargo +1.98.1 check --locked --target x86_64-pc-windows-msvc` passed.
This compile check does not establish Windows load or inference success.
