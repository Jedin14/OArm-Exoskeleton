import cv2
import imageio.v3 as iio

cap = cv2.VideoCapture(4)
ret, frame = cap.read()
if ret:
    # Save with OpenCV (assumes frame is BGR)
    cv2.imwrite('test_bgr_assumption.jpg', frame)
    # Save with imageio (assumes frame is RGB)
    iio.imwrite('test_rgb_assumption.jpg', frame)
