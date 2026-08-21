#!/usr/bin/env python3
"""
Raiden -> ChipSHOUTER, reset-driven. No external trigger.

Raiden pulses the target's reset line, counts off the delay and fires. Nothing
is connected to IO29. Same structure as raiden-cs.py, with "free" as the
trigger source so arm(1) starts the sequence itself.

Wiring:
    Raiden reset_out, IO40 (P18) ---> target reset (ACTIVE LOW)
    Raiden extension board SMB    ---> ChipSHOUTER SMB
    common ground

Check the target's reset polarity and levels before connecting. reset_out is
active low at 3.3 V CMOS; a 5 V target, or one with its own reset supervisor
holding the line, needs a level shifter or an open-drain arrangement.

TIMING: reset_cnt and the delay counter both start when the FSM enters DELAY,
so --delay is measured from the START of the reset pulse, not from its release.
To land N ticks into the boot, pass reset_ticks + N. The script prints both
figures and refuses a delay that would land inside the reset.
"""
import argparse
import sys
import time

from raiden_python.raiden import Raiden
from chipshouter import ChipSHOUTER

p = argparse.ArgumentParser()
p.add_argument('-p', '--port', default="/dev/raiden")
p.add_argument('--cs-port', default="/dev/chipshouter", dest="cs_port")
p.add_argument('-m', '--mhz', type=int, default=200)
p.add_argument('-r', '--reset', type=int, default=20000,
               help="reset pulse length in ticks (40000 = 200 us @ 200 MHz)")
p.add_argument('-d', '--delay', type=int, default=30000,
               help="ticks from the START of reset to the glitch")
p.add_argument('-w', '--width', type=int, default=16, help="pulse width in ticks (16 = 80 ns)")
p.add_argument('-g', '--gap', type=int, default=2000, help="gap in ticks")
p.add_argument('-c', '--count', type=int, default=1, help="pulses per cycle")
p.add_argument('-v', '--vstart', type=int, default=1, help="glitch_out idle level")
p.add_argument('-n', '--shots', type=int, default=5, help="stop after N (0 = forever)")
p.add_argument('--voltage', type=int, default=150, help="ChipSHOUTER voltage")
p.add_argument('--settle', type=float, default=0.3,
               help="seconds to let the target boot before the next reset")
p.add_argument('--timeout', type=float, default=30.0,
               help="seconds to wait for a cycle to finish before giving up")
p.add_argument('--check-every', type=float, default=10.0, dest="check_every",
               help="seconds between ChipSHOUTER health checks")
p.add_argument('--dry', action='store_true', help="Raiden only, coil stays disarmed")
args = p.parse_args()

if args.voltage > 500:
    sys.exit("Refusing: voltage above 500 V.")
if args.width <= 0:
    sys.exit("Refusing: zero width produces no pulse.")
if args.delay < args.reset:
    sys.exit("delay ({}) is inside the reset pulse ({}) - the shot would land "
             "while the target is still held in reset.".format(args.delay, args.reset))

tick = 1000.0 / args.mhz
after = args.delay - args.reset
print("{} MHz -> {:.1f} ns/tick".format(args.mhz, tick))
print("reset {:>8} ticks = {:>9.1f} us".format(args.reset, args.reset * tick / 1000.0))
print("delay {:>8} ticks = {:>9.1f} us from reset START".format(
    args.delay, args.delay * tick / 1000.0))
print("                        = {:>9.1f} us AFTER reset release".format(
    after * tick / 1000.0))
print("width {:>8} ticks = {:>9.1f} ns".format(args.width, args.width * tick))


def oracle():
    """Post-glitch target check. Return True on a hit.

    Left empty on purpose. For a TriCore target over BSL this is where the
    autobaud handshake goes: wait for the start byte after reset and treat a
    missing or mangled response as a candidate.
    """
    return False


dev = None
cs = None
shots = 0
hits = 0

try:
    dev = Raiden(mhz=args.mhz, serial_dev=args.port, baud=115200, ticks=True)
    dev.arm(0)
    dev.reset_glitcher()

    if not args.dry:
        cs = ChipSHOUTER(args.cs_port)
        cs.mute = True
        cs.voltage = args.voltage
        cs.armed = 0
        cs.armed = 1
        cs.absent_temp = 30
        deadline = time.time() + 20
        while time.time() < deadline and cs.state != 'armed':
            time.sleep(0.2)
        if cs.state != 'armed':
            sys.exit("did not reach armed: state={} faults={}".format(
                cs.state, cs.faults_current))
        print("ChipSHOUTER {} | {} V | {}".format(
            str(cs.id).strip(), cs.voltage.measured, cs.state))
    else:
        print("DRY RUN - coil not armed")

    print(dev.get_buildtime())

    # Sent once. reset_glitcher() clears the glitcher's state, not these
    # registers, so a sweep only has to push the value it is changing.
    dev.set_trigger_source("free")
    dev.set_param(param="CMD_VSTART",       value=args.vstart)
    dev.set_param(param="CMD_GLITCH_MAX",   value=1)
    dev.set_param(param="CMD_RESET_TARGET", value=args.reset)
    dev.set_param(param="CMD_GLITCH_DELAY", value=args.delay)
    dev.set_param(param="CMD_GLITCH_WIDTH", value=args.width)
    dev.set_param(param="CMD_GLITCH_GAP",   value=args.gap)
    dev.set_param(param="CMD_GLITCH_COUNT", value=args.count)

    print("\nfree-run: arm(1) resets the target and fires. CTRL-C to quit.\n")

    next_check = 0.0
    while True:
        if not args.dry and time.time() >= next_check:
            if not cs.trigger_safe:
                print("trigger not safe: state={} faults={}".format(
                    cs.state, cs.faults_current))
                break
            next_check = time.time() + args.check_every

        dev.reset_glitcher()
        dev.arm(1)

        deadline = time.time() + args.timeout
        while not dev.is_finished():
            if time.time() > deadline:
                print("\ntimed out after {:.0f}s".format(args.timeout))
                raise KeyboardInterrupt
            time.sleep(0.005)

        dev.arm(0)
        shots += 1

        if oracle():
            hits += 1
            print("[!] HIT on shot #{} (reset={}, delay={}, width={})".format(
                shots, args.reset, args.delay, args.width))
        else:
            print("shot #{}".format(shots))

        if args.shots and shots >= args.shots:
            break

        # Let the target come up before pulling reset again.
        time.sleep(args.settle)

except KeyboardInterrupt:
    print("\nInterrupted.")
except Exception as e:
    print("\nError: {}".format(e))
finally:
    if cs is not None:
        try:
            cs.armed = 0
            print("ChipSHOUTER disarmed. latched: {}".format(cs.faults_latched))
        except Exception as e:
            print("Disarm failed: {} - power it down manually.".format(e))
    if dev is not None:
        try:
            dev.arm(0)
            dev.reset_glitcher()
            dev.disc()
        except Exception as e:
            print("Raiden cleanup failed: {}".format(e))
    print("shots: {}  hits: {}".format(shots, hits))
