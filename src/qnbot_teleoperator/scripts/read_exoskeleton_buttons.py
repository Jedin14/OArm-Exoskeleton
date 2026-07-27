#!/usr/bin/env python3
"""Read and display exoskeleton button states.

Run after the exoskeleton websocket/teleoperator node is active:

    python3 src/qnbot_teleoperator/scripts/read_exoskeleton_buttons.py

The script is diagnostic only. It never starts, stops, pauses, or modifies
recording state.
"""

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Joy


BUTTON_NAMES = (
    "joystick_button",
    "red_button_a",
    "blue_button_b",
    "white_button_c",
    "button_d",
    "toggle_switch",
)


class ExoskeletonButtonReader(Node):
    def __init__(self) -> None:
        super().__init__("exoskeleton_button_reader")
        self.declare_parameter("topic", "/exo/gamepad_keys")
        topic = str(self.get_parameter("topic").value)
        self._previous = [0] * 16
        self._received = False
        self.create_subscription(Joy, topic, self._button_callback, 10)
        self.get_logger().info(f"Listening for exoskeleton buttons on {topic}")
        self.get_logger().info(
            "Press buttons one at a time. A/B/C/D are protocol names; "
            "the physical colors can then be mapped safely."
        )

    @staticmethod
    def _button_name(index: int) -> str:
        if 4 <= index <= 9:
            return f"left_{BUTTON_NAMES[index - 4]}"
        if 10 <= index <= 15:
            return f"right_{BUTTON_NAMES[index - 10]}"
        return f"control_{index}"

    def _button_callback(self, msg: Joy) -> None:
        buttons = [int(bool(value)) for value in msg.buttons]
        if len(buttons) < 16:
            buttons.extend([0] * (16 - len(buttons)))

        if not self._received:
            self._previous = buttons
            self._received = True
            self.get_logger().info(
                "Initial state: "
                + ", ".join(
                    f"{self._button_name(i)}={'PRESSED' if value else 'released'}"
                    for i, value in enumerate(buttons)
                    if i >= 4
                )
            )
            return

        for index, (old, new) in enumerate(zip(self._previous, buttons)):
            if old == new or index < 4:
                continue
            state = "PRESSED" if new else "released"
            self.get_logger().info(f"{self._button_name(index)}: {state}")

        self._previous = buttons


def main(args=None) -> None:
    rclpy.init(args=args)
    node = ExoskeletonButtonReader()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
