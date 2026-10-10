"""Bake a clean A-pose or T-pose rest stance into Kimodo BVH files.

Kimodo's SOMA BVH uses the BONES-SEED rest pose: at zero rotation every bone
points down its joint's local X axis, so the skeleton is a tangle until the
motion is applied. This rewrites the hierarchy so that all-zero rotations give
a clean stance, and re-expresses every frame against it so the motion itself
is unchanged (same world joint positions on every frame).

The stance is the one the Maya importer (Maya/BVH Conversion, PoseSolver and
apply_rest_pose) builds: spine up, legs down, feet forward, arms out to the
sides, a few per-joint extra rolls, the right arm rolled 180 degrees so both
palms match, and for the A-pose both arms lowered by `apose_angle` degrees.

BVH has no joint-orient field, so each joint's local axes line up with the
world axes in the rest stance. Maya's importer gave them the solved
orientation as jointOrient instead; the stance and the motion are identical,
only the direction of the rotate axes differs.
"""
import math
import re

import numpy as np

REST_POSES = ["A-pose", "T-pose", "kimodo"]
DEFAULT_APOSE_ANGLE = 29.0

# Extra rest-pose roll (degrees) folded in per joint, about the joint's own
# axes, as JOINT_EXTRA_Z / JOINT_EXTRA_Y in the Maya importer. Changes the
# stance only, never the motion.
JOINT_EXTRA_Z = {
    "LeftFoot": 15.5,
    "RightFoot": 15.5,
    "LeftHandThumb1": 14.0,
    "RightHandThumb1": 14.0,
}
JOINT_EXTRA_Y = {}

_IDENT = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


# --- BVH text -------------------------------------------------------------

class _Node:
    def __init__(self, name, parent, is_end_site=False):
        self.name = name
        self.parent = parent
        self.is_end_site = is_end_site
        self.offset = (0.0, 0.0, 0.0)
        self.offset_line = None
        self.channels = []
        self.channel_start = 0
        self.children = []


def _parse_hierarchy(lines):
    """Nodes in file order (end sites included) and the total channel count."""
    nodes, stack, pending, column = [], [], None, 0
    for i, line in enumerate(lines):
        parts = line.split()
        if not parts:
            continue
        key = parts[0].upper()
        if key in ("ROOT", "JOINT"):
            pending = _Node(" ".join(parts[1:]), stack[-1] if stack else None)
        elif key == "END":
            pending = _Node(stack[-1].name + "_End", stack[-1], is_end_site=True)
        elif key == "{":
            if pending is not None:
                if pending.parent is not None:
                    pending.parent.children.append(pending)
                nodes.append(pending)
                stack.append(pending)
                pending = None
        elif key == "}":
            stack.pop()
        elif key == "OFFSET":
            stack[-1].offset = tuple(float(v) for v in parts[1:4])
            stack[-1].offset_line = i
        elif key == "CHANNELS":
            stack[-1].channels = parts[2:2 + int(parts[1])]
            stack[-1].channel_start = column
            column += int(parts[1])
    return nodes, column


# --- T-pose solve (ported from PoseSolver in the Maya importer) ----------
# Matrices here are 3x3 lists in Maya's row-vector convention: v' = v * M,
# and a joint's world rotation is local * parent_world.

def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _unit(v):
    length = math.sqrt(_dot(v, v))
    return (v[0] / length, v[1] / length, v[2] / length) if length >= 1e-9 else None


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _transpose(m):
    return [[m[j][i] for j in range(3)] for i in range(3)]


def _vec_mul(v, m):
    return tuple(sum(v[k] * m[k][j] for k in range(3)) for j in range(3))


def _rot_from_to(a, b):
    k, c = _cross(a, b), _dot(a, b)
    s2 = _dot(k, k)
    if s2 < 1e-12:
        if c > 0:
            return _IDENT
        axis = _unit(_cross(a, (1.0, 0.0, 0.0))) or _unit(_cross(a, (0.0, 1.0, 0.0)))
        return [[2.0 * axis[i] * axis[j] - (1.0 if i == j else 0.0) for j in range(3)] for i in range(3)]
    kx, ky, kz = k
    kk = [[0.0, -kz, ky], [kz, 0.0, -kx], [-ky, kx, 0.0]]
    kk2 = _mul(kk, kk)
    f = (1.0 - c) / s2
    return _transpose([[(1.0 if i == j else 0.0) + kk[i][j] + kk2[i][j] * f for j in range(3)] for i in range(3)])


def _triad(up, left):
    e2 = _unit(up)
    if not e2:
        return None
    e1 = _unit(_sub(left, tuple(e2[i] * _dot(left, e2) for i in range(3))))
    if not e1:
        return None
    e3 = _cross(e1, e2)
    return [[e1[0], e2[0], e3[0]], [e1[1], e2[1], e3[1]], [e1[2], e2[2], e3[2]]]


def _roll_about(w, u, z_target):
    """Roll world frame `w` about unit axis `u` so its local Z points toward z_target."""
    zw = w[2]
    zp = tuple(zw[i] - u[i] * _dot(zw, u) for i in range(3))
    zt = tuple(z_target[i] - u[i] * _dot(z_target, u) for i in range(3))
    nz, nt = _unit(zp), _unit(zt)
    if not nz or not nt:
        return None
    c = _dot(nz, nt)
    s = _dot(u, _cross(nz, nt))
    ux, uy, uz = u
    rc = [
        [c + ux * ux * (1 - c), ux * uy * (1 - c) - uz * s, ux * uz * (1 - c) + uy * s],
        [uy * ux * (1 - c) + uz * s, c + uy * uy * (1 - c), uy * uz * (1 - c) - ux * s],
        [uz * ux * (1 - c) - uy * s, uz * uy * (1 - c) + ux * s, c + uz * uz * (1 - c)],
    ]
    return _mul(w, _transpose(rc))


def _side_of(name):
    toks = [t.lower() for t in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name)]
    if "left" in toks or "l" in toks:
        return "left"
    if "right" in toks or "r" in toks:
        return "right"
    return None


def _has_xyz_rotation(node):
    return {c.lower() for c in node.channels if c.lower().endswith("rotation")} == {
        "xrotation", "yrotation", "zrotation"}


def _solve_tpose(nodes):
    """Local rest rotations {id(node): row matrix} that put a Y-up skeleton in a T-pose."""
    x_dir, y_dir, z_dir = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    down = tuple(-c for c in y_dir)
    order = nodes
    parent = {id(n): n.parent for n in nodes}
    pos, aim, chains = {}, {}, {}
    for n in order:
        base = pos[id(n.parent)] if n.parent else (0.0, 0.0, 0.0)
        pos[id(n)] = (base[0] + n.offset[0], base[1] + n.offset[1], base[2] + n.offset[2])

    def subtree(n):
        return [n] + [x for c in n.children for x in subtree(c)]

    def find_first(top, regex):
        queue = [c for c in top.children if not c.is_end_site]
        while queue:
            n = queue.pop(0)
            if re.search(regex, n.name.lower()):
                return n
            queue.extend(c for c in n.children if not c.is_end_site)
        return None

    def path(top, target):
        chain = [target]
        while chain[-1] is not top:
            chain.append(parent[id(chain[-1])])
        return chain[::-1]

    def has_foot(n):
        return any(re.search(r"foot|ankle|toe", x.name.lower()) for x in subtree(n))

    pelvis, tops = None, None
    for n in order:
        kids = [c for c in n.children if not c.is_end_site]
        lefts = [c for c in kids if _side_of(c.name) == "left" and has_foot(c)]
        rights = [c for c in kids if _side_of(c.name) == "right" and has_foot(c)]
        if lefts and rights:
            pelvis, tops = n, {"left": lefts[0], "right": rights[0]}
            break

    spine = []
    if pelvis:
        cur = pelvis
        while True:
            cands = [c for c in cur.children
                     if not c.is_end_site and not _side_of(c.name) and c not in tops.values()]
            if not cands:
                break
            nxt = max(cands, key=lambda c: len(subtree(c)))
            spine.append(nxt)
            cur = nxt
            if "head" in nxt.name.lower():
                break
        for i, j in enumerate(spine):
            child = spine[i + 1] if i + 1 < len(spine) else next((c for c in j.children if c.is_end_site), None)
            if child:
                aim[id(j)] = (child, y_dir)

    for side, top in (tops or {}).items():
        foot = find_first(top, r"foot|ankle")
        if not foot:
            continue
        chain = path(top, foot)
        chains[side] = chain
        for a, b in zip(chain, chain[1:]):
            aim[id(a)] = (b, down)
        toes = [c for c in foot.children if not c.is_end_site]
        toe = next((c for c in toes if re.search(r"toe|ball", c.name.lower())), toes[0] if toes else None)
        if toe:
            aim[id(foot)] = (toe, z_dir)

    for n in order:
        side, par = _side_of(n.name), parent[id(n)]
        if not side or n.is_end_site or (par and _side_of(par.name)) or (tops and n in tops.values()):
            continue
        hand = find_first(n, r"hand|wrist")
        if not hand:
            continue
        chain = path(n, hand)
        direction = x_dir if side == "left" else tuple(-c for c in x_dir)
        for a, b in zip(chain, chain[1:]):
            aim[id(a)] = (b, direction)

    triad = None
    if pelvis and spine:
        triad = _triad(_sub(pos[id(spine[-1])], pos[id(pelvis)]),
                       _sub(pos[id(tops["left"])], pos[id(tops["right"])]))
        if triad:
            triad = _mul(triad, [list(x_dir), list(y_dir), list(z_dir)])

    def build(ref_w=None, pairs=None):
        rests, worlds = {}, {}
        for n in order:
            par = parent[id(n)]
            wp = worlds[id(par)] if par else _IDENT
            rot = triad if n is pelvis and triad else _IDENT
            if id(n) in aim:
                child, target = aim[id(n)]
                a = _unit(child.offset)
                if a:
                    rot = _rot_from_to(a, _vec_mul(target, _transpose(wp)))
            if rot is not _IDENT and not _has_xyz_rotation(n):
                rot = _IDENT
            wn = _mul(rot, wp)
            # Match this joint's roll to its partner on the other leg.
            if ref_w and pairs and id(n) in pairs and id(n) in aim and rot is not _IDENT:
                ref = ref_w.get(id(pairs[id(n)]))
                if ref:
                    adj = _roll_about(wn, aim[id(n)][1], ref[2])
                    if adj:
                        wn = adj
                        rot = _mul(wn, _transpose(wp))
            worlds[id(n)] = wn
            if rot is not _IDENT:
                rests[id(n)] = rot
        return rests, worlds

    rests, worlds = build()
    if "left" in chains and "right" in chains and len(chains["left"]) == len(chains["right"]):
        # The right leg is the reference; the left leg copies its roll.
        pairs = {id(l): r for l, r in zip(chains["left"], chains["right"])}
        rests, worlds = build(worlds, pairs)
    return rests


# --- Stance ----------------------------------------------------------------
# From here on matrices are numpy arrays in the column-vector convention
# (v' = M @ v, world = parent_world @ local), the same as Kimodo.

def _axis_rot(axis, deg):
    """Column-convention rotation of `deg` degrees about unit vector `axis`."""
    x, y, z = axis
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    t = 1.0 - c
    return np.array([
        [c + x * x * t, x * y * t - z * s, x * z * t + y * s],
        [y * x * t + z * s, c + y * y * t, y * z * t - x * s],
        [z * x * t - y * s, z * y * t + x * s, c + z * z * t],
    ])


def _rest_worlds(nodes, local):
    world = []
    for i, n in enumerate(nodes):
        p = nodes.index(n.parent) if n.parent else None
        world.append(local[i] if p is None else world[p] @ local[i])
    return world


def _rest_positions(nodes, world):
    pos = []
    for i, n in enumerate(nodes):
        p = nodes.index(n.parent) if n.parent else None
        off = np.array(n.offset)
        pos.append(off if p is None else pos[p] + world[p] @ off)
    return pos


def _hand_mismatch(nodes, local):
    """How far the right hand's finger bases are from the mirror image of the left's."""
    pos = dict(zip((n.name for n in nodes), _rest_positions(nodes, _rest_worlds(nodes, local))))
    mirror = np.array([-1.0, 1.0, 1.0])
    total = 0.0
    for finger in ("Thumb1", "Index1", "Pinky1"):
        keys = ("LeftHand", "LeftHand" + finger, "RightHand", "RightHand" + finger)
        if all(k in pos for k in keys):
            left = (pos[keys[1]] - pos[keys[0]]) * mirror
            total += np.linalg.norm(left - (pos[keys[3]] - pos[keys[2]]))
    return total


def stance_rotations(nodes, pose, apose_angle=DEFAULT_APOSE_ANGLE):
    """World rotation of every node (column convention) in the requested rest stance,
    built on the file's current zero-rotation pose."""
    solved = _solve_tpose(nodes)
    local = []
    for n in nodes:
        rest = np.array(solved.get(id(n), _IDENT), dtype=float).T
        # Maya folds the extras in as extra_row * rest_row, a roll about the joint's own axis.
        if JOINT_EXTRA_Z.get(n.name):
            rest = rest @ _axis_rot((0.0, 0.0, 1.0), JOINT_EXTRA_Z[n.name])
        if JOINT_EXTRA_Y.get(n.name):
            rest = rest @ _axis_rot((0.0, 1.0, 0.0), JOINT_EXTRA_Y[n.name])
        local.append(rest)

    # In the BONES-SEED rest Kimodo's right-side bones are rolled 180 degrees
    # relative to the left; rolling the right shoulder puts the right palm back
    # in line with the left one. Only done when it makes the hands mirror each
    # other, since the standard T-pose rest doesn't have that roll.
    names = [n.name for n in nodes]
    if "RightShoulder" in names:
        shoulder = names.index("RightShoulder")
        unrolled = _hand_mismatch(nodes, local)
        local[shoulder] = local[shoulder] @ _axis_rot((1.0, 0.0, 0.0), 180.0)
        if _hand_mismatch(nodes, local) >= unrolled:
            local[shoulder] = local[shoulder] @ _axis_rot((1.0, 0.0, 0.0), 180.0)

    if pose == "A-pose" and apose_angle:
        for side in ("Left", "Right"):
            if f"{side}Arm" not in names or f"{side}ForeArm" not in names:
                continue
            arm, fore = names.index(f"{side}Arm"), names.index(f"{side}ForeArm")
            world = _rest_worlds(nodes, local)
            pos = _rest_positions(nodes, world)
            bone = pos[fore] - pos[arm]
            bone /= np.linalg.norm(bone)
            # Swing the arm straight down about the axis perpendicular to the bone and world down.
            axis = np.cross(bone, (0.0, -1.0, 0.0))
            if np.linalg.norm(axis) < 1e-6:
                continue
            axis /= np.linalg.norm(axis)
            parent_world = world[nodes.index(nodes[arm].parent)]
            local[arm] = parent_world.T @ _axis_rot(axis, apose_angle) @ world[arm]
    return _rest_worlds(nodes, local)


# --- Euler helpers (BVH ZYX channels: M = Rz @ Ry @ Rx) ------------------

def _euler_to_mats(channels, values):
    """values (T, 3) degrees in channel order -> (T, 3, 3)."""
    mats = np.broadcast_to(np.eye(3), (values.shape[0], 3, 3)).copy()
    for k, chan in enumerate(channels):
        a = np.radians(values[:, k])
        c, s = np.cos(a), np.sin(a)
        r = np.zeros_like(mats)
        axis = "xyz".index(chan[0].lower())
        i, j = (axis + 1) % 3, (axis + 2) % 3
        r[:, axis, axis] = 1.0
        r[:, i, i], r[:, j, j] = c, c
        r[:, i, j], r[:, j, i] = -s, s
        mats = mats @ r
    return mats


def _mats_to_zyx(mats, prev=None):
    """(T, 3, 3) -> (T, 3) degrees as (z, y, x), continuous from frame to frame."""
    y = np.arcsin(np.clip(-mats[:, 2, 0], -1.0, 1.0))
    x = np.arctan2(mats[:, 2, 1], mats[:, 2, 2])
    z = np.arctan2(mats[:, 1, 0], mats[:, 0, 0])
    gimbal = np.abs(mats[:, 2, 0]) > 1.0 - 1e-9
    if gimbal.any():
        z[gimbal] = 0.0
        x[gimbal] = np.arctan2(-mats[gimbal, 1, 2], mats[gimbal, 1, 1])
    out = np.degrees(np.stack([z, y, x], axis=1))
    # Pick, per frame, whichever of the two equivalent Euler triples (plus 360s)
    # sits closest to the previous frame, so curves don't flip.
    for t in range(len(out)):
        ref = out[t - 1] if t else prev
        if ref is None:
            continue
        z0, y0, x0 = out[t]
        best = None
        for cand in ((z0, y0, x0), (z0 + 180.0, 180.0 - y0, x0 + 180.0)):
            cand = np.array(cand)
            cand -= 360.0 * np.round((cand - ref) / 360.0)
            cost = np.abs(cand - ref).sum()
            if best is None or cost < best[0]:
                best = (cost, cand)
        out[t] = best[1]
    return out


# --- Rewrite -----------------------------------------------------------------

def _fmt(v):
    return f"{round(v, 6) + 0.0:.6f}"


def apply_rest_pose(bvh_path, pose="A-pose", apose_angle=DEFAULT_APOSE_ANGLE):
    """Rewrite a BVH file in place so zero rotation is the chosen rest stance.

    pose: "A-pose", "T-pose", or "kimodo" (leave the file as Kimodo wrote it).
    Returns True if the file was changed.
    """
    if pose == "kimodo":
        return False
    if pose not in REST_POSES:
        raise ValueError(f"Unknown rest pose {pose!r}; choose from {REST_POSES}")
    with open(bvh_path, encoding="utf-8", newline="") as f:
        text = f.read()
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    motion = next(i for i, line in enumerate(lines) if line.strip().upper() == "MOTION")
    nodes, n_channels = _parse_hierarchy(lines[:motion])
    if not any(n.name == "Hips" for n in nodes) or not any(n.name == "LeftArm" for n in nodes):
        raise ValueError("Not a Kimodo SOMA skeleton (no Hips / LeftArm joint); can't set its rest pose.")

    first = motion + 3  # MOTION, Frames:, Frame Time:
    rows = [i for i in range(first, len(lines)) if lines[i].strip()]
    data = np.array([[float(v) for v in lines[i].split()] for i in rows]).reshape(len(rows), n_channels)
    frames = data.shape[0]

    rest_world = stance_rotations(nodes, pose, apose_angle)
    index = {id(n): i for i, n in enumerate(nodes)}

    # Original local and world rotations on every frame.
    local, world = [], []
    for n in nodes:
        rot_chans = [(k, c) for k, c in enumerate(n.channels) if c.lower().endswith("rotation")]
        if rot_chans:
            if [c[0].upper() for _, c in rot_chans] != ["Z", "Y", "X"]:
                raise ValueError(f"{n.name}: expected Zrotation Yrotation Xrotation channels.")
            cols = [n.channel_start + k for k, _ in rot_chans]
            m = _euler_to_mats([c for _, c in rot_chans], data[:, cols])
        else:
            m = np.broadcast_to(np.eye(3), (frames, 3, 3))
        local.append(m)
        world.append(m if n.parent is None else world[index[id(n.parent)]] @ m)

    # Re-express against the new rest: world' = world @ rest_world^T, so the
    # same world pose results while zero rotation now means the rest stance.
    new_world = [world[i] @ rest_world[i].T for i in range(len(nodes))]
    out = data.copy()
    for i, n in enumerate(nodes):
        rot_chans = [k for k, c in enumerate(n.channels) if c.lower().endswith("rotation")]
        if not rot_chans:
            continue
        pw = np.eye(3) if n.parent is None else new_world[index[id(n.parent)]]
        new_local = np.swapaxes(pw, -1, -2) @ new_world[i]
        out[:, [n.channel_start + k for k in rot_chans]] = _mats_to_zyx(new_local)

    # Offsets: a bone keeps its length and now points where the stance puts it.
    for n in nodes:
        if n.parent is None or n.offset_line is None:
            continue
        off = rest_world[index[id(n.parent)]] @ np.array(n.offset)
        indent = lines[n.offset_line][: len(lines[n.offset_line]) - len(lines[n.offset_line].lstrip())]
        lines[n.offset_line] = indent + "OFFSET " + " ".join(_fmt(v) for v in off)

    for r, i in enumerate(rows):
        lines[i] = " ".join(_fmt(v) for v in out[r])
    with open(bvh_path, "w", encoding="utf-8", newline="") as f:
        f.write(newline.join(lines) + newline)
    return True
