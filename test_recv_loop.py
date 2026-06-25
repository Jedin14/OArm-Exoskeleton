import openarm_can as oa
import time
canport = "can0"
openarm = oa.OpenArm(canport, True)
openarm.init_arm_motors(
    [oa.MotorType.DM8009, oa.MotorType.DM8009, oa.MotorType.DM4340, oa.MotorType.DM4340,
     oa.MotorType.DM4310, oa.MotorType.DM4310, oa.MotorType.DM4310],
    [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07],
    [0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17]
)
openarm.set_callback_mode_all(oa.CallbackMode.STATE)
openarm.enable_all()
for i in range(10):
    openarm.recv_all()
    time.sleep(0.01)
arm = openarm.get_arm()
initial_arm_q = [m.get_position() for m in arm.get_motors()]
print("Initial arm q after 10 loops:", initial_arm_q)
openarm.disable_all()
