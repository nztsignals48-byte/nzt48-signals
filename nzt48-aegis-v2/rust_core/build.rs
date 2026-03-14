//! Build script for Quantum Apex C++ FFI
//! Compiles quantum_apex.cpp as a static library

fn main() {
    cc::Build::new()
        .file("src/quantum_apex.cpp")
        .cpp(true)
        .opt_level(3)
        .flag_if_supported("-std=c++17")
        .flag_if_supported("-fPIC")
        .warnings(false)
        .compile("quantum_apex");

    // Tell cargo to link the quantum_apex library
    println!("cargo:rustc-link-lib=static=quantum_apex");

    // Tell cargo to invalidate the built crate if build.rs or the C++ file changes
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed=src/quantum_apex.cpp");
}
