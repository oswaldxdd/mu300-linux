#!/usr/bin/env python3
"""Run U30-only policy simulations and release-integration contract checks."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

here = Path(__file__).resolve().parent
tests = here / "fcc-learning" / "tests"
learning = here / "fcc-learning"
env = dict(os.environ)
env["CPATH"] = str(learning) + os.pathsep + str(learning / "source") + os.pathsep + env.get("CPATH", "")

for name in ("test-fcc-policy.c", "test-fcc-state.c", "test-fcc-quiet.c",
             "test-capacity-tenth.c"):
    source = tests / name
    with tempfile.TemporaryDirectory(prefix="mu300-u30air-test-") as tmp:
        binary = Path(tmp) / Path(name).stem
        subprocess.run(["gcc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-I", str(learning), "-I", str(learning / "source"),
                        str(source), "-o", str(binary)], check=True, env=env)
        subprocess.run([str(binary)], check=True, env=env)

with tempfile.TemporaryDirectory(prefix="mu300-u30air-host-policy-") as tmp:
    binary = Path(tmp) / "test-host-policy"
    subprocess.run(["gcc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-I", str(here / "fcc-learning" / "source"),
                    str(here / "source" / "test-host-policy.c"), "-o", str(binary)],
                   check=True, env=env)
    subprocess.run([str(binary)], check=True, env=env)

for name in ("test-charger-quiet.py", "test-fgu-quiet.py", "test-kernel-adapter.py",
             "test-full-worker.py", "test-restore-write.py"):
    subprocess.run([sys.executable, str(tests / name)], cwd=learning,
                   check=True, env=env)
subprocess.run([sys.executable, str(here / "tests" / "test-fcc-device.py")],
               cwd=here, check=True, env=env)
subprocess.run([sys.executable, str(here / "tests" / "test-fcc-record.py")],
               cwd=here, check=True, env=env)
subprocess.run([sys.executable, str(here / "tests" / "test-status-contract.py")],
               cwd=here, check=True, env=env)

print("PASS: U30 Air FCC/quiet-lease/Host policies, source adapters and 0.1% SOC contract")
