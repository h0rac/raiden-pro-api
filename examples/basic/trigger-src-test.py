#!/usr/bin/env python3
"""
Interactive check of the trigger_src modes.

Run from the repo root:
    PYTHONPATH=. python3 examples/basic/trigger-src-test.py

Each phase waits for ENTER so there is time to attach or remove the wire on
IO29 (trigger_in) before the flags are sampled. Flags are polled for a couple
of seconds rather than read once, since glitched/finished latch briefly and a
single read can miss them.
"""
import sys
import time

from raiden_python.raiden import Raiden

PORT = "/dev/raiden"
MHZ = 200


def configure(d, src):
    d.arm(0)
    d.reset_glitcher()
    d.set_target_power("auto")
    d.set_trigger_source(src)
    d.set_param(param="CMD_GLITCH_DELAY", value=0)
    d.set_param(param="CMD_GLITCH_WIDTH", value=2000)
    d.set_param(param="CMD_GLITCH_GAP",   value=1000)
    d.set_param(param="CMD_GLITCH_COUNT", value=1)
    d.set_param(param="CMD_GLITCH_MAX",   value=1)
    d.arm(1)


def watch(d, seconds=3.0, label=""):
    """Poll flags and latch anything that went high during the window."""
    seen_trig = seen_glitch = seen_fin = 0
    deadline = time.time() + seconds
    while time.time() < deadline:
        f = d.flag_status()
        armed = f & 1
        glitched = (f >> 1) & 1
        finished = (f >> 2) & 1
        gout = (f >> 3) & 1
        trig = (f >> 4) & 1
        seen_trig |= trig
        seen_glitch |= glitched
        seen_fin |= finished
        print("  armed={} trig={} glitched={} finished={} out={}   ".format(
            armed, trig, glitched, finished, gout), end="\r", flush=True)
        time.sleep(0.02)
    print("\n  [{}] latched: trig={} glitched={} finished={}".format(
        label, seen_trig, seen_glitch, seen_fin))
    return seen_trig, seen_glitch, seen_fin


d = None
try:
    d = Raiden(mhz=MHZ, serial_dev=PORT, ticks=True)

    # ---- phase 1: external, pin left alone ----------------------------------
    print("\n=== 1/3  external, NOTHING on IO29 ===")
    print("Disconnect anything from IO29 (a 10k pulldown to GND is ideal).")
    input("ENTER when ready...")
    configure(d, "external")
    watch(d, 3.0, "external, no signal")
    print("  expected: trig=0 glitched=0 finished=0")
    print("  trig=1 here means IO29 is floating high, not that the mode is broken.")

    # ---- phase 2: external, 3.3 V applied -----------------------------------
    print("\n=== 2/3  external, 3.3 V ON IO29 ===")
    print("Now touch 3.3 V to IO29 and hold it there.")
    input("ENTER when the wire is on...")
    configure(d, "external")
    watch(d, 3.0, "external, 3.3 V")
    print("  expected: trig=1 glitched=1 finished=1")

    # ---- phase 3: free, pin irrelevant --------------------------------------
    print("\n=== 3/3  free-run, remove the wire from IO29 ===")
    print("Take the wire off IO29. This mode must fire without it.")
    input("ENTER when the wire is off...")
    configure(d, "free")
    watch(d, 3.0, "free-run")
    print("  expected: glitched=1 finished=1 regardless of trig")

except KeyboardInterrupt:
    print("\nInterrupted.")
except Exception as e:
    print("\nError: {}".format(e))
finally:
    if d is not None:
        try:
            d.arm(0)
            d.reset_glitcher()
            d.device.close()
            print("\nRaiden disarmed and closed.")
        except Exception as e:
            print("Cleanup failed: {}".format(e))
