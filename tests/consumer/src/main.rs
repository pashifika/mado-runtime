//! A Windows MSVC Rust consumer of the packaged DLL, matching MadoPilot's
//! loader and ort feature boundary. It uses no game, capture, or input API.

#[cfg(not(all(target_os = "windows", target_env = "msvc", target_arch = "x86_64")))]
compile_error!("This consumer smoke requires x86_64-pc-windows-msvc");

#[cfg(all(target_os = "windows", target_env = "msvc", target_arch = "x86_64"))]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    use std::ffi::CStr;
    use std::path::PathBuf;

    use libloading::os::windows::{
        LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR, LOAD_LIBRARY_SEARCH_SYSTEM32, Library,
    };
    use ort::session::Session;
    use ort::value::TensorRef;

    let mut args = std::env::args_os().skip(1);
    let dll =
        PathBuf::from(args.next().ok_or("usage: consumer DLL IDENTITY_MODEL")?).canonicalize()?;
    let model = PathBuf::from(args.next().ok_or("missing identity model")?).canonicalize()?;
    if args.next().is_some() || dll.file_name() != Some(std::ffi::OsStr::new("onnxruntime.dll")) {
        return Err(
            "expected exactly DLL and model paths; DLL must be named onnxruntime.dll".into(),
        );
    }
    if std::env::var("ORT_DISABLE_TELEMETRY").as_deref() != Ok("1") {
        return Err("set ORT_DISABLE_TELEMETRY=1 before process startup".into());
    }

    // Match MadoPilot c1c6fd3 loader.rs: never use PATH or the working directory
    // to resolve dependent DLLs. Keep the original Windows error for diagnosis.
    let library = unsafe {
        Library::load_with_flags(
            &dll,
            LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32,
        )
    }
    .map_err(|error| format!("restricted DLL load failed: {error}"))?;
    type GetApiBase = unsafe extern "system" fn() -> *const ort::sys::OrtApiBase;
    let getter = unsafe { library.get::<GetApiBase>(b"OrtGetApiBase\0") }?;
    let base = unsafe { getter().as_ref() }.ok_or("OrtGetApiBase returned null")?;
    let version_ptr = unsafe { (base.GetVersionString)() };
    if version_ptr.is_null() {
        return Err("null runtime version".into());
    }
    let version = unsafe { CStr::from_ptr(version_ptr) }.to_str()?;
    if version != "1.29.0" || ort::sys::ORT_API_VERSION != 17 {
        return Err(format!(
            "unexpected runtime/API: {version}/{}",
            ort::sys::ORT_API_VERSION
        )
        .into());
    }
    let api = unsafe { (base.GetApi)(17).as_ref() }
        .ok_or("GetApi(17) returned null")?
        .clone();
    // ort stores process-global function pointers. As in the production SDK,
    // retain the library until process exit, including initialization failures.
    std::mem::forget(library);
    if !ort::set_api(api) {
        return Err("ort API was already initialized".into());
    }
    if !ort::init()
        .with_name("mado-pilot-onnx-cpu")
        .with_telemetry(false)
        .commit()
    {
        return Err("ort environment was already initialized".into());
    }
    println!("PASS: restricted load, runtime 1.29.0, C API 17, Rust ort initialization");

    let mut session = Session::builder()?
        .with_intra_threads(1)?
        .commit_from_file(model)?;
    let values = [1.0_f32, 2.0, 3.0];
    let input = TensorRef::from_array_view(([3_i64], values.as_slice()))?;
    let outputs = session.run(ort::inputs!["input" => input])?;
    let output = outputs.get("output").ok_or("missing output")?;
    let (shape, actual) = output.try_extract_tensor::<f32>()?;
    if &shape[..] != [3_i64] || actual != values {
        return Err(format!("unexpected identity output: {shape:?} {actual:?}").into());
    }
    drop(outputs);
    drop(session);
    println!("PASS: identity inference, exact output shape/values, Rust session cleanup");
    Ok(())
}
