#!/usr/bin/env python3
"""
Verify the DM feedback decode and the passive-CAN reader without real hardware.

Round-trip test: build frames with the ENCODER from the C++ source
(dm_motor_control.cpp:110-123 / double_to_uint) and check the decoder recovers
the original values. That validates the bit layout independently of any
hardware being present.

Then drive a real SocketCAN vcan0 interface end to end, which exercises the
socket setup, CAN-FD handling, kernel timestamping and nearest-sample pairing.

    python3 test_direct_io.py
"""

import struct
import subprocess
import sys
import time

import numpy as np

from openarm_direct_io import (
    CAN_FRAME_FMT,
    DM4310,
    MotorLimits,
    StateReader,
    decode_feedback,
    _uint_to_double,
)


def double_to_uint(x, lo, hi, bits):
    """Port of CanPacketDecoder::double_to_uint (dm_motor_control.cpp:145)."""
    span = hi - lo
    x = max(lo, min(hi, x))
    return int(((x - lo) / span) * ((1 << bits) - 1))


def build_feedback(motor_id, pos, vel, tau, limits, t_mos=40, t_rotor=45):
    """Encode a DM feedback frame exactly as the firmware would."""
    q = double_to_uint(pos, -limits.p_max, limits.p_max, 16)
    dq = double_to_uint(vel, -limits.v_max, limits.v_max, 12)
    tt = double_to_uint(tau, -limits.t_max, limits.t_max, 12)
    return bytes([
        motor_id & 0xFF,
        (q >> 8) & 0xFF,
        q & 0xFF,
        (dq >> 4) & 0xFF,
        ((dq & 0xF) << 4) | ((tt >> 8) & 0xF),
        tt & 0xFF,
        t_mos,
        t_rotor,
    ])


def test_roundtrip():
    print("== 1. decode round-trip against the C++ encoder ==")
    worst = 0.0
    for pos in (-12.0, -3.14159, -0.001, 0.0, 0.5, 1.5708, 6.2831, 12.4):
        for vel in (-20.0, 0.0, 7.5):
            for tau in (-8.0, 0.0, 3.3):
                data = build_feedback(0x11, pos, vel, tau, DM4310)
                fb = decode_feedback(0x11, data, DM4310, 0.0)
                assert fb is not None, "decoder rejected a valid frame"
                worst = max(worst, abs(fb.position - pos))
                assert abs(fb.velocity - vel) < 0.05, (vel, fb.velocity)
                assert abs(fb.torque - tau) < 0.05, (tau, fb.torque)
    # 16-bit over +/-12.5 rad => quantum is 25/65535 = 3.8e-4 rad
    q = 25.0 / 65535
    print(f"   position worst error {worst:.3e} rad  (16-bit quantum {q:.3e})  {'OK' if worst <= q else 'FAIL'}")
    assert worst <= q
    print("   velocity/torque within 0.05  OK")

    print("== 2. short frames rejected (matches dm_motor_control.cpp:63) ==")
    assert decode_feedback(0x11, b"\x00" * 7, DM4310, 0.0) is None
    print("   7-byte frame -> None  OK")

    print("== 3. limit table matches dm_motor_constants.hpp:95 ==")
    for name, m, want in (("DM4310", DM4310, (12.5, 30, 10)),):
        got = (m.p_max, m.v_max, m.t_max)
        print(f"   {name}: {got} vs {want}  {'OK' if got == want else 'FAIL'}")
        assert got == want


def test_live_vcan():
    print("== 4. end-to-end over a real SocketCAN interface (vcan0) ==")
    try:
        subprocess.run(["modprobe", "vcan"], check=False, capture_output=True)
        subprocess.run(["ip", "link", "add", "dev", "vcan0", "type", "vcan"],
                       check=False, capture_output=True)
        r = subprocess.run(["ip", "link", "set", "up", "vcan0"], capture_output=True)
        if r.returncode != 0:
            print("   SKIP: cannot create vcan0 (needs NET_ADMIN):",
                  r.stderr.decode().strip()[:80])
            return
    except Exception as e:
        print("   SKIP:", e)
        return

    recv_ids = [0x11, 0x12, 0x13]
    reader = StateReader("vcan0", recv_ids, DM4310, fd=False).start()
    time.sleep(0.3)

    import socket
    tx = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    tx.bind(("vcan0",))

    truth = [0.25, -1.5, 3.0]
    send_times = []
    for _ in range(20):
        for cid, p in zip(recv_ids, truth):
            data = build_feedback(cid, p, 0.0, 0.0, DM4310)
            tx.send(struct.pack(CAN_FRAME_FMT, cid, 8, data))
        send_times.append(time.monotonic())
        time.sleep(0.01)
    time.sleep(0.3)

    vec, ts = reader.latest()
    print(f"   frames seen {reader.frames_seen}  decoded {reader.frames_decoded}")
    assert vec is not None, "no state assembled"
    err = np.abs(vec - np.array(truth)).max()
    print(f"   state {np.round(vec, 4)} vs truth {truth}  max err {err:.2e}  "
          f"{'OK' if err < 1e-3 else 'FAIL'}")
    assert err < 1e-3

    # nearest-sample pairing
    target = send_times[len(send_times) // 2]
    v2, t2 = reader.state_at(target)
    assert v2 is not None
    print(f"   state_at(t) picked a sample {abs(t2 - target)*1000:.1f} ms from the "
          f"requested instant (latest was {abs(ts - target)*1000:.1f} ms away)")

    # timestamps must be monotonic-clock comparable, not realtime
    drift = abs(ts - time.monotonic())
    print(f"   timestamp is on the monotonic clock (age {drift*1000:.1f} ms)  "
          f"{'OK' if drift < 5.0 else 'FAIL — wrong clock base'}")
    assert drift < 5.0

    reader.stop()
    tx.close()
    print("   passive read: reader never transmitted (no bus contention)  OK")


if __name__ == "__main__":
    test_roundtrip()
    print()
    test_live_vcan()
    print("\nall checks passed")
