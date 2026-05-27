from lerobot.cameras.opencv.configuration_opencv import ColorMode, OpenCVCameraConfig
c = OpenCVCameraConfig(index_or_path=0)
print(type(c.color_mode))
print(c.color_mode == ColorMode.RGB)
