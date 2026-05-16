import pyautogui
import time
import sys

print("--- Mouse Tracker Started ---")
print("Move your mouse over Master Duel.")
print("The coordinates will print every second.")
print("Press CTRL+C to stop.\n")

try:
    while True:
        # Get current mouse position
        x, y = pyautogui.position()

        # Print coordinates normally (no overwrite trick)
        # flush=True ensures the text appears immediately
        print(f"X: {x} | Y: {y}", flush=True)

        # We wait 1 second so your terminal doesn't explode with text
        time.sleep(1.0)

except KeyboardInterrupt:
    print("\nTracker stopped.")