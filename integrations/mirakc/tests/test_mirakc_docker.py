#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Optional real mirakc HTTP smoke test with a synthetic tuner stream."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
IMAGE = os.environ.get("MIRAKC_TEST_IMAGE", "mirakc/mirakc:alpine")


def docker(*args, input_data=None):
    return subprocess.run(["docker", *map(str, args)], input=input_data,
                          capture_output=True, check=True, timeout=30)


def read_json(url):
    with urlopen(url, timeout=3) as response:
        return json.load(response)


def wait_for(predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    raise AssertionError("timed out waiting for mirakc")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-sample", choices=("config.example.yml",
                                                     "config.satellite.example.yml"),
                        default="config.example.yml")
    args = parser.parse_args()
    name = f"pix-mirakc-test-{os.getpid()}"
    with tempfile.TemporaryDirectory(prefix="pix-mirakc-docker-") as work:
        root = Path(work)
        fixture = root / "synthetic.ts"
        subprocess.run([sys.executable, str(HERE / "generate_synthetic_ts.py"), str(fixture)],
                       check=True)
        config = root / "config.yml"
        config.write_text((ROOT / args.config_sample).read_text() +
                          "\njobs:\n  sync-clocks:\n    disabled: true\n"
                          "  update-schedules:\n    disabled: true\n")
        (root / "epg").mkdir()
        try:
            docker("run", "-d", "--rm", "--init", "--name", name,
                   "-p", "127.0.0.1::40772", "-e", "PIX_TS_BIN=/usr/local/bin/pix-ts-mock",
                   "-e", "PIX_MOCK_TS=/tmp/synthetic.ts",
                   "-v", f"{config}:/etc/mirakc/config.yml:ro",
                   "-v", f"{ROOT / 'pix-mirakc-tuner'}:/usr/local/bin/pix-mirakc-tuner:ro",
                   "-v", f"{HERE / 'mock_pix_ts_stream.sh'}:/usr/local/bin/pix-ts-mock:ro",
                   "-v", f"{fixture}:/tmp/synthetic.ts:ro",
                   "-v", f"{root / 'epg'}:/var/lib/mirakc/epg", IMAGE)
            port = docker("port", name, "40772").stdout.decode().strip().rsplit(":", 1)[1]
            base = f"http://127.0.0.1:{port}"
            wait_for(lambda: read_json(base + "/api/tuners"))
            # This exercises the image's actual ARIB service scanner through mirakc jobs.
            services = wait_for(lambda: read_json(base + "/api/services") or None)
            assert any(service["serviceId"] == 101 for service in services), services
            wait_for(lambda: read_json(base + "/api/tuners")[0]["isFree"])
            for _ in range(2):
                with urlopen(base + "/api/channels/GR/T27/stream?decode=0", timeout=5) as stream:
                    data = stream.read(188 * 20)
                assert len(data) == 188 * 20
                # A shared tuner may start a subscriber in the middle of a packet.
                assert any(all(data[offset + index * 188] == 0x47 for index in range(10))
                           for offset in range(188))
                wait_for(lambda: read_json(base + "/api/tuners")[0]["isFree"])
            print("mirakc config, API stream/disconnect/reuse, and service scan passed")
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)


if __name__ == "__main__":
    main()
