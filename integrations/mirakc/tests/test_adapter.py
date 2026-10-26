#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "pix-mirakc-tuner"
MOCK = Path(__file__).with_name("mock_pix_ts.py")
PACKET = bytes([0x47, 0x1f, 0xff, 0x10]) + bytes([0xff]) * 184
BASE = ["--type", "GR", "--channel", "T27", "--duration", "-", "--instance", "dt295"]


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.args_file = Path(self.tmp.name) / "args.json"
        self.env = dict(os.environ, PIX_TS_BIN=str(MOCK), PIX_MOCK_ARGS=str(self.args_file))

    def run_adapter(self, args=BASE, **env):
        return subprocess.run([str(ADAPTER), *args], env=dict(self.env, **env),
                              capture_output=True, timeout=10)

    def test_gr_bytes_and_arguments(self):
        result = self.run_adapter(["--type", "GR", "--channel", "27", "--duration", "12",
                                   "--instance", "dt295", "--runtime-dir", "/run/pixd", "--group"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, PACKET * 128)
        self.assertEqual(result.stderr, b"synthetic diagnostic\n")
        self.assertEqual(json.loads(self.args_file.read_text()),
                         ["--instance", "dt295", "--system", "isdb-t", "--channel", "T27",
                          "--time", "12", "--output", "-", "--runtime-dir", "/run/pixd", "--group"])

    def test_satellite_selection(self):
        cases = [
            ("BS", "BS01_0", [], "BS01", ["--slot", "0"]),
            ("BS", "BS23@16400", [], "BS23", ["--tsid", "16400"]),
            ("BS", "BS01", ["--tsid", "16400"], "BS01", ["--tsid", "16400"]),
            ("CS", "CS22_7", [], "CS22", ["--slot", "7"]),
            ("CS", "CS22@29024", [], "CS22", ["--tsid", "29024"]),
            ("CS", "CS22", ["--slot", "0"], "CS22", ["--slot", "0"]),
        ]
        for kind, channel, extra, expected_channel, selection in cases:
            with self.subTest(channel=channel):
                result = self.run_adapter(["--type", kind, "--channel", channel,
                                           "--duration", "-", "--instance", "dt295", *extra])
                self.assertEqual(result.returncode, 0, result.stderr)
                args = json.loads(self.args_file.read_text())
                self.assertEqual(args[2:8], ["--system", "isdb-s", "--channel", expected_channel,
                                             "--time", "-"])
                self.assertEqual(args[-2:], selection)

    def test_reject_invalid_before_execution(self):
        invalid = [
            ["--type", "SKY"], ["--channel", "T12"], ["--channel", "T63"],
            ["--channel", "T27;touch /tmp/never"], ["--duration", "0"],
            ["--duration", "1;id"], ["--instance", "x;id"],
            ["--channel", "BS01_8", "--type", "BS"],
            ["--channel", "BS01", "--type", "BS"],
            ["--channel", "CS22", "--type", "CS"],
            ["--channel", "CS22@65535", "--type", "CS"],
            ["--channel", "CS22@0", "--type", "CS"],
            ["--channel", "BS02_0", "--type", "BS"],
            ["--channel", "CS22_0", "--type", "CS", "--tsid", "1"],
            ["--group"], ["--runtime-dir", "relative"],
            ["--unexpected", "value"],
        ]
        for changes in invalid:
            with self.subTest(changes=changes):
                args = BASE.copy()
                if changes[0] in ("--type", "--channel", "--duration", "--instance", "--runtime-dir"):
                    for index in range(0, len(changes) - 1, 2):
                        if changes[index] in args:
                            args[args.index(changes[index]) + 1] = changes[index + 1]
                        else:
                            args.extend(changes[index:index + 2])
                    if len(changes) % 2:
                        args.append(changes[-1])
                else:
                    args.extend(changes)
                self.args_file.unlink(missing_ok=True)
                result = self.run_adapter(args)
                self.assertEqual(result.returncode, 2, (changes, result.stderr))
                self.assertEqual(result.stdout, b"")
                self.assertFalse(self.args_file.exists())

    def test_errors_propagate(self):
        failure = self.run_adapter(PIX_MOCK_MODE="fail")
        self.assertEqual(failure.returncode, 3)
        self.assertEqual(failure.stdout, b"")
        disconnected = self.run_adapter(PIX_MOCK_MODE="disconnect")
        self.assertEqual(disconnected.returncode, 7)
        self.assertEqual(disconnected.stdout, PACKET * 8)
        missing = self.run_adapter(PIX_TS_BIN=str(Path(self.tmp.name) / "missing"))
        self.assertNotEqual(missing.returncode, 0)
        self.assertEqual(missing.stdout, b"")

    def test_streaming_backpressure_and_pipe_close(self):
        env = dict(self.env, PIX_MOCK_PACKETS="8192")
        with subprocess.Popen([str(ADAPTER), *BASE], env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) as proc:
            time.sleep(0.1)
            self.assertIsNone(proc.poll())  # pipe capacity applies backpressure
            output, diagnostics = proc.communicate(timeout=15)
            self.assertEqual(proc.returncode, 0, diagnostics)
            self.assertEqual(output, PACKET * 8192)
        with subprocess.Popen([str(ADAPTER), *BASE], env=dict(self.env, PIX_MOCK_MODE="endless"),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
            self.assertEqual(proc.stdout.read(188), PACKET)
            proc.stdout.close()
            proc.wait(timeout=5)
            self.assertEqual(proc.returncode, 4)

    def test_signals_release_and_reuse(self):
        lock_path = str(Path(self.tmp.name) / "lock")
        env = dict(self.env, PIX_MOCK_MODE="endless", PIX_MOCK_LOCK=lock_path)
        for signum in (signal.SIGTERM, signal.SIGINT):
            with subprocess.Popen([str(ADAPTER), *BASE], env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE) as first:
                self.assertEqual(first.stdout.read(188), PACKET)
                busy = self.run_adapter(PIX_MOCK_LOCK=lock_path)
                self.assertEqual(busy.returncode, 3)
                first.send_signal(signum)
                first.wait(timeout=5)
                self.assertNotEqual(first.returncode, 0)
            again = self.run_adapter(PIX_MOCK_LOCK=lock_path)
            self.assertEqual(again.returncode, 0, again.stderr)


if __name__ == "__main__":
    unittest.main()
