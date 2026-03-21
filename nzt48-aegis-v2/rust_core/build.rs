//! Build script — no external C/C++ dependencies after theater code removal.

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
}
