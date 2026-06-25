import numpy as np

# Suppose JOINT_SIGN is +1
# ideal limit = pi/2
# uncalibrated limit hit = q_hit

# If motor moves in same direction as ideal:
# target zero = q_hit - ideal_limit

# If motor moves in OPPOSITE direction (JOINT_SIGN = -1):
# ideal limit = pi/2 (ideal frame)
# in motor frame, the limit is at -pi/2 distance from zero!
# So target zero = q_hit - (-pi/2) = q_hit + pi/2
# target zero = q_hit + ideal_limit * JOINT_SIGN
