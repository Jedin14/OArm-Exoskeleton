#!/usr/bin/env python3
import os
import glob
import yaml
import time
import subprocess
import openarm_can as oa

def configure_can_interface(interface):
    print(f"Configuring {interface}...")
    subprocess.run(["sudo", "ip", "link", "set", interface, "down"], stderr=subprocess.DEVNULL)
    subprocess.run(["sudo", "ip", "link", "set", interface, "type", "can", "bitrate", "1000000", "dbitrate", "5000000", "fd", "on"], check=False)
    subprocess.run(["sudo", "ip", "link", "set", interface, "up"], check=False)

def list_datasets():
    base_path = os.path.expanduser("~/.cache/huggingface/lerobot")
    datasets = glob.glob(os.path.join(base_path, "*/*"))
    # Filter only those that look like datasets
    valid_datasets = [d for d in datasets if os.path.isdir(d)]
    return sorted(valid_datasets)

def main():
    print("=== 7DOF-OArm Manual Zero Calibration ===")
    print("This script allows you to skip the long bump-to-limit calibration.")
    print("It will force the current physical position of the arms to be the new zero.")
    
    datasets = list_datasets()
    if not datasets:
        print("No datasets found in ~/.cache/huggingface/lerobot/")
        print("Continuing anyway...")
        dataset_path = None
    else:
        print("\nAvailable datasets:")
        for i, d in enumerate(datasets):
            print(f"  {i+1}. {os.path.basename(os.path.dirname(d))}/{os.path.basename(d)}")
        
        while True:
            sel = input("\nSelect a dataset to associate this calibration with (or 0 to skip): ")
            try:
                sel = int(sel)
                if sel == 0:
                    dataset_path = None
                    break
                if 1 <= sel <= len(datasets):
                    dataset_path = datasets[sel-1]
                    break
            except ValueError:
                pass
            print("Invalid selection.")

    print("\n---------------------------------------------------------")
    print("STEP 1: PHYSICAL ALIGNMENT")
    print("Please physically move both arms to their exact HOME position:")
    print("  - Arms hanging straight down")
    print("  - Grippers FULLY CLOSED")
    print("---------------------------------------------------------")
    input("Press ENTER when the arms are in the HOME position...")

    print("\nConfiguring CAN interfaces...")
    configure_can_interface("can0")
    configure_can_interface("can1")

    print("\nConnecting to arms...")
    # Initialize Right Arm (can0)
    right_arm = oa.OpenArm("can0", True)
    right_arm.init_arm_motors(
        [oa.MotorType.DM8009, oa.MotorType.DM8009, oa.MotorType.DM4340, oa.MotorType.DM4340,
         oa.MotorType.DM4310, oa.MotorType.DM4310, oa.MotorType.DM4310],
        [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07],
        [0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17]
    )
    right_arm.init_gripper_motor(oa.MotorType.DM4310, 0x08, 0x18)
    right_arm.set_callback_mode_all(oa.CallbackMode.STATE)

    # Initialize Left Arm (can1)
    left_arm = oa.OpenArm("can1", True)
    left_arm.init_arm_motors(
        [oa.MotorType.DM8009, oa.MotorType.DM8009, oa.MotorType.DM4340, oa.MotorType.DM4340,
         oa.MotorType.DM4310, oa.MotorType.DM4310, oa.MotorType.DM4310],
        [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07],
        [0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17]
    )
    left_arm.init_gripper_motor(oa.MotorType.DM4310, 0x08, 0x18)
    left_arm.set_callback_mode_all(oa.CallbackMode.STATE)

    print("\nWriting zero positions to motors (this replaces the bump-to-limit calibration)...")
    right_arm.set_zero_all()
    left_arm.set_zero_all()
    right_arm.recv_all()
    left_arm.recv_all()
    time.sleep(0.5)

    print("\nReading new gripper closed positions...")
    right_arm.recv_all()
    left_arm.recv_all()
    
    right_grip_pos = right_arm.get_gripper().get_motors()[0].get_position()
    left_grip_pos = left_arm.get_gripper().get_motors()[0].get_position()
    
    print(f"Right Gripper Closed Position: {right_grip_pos:.4f} rad")
    print(f"Left Gripper Closed Position: {left_grip_pos:.4f} rad")

    # Update global gripper_home.yaml
    workspace_yaml = os.path.join(os.path.dirname(__file__), "gripper_home.yaml")
    calib_data = {
        'gripper_home': {
            'right_arm_can0': float(right_grip_pos),
            'left_arm_can1': float(left_grip_pos)
        }
    }
    
    with open(workspace_yaml, "w") as f:
        yaml.dump(calib_data, f)
    print(f"\nUpdated workspace calibration: {workspace_yaml}")

    # If dataset selected, also save it inside the dataset
    if dataset_path:
        dataset_yaml = os.path.join(dataset_path, "calibration.yaml")
        with open(dataset_yaml, "w") as f:
            yaml.dump(calib_data, f)
        print(f"Saved dataset calibration to: {dataset_yaml}")
        
    print("\n=======================================================")
    print("✅ SUCCESS! The arm has been manually calibrated.")
    print("You NEVER have to run calibrate_arms.py again.")
    print("You can now safely resume recording episodes or run inference!")
    print("=======================================================")

if __name__ == "__main__":
    main()
