"""Manually reposition an AMF selector (M-switch) that cannot home.

Use this when the valve's head (which carries the homing reference
magnet - confirmed in the SDK's own error-code table, error 229: "Please
also check that the valve head is properly fastened to the motor") is
off, so home() cannot succeed and getValvePosition() reads 0 (unknown -
not "port 1", genuinely no known position). Ordinary move commands
(valveShortestPath / valveIncrementalMove / valveDecrementalMove) refuse
to run in this state and raise error 144 "Not homed", because their
shortest-path/no-op logic needs a real current position to reason from.

This script uses the ENFORCED move variants instead - a different
underlying protocol command (not just a flag), which per the SDK
comments forces movement in an explicit direction rather than computing
a path from the (unknown) current position. IMPORTANT: enforced moves are
documented as forcing a full 360-degree rotation in some cases - this is
not guaranteed to be a small, single-step nudge. Watch the hardware while
it runs. With the head off there's nothing for the rotor to collide with,
but don't assume the exact motion in advance.

If enforced moves still raise "Not homed" here, that means this AMF
model requires the head's magnet unconditionally and there is no
software-only way around it - stop and check AMF's own hardware manual /
contact support@amf.ch rather than guessing further blind.

BEFORE RUNNING: make sure the LSPR Acquisition app is closed / this
selector is disconnected in the app - only one process can hold the
serial port at a time.

Edit the settings below, then run: python amf_manual_move.py
"""
import amfTools

# --- Edit these ---
PORT = "COM9"          # the selector's COM port (check Device Manager > Port list, or Windows Device Manager)

DO_MOVE = False         # keep False to just READ current position/port count/status first, no movement

# Direction to force the rotor to move. Pick ONE port count away from
# wherever it currently thinks it is (target is somewhat notional here
# since current position is unknown - what matters is direction + that it
# moves a defined, single step).
DIRECTION = "clockwise"  # "clockwise" or "counter_clockwise"
TARGET_PORT = 1           # nominal target port passed to the enforced command (1..port_count)
# --- end edit ---

dev = amfTools.AMF(PORT)
try:
    port_count = dev.getPortNumber()
    current_port = dev.getValvePosition()
    homed = dev.getHomeStatus()
    print(f"Port count on this unit: {port_count}")
    print(f"Current port position:   {current_port} (0 = unknown/not homed)")
    print(f"Firmware homed status:   {homed}")
    print(f"One port step = {360.0 / port_count:.1f} degrees")

    if not DO_MOVE:
        print("\nDO_MOVE is False - nothing moved. Set DO_MOVE = True and re-run to actually move.")
    else:
        print(f"\nForcing an ENFORCED {DIRECTION} move toward port {TARGET_PORT}...")
        print("Watch the hardware now - this may rotate further than one step.")
        try:
            if DIRECTION == "clockwise":
                dev.valveIncrementalMove(TARGET_PORT, enforced=True, block=True)
            elif DIRECTION == "counter_clockwise":
                dev.valveDecrementalMove(TARGET_PORT, enforced=True, block=True)
            else:
                raise ValueError("DIRECTION must be 'clockwise' or 'counter_clockwise'")
        except Exception as exc:
            print(f"\nMove command raised an error: {exc}")
            print("If this is still 'Not homed' (144), enforced moves aren't enough for this")
            print("model - stop here and check AMF's hardware manual / contact support@amf.ch.")
        else:
            print(f"New position reported: {dev.getValvePosition()}")
finally:
    dev.disconnect()
