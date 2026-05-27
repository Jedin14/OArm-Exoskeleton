import cv2
img = cv2.imread('test_red.jpg')
print("Blue channel max:", img[:,:,0].max())
print("Green channel max:", img[:,:,1].max())
print("Red channel max:", img[:,:,2].max())
