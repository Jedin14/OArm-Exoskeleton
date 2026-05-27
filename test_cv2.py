import cv2
import numpy as np

img = np.zeros((100, 100, 3), dtype=np.uint8)
img[:,:,2] = 255  # Red channel in BGR

_, buf = cv2.imencode('.jpg', img)
with open('test_red.jpg', 'wb') as f:
    f.write(buf)
