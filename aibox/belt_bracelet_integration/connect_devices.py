import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) # for imports from the parent dir

from pybelt.belt_scanner import *
from pybelt.belt_controller import *
from auto_connect import interactive_belt_connect, setup_logger

# device ids:
belt_address = ''
bracelet_address = ''

class Delegate(BeltControllerDelegate):
    pass

def connect_belt():
    """
    This function initializes a logger, creates a belt controller delegate, and attempts to connect to the bracelet.
    If the connection is successful, it sets the belt mode to APP mode.

    Returns:
        tuple: A tuple containing a boolean indicating the success of the connection and the belt controller instance.
               (True, belt_controller) if the connection is successful,
               (False, belt_controller) otherwise.
    """

    setup_logger()
    belt_controller_delegate = Delegate()
    belt_controller = BeltController(belt_controller_delegate)
    interactive_belt_connect(belt_controller)

    if belt_controller.get_connection_state() != BeltConnectionState.CONNECTED:
        print("Connection failed.")
        return False, belt_controller
    else:
        # Change belt mode to APP mode
        belt_controller.set_belt_mode(BeltMode.APP_MODE)
        return True, belt_controller

belt_controller = BeltController()
bracelet_controller = BeltController()
# Retrieve the list of available devices
with create() as scanner:
    devices = scanner.scan()

print(devices)

if len(devices) > 0:
    try:
        for device in devices:
            if device.address == belt_address:
                belt = device
            if device.address == bracelet_address:
                bracelet = device
        print(f'belt: {belt}, bracelet: {bracelet}')
        belt_controller.connect(belt)
        print('belt connected')
        print(belt_controller)
        bracelet_controller.connect(bracelet)
        print('bracelet connected')
        print(bracelet_controller)
        
        print('sending vibration command to the bracelet:')
        
        while True:
            bracelet_controller.vibrate_at_angle(90, channel_index=0)
            belt_controller.vibrate_at_angle(90, channel_index=0)

    except Exception as e:
        print(e)