#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Synthetic 188-byte transport packets; never contains broadcast content."""
import fcntl
import json
import os
import sys
import time

args_path = os.environ.get("PIX_MOCK_ARGS")
if args_path:
    with open(args_path, "w", encoding="utf-8") as out:
        json.dump(sys.argv[1:], out)

mode = os.environ.get("PIX_MOCK_MODE", "finite")
if mode == "fail":
    print("synthetic IPC failure", file=sys.stderr)
    sys.exit(3)

lock_file = os.environ.get("PIX_MOCK_LOCK")
if lock_file:
    lock = open(lock_file, "w", encoding="utf-8")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("synthetic tuner busy", file=sys.stderr)
        sys.exit(3)

print("synthetic diagnostic", file=sys.stderr)
packet = bytes([0x47, 0x1f, 0xff, 0x10]) + bytes([0xff]) * 184
count = int(os.environ.get("PIX_MOCK_PACKETS", "128"))
written = 0
try:
    while mode == "endless" or written < count:
        os.write(sys.stdout.fileno(), packet)
        written += 1
        if mode == "disconnect" and written == 8:
            print("synthetic daemon disconnected", file=sys.stderr)
            sys.exit(7)
        if mode == "endless":
            time.sleep(0.002)
except BrokenPipeError:
    print("synthetic pipe closed", file=sys.stderr)
    sys.exit(4)
except OSError as exc:
    if exc.errno == 32:
        print("synthetic pipe closed", file=sys.stderr)
        sys.exit(4)
    raise
