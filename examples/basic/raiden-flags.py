from raiden_python import raiden
import argparse
import serial
import time

parser = argparse.ArgumentParser()
parser.add_argument('-p', '--port',
                    required=False,
                    type=str,
                    default="/dev/raiden", 
                    dest="port",
                    metavar="<port>",
                    help="Raiden serial port")

args = parser.parse_args()
print(args)


try:
    raiden = raiden.Raiden(mhz= 100, serial_dev= args.port, baud= 115200, ticks= True)
except:
    print("Can't open Raiden!")
    exit(True)

def showflags():
    stat= raiden.flag_status()
    print("Current flags: %d" % stat)
    print("  Armed: %d" % raiden.is_armed())
    print("  Trigger: %d" % raiden.is_triggered())
    print("  Glitched: %d" % raiden.is_glitched());
    print("  Finished: %d" % raiden.is_finished());
    print("  Power: %d" % raiden.glitch_out());
    print("  GPIO1_in: %d" % raiden.is_gpio1_in_high());
    print("  GPIO2_in: %d" % raiden.is_gpio2_in_high());
    print("  GPIO_out: %d" % raiden.gpio_out_status());

print(raiden.get_buildtime())

print("clearing flags")
print("")
raiden.reset_glitcher()
raiden.arm(0)
raiden.set_target_power("off")
showflags()

print("")
print("arming...")
raiden.arm(1)
showflags()

print("")
print("power on...")
raiden.set_target_power("on")
showflags()

print("")
print("gpio_out on...")
raiden.set_param(param="CMD_GPIO_OUT", value= True)
showflags()

print("")
print("toggling gpio_out")
raiden.set_param(param="CMD_GPIO_OUT", value= False)
time.sleep(1)
raiden.set_param(param="CMD_GPIO_OUT", value= True)
print("done")
print("CTL-C to exit")

while 42:
    pass
