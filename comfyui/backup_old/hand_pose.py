"""Optional hand poses for Kimodo BVH files.

Kimodo's SOMA models don't generate finger motion: every finger joint in the
exported BVH holds the same relaxed rest pose for the whole clip. This writes
a constant hand pose into those finger channels and leaves every other
channel untouched.

Curl is a rotation about each finger joint's local Y axis; negative values
bend the finger toward the palm on both hands (the rest pose already uses
small negative Y values for its relaxed curl).
"""

FINGERS = ("Index", "Middle", "Ring", "Pinky")

# Per finger segment: 1 = metacarpal (left as is), 2 = knuckle,
# 3 = middle joint, 4 = last joint. Z/X are zeroed so the fingers close together.
FIST_FINGER = {
    2: {"Z": 0.0, "Y": -85.0, "X": 0.0},
    3: {"Z": 0.0, "Y": -100.0, "X": 0.0},
    4: {"Z": 0.0, "Y": -65.0, "X": 0.0},
}
# The thumb base keeps its rest rotation; the two outer joints fold it across the fingers.
FIST_THUMB = {"Thumb2": {"Y": -40.0}, "Thumb3": {"Y": -55.0}}

HAND_POSES = ["none", "fist"]


def _fist_targets():
    targets = {}
    for side in ("Left", "Right"):
        for finger in FINGERS:
            for seg, values in FIST_FINGER.items():
                targets[f"{side}Hand{finger}{seg}"] = values
        for joint, values in FIST_THUMB.items():
            targets[f"{side}Hand{joint}"] = values
    return targets


def _rotation_columns(header_lines):
    """Map joint name -> {axis letter: column in the MOTION rows} for rotation channels."""
    columns, column, current = {}, 0, None
    for line in header_lines:
        parts = line.split()
        if not parts:
            continue
        if parts[0] in ("ROOT", "JOINT"):
            current = parts[1]
        elif parts[0] == "End":
            current = None
        elif parts[0] == "CHANNELS":
            for i, name in enumerate(parts[2:]):
                if name.endswith("rotation") and current is not None:
                    columns.setdefault(current, {})[name[0]] = column + i
            column += int(parts[1])
    return columns


def apply_hand_pose(bvh_path, pose):
    """Overwrite the finger rotation channels of a BVH file in place.

    Returns the number of joints posed (0 for pose "none").
    """
    if pose == "none":
        return 0
    if pose != "fist":
        raise ValueError(f"Unknown hand pose {pose!r}; choose from {HAND_POSES}")
    with open(bvh_path, encoding="utf-8", newline="") as f:
        text = f.read()
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    motion = next(i for i, line in enumerate(lines) if line.strip() == "MOTION")
    columns = _rotation_columns(lines[:motion])
    targets = {name: values for name, values in _fist_targets().items() if name in columns}
    if not targets:
        raise ValueError("No SOMA finger joints (e.g. LeftHandIndex2) found in the BVH.")
    edits = {
        columns[name][axis]: f"{value:.6f}"
        for name, values in targets.items()
        for axis, value in values.items()
    }
    # lines[motion + 1] is "Frames:", lines[motion + 2] is "Frame Time:".
    for i in range(motion + 3, len(lines)):
        tokens = lines[i].split(" ")
        if len(tokens) < 2:
            continue
        for col, value in edits.items():
            tokens[col] = value
        lines[i] = " ".join(tokens)
    with open(bvh_path, "w", encoding="utf-8", newline="") as f:
        f.write(newline.join(lines) + newline)
    return len(targets)
