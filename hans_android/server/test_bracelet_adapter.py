from feedback_devices import BraceletAdapter
import time


bracelet = BraceletAdapter()

print("Connecting to bracelet...")

if not bracelet.connect():
    print("Connection failed.")
    raise SystemExit(1)

print("Connected.")

# CHECK THE VIBRATIONS
try:
    directions = [
        (0, "RIGHT"),
        (90, "TOP"),
        (180, "LEFT"),
        (270, "DOWN"),
    ]

    for angle, name in directions:
        print(f"\nTesting {name} ({angle}°)...")

        bracelet._send_navigation_command(angle)

        time.sleep(1)

    input("\nPress ENTER to stop and disconnect...")
    '''

# CHECK THE SIGNAL EVENT 
try:
    print("Testing target_found...")
    bracelet.signal_event("list_complete")

    input("Press ENTER to stop and disconnect...")'''

finally:
    bracelet.stop()
    bracelet.disconnect()

print("Disconnected.")

