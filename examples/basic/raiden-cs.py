#!/usr/bin/env python3
"""
Bring-up test: external trigger -> Raiden -> ChipSHOUTER.

Wiring:
    trigger source    ---> Raiden trigger_in  (3.3 V edge, 10k pulldown to GND)
    Raiden glitch_out ---> ChipSHOUTER HW trigger input
    common ground between all three

The ChipSHOUTER is armed over USB and fired by the hardware edge from
glitch_out. USB is only used for arm / voltage / fault readback.

Goal: prove the chain fires at all. No sweep, no oracle, no target reset.
One trigger edge = one coil pulse.

Note on arming: the device passes through a transient 'arming' state while
the cap charges, roughly a second at 150 V and longer at higher voltages.
Reading cs.armed too early returns False with an empty fault list, which
looks like a refusal but is not. arm_cs() polls through that window.
"""
import argparse
import sys
import time

from raiden_python.raiden import Raiden
from chipshouter import ChipSHOUTER

p = argparse.ArgumentParser()
p.add_argument('-p', '--port', default="/dev/raiden", help="Raiden serial port")
p.add_argument('--cs-port', default="/dev/chipshouter", dest="cs_port")
p.add_argument('-m', '--mhz', type=int, default=200, help="FPGA clock in MHz")
p.add_argument('-d', '--delay', type=int, default=0, help="Delay in ticks from trigger")
p.add_argument('-w', '--width', type=int, default=200, help="glitch_out pulse width in ticks")
p.add_argument('-g', '--gap', type=int, default=1000, help="Gap between pulses in ticks")
p.add_argument('-c', '--count', type=int, default=1, help="Pulses per trigger")
p.add_argument('-v', '--vstart', type=int, default=0, help="glitch_out idle level 0/1")
p.add_argument('-i', '--invert', action='store_true', help="Invert Raiden trigger polarity")
p.add_argument('-n', '--shots', type=int, default=5, help="Stop after N triggers (0 = forever)")
p.add_argument('--voltage', type=int, default=150, dest="voltage",
               help="ChipSHOUTER charge voltage")
p.add_argument('--cs-width', type=int, default=80, dest="cs_width",
               help="ChipSHOUTER internal pulse width in ns")
p.add_argument('--arm-timeout', type=float, default=8.0, dest="arm_timeout",
               help="Seconds to wait for the arming state to settle")
p.add_argument('--dry', action='store_true', help="Raiden only, do not arm the coil")
args = p.parse_args()

if args.voltage > 500:
    sys.exit("Refusing: voltage above 500 V.")
if args.width <= 0:
    sys.exit("Refusing: width of 0 ticks produces no pulse on glitch_out.")

tick_ns = 1000.0 / args.mhz
print("clock {} MHz -> {:.1f} ns/tick".format(args.mhz, tick_ns))
print("delay {} ticks = {:.2f} us | width {} ticks = {:.1f} ns".format(
    args.delay, args.delay * tick_ns / 1000.0, args.width, args.width * tick_ns))


def arm_cs(cs, timeout):
    """Arm and poll through the transient 'arming' state.

    Returns True once cs.armed reads True, False on timeout. An empty
    faults_current alongside a False return means the cap simply did not
    finish charging in time -- raise --arm-timeout rather than assuming
    a hardware problem.
    """
    cs.armed = True
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.2)
        if cs.armed:
            return True
    return False


def configure_raiden(dev):
    """Reload every parameter and arm.

    reset_glitcher() wipes config back to defaults, so this must run in full
    before each arm. GLITCH_MAX is 1 so `finished` latches after each cycle,
    giving a clean point to read faults and re-arm.
    """
    dev.arm(0)
    dev.reset_glitcher()
    dev.set_param(param="CMD_GLITCH_DELAY", value=args.delay)
    dev.set_param(param="CMD_GLITCH_WIDTH", value=args.width)
    dev.set_param(param="CMD_GLITCH_GAP",   value=args.gap)
    dev.set_param(param="CMD_GLITCH_COUNT", value=args.count)
    dev.set_param(param="CMD_VSTART",       value=args.vstart)
    dev.set_param(param="CMD_GLITCH_MAX",   value=1)
    dev.set_trigger_source("external")
    if args.invert:
        dev.set_param(param="CMD_INVERT_TRIGGER", value=1)
    dev.arm(1)


dev = None
cs = None
shots = 0
fault_shots = 0

try:
    # ---------------- ChipSHOUTER ----------------
    cs = ChipSHOUTER(args.cs_port)
    print("ChipSHOUTER {} | mosfet {}C diode {}C".format(
        str(cs.id).strip(), cs.temperature_mosfet, cs.temperature_diode))

    cs.faults_current = []
    time.sleep(0.5)

    cs.mute = True
    cs.voltage = args.voltage
    cs.pulse.width = args.cs_width
    cs.pulse.repeat = 1
    cs.hwtrig_term = True

    residual = cs.faults_current
    if residual:
        sys.exit("Faults present after clear: {} -- check HW trigger wiring.".format(residual))
    print("pulse.width readback: {} (requested {})".format(cs.pulse.width, args.cs_width))

    # ---------------- Raiden ----------------
    dev = Raiden(mhz=args.mhz, serial_dev=args.port, baud=115200, ticks=True)
    print(dev.get_buildtime())
    configure_raiden(dev)

    # ---------------- arm the coil ----------------
    if args.dry:
        print("DRY RUN - coil not armed")
    else:
        print("arming ChipSHOUTER at {} V...".format(args.voltage))
        if not arm_cs(cs, args.arm_timeout):
            sys.exit("Failed to arm: state={} faults={}".format(
                cs.state, cs.faults_current))
        print("ChipSHOUTER armed (state={})".format(cs.state))

    print("Give a 3.3 V edge on trigger_in. CTRL-C to quit.\n")

    while True:
        flags = dev.flag_status()
        armed    = (flags >> 0) & 1
        glitched = (flags >> 1) & 1
        finished = (flags >> 2) & 1
        gout     = (flags >> 3) & 1
        trig     = (flags >> 4) & 1

        print("armed={} trig={} glitched={} finished={} out={}  shots={} cs_faults={}   ".format(
            armed, trig, glitched, finished, gout, shots, fault_shots),
            end="\r", flush=True)

        if finished:
            shots += 1
            faults = []
            if not args.dry:
                faults = cs.faults_current
                if faults:
                    fault_shots += 1
                    cs.faults_current = []
            print("\nshot #{}{}".format(
                shots, "  faults: {}".format(faults) if faults else "  ok"))

            if args.shots and shots >= args.shots:
                break

            configure_raiden(dev)

            # The coil often stays armed across a shot; only pay the charge
            # cost when it actually dropped out.
            if not args.dry and not cs.armed:
                if not arm_cs(cs, args.arm_timeout):
                    print("re-arm failed: state={} faults={}".format(
                        cs.state, cs.faults_current))
                    break

        time.sleep(0.02)

except KeyboardInterrupt:
    print("\nInterrupted.")
except Exception as e:
    print("\nError: {}".format(e))
finally:
    # Disarm the coil first; a live ChipSHOUTER matters more than a dangling
    # serial handle.
    if cs is not None:
        try:
            cs.armed = False
            print("ChipSHOUTER disarmed.")
        except Exception as e:
            print("Disarm failed: {} -- power it down manually.".format(e))
    if dev is not None:
        try:
            dev.arm(0)
            dev.reset_glitcher()
            dev.disc()
        except Exception as e:
            print("Raiden cleanup failed: {}".format(e))
    print("total shots: {} (with faults: {})".format(shots, fault_shots))
