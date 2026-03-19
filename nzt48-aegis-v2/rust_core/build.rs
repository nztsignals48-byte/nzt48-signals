//! Build script for Quantum Apex C++ FFI (gated behind feature flag)

fn main() {
    #[cfg(feature = "quantum_apex")]
    {
        cc::Build::new()
            .file("src/quantum_apex.cpp")
            .cpp(true)
            .opt_level(3)
            .flag_if_supported("-std=c++17")
            .flag_if_supported("-fPIC")
            .warnings(false)
            .compile("quantum_apex");
        println!("cargo:rerun-if-changed=src/quantum_apex.cpp");
    }
    println!("cargo:rerun-if-changed=build.rs");
}
