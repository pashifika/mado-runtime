# Adding a runtime

Keep build-system details inside a concrete recipe, not in a general-purpose build framework.

1. Add a module under `recipes/` with `validate(config)`, `required_files(config, target)`, `build(...)` and `smoke(...)` following the existing recipe
2. Register that module in `RECIPES` in `scripts/runtime.py`
3. Add a reviewed manifest for the new runtime and select it explicitly before expanding the workflow to multiple manifests
4. Pin its source commit and required toolchain; specify its actual allocator and supported native targets
5. Package its public interface, runtime libraries, licenses and notices; fail on missing required files
6. Add a deterministic consumer test that loads the packaged library, creates the runtime's equivalent of a session and performs a real operation
7. Extend unit tests and CI, then validate every native target before enabling publication

The current manifest represents one runtime release. Do not add unused provider flags, cross-compilation combinations or generic command templates. A second runtime is the point to introduce multiple manifests if needed. Preserve the complete-asset release gate, path validation and refusal to overwrite releases.
