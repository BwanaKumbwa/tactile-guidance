"""feelSpace orientation types — must match pybelt.BeltOrientationType."""

BINARY_MASK = 0       # orientation = bit mask of motors
MOTOR_INDEX = 1       # orientation = motor index 0 .. 15
ANGLE = 2             # orientation = degrees 0 .. 359 (belt-local)
MAGNETIC_BEARING = 3  # orientation = degrees 0 .. 359 (magnetic)
