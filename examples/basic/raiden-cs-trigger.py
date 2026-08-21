#!/usr/bin/env python3
"""
Raiden -> ChipSHOUTER, external trigger on IO29.

Wiring:
    trigger source     ---> IO29 (trigger_in), 3.3 V edge
    IO38 invert_glitch ---> ChipSHOUTER SMB hardware trigger
    common ground

What the per-shot loop does NOT do, and why:

  * It does not re-send the glitch parameters. reset_glitcher() only pulses
    glitch.v's reset line, which clears state, vout, the counters, `glitched`
    and `cycles`. delay/width/gap/count/vstart/glitch_max are registers in
    cmd.v and survive it. Sending them once is enough; a sweep only needs to
    push the value it is actually changing.

  * It does not park the line after the shot. With the glitch.v fix in place,
    !enable restores vout to vstart, so arm(0) alone is sufficient. The old
    reset+VSTART block was working around the frozen-vout bug.

  * It does not poll the ChipSHOUTER every shot. trigger_safe only has to be
    renewed inside absent_temp (30 s by default), and faults are worth reading
    periodically rather than after every trigger. Both are USB round trips and
    dominate the loop time when shots come quickly.
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
p.add_argument('-d', '--delay', type=int, default=0, help="ticks from trigger")
p.add_argument('-w', '--width', type=int, default=2, help="pulse width in ticks (2 = 10 ns)")
p.add_argument('-g', '--gap', type=int, default=2000, help="gap in ticks")
p.add_argument('-c', '--count', type=int, default=1, help="pulses per trigger")
p.add_argument('-v', '--vstart', type=int, default=1, help="glitch_out idle level")
p.add_argument('-n', '--shots', type=int, default=5, help="stop after N (0 = forever)")
p.add_argument('--voltage', type=int, default=150, help="ChipSHOUTER voltage")
p.add_argument('--timeout', type=float, default=30.0,
               help="seconds to wait for a trigger before giving up")
p.add_argument('--check-every', type=float, default=10.0, dest="check_every",
               help="seconds between ChipSHOUTER health checks")
p.add_argument('--free', action='store_true', help="free-run instead of IO29 trigger")
p.add_argument('--dry', action='store_true', help="Raiden only, coil stays disarmed")
args = p.parse_args()

if args.voltage > 500:
    sys.exit("Refusing: voltage above 500 V.")
if args.width <= 0:
    sys.exit("Refusing: zero width produces no pulse.")

tick = 1000.0 / args.mhz
print("{} MHz -> {:.1f} ns/tick".format(args.mhz, tick))
print("delay {} = {:.1f} ns | width {} = {:.1f} ns | gap {} = {:.1f} ns | count {}".format(
    args.delay, args.delay * tick, args.width, args.width * tick,
    args.gap, args.gap * tick, args.count))

dev = None
cs = None
shots = 0

try:
    dev = Raiden(mhz=args.mhz, serial_dev=args.port, baud=115200, ticks=True)
    dev.arm(0)
    dev.reset_glitcher()

    if not args.dry:
        cs = ChipSHOUTER(args.cs_port)
        cs.mute = True
        cs.voltage = args.voltage
        # Cycle through disarmed before arming; going straight to armed on a
        # device holding stale state leaves the first shot faulting.
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

    # Everything below is sent once. cmd.v holds these registers until they are
    # overwritten or the FPGA is reconfigured.
    dev.set_trigger_source("free" if args.free else "external")
    dev.set_param(param="CMD_VSTART",       value=args.vstart)
    dev.set_param(param="CMD_GLITCH_MAX",   value=1)
    dev.set_param(param="CMD_GLITCH_DELAY", value=args.delay)
    dev.set_param(param="CMD_GLITCH_WIDTH", value=args.width)
    dev.set_param(param="CMD_GLITCH_GAP",   value=args.gap)
    dev.set_param(param="CMD_GLITCH_COUNT", value=args.count)

    if args.free:
        print("\nfree-run: firing without an external trigger\n")
    else:
        print("\narmed - give a 3.3 V edge on IO29 (CTRL-C to quit)\n")

    next_check = 0.0
    while True:
        if not args.dry and time.time() >= next_check:
            # trigger_safe is a read, not an assignment: reading it takes the
            # sensor sample and renews the window. A False return means the
            # device is disarmed or in a fault, so firing is pointless.
            if not cs.trigger_safe:
                print("trigger not safe: state={} faults={}".format(
                    cs.state, cs.faults_current))
                break
            next_check = time.time() + args.check_every

        dev.reset_glitcher()      # clears glitched/cycles so finished can latch
        dev.arm(1)

        deadline = time.time() + args.timeout
        while not dev.is_finished():
            if time.time() > deadline:
                print("\ntimed out after {:.0f}s waiting for a trigger".format(
                    args.timeout))
                raise KeyboardInterrupt
            time.sleep(0.005)

        dev.arm(0)                # !enable restores vout to vstart in glitch.v
        shots += 1
        print("shot #{}".format(shots))

        if args.shots and shots >= args.shots:
            break

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
    print("total shots: {}".format(shots))
