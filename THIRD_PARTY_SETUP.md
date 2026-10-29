# Prospective Mac clean-CLI package archive metadata

This inventory covers the 64 separately downloaded package archives in the prospective Mac CPython 3.13 setup manifest: 9 wheels and 55 Rust crates. The preserved attempt hash-verified the archives, then stopped at the first configured data-feed HTTP 403 before any package installation or build. It is a local setup-input record; these package archives are not bundled here.

The table copies declared licence metadata verbatim from wheel `METADATA` (`License-Expression`, otherwise `License`) or crate `Cargo.toml` (`package.license`). “Embedded notice members” lists only archive member basenames matching LICENSE/LICENCE/COPYING/NOTICE; “None” means no matching filename was present. License-file contents were not read.

This inventory does not state legal compatibility, redistribution rights, package adoption, completed setup, or release readiness. Some Cargo crates are platform-specific/transitive entries in the pinned archive set. The exact package archive pins are in [`mac-cp313-arm64.artifacts.json`](tools/setup/mac-cp313-arm64.artifacts.json); wheel locks are [`runtime`](tools/setup/requirements-runtime-cp313-macos-arm64.candidate.lock) and [`build tools`](tools/setup/requirements-build-tools-cp313-macos-arm64.candidate.lock).

| Package | Version | Archive input | Declared licence metadata | Embedded notice members |
|---|---:|---|---|---|
| certifi | 2026.7.22 | wheel | MPL-2.0 | LICENSE |
| charset-normalizer | 3.5.2 | wheel | MIT | LICENSE |
| idna | 3.20 | wheel | BSD-3-Clause | LICENSE.md |
| maturin | 1.15.0 | wheel | MIT OR Apache-2.0 | license-apache, license-mit |
| python-dateutil | 2.8.0 | wheel | Dual License | LICENSE |
| requests | 2.33.0 | wheel | Apache-2.0 | LICENSE, NOTICE |
| six | 1.17.0 | wheel | MIT | LICENSE |
| urllib3 | 2.8.0 | wheel | MIT | LICENSE.txt |
| websockets | 14.1 | wheel | BSD-3-Clause | LICENSE |
| anstream | 0.6.13 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| anstyle | 1.0.6 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| anstyle-parse | 0.2.3 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| anstyle-query | 1.0.2 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| anstyle-wincon | 3.0.2 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| bitflags | 2.11.1 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| cfg-if | 1.0.0 | Cargo crate | MIT/Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| clap | 4.5.4 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| clap_builder | 4.5.2 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| clap_derive | 4.5.4 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| clap_lex | 0.7.0 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| colorchoice | 1.0.0 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| crossbeam-utils | 0.8.21 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| dashmap | 6.1.0 | Cargo crate | MIT | LICENSE |
| getrandom | 0.3.3 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| hashbrown | 0.14.5 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| heck | 0.5.0 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| libc | 0.2.155 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| lock_api | 0.4.14 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| once_cell | 1.21.3 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| parking_lot_core | 0.9.12 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| portable-atomic | 1.7.0 | Cargo crate | Apache-2.0 OR MIT | LICENSE-APACHE, LICENSE-MIT |
| ppv-lite86 | 0.2.17 | Cargo crate | MIT/Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| proc-macro2 | 1.0.86 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| pyo3 | 0.29.0 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| pyo3-build-config | 0.29.0 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| pyo3-ffi | 0.29.0 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| pyo3-macros | 0.29.0 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| pyo3-macros-backend | 0.29.0 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| quote | 1.0.45 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| r-efi | 5.3.0 | Cargo crate | MIT OR Apache-2.0 OR LGPL-2.1-or-later | None |
| rand | 0.9.4 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| rand_chacha | 0.9.0 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| rand_core | 0.9.3 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| redox_syscall | 0.5.18 | Cargo crate | MIT | LICENSE |
| scopeguard | 1.2.0 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| smallvec | 1.15.1 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| strsim | 0.11.0 | Cargo crate | MIT | LICENSE |
| syn | 2.0.76 | Cargo crate | MIT OR Apache-2.0 | LICENSE-APACHE, LICENSE-MIT |
| target-lexicon | 0.13.3 | Cargo crate | Apache-2.0 WITH LLVM-exception | LICENSE |
| unicode-ident | 1.0.12 | Cargo crate | (MIT OR Apache-2.0) AND Unicode-DFS-2016 | LICENSE-APACHE, LICENSE-MIT, LICENSE-UNICODE |
| utf8parse | 0.2.1 | Cargo crate | Apache-2.0 OR MIT | LICENSE-APACHE, LICENSE-MIT |
| wasi | 0.14.7+wasi-0.2.4 | Cargo crate | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT | LICENSE-APACHE, LICENSE-Apache-2.0_WITH_LLVM-exception, LICENSE-MIT |
| wasip2 | 1.0.1+wasi-0.2.4 | Cargo crate | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT | None |
| windows-link | 0.2.1 | Cargo crate | MIT OR Apache-2.0 | license-apache-2.0, license-mit |
| windows-sys | 0.52.0 | Cargo crate | MIT OR Apache-2.0 | license-apache-2.0, license-mit |
| windows-targets | 0.52.4 | Cargo crate | MIT OR Apache-2.0 | license-apache-2.0, license-mit |
| windows_aarch64_gnullvm | 0.52.4 | Cargo crate | MIT OR Apache-2.0 | license-apache-2.0, license-mit |
| windows_aarch64_msvc | 0.52.4 | Cargo crate | MIT OR Apache-2.0 | license-apache-2.0, license-mit |
| windows_i686_gnu | 0.52.4 | Cargo crate | MIT OR Apache-2.0 | license-apache-2.0, license-mit |
| windows_i686_msvc | 0.52.4 | Cargo crate | MIT OR Apache-2.0 | license-apache-2.0, license-mit |
| windows_x86_64_gnu | 0.52.4 | Cargo crate | MIT OR Apache-2.0 | license-apache-2.0, license-mit |
| windows_x86_64_gnullvm | 0.52.4 | Cargo crate | MIT OR Apache-2.0 | license-apache-2.0, license-mit |
| windows_x86_64_msvc | 0.52.4 | Cargo crate | MIT OR Apache-2.0 | license-apache-2.0, license-mit |
| wit-bindgen | 0.46.0 | Cargo crate | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT | LICENSE-APACHE, LICENSE-Apache-2.0_WITH_LLVM-exception, LICENSE-MIT |
