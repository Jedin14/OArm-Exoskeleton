import time
import numpy as np

try:
    import openarm_can as oa
except ImportError:
    raise ImportError("openarm_can is not installed. Please install it to use this translation script.")

class VLACANInterface:
    """
    Translates Vision-Language-Action (VLA) model outputs into physical 
    motor commands on the OpenArm CAN bus infrastructure.
    Supports a bimanual (dual-arm) setup over two CAN interfaces.
    """

    # Default gains mapped to the 7 arm joints + 1 gripper joint per arm
    DEFAULT_KP = [20.0, 20.0, 20.0, 20.0, 5.0, 5.0, 5.0, 0.5]
    DEFAULT_KD = [2.75, 2.5, 0.7, 0.4, 0.7, 0.6, 0.5, 0.1]

    def __init__(
        self,
        right_interface: str = "can0",
        left_interface: str = "can1",
        enable_fd: bool = True,
        kp_per_arm: list[float] | None = None,
        kd_per_arm: list[float] | None = None,
    ):
        """
        Initializes the left and right CAN interfaces.
        Sets up 14 arm motors + 2 grippers (16 DOFs total).
        """
        self.right_interface = right_interface
        self.left_interface = left_interface
        
        self.kp = kp_per_arm if kp_per_arm is not None else self.DEFAULT_KP
        self.kd = kd_per_arm if kd_per_arm is not None else self.DEFAULT_KD

        assert len(self.kp) == 8, "Kp array must have exactly 8 elements (7 arm joints + 1 gripper)"
        assert len(self.kd) == 8, "Kd array must have exactly 8 elements (7 arm joints + 1 gripper)"

        print(f"[VLACANInterface] Initializing Right Arm on {self.right_interface}...")
        self.right_arm = oa.OpenArm(self.right_interface, enable_fd)
        
        print(f"[VLACANInterface] Initializing Left Arm on {self.left_interface}...")
        self.left_arm = oa.OpenArm(self.left_interface, enable_fd)

        # Standard motor configuration
        arm_motor_types = [oa.MotorType.DM4310] * 7
        arm_send_ids = [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07]
        arm_recv_ids = [0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17]

        # Initialize Right Arm
        self.right_arm.init_arm_motors(arm_motor_types, arm_send_ids, arm_recv_ids)
        self.right_arm.init_gripper_motor(oa.MotorType.DM4310, 0x08, 0x18)
        self.right_arm.set_callback_mode_all(oa.CallbackMode.STATE)
        
        # Initialize Left Arm
        self.left_arm.init_arm_motors(arm_motor_types, arm_send_ids, arm_recv_ids)
        self.left_arm.init_gripper_motor(oa.MotorType.DM4310, 0x08, 0x18)
        self.left_arm.set_callback_mode_all(oa.CallbackMode.STATE)
        
        # Enable all motors
        self.right_arm.enable_all()
        self.left_arm.enable_all()
        
        self.right_arm.recv_all()
        self.left_arm.recv_all()
        
        time.sleep(0.1)
        print("[VLACANInterface] All motors initialized and enabled (16 DOFs).")

    def get_observation(self) -> dict:
        """
        Polls both CAN buses for the current state.
        Returns positions, velocities, and torques of shape [16].
        Index 0-7: Left Arm + Gripper
        Index 8-15: Right Arm + Gripper
        """
        self.left_arm.refresh_all()
        self.right_arm.refresh_all()
        
        self.left_arm.recv_all()
        self.right_arm.recv_all()

        positions, velocities, torques = [], [], []

        # Helper to read from an arm
        def read_arm(arm_obj):
            for motor in arm_obj.get_arm().get_motors():
                positions.append(motor.get_position())
                velocities.append(motor.get_velocity())
                torques.append(motor.get_torque())
            for motor in arm_obj.get_gripper().get_motors():
                positions.append(motor.get_position())
                velocities.append(motor.get_velocity())
                torques.append(motor.get_torque())

        # VLA convention usually puts Left first, then Right
        read_arm(self.left_arm)
        read_arm(self.right_arm)

        return {
            "positions": np.array(positions, dtype=np.float32),
            "velocities": np.array(velocities, dtype=np.float32),
            "torques": np.array(torques, dtype=np.float32),
        }

    def send_action(self, target_positions: list[float] | np.ndarray) -> None:
        """
        Sends the 16 target positions predicted by the VLA to the motors.
        target_positions[0:8] -> Left Arm + Gripper
        target_positions[8:16] -> Right Arm + Gripper
        """
        if len(target_positions) != 16:
            raise ValueError(f"Expected exactly 16 target positions, got {len(target_positions)}.")

        def dispatch_arm(arm_obj, start_idx):
            arm_params = []
            for i in range(7):
                param = oa.MITParam(
                    target_positions[start_idx + i], 
                    0.0, 
                    self.kp[i], 
                    self.kd[i], 
                    0.0
                )
                arm_params.append(param)
                
            gripper_param = oa.MITParam(
                target_positions[start_idx + 7],
                0.0,
                self.kp[7],
                self.kd[7],
                0.0
            )

            arm_obj.get_arm().mit_control_all(arm_params)
            arm_obj.get_gripper().mit_control_all([gripper_param])

        # Dispatch Left (indices 0-7)
        dispatch_arm(self.left_arm, 0)
        # Dispatch Right (indices 8-15)
        dispatch_arm(self.right_arm, 8)
        
        # Pull responses to keep state in sync
        self.left_arm.recv_all()
        self.right_arm.recv_all()

    def shutdown(self):
        """
        Safely disables all motors. Call this when shutting down your script.
        """
        self.left_arm.disable_all()
        self.right_arm.disable_all()
        self.left_arm.recv_all()
        self.right_arm.recv_all()
        print("[VLACANInterface] All motors safely disabled.")


if __name__ == "__main__":
    print("Testing VLACANInterface...")
    try:
        # Connect to the CAN buses (Left on can1, Right on can0)
        robot = VLACANInterface(left_interface="can1", right_interface="can0")
        
        # Read the current positions multiple times to ensure we catch hardware responses
        print("\nPolling hardware...")
        for i in range(5):
            obs = robot.get_observation()
            time.sleep(0.05)
            
        print("\n--- Current Robot State ---")
        print(f"Positions (16 DOFs): {np.round(obs['positions'], 4)}")
        print(f"Velocities (16 DOFs): {np.round(obs['velocities'], 4)}")
        print(f"Torques (16 DOFs): {np.round(obs['torques'], 4)}\n")
        
        # Check if we actually got non-zero data
        if np.all(obs['positions'] == 0.0):
            print("Warning: All positions are exactly 0.0. ")
            print("This usually means the script is talking to the socket, but the hardware is powered off,")
            print("the CAN IDs don't match, or the physical CAN connection is loose.")
        else:
            print("Success! The script is actively receiving data from the robot.")
        
    except Exception as e:
        print(f"Error during testing: {e}")
    finally:
        if 'robot' in locals():
            robot.shutdown()
