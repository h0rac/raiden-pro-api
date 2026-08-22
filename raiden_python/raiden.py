import struct 
import serial
import os
import math
import time

class Raiden:
    """
    Raiden defines class fields and methods
    """
    def __init__(self, baud= 115200, mhz= 100, serial_dev="/dev/cu.usbserial-00004114B", ticks= False, debug=False, vstart=1, glitch_max=1):
        """
        Construct a new 'Raiden' object.

        :param mhz: FPGA used frequency
        :param baud: Baud rate for UART 
        :param serial_dev: UART serial port for FPGA
        :param ticks: if set to True, seconds param take clock ticks, if set to False take time in seconds
        """
        self._hz= float(mhz * 1000000)
        self.ticks= ticks
        self.vstart = vstart
        self.glitch_max = glitch_max
        self.debug = debug
        self._commands = {
            "CMD_RST_GLITCHER":65,
            "CMD_FORCE_GLITCH_OUT_STATE": 66,
            "CMD_FLAGS_STATUS":68,
            "CMD_GLITCH_DELAY":69,
            "CMD_GLITCH_WIDTH":70,
            "CMD_GLITCH_COUNT":71,
            "CMD_ARM":72,
            "CMD_GLITCH_GAP":73,
            "CMD_RST": 74,
            "CMD_VSTART":75,
            "CMD_GLITCH_MAX":76,
            "CMD_BUILDTIME":77,
            "CMD_INVERT_TRIGGER":78,
            "CMD_RESET_TARGET":79,
            "CMD_GPIO_OUT": 80,
            "CMD_UART_TRIGGER":81,
            "CMD_UART_TRIGGER_BAUD":82,
            "CMD_EMMC_TRIGGER_DATA":83,
            "CMD_TRIGGER_SRC":84,
            "CMD_BDM_CTRL":85,
            "CMD_BDM_XFER":86
        }
        
        self.device = serial.Serial(serial_dev, baudrate= baud, timeout=2.5, writeTimeout=2.5)
        self.device.rtscts = False
        self.device.dsrdtr = False 
        self.__raiden_cmd(self.device, self._commands["CMD_VSTART"], self.vstart)
        self.__raiden_cmd(self.device, self._commands["CMD_GLITCH_MAX"], self.glitch_max)
        print("Raiden started...")

    def check_glitch_frequency(self):
        return self._hz

    def __raiden_cmd(self, device, command, value=None):

        cmd = [k for k,v in self._commands.items() if v == command][0]
        byte_cmd = chr(command).encode("ASCII")

        if cmd == "CMD_BUILDTIME":
            self.device.write(chr(command).encode("ASCII"))
            raw = device.read(4)
            return raw

        if cmd == "CMD_FLAGS_STATUS":
             self.device.write(chr(command).encode("ASCII"))
             raw =  device.read(1)[0]
             return raw

        #  CMD assertion
        if self.debug:
            print("[+] CMD byte to send {0}, CMD: {1}".format(byte_cmd, cmd))
        self.device.write(chr(command).encode("ASCII"))
        raw =  device.read(1)
        if self.debug:
            print("[+] CMD byte recv {0}, CMD int recv: {1}".format(raw, raw[0]))
        assert (raw == byte_cmd), "send:{0} recv_raw:{1}".format(byte_cmd, raw)
        
        # Single Byte command handling
        if (ord(raw) == self._commands["CMD_VSTART"] or 
            ord(raw) == self._commands["CMD_ARM"] or 
            ord(raw) == self._commands["CMD_FORCE_GLITCH_OUT_STATE"] or 
            ord(raw) == self._commands["CMD_RST"] or
            ord(raw) == self._commands["CMD_INVERT_TRIGGER"] or
            ord(raw) == self._commands["CMD_GPIO_OUT"] or
            ord(raw) == self._commands["CMD_UART_TRIGGER"] or
            ord(raw) == self._commands["CMD_TRIGGER_SRC"] or
            ord(raw) == self._commands["CMD_RST_GLITCHER"]):

            data = struct.pack(">B", value)
            if self.debug:
                print("[+] outgoing byte", data)
            self.device.write(data)
            raw = device.read(1)
            assert (raw == data), "send:{0} recv_raw:{1}".format(data, raw)
            if self.debug:
                print("[+] incoming byte", raw)
            return

        # BDM commands answer with data from the target rather than an echo of
        # what was sent, so they get their own branch - the four-byte path
        # below asserts reply == request, which would fail on every transfer.
        if (ord(raw) == self._commands["CMD_BDM_CTRL"] or
            ord(raw) == self._commands["CMD_BDM_XFER"]):

            data = struct.pack(">I", value)
            if self.debug:
                print("[+] BDM outgoing", data.hex())
            self.device.write(data)
            raw = device.read(4)[::-1]
            if len(raw) != 4:
                raise IOError("short BDM reply: {} bytes".format(len(raw)))
            reply = struct.unpack(">I", raw)[0]
            if self.debug:
                print("[+] BDM incoming {:08x}".format(reply))
            return reply

        # 4 byte command handling
        if (ord(raw) == self._commands["CMD_GLITCH_DELAY"] or 
            ord(raw) == self._commands["CMD_GLITCH_WIDTH"] or 
            ord(raw) == self._commands["CMD_GLITCH_COUNT"] or
            ord(raw) == self._commands["CMD_GLITCH_GAP"] or
            ord(raw) == self._commands["CMD_GLITCH_MAX"] or
            ord(raw) == self._commands["CMD_UART_TRIGGER_BAUD"] or
            ord(raw) == self._commands["CMD_EMMC_TRIGGER_DATA"] or
            ord(raw) == self._commands["CMD_RESET_TARGET"]):

            data = struct.pack(">I", value)
            if self.debug:
                print("[+] outgoing bytes", data)
            self.device.write(data)
            raw = device.read(4)[::-1]
            assert (raw == data), "send:{0} recv_raw:{1}".format(data, raw)
            if self.debug:
                print("[+] incoming bytes", raw)
            return
        return

    # ------------------------------------------------------------------ #
    # MPC5xx development port (BDM)
    #
    # A frame is 35 bits: start, mode, control, then 32 bits of payload. The
    # start bit is asserted in hardware. Because 35 bits do not fit one 32-bit
    # command, mode/control and the DSCK idle level live in their own register
    # that persists across transfers, and the three status bits of a reply come
    # back on the acknowledge of the NEXT bdm_ctrl call.
    #
    #   mode control
    #    0     0     instruction to CPU
    #    0     1     data to CPU
    #    1     0     write trap enable control register
    #    1     1     debug port command
    # ------------------------------------------------------------------ #

    def bdm_ctrl(self, mode=0, control=0, dsck_idle=0):
        """Set the frame type for following transfers.

        Returns the three status bits of the previous transfer: bit 2 is
        ready, bits 1:0 the status field. On a locked part that never answers
        these read back as whatever the pulldown gives, which is how a dead
        link looks.
        """
        value = (control & 1) | ((mode & 1) << 1) | ((dsck_idle & 1) << 2)
        return self.__raiden_cmd(self.device, self._commands["CMD_BDM_CTRL"], value)

    def bdm_xfer(self, data=0):
        """Shift one 35-bit frame. Returns the low 32 bits of the reply.

        Blocks in the FPGA until the frame completes - a few microseconds at
        the default divider, well under the serial turnaround, so there is
        nothing to poll.
        """
        return self.__raiden_cmd(self.device, self._commands["CMD_BDM_XFER"],
                                 data & 0xFFFFFFFF)

    def bdm_instruction(self, word):
        """Hand an instruction to the CPU (mode 0, control 0)."""
        self.bdm_ctrl(mode=0, control=0)
        return self.bdm_xfer(word)

    def bdm_data(self, word):
        """Hand data to the CPU (mode 0, control 1)."""
        self.bdm_ctrl(mode=0, control=1)
        return self.bdm_xfer(word)

    def bdm_command(self, opcode):
        """Send a debug port command (mode 1, control 1).

        Opcodes from Table 22-11: 0 NOP, 1 hard reset, 2 soft reset,
        0x1F breakpoint control.
        """
        self.bdm_ctrl(mode=1, control=1)
        return self.bdm_xfer(opcode)

    def bdm_status(self):
        """Read the status bits left by the last transfer.

        Bit 2 is ready, bits 1:0 the status field:
            00  valid data from CPU
            01  sequencing error - the freeze bit rides in the data field
            10  CPU interrupt
            11  null
        """
        return self.bdm_ctrl() & 0x7

    def bdm_frozen(self):
        """True when the CPU is sitting in debug mode.

        The freeze indication is bit 31 of the data field of a sequencing
        error reply; section 22.5.5 also exposes it on the VFLS pins, but
        reading it out of the bitstream costs no extra wiring.
        """
        status = self.bdm_status()
        if (status & 0x3) != 0x1:
            return False
        return bool(self.bdm_xfer(0) & 0x80000000)

    def set_param(self, param="CMD_GLITCH_DELAY", value=1):
        """
        Set glitching params before arming Raiden.

        :param param: Raiden command
        :param value: value for CMD_GLITCH_COUNT, CMD_VSTART, CMD_GLITCH_MAX
        """
        if(param == "CMD_GLITCH_COUNT" or param == "CMD_VSTART" or param == "CMD_GLITCH_MAX" or param == "CMD_INVERT_TRIGGER" or param == "CMD_GPIO_OUT"):
            self.__raiden_cmd(self.device, self._commands[param], int(value))
            return
        if(self.ticks):
            fpga_ticks= int(value)
        else:
            fpga_ticks= math.ceil(self._hz * value)
        self.__raiden_cmd(self.device, self._commands[param], fpga_ticks)
    
    def arm(self, value=1):
        """
        Arm Raiden for pulse generation.

        :param value: True/False 0/1, where True arm Raiden and False disarm
        """
        if(value == 0 or value == 1):
             self.__raiden_cmd(self.device, self._commands["CMD_ARM"], value)
             return
        else:
            print("Supported values arm values 1 or 0")
            return

    def set_trigger_source(self, source="external"):
        """
        Select which source is allowed to fire the glitcher.

        Only the selected source reaches the glitcher. All three used to be
        XOR-ed together, so an unconfigured UART trigger sitting high would
        invert the external trigger rather than being ignored.

        "free" holds the trigger asserted and is what reset-driven campaigns
        need: CMD_RESET_TARGET pulses the target, CMD_GLITCH_DELAY counts from
        the release, and the shot fires with no external edge at all. This
        replaces strapping trigger_in to 3.3 V.

        :param source: "external" (trigger_in), "uart", "emmc", or "free"
        """
        sources = {"external": 0, "uart": 1, "emmc": 2, "free": 3}
        key = source.lower()
        if key not in sources:
            raise ValueError("source must be one of {}".format(sorted(sources)))
        self.__raiden_cmd(self.device, self._commands["CMD_TRIGGER_SRC"],
                          sources[key])

    def set_target_power(self, power="auto"):
        """
        Target reset control.

        :param power: "on" set voltage line HIGH, "off set voltage line LOW
        """
        if power.lower() == "auto":
            self.__raiden_cmd(self.device, self._commands["CMD_FORCE_GLITCH_OUT_STATE"], 2)
        if power.lower() == "on":
            self.__raiden_cmd(self.device, self._commands["CMD_FORCE_GLITCH_OUT_STATE"], 1)
        if power.lower() == "off":
            self.__raiden_cmd(self.device, self._commands["CMD_FORCE_GLITCH_OUT_STATE"], 0)

    def flag_status(self):
        """
        Return status of internal flags

        :return bitfield:
          flags[0] armed       - API armed
          flags[1] glitched    - glitching has started
          flags[2] finished    - glitching has completed
          flags[3] glitch_out  - current state of glitch out
          flags[4] trigger_in  - current state of trigger in
          flags[5] gpio_in - current GPIO_IN status
          flags[6] gpio1_out - current GPIO1_OUT status
          flags[7] gpio2_out - current GPIO2_OUT status
        """
        return self.__raiden_cmd(self.device, self._commands["CMD_FLAGS_STATUS"])

    def is_armed(self):
        """
        Checks status of armed flag

        :return 0 if not armed, 1 if armed
        """
        return ((self.flag_status()) & 0x01)
    
    def is_glitched(self):
        """
        Checks status of glitched flag

        :return 0 if glitching has not started, 1 if started
        """
        return ((self.flag_status() >> 1) & 0x01)
    
    def is_finished(self):
        """
        Checks status of finished flag

        :return 0 if glitching has not finished, 1 if finished
        """
        return ((self.flag_status() >> 2) & 0x01)
    
    def glitch_out(self):
        """
        Checks status of glitch_out

        :return 0 if glitch_out is LOW and 1 if HIGH
        """
        return ((self.flag_status() >> 3) & 0x01)
    
    def is_triggered(self):
        """
        Checks status of trigger

        :return 0 if external trigger is LOW and 1 if HIGH
        """
        return ((self.flag_status() >> 4) & 0x01)
   
    def is_gpio1_in_high(self):
        """
        :return 0 if GPIO is not HIGH, else return 1
        """
        return ((self.flag_status()>>5) & 0x01)

    def is_gpio2_in_high(self):
        """
        :return 0 if GPIO is not HIGH, else return 1
        """
        return ((self.flag_status()>>7) & 0x01)

    def gpio_out_status(self):
        """
        return 0 if GPIO LOW or 1 if GPIO HIGH
        """
        return ((self.flag_status()>>6) &0x01)

    def reset_glitcher(self):
        """
        Reset Raiden modules to default values
        """
        self.__raiden_cmd(self.device, self._commands["CMD_RST_GLITCHER"], 1)
    
    
    def disc(self):
        """
        Close the serial port.

        CMD_RST is deliberately NOT sent here. In cmd.v the RST state has no
        exit path back to IDLE, so once the FSM enters it every later byte is
        echoed blindly and never parsed as an opcode. The next process to open
        the port then fails on a truncated read, and only reconfiguring the
        FPGA clears it. Callers wanting the glitcher back at defaults should
        use reset_glitcher(), which returns to IDLE correctly.
        """
        self.device.close()

    def resync(self):
        """
        Flush a half-consumed 4-byte value out of the FPGA command FSM.

        0x00 is not a valid opcode (valid range is 65-83), so an idle Raiden
        ignores the padding while one stuck waiting on the tail of a 4-byte
        value consumes it and falls back to IDLE. Returns True once the
        device answers a single-byte status read.
        """
        for _ in range(3):
            self.device.reset_input_buffer()
            self.device.reset_output_buffer()
            self.device.write(b"\x00" * 8)
            time.sleep(0.15)
            self.device.reset_input_buffer()
            self.device.write(chr(self._commands["CMD_FLAGS_STATUS"]).encode("ASCII"))
            if len(self.device.read(1)) == 1:
                return True
        return False
    
    def available_commands(self):
        """
        Available commands for Raiden

        :return available Raiden commands as a list
        """
        return [x for x in self._commands]

    def get_buildtime(self):
        raw = self.__raiden_cmd(self.device, self._commands["CMD_BUILDTIME"])
        day    = raw[3] >> 3
        month  = ((raw[3] & 0x7) << 1) + (raw[2] >> 7)
        year   = ((raw[2] >> 1) & 0x3f) + 2000
        hour   = ((raw[2] & 0x1) << 4) + (raw[1] >> 4)
        minute = ((raw[1] & 0xf) << 2) + (raw[0] >> 6)
        second = raw[0] & 0x3f
        return "Raiden build time: {:02d}/{:02d}/{}, {:02d}:{:02d}:{:02d}".format(
            day, month, year, hour, minute, second)

