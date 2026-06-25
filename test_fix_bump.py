import numpy as np

def test_bump():
    q_start = 0.0
    q_target = 0.0
    step_rad = 0.1
    for i in range(10):
        q_target += step_rad
        # motor moves slightly less than q_target due to resistance
        motor_pos = q_target - 0.2
        torque = 3.0
        
        if torque > 2.0:
            print(f"Hit limit at {motor_pos}. But target is still {q_target}!")
            # Fix:
            q_target = motor_pos
            print(f"Setting target back to {q_target} to relax strain.")
            return

test_bump()
