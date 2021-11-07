from raiden_python import raiden 
import time
raiden = raiden.Raiden(mhz=100, serial_dev="/dev/lab_raiden", baud= 115200, ticks= True) 

raiden.reset_glitcher()
raiden.arm(0)

print(raiden.get_buildtime())
raiden.set_param(param="CMD_VSTART", value=1)
raiden.set_param(param="CMD_GLITCH_MAX", value= 1)

raiden.set_target_power("off")
time.sleep(1)
raiden.set_target_power("on")
time.sleep(1)

while True:	   
    raiden.set_target_power("auto")
    raiden.arm(0)  
    raiden.reset_glitcher()	    
    raiden.set_param(param="CMD_GLITCH_DELAY", value= 0)
    raiden.set_param(param="CMD_GLITCH_WIDTH", value= 8)
    raiden.set_param(param="CMD_GLITCH_GAP", value= 5)
    raiden.set_param(param="CMD_GLITCH_COUNT", value= 3)
    raiden.set_param(param="CMD_UART_TRIGGER", value= 0x4A)
    raiden.set_param(param="CMD_UART_TRIGGER_BAUD", value=115200)
    raiden.arm(1)
    while not raiden.is_finished():
        pass
    print("glitch cycle done")
