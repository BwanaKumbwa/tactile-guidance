import base64

from pybracelet import (
    PATTERN_SINGLE,
    PATTERN_MULTI,
    PATTERN_SEQUENTIAL,
)


def build_orientation_command(
    channel: int,
    pattern: int,
    roll: int,
    on_duration: int,
    period: int,
    delay: int,
    reset: bool,
) -> bytes:
    payload = bytes([
        channel & 0xFF,
        pattern & 0xFF,
        (roll >> 8) & 0xFF,
        roll & 0xFF,
        (on_duration >> 8) & 0xFF,
        on_duration & 0xFF,
        (period >> 8) & 0xFF,
        period & 0xFF,
        (delay >> 8) & 0xFF,
        delay & 0xFF,
        0x01 if reset else 0x00,
    ])

    return bytes([
        0x05,
        len(payload),
    ]) + payload

def build_position_command(
    channel: int,
    first_motor: int,
    second_motor: int,
    third_motor: int,
    on_duration: int,
    period: int,
    delay: int,
    reset: bool,
) -> bytes:
    payload = bytes([
        channel & 0xFF,
        first_motor & 0xFF,
        second_motor & 0xFF,
        third_motor & 0xFF,
        (on_duration >> 8) & 0xFF,
        on_duration & 0xFF,
        (period >> 8) & 0xFF,
        period & 0xFF,
        (delay >> 8) & 0xFF,
        delay & 0xFF,
        0x01 if reset else 0x00,
    ])

    return bytes([
        0x04,
        len(payload),
    ]) + payload

def build_set_intensity_command(intensities) -> bytes:
    if len(intensities) != 6:
        raise ValueError("Expected exactly 6 motor intensities")

    intensities = [
        max(5, min(int(value), 100))
        for value in intensities
    ]

    payload = bytes(intensities)

    return bytes([
        0x02,
        len(payload),
    ]) + payload


def build_set_mode_command(mode: int) -> bytes:
    payload = bytes([
        mode & 0xFF,
    ])

    return bytes([
        0x08,
        len(payload),
    ]) + payload


def send_command(result_queue, target_device, command_bytes) -> bool:
    if result_queue is None or result_queue.full():
        return False

    b64_str = base64.b64encode(command_bytes).decode("utf-8")

    result_queue.put({
        "vibration_command": b64_str,
        "target_device": target_device,
    })

    return True

