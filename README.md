# raiden-pro-api

Python control library for the Raiden-Pro glitcher on Artix-7 (Arty A7), with
support for driving a NewAE ChipSHOUTER over its hardware trigger input.

The gateware lives in a separate repository:
**[h0rac/raiden-pro](https://github.com/h0rac/raiden-pro)** — Vivado project,
Verilog sources, constraints and prebuilt bitstreams. This library talks to
whatever is flashed on the board; if something here does not behave, check
`dev.get_buildtime()` against the bitstream you expect to be running.

- [Install](#install)
- [Device mapping](#device-mapping)
- [Quick start](#quick-start)
- [Constructor](#constructor)
- [Methods](#methods)
- [Pin map](#pin-map)
- [Notes on the pulse shape](#notes-on-the-pulse-shape)
- [Raiden + ChipSHOUTER (EMFI)](#raiden--chipshouter-emfi)
- [Reset-driven setup (no external trigger)](#reset-driven-setup-no-external-trigger)

## Install

```bash
git clone https://github.com/h0rac/raiden-pro-api
cd raiden-pro-api
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Requires `pyserial`, and `chipshouter` for the EMFI scripts.

Run examples from the repo root — `python3 script.py` puts the script's own
directory on `sys.path`, not the working directory:

```bash
PYTHONPATH=. python3 examples/basic/raiden-cs.py
```

## Device mapping

The Arty enumerates as an FT2232H with two serial channels. The UART is on
interface 01; interface 00 is the JTAG side. Numbering is not stable across
reconnects, so pin the port with a udev rule:

```
# /etc/udev/rules.d/99-raiden.rules
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6010", \
  ATTRS{serial}=="<your-serial>", ENV{ID_USB_INTERFACE_NUM}=="01", \
  SYMLINK+="raiden", MODE="0666"

# ChipSHOUTER, FT230X
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6015", \
  ATTRS{serial}=="<cs-serial>", SYMLINK+="chipshouter", MODE="0666"
```

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=tty
```

Read the serials with:

```bash
udevadm info -q property -n /dev/ttyUSB1 | grep -E 'SERIAL_SHORT|INTERFACE_NUM'
```

## Quick start

```python
from raiden_python.raiden import Raiden

dev = Raiden(mhz=200, serial_dev="/dev/raiden", ticks=True)

dev.arm(0)
dev.reset_glitcher()
dev.set_trigger_source("external")
dev.set_param(param="CMD_VSTART",       value=1)
dev.set_param(param="CMD_GLITCH_MAX",   value=1)
dev.set_param(param="CMD_GLITCH_DELAY", value=0)
dev.set_param(param="CMD_GLITCH_WIDTH", value=200)   # 200 ticks = 1 us @ 200 MHz
dev.set_param(param="CMD_GLITCH_COUNT", value=1)
dev.arm(1)

while not dev.is_finished():
    pass

dev.arm(0)
dev.disc()
```

## Constructor

```python
Raiden(baud=115200, mhz=100, serial_dev="/dev/raiden",
       ticks=False, debug=False, vstart=1, glitch_max=1)
```

| Argument | Meaning |
|---|---|
| `mhz` | FPGA clock in MHz. Must match the bitstream — a mismatch silently scales every timing value. |
| `ticks` | `True`: timings are raw clock ticks. `False`: seconds, converted with `ceil(hz * value)`. |
| `vstart` | Idle level of `glitch_out`, sent during construction. |
| `glitch_max` | Cycle limit, sent during construction. |
| `debug` | Print every command byte and its echo. |

At 200 MHz one tick is 5 ns.

## Methods

### Timing and pulse shape

```python
dev.set_param(param="CMD_GLITCH_DELAY", value=n)
```

Ticks (or seconds) from the trigger to the first pulse.

```python
dev.set_param(param="CMD_GLITCH_WIDTH", value=n)
```

Width of one pulse. Zero produces no pulse at all.

```python
dev.set_param(param="CMD_GLITCH_GAP", value=n)
```

Spacing between pulses when `count > 1`.

```python
dev.set_param(param="CMD_GLITCH_COUNT", value=n)
```

Pulses per trigger.

```python
dev.set_param(param="CMD_GLITCH_MAX", value=n)
```

Cycles before the glitcher stops. `1` fires once per trigger and latches
`finished`. `0` means no limit: `finished` never asserts and the sequence
repeats for as long as the trigger stays valid — useful for getting a stable
trace on a scope, useless as a campaign oracle.

```python
dev.set_param(param="CMD_VSTART", value=0|1)
```

Idle level of `glitch_out` before the cycle starts. Note that during `DELAY`
the output is driven HIGH regardless, and the pulse itself is a LOW-going
notch; `vstart` only governs the resting level.

```python
dev.set_param(param="CMD_RESET_TARGET", value=n)
```

Length of the reset pulse on `reset_out` (active low). `reset_cnt` and the
delay counter both start when the FSM enters `DELAY`, so `CMD_GLITCH_DELAY` is
measured from the **start** of the reset, not from its release. To land N ticks
after the target comes out of reset, pass `reset_ticks + N`.

### Trigger source

```python
dev.set_trigger_source("external")   # trigger_in on IO29 (default)
dev.set_trigger_source("uart")       # UART pattern match on target_rx
dev.set_trigger_source("emmc")       # eMMC pattern match
dev.set_trigger_source("free")       # trigger held asserted
```

Only the selected source reaches the glitcher. `"free"` is what reset-driven
campaigns need: `CMD_RESET_TARGET` pulses the target, the delay counts from
there, and the shot fires with no external edge involved.

For `"uart"`, set the pattern and the tick count per bit first:

```python
dev.set_param(param="CMD_UART_TRIGGER_BAUD", value=868)   # 100e6 / 115200
dev.set_param(param="CMD_UART_TRIGGER",      value=0xA5)
```

```python
dev.set_param(param="CMD_INVERT_TRIGGER", value=1)
```

Inverts the polarity of the selected source. Leave it off in `"free"` mode —
it would invert the constant assertion and disable the trigger entirely.

### Arming

```python
dev.arm(1)   # arm
dev.arm(0)   # disarm
```

```python
dev.reset_glitcher()
```

Pulses the reset line into `glitch.v`, clearing `state`, `vout`, the counters,
`glitched` and `cycles`. It does **not** clear the parameter registers — delay,
width, gap, count, vstart, glitch_max and the trigger source all live in
`cmd.v` and survive it. A sweep only needs to re-send the value it is changing.

Call this between shots so `glitched` and `cycles` reset and `finished` can
latch again.

### Output override

```python
dev.set_target_power("auto")   # glitcher drives glitch_out
dev.set_target_power("on")     # force glitch_out HIGH
dev.set_target_power("off")    # force glitch_out LOW
```

Anything other than `"auto"` bypasses the glitcher: `enable` is gated on
`force_state == AUTO`, so a forced state stops glitching entirely. Useful for
parking the line at a known level, and for checking wiring with a multimeter.

### Status

```python
dev.flag_status()      # raw bitfield
dev.is_armed()         # bit 0
dev.is_glitched()      # bit 1 - a cycle has started
dev.is_finished()      # bit 2 - the cycle count has been reached
dev.glitch_out()       # bit 3 - current level of glitch_out
dev.is_triggered()     # bit 4 - current level of trigger_in
dev.is_gpio1_in_high() # bit 5
dev.gpio_out_status()  # bit 6
dev.is_gpio2_in_high() # bit 7
```

Each helper is its own serial round trip. In a polling loop, read
`flag_status()` once and mask the bits yourself.

### GPIO

```python
dev.set_param(param="CMD_GPIO_OUT", value=0|1)
```

### Housekeeping

```python
dev.get_buildtime()          # bitstream timestamp from USR_ACCESSE2
dev.check_glitch_frequency() # configured clock in Hz
dev.available_commands()     # list of command names
dev.resync()                 # flush a half-consumed value out of the FSM
dev.disc()                   # close the port
```

`get_buildtime()` is the quickest way to confirm which bitstream is actually
running on the board.

`disc()` deliberately does not send `CMD_RST`.

## Pin map

| Signal | Arty pin | Direction |
|---|---|---|
| `trigger_in` | IO29 (R10) | in, pulldown |
| `glitch_out` | IO41 (N17) | out, active-low pulse |
| `invert_glitch_out` | IO38 (T18) | out, active-high pulse |
| `reset_out` | IO40 (P18) | out, active low |
| `target_rx` | IO30 (R11) | in, pulldown |

`invert_glitch_out` is a hardware inversion of `glitch_out` on a separate pin —
there is nothing to configure. Use IO41 for crowbar work and IO38 for anything
expecting a positive-going trigger.

On the extension board the **SMB socket is wired to IO38**, so a coax straight
from there to a ChipSHOUTER's SMB input is the whole trigger path. Bear in mind
when probing that the header pin and the SMB centre are the same net.

## Notes on the pulse shape

The pulse is a negative-going notch on `glitch_out`:

```
idle (vstart) ___
                 |___ armed, waiting for the trigger
trigger      ____|‾‾‾‾‾‾‾‾‾‾‾‾‾  DELAY drives the output HIGH
                              |
delay elapses                 |__  the pulse
                                 |
width elapses                    |‾‾‾‾‾‾‾  back HIGH until disarm
```

Trigger a scope on the falling edge. The high level before and after the notch
is normal, not a stuck output.

With `glitch_max = 0` and `delay = 0` the last pulse of one cycle and the first
of the next are separated by a single clock tick, which reads as one wide pulse
on anything short of a very fast scope. Give the delay a non-zero value when
counting pulses on screen.

---

## Raiden + ChipSHOUTER (EMFI)

Wiring and settings for driving a NewAE ChipSHOUTER from the Raiden-Pro
hardware trigger. Verified against firmware 2.0.3.

### Wiring

| From | To |
|---|---|
| trigger source | Raiden `trigger_in`, IO29 (R10) |
| Raiden extension board **SMB** socket | ChipSHOUTER SMB hardware trigger |
| common ground | both boards |

**The SMB socket on the Raiden extension board is wired to IO38**
(`invert_glitch_out`, pin T18). There is no jumper or option — plug a coax
between the two SMB connectors and the trigger path is complete.

That choice is deliberate. The ChipSHOUTER's hardware trigger expects a
positive edge in its default polarity, and `glitch_out` on IO41 produces a
negative notch. `invert_glitch_out` is a hardware inversion of it on a separate
pin, so the extension board routes that one to the SMB. IO41 stays available on
the header for crowbar work, where the negative notch is what you want.

If you are probing with a scope, note that measuring IO38 on the header and
measuring the SMB centre pin are the same net — a short there will look like
the FPGA output collapsing.

The MCX connectors on the front panel are the scope monitors, not inputs:
voltage (20:1) and current (10:1, uncalibrated, shape only). They need the
adapter probes that shipped with the device and a 1 MΩ / 10-25 pF scope input.

### ChipSHOUTER settings

```python
from chipshouter import ChipSHOUTER
import time

cs = ChipSHOUTER("/dev/chipshouter")
cs.mute = True                # silence the buzzer
cs.voltage = 150              # 150-500 V; start low
cs.pulse.width = 80           # 80-1000 ns
cs.pulse.repeat = 1
cs.hwtrig_term = False        # see below
cs.hwtrig_mode = True         # True = active high, matches IO38

cs.armed = 0                  # cycle through disarmed first
cs.armed = 1
deadline = time.time() + 20
while time.time() < deadline and cs.state != 'armed':
    time.sleep(0.2)
```

### hwtrig_term

`True` puts 50 Ω across the input, `False` leaves roughly 225 kΩ.

50 Ω is more than a bare LVCMOS33 pin will drive: the signal at the connector
collapses to around 1.2 V, well under the input threshold, and nothing fires.
Getting the termination back means putting a buffer (74AHCT125 or similar, 5 V
rail) between IO38 and the SMB.

With `False` the pin drives the input to a full 3.3 V and the trigger works,
at the cost of an input sensitive enough to pick up noise on an idle cable.
That is only a problem if the line is left floating — see below.

### hwtrig_mode

`True` is active high, `False` is active low. With active low the pin must be
externally driven high while idle or the device sees a permanent trigger.

### Arming

Set `armed = 0` before `armed = 1`. Going straight to armed on a device holding
stale state leaves the first shot faulting.

Then wait for `cs.state == 'armed'`. The device passes through `arming` while
the cap charges — about a second at 150 V, longer higher up. Firing during that
window lands on a device that is not armed yet, which is exactly what
`fault_trigger_glitch` reports.

### trigger_safe

Reading `cs.trigger_safe` — reading, not assigning — takes one sensor sample
and stops the device polling temperature on its own schedule. Sensor reads that
land during a discharge fail and surface as faults.

It has to be renewed inside `absent_temp` seconds (30 by default) or the device
faults for the opposite reason. Reading it every shot is wasteful; every ten
seconds is enough, and a `False` return doubles as a health check.

### Raiden settings

```python
dev = Raiden(mhz=200, serial_dev="/dev/raiden", ticks=True)
dev.arm(0)
dev.reset_glitcher()
dev.set_trigger_source("external")        # or "free" for reset-driven work
dev.set_param(param="CMD_VSTART",       value=1)     # important, see below
dev.set_param(param="CMD_GLITCH_MAX",   value=1)
dev.set_param(param="CMD_GLITCH_DELAY", value=0)
dev.set_param(param="CMD_GLITCH_WIDTH", value=16)    # 16 ticks = 80 ns
dev.set_param(param="CMD_GLITCH_COUNT", value=1)
```

### VSTART must be 1

`vstart = 1` parks `glitch_out` HIGH, which puts `invert_glitch_out` LOW —
inactive for `hwtrig_mode = True`.

With `vstart = 0` the polarity flips: IO38 idles HIGH and the ChipSHOUTER sees
a trigger held asserted indefinitely. Anything past 10 ms is
`fault_trigger_error`, and the device will not arm at all.

### Order of operations

Park the Raiden output **before** arming the coil. Arming the Raiden first
leaves a live trigger line during the second or so the cap takes to charge, and
any edge in that window is a trigger on a disarmed device.

```
Raiden: arm(0), reset_glitcher(), set VSTART
    -> ChipSHOUTER: armed = 0, armed = 1, wait for state == 'armed'
        -> Raiden: set params, arm(1)
```

### Pulse width

The width set on the Raiden governs how long the trigger line is asserted. The
energy delivered to the coil is set on the ChipSHOUTER through `voltage` and
`pulse.width`; the Raiden pulse only has to be wide enough to be recognised.
Widths from 80 ns to 10 µs all trigger reliably in testing.

### Reset-driven setup (no external trigger)

Some targets have no signal worth triggering on. The alternative is to drive
the reset yourself and count from there: Raiden pulses the target's reset line,
waits, and fires. Nothing is connected to IO29.

Wiring adds one line to the EMFI setup above:

| From | To |
|---|---|
| Raiden `reset_out`, IO40 (P18) | target reset (active low) |
| Raiden extension board SMB | ChipSHOUTER SMB |
| common ground | everything |

Check the target's reset polarity and voltage before connecting. `reset_out` is
active low and drives 3.3 V CMOS; a target expecting 5 V, or one with its own
reset supervisor holding the line, needs a level shifter or an open-drain
arrangement rather than a direct connection.

```python
dev.arm(0)
dev.reset_glitcher()
dev.set_trigger_source("free")            # trigger held asserted internally
dev.set_param(param="CMD_VSTART",       value=1)
dev.set_param(param="CMD_GLITCH_MAX",   value=1)
dev.set_param(param="CMD_RESET_TARGET", value=reset_ticks)
dev.set_param(param="CMD_GLITCH_DELAY", value=reset_ticks + window_ticks)
dev.set_param(param="CMD_GLITCH_WIDTH", value=16)
dev.set_param(param="CMD_GLITCH_COUNT", value=1)
dev.arm(1)                                 # the sequence starts here
```

`"free"` holds the trigger asserted inside the FPGA, so `arm(1)` starts
everything immediately:

```
arm(1)
  -> enable asserts, FSM enters DELAY
  -> reset_cnt counts up, reset_out held LOW (target in reset)
  -> reset_cnt == reset_target, reset_out releases HIGH
  -> width_cnt == glitch_delay, FSM enters GLITCH, pulse on glitch_out
```

**Both counters start together.** `reset_cnt` and `width_cnt` begin when the
FSM enters `DELAY`, so `CMD_GLITCH_DELAY` is measured from the **start** of the
reset pulse, not from its release. To land N ticks into the boot, pass
`reset_ticks + N`. Passing anything below `reset_ticks` puts the shot inside
the reset itself, where it achieves nothing.

Pick `reset_ticks` from the target's datasheet — a few hundred microseconds is
typical for a reset pulse, not the tens of milliseconds that a bench script
might get away with. At 200 MHz, 200 µs is 40000 ticks.

Between shots, give the target time to boot before pulling reset again.
Something in the region of a few hundred milliseconds is usually enough, but it
depends entirely on what is running.

```python
while True:
    dev.arm(0)
    dev.reset_glitcher()
    dev.set_param(param="CMD_GLITCH_DELAY", value=delay)   # only what changes
    dev.arm(1)
    while not dev.is_finished():
        time.sleep(0.005)
    dev.arm(0)

    hit = oracle()          # read the target here

    time.sleep(0.3)         # let it boot before the next reset
```

The ChipSHOUTER side is unchanged: same arming order, same `vstart = 1`, same
`hwtrig_mode = True`. Park the Raiden output before arming the coil, because
`arm(1)` in free-run mode fires the moment it lands.

Verify the timing on a scope before connecting a target — one channel on
`reset_out`, one on the SMB. You want to see the reset pulse at its programmed
length, then the glitch landing the intended distance after the release. That
also confirms the polarity is what the target expects.

### Required HDL fix

This one is in the gateware, not here — edit `Raiden.srcs/sources_1/new/glitch.v`
in [h0rac/raiden-pro](https://github.com/h0rac/raiden-pro), resynthesise and
reflash.

Older bitstreams leave `vout` frozen at whatever level the cycle ended on,
because `glitch.v` only restored `vstart` while `glitched` was still low:

```verilog
if(!enable)
  begin
     state <= DELAY;
     if(!glitched)          // once glitched latches, vout never returns
       begin
         vout <= vstart;
       end
```

With `invert_glitch_out` feeding the SMB, a frozen level reads as a trigger
held asserted and every shot ends in `fault_trigger_error`. Drop the guard so
`!enable` always restores `vstart`:

```verilog
if(!enable)
  begin
     state <= DELAY;
     vout <= vstart;
     width_cnt <= 32'd0;
     cnt <= 32'd0;
  end
```

Confirm which bitstream is on the board with `dev.get_buildtime()`. Without
this fix no amount of host-side workaround makes the trigger line behave.

### Faults

| Fault | Meaning |
|---|---|
| `fault_trigger_glitch` | trigger arrived while the device was disarmed |
| `fault_trigger_error` | trigger while disarmed, or held asserted past 10 ms |

Both clear with `cs.faults_current = []`, but only once the cause is gone. If
they come back immediately, the line is sitting in its active state — check
`vstart` and the idle level on IO38 with a meter.

`cs.faults_latched` keeps faults that appeared briefly and cleared themselves,
which `faults_current` will miss.

If `cs.state` reads `fault` with an empty fault list and will not clear, the
firmware is wedged: `cs.reset = True`, wait about eight seconds, or power cycle.
Unplug the SMB first so the line cannot put it straight back.

### Verifying the chain

Work outward, one stage at a time.

**Raiden alone**, coil disarmed, scope on IO38:

```bash
python examples/basic/raiden-cs.py --dry -w 2000 -c 1 -n 1
```

10 µs positive pulse returning to 0 V. If it stays high afterwards, the HDL fix
is missing.

**ChipSHOUTER alone**, software trigger, scope on the voltage monitor:

```bash
python examples/basic/cs-pulse.py -n 10 -i 1 -v 150
```

A sharp spike with ringing that damps out in roughly 1.5 µs. At 150 V the peak
reads around 90 V at the probe, so about 1.8 kV before the 20:1 divider.

**Both**, external trigger on IO29:

```bash
python examples/basic/raiden-cs.py -w 16 -c 1 -n 5 --voltage 150
```

Shots reported with no faults, and the same waveform on the monitor.

Measure the delay between the edge on IO38 and the peak on the voltage monitor
once and keep the number: it is a constant to subtract when calibrating the
delay against a target.
