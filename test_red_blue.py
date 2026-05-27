import cv2
import numpy as np

cap = cv2.VideoCapture(4)
ret, frame = cap.read()
if ret:
    # Save the frame directly without any conversion
    cv2.imwrite('test_raw.jpg', frame)
    print("Frame saved to test_raw.jpg")
