#!/usr/bin/env python3
"""Builds pythia._native (native/) with cargo for meson, and puts the module
where meson installs it from.

usage: build_native.py SOURCE_ROOT OUTPUT

OUTPUT is the module's file (_native.abi3.so; _native.pyd on Windows). Cargo's
own output goes beside it, under native-target/. Cargo runs in native/, so a
source package with the crates vendored there (native/.cargo/config.toml, as
the PPA build makes it) builds without a network.
"""

import os
import shutil
import subprocess
import sys


def main() -> int:
    source_root, output = sys.argv[1], sys.argv[2]
    target_dir = os.path.abspath(os.path.join(os.path.dirname(output), "native-target"))
    native = os.path.join(source_root, "native")
    subprocess.run(["cargo", "build", "--release", "--locked", "--target-dir", target_dir], cwd=native, check=True)
    built = {"win32": "_native.dll", "darwin": "lib_native.dylib"}.get(sys.platform, "lib_native.so")
    shutil.copyfile(os.path.join(target_dir, "release", built), output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
