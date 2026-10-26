#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run the adapter through real pix-ts IPC with Core's synthetic pixd fixture."""
import argparse
import os
from pathlib import Path
import select
import signal
import subprocess
import tempfile
import time

ADAPTER = Path(__file__).resolve().parents[1] / "pix-mirakc-tuner"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pixd-fixture", type=Path, required=True)
    parser.add_argument("--pix-ts", type=Path, required=True)
    parser.add_argument("--pixctl", type=Path, required=True)
    paths = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="pix-mirakc-ipc-") as work:
        root = Path(work)
        root.chmod(0o770)
        assets = root / "assets"
        assets.mkdir()
        for name, length in (("fpga.bin", 699673), ("bootstrap.bin", 16),
                             ("validation.bin", 64)):
            with (assets / name).open("wb") as out:
                out.truncate(length)
        env = dict(os.environ, PIX_TS_BIN=str(paths.pix_ts.resolve()))
        command = [str(ADAPTER), "--type", "GR", "--channel", "T13",
                   "--duration", "1", "--instance", "dt295", "--runtime-dir", work,
                   "--group"]
        absent = subprocess.run(command, env=env, capture_output=True, timeout=5)
        assert absent.returncode == 3 and absent.stdout == b"", absent

        daemon_command = [str(paths.pixd_fixture), "--device", "serial:PIXDT18101",
                          "--assets", str(assets), "--runtime-dir", work,
                          "--instance", "dt295", "--group"]
        daemon_log = root / "pixd.log"
        with daemon_log.open("wb") as log:
            for cycle in range(2):
                daemon = subprocess.Popen(daemon_command, stdout=log, stderr=subprocess.STDOUT)
                try:
                    deadline = time.monotonic() + 5
                    endpoint = root / "pix-userland" / "v1" / "dt295" / "control.sock"
                    while not endpoint.exists() and time.monotonic() < deadline:
                        assert daemon.poll() is None, daemon_log.read_text(errors="replace")
                        time.sleep(0.01)
                    assert endpoint.exists(), daemon_log.read_text(errors="replace")
                    unlimited = command.copy()
                    unlimited[unlimited.index("--duration") + 1] = "-"
                    with subprocess.Popen(unlimited, env=env, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE) as active:
                        assert select.select([active.stdout], [], [], 5)[0], "no TS from pix-ts"
                        assert os.read(active.stdout.fileno(), 188)[0] == 0x47
                        busy = subprocess.run(command, env=env, capture_output=True, timeout=5)
                        assert busy.returncode == 4 and busy.stdout == b"", busy
                        active.send_signal(signal.SIGTERM if cycle == 0 else signal.SIGINT)
                        active.wait(timeout=5)
                        assert active.returncode == 0, active.stderr.read()
                    for _ in range(2):
                        capture = subprocess.run(command, env=env, capture_output=True, timeout=7)
                        assert capture.returncode == 0, capture.stderr
                        assert capture.stdout and len(capture.stdout) % 188 == 0
                        assert all(capture.stdout[offset] == 0x47
                                   for offset in range(0, len(capture.stdout), 188))
                        assert b"stream packets=" in capture.stderr
                        status = subprocess.run([str(paths.pixctl), "--runtime-dir", work,
                                                 "--instance", "dt295", "--group", "status"],
                                                capture_output=True, timeout=5)
                        assert status.returncode == 0, status.stderr
                        assert b'"state":"free"' in status.stdout, status.stdout
                        assert b'"leased":false' in status.stdout, status.stdout
                finally:
                    daemon.send_signal(signal.SIGTERM)
                    daemon.wait(timeout=5)
                    assert daemon.returncode == 0, daemon_log.read_text(errors="replace")
        print("adapter -> pix-ts -> pixd fixture: IPC, TS, release, reuse, restart passed")


if __name__ == "__main__":
    main()
