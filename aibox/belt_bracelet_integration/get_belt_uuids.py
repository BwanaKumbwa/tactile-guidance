import sys
import time
from pybelt.belt_controller import BeltController, BeltConnectionState, BeltControllerDelegate

class Delegate(BeltControllerDelegate):
    def on_connection_state_changed(self, state, error=None):
        print(f"Connection State: {state}")

def main():
    belt_controller = BeltController(Delegate())
    
    # UPDATE THIS WITH YOUR CONNECTION
    # Bluetooth: "AA:BB:CC:DD:EE:FF"
    # USB: "COM3"
    belt_controller.connect("COM3") 
    
    # Wait loop
    for i in range(5):
        if belt_controller.get_connection_state() == BeltConnectionState.CONNECTED:
            break
        time.sleep(1)
    
    if belt_controller.get_connection_state() == BeltConnectionState.CONNECTED:
        print("\n" + "="*40)
        print("BELT UUID CONFIGURATION")
        print("="*40)
        
        profile = belt_controller._gatt_profile
        if profile:
            # We skip 'service' and go straight to chars
            print(f"Vibration Command (WRITE_UUID):   {profile.vibration_command_char.uuid}")
            print(f"Param Request (PARAM_UUID):       {profile.param_request_char.uuid}")
            print(f"Param Notify (NOTIFY_UUID):       {profile.param_notification_char.uuid}")
            print(f"Keep Alive Char:                  {profile.keep_alive_char.uuid}")
        else:
            print("Profile not loaded.")
            
        belt_controller.disconnect_belt()
    else:
        print("Failed to connect.")

if __name__ == "__main__":
    main()