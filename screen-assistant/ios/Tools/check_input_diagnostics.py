#!/usr/bin/env python3
"""Run real Swift/Foundation behavior tests; NOT an iOS build or device test."""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ios = Path(__file__).resolve().parent.parent
compiler = shutil.which("swiftc")
if compiler is None:
    print("NOT RUN: swiftc is required (use Xcode Command Line Tools on a Mac).")
    sys.exit(77)

with tempfile.TemporaryDirectory(prefix="input-diagnostics-") as temp:
    binary = Path(temp) / ("input-diagnostics-tests.exe" if sys.platform == "win32" else "input-diagnostics-tests")
    compiled = subprocess.run([
        compiler, str(ios / "Shared/AppGroup.swift"), str(ios / "Shared/InputDiagnostics.swift"),
        str(ios / "Tools/Tests/InputDiagnostics/main.swift"), "-o", str(binary),
    ])
    if compiled.returncode:
        sys.exit(compiled.returncode)
    sys.exit(subprocess.run([str(binary)]).returncode)
