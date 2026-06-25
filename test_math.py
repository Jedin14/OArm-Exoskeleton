def calc_delta_to_zero_pos_joint(initial_rad, ideal_limit_rad, delta_to_stop_rad, joint_id):
    q_hit = initial_rad + delta_to_stop_rad
    delta_to_ideal = ideal_limit_rad - q_hit
    return float(delta_to_ideal)

initial_rad = 0.0
ideal_limit_rad = 0.0 # say limit is at 0
delta_to_stop_rad = -0.1 # we moved -0.1 to hit it
print("delta_to_ideal:", calc_delta_to_zero_pos_joint(initial_rad, ideal_limit_rad, delta_to_stop_rad, None))
