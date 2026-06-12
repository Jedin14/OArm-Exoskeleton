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
    yaml_file = 'gripper_home.yaml'
    if not os.path.exists(yaml_file):
        print(f"ERROR: '{yaml_file}' not found.", file=sys.stderr)
        print("Please run 'python3 save_gripper_home.py' first to record the closed position.", file=sys.stderr)
        sys.exit(2)

    # Read target positions
    target_r = 0.0
    target_l = 0.0
    try:
        if has_yaml:
            with open(yaml_file, 'r') as f:
                data = yaml.safe_load(f)
                target_r = data['gripper_home']['right_arm_can0']
                target_l = data['gripper_home']['left_arm_can1']
        else:
            with open(yaml_file, 'r') as f:
                lines = f.readlines()
                for line in lines:
                    if 'right_arm_can0' in line:
                        target_r = float(line.split(':')[1].strip())
                    if 'left_arm_can1' in line:
                        target_l = float(line.split(':')[1].strip())
    except Exception as e:
        print(f"Failed to parse {yaml_file}: {e}", file=sys.stderr)
        sys.exit(2)

    print(f"Moving Right Gripper to {target_r:.3f} rad")
    print(f"Moving Left Gripper to {target_l:.3f} rad")

    try:
        arm_r = oa.OpenArm("can0", True)
        arm_r.init_gripper_motor(oa.MotorType.DM4310, 0x08, 0x18)
        
        arm_l = oa.OpenArm("can1", True)
        arm_l.init_gripper_motor(oa.MotorType.DM4310, 0x08, 0x18)
    except Exception as e:
        print(f"Failed to initialize CAN interfaces: {e}", file=sys.stderr)
        sys.exit(1)

    arm_r.set_callback_mode_all(oa.CallbackMode.IGNORE)
    arm_l.set_callback_mode_all(oa.CallbackMode.IGNORE)

    arm_r.enable_all()
    arm_l.enable_all()
    
    # Send a moderate Kp/Kd to pull them to the position smoothly
    # We'll slowly ramp Kp up to prevent a harsh jerk
    print("Moving...")
    for step in range(1, 21):
        current_kp = 10.0 * (step / 20.0) # Ramp up to Kp=10.0 over 1 second
        mit_r = oa.MITParam(current_kp, 0.5, target_r, 0.0, 0.0)
        mit_l = oa.MITParam(current_kp, 0.5, target_l, 0.0, 0.0)
        
        for _ in range(5): # 50ms per step
            arm_r.get_gripper().mit_control_all([mit_r])
            arm_l.get_gripper().mit_control_all([mit_l])
            time.sleep(0.01)

    # Hold the position securely for 1 more second
    mit_r = oa.MITParam(10.0, 0.5, target_r, 0.0, 0.0)
    mit_l = oa.MITParam(10.0, 0.5, target_l, 0.0, 0.0)
    for _ in range(100):
        arm_r.get_gripper().mit_control_all([mit_r])
        arm_l.get_gripper().mit_control_all([mit_l])
        time.sleep(0.01)

    print("Reached home position.")
    
    # Disable before exiting so the zeroing tool takes full control
    arm_r.disable_all()
    arm_l.disable_all()

if __name__ == "__main__":
    main()
