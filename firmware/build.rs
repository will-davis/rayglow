//! Build script: makes `memory.x` available on the linker search path.
//!
//! `cortex-m-rt`'s `link.x` does `INCLUDE memory.x`, so the linker must be able
//! to find it. We copy it into the Cargo `OUT_DIR` (always on the search path)
//! and tell the linker to look there. Re-running only when `memory.x` changes
//! keeps incremental builds fast.

use std::env;
use std::fs::File;
use std::io::Write;
use std::path::PathBuf;

fn main() {
    let out = &PathBuf::from(env::var_os("OUT_DIR").unwrap());
    File::create(out.join("memory.x"))
        .unwrap()
        .write_all(include_bytes!("memory.x"))
        .unwrap();
    println!("cargo:rustc-link-search={}", out.display());

    println!("cargo:rerun-if-changed=memory.x");
    println!("cargo:rerun-if-changed=build.rs");
}
