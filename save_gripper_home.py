import openarm_can as oa
import time
import sys
import os
try:
    import yaml
    has_yaml = True
except ImportError:
    has_yaml = False

def main():
    print("Initializing CAN interfaces for Left (can1) and Right (can0) grippers...")
    try:
        # Initialize right gripper on can0
        arm_r = oa.OpenArm("can0", True)
        arm_r.init_gripper_motor(oa.MotorType.DM4310, 0x08, 0x18)
        
        # Initialize left gripper on can1
        arm_l = oa.OpenArm("can1", True)
        arm_l.init_gripper_motor(oa.MotorType.DM4310, 0x08, 0x18)
    except Exception as e:
        print(f"Failed to initialize CAN interfaces: {e}")
        sys.exit(1)

    print("\nSetting motors to free-drive (limp) mode...")
    arm_r.set_callback_mode_all(oa.CallbackMode.IGNORE)
    arm_l.set_callback_mode_all(oa.CallbackMode.IGNORE)

    arm_r.enable_all()
    arm_l.enable_all()
    arm_r.recv_all()
    arm_l.recv_all()

    arm_r.set_callback_mode_all(oa.CallbackMode.STATE)
    arm_l.set_callback_mode_all(oa.CallbackMode.STATE)

    zero_mit = oa.MITParam(0.0, 0.0, 0.0, 0.0, 0.0)
    arm_r.get_gripper().mit_control_all([zero_mit])
    arm_l.get_gripper().mit_control_all([zero_mit])
    arm_r.recv_all()
    arm_l.recv_all()

    print("\n" + "="*60)
    print("The grippers are now limp. You can move them by hand.")
    print("Please manually push BOTH grippers to their FULLY CLOSED position.")
    print("="*60 + "\n")
    
    input("Press ENTER when both grippers are closed to save their positions...")

    # Request the absolute latest state
    arm_r.refresh_all()
    arm_l.refresh_all()
    time.sleep(0.05)
    arm_r.recv_all()
    arm_l.recv_all()

    motor_r = arm_r.get_gripper().get_motors()[0]
    motor_l = arm_l.get_gripper().get_motors()[0]

    pos_r = motor_r.get_position()
    pos_l = motor_l.get_position()

    # Gracefully disable the motors
    arm_r.disable_all()
    arm_l.disable_all()

    yaml_file = 'gripper_home.yaml'
    
    # Save to YAML
    if has_yaml:
        data = {
            'gripper_home': {
                'right_arm_can0': float(pos_r),
                'left_arm_can1': float(pos_l)
            }
        }
        with open(yaml_file, 'w') as f:
            yaml.dump(data, f, default_flow_style=False)
    else:
        # Fallback to manual yaml writing if PyYAML isn't installed
        with open(yaml_file, 'w') as f:
            f.write("gripper_home:\n")
            f.write(f"  right_arm_can0: {float(pos_r)}\n")
            f.write(f"  left_arm_can1: {float(pos_l)}\n")

    print(f"\nSuccessfully saved closed positions to {os.path.abspath(yaml_file)}")
    print(f"  Right Gripper (can0): {pos_r:.3f} rad")
    print(f"  Left Gripper  (can1): {pos_l:.3f} rad")

if __name__ == "__main__":
    main()
