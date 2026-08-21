# raiden-pro-api

Python control library for the Raiden-Pro glitcher on Artix-7 (Arty A7).

## Install

```bash
git clone https://github.com/h0rac/raiden-pro-api
cd raiden-pro-api
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Requires `pyserial`.

## Device mapping

The Arty enumerates as an FT2232H with two serial channels. The UART is on
interface 01; interface 00 is the JTAG side. Numbering is not stable across
reconnects, so pin the port with a udev rule:

```
# /etc/udev/rules.d/99-raiden.rules
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6010", \
  ATTRS{serial}=="<your-serial>", ENV{ID_USB_INTERFACE_NUM}=="01", \
  SYMLINK+="raiden", MODE="0666"
```

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=tty
```

Read your serial with `udevadm info -q property -n /dev/ttyUSB1 | grep SERIAL_SHORT`.

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
expecting a positive-going trigger, such as a ChipSHOUTER SMB input.

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
