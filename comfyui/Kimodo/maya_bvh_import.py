import math
import os
import re
import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
# The UI is optional so this file also runs in mayapy (no Maya window), e.g. for
# the Kimodo FBX export nodes in ComfyUI. Everything except QtUI works without it.
try:
    from maya.app.general.mayaMixin import MayaQWidgetBaseMixin
    from PySide6 import QtWidgets, QtCore
except ImportError:
    MayaQWidgetBaseMixin = QtWidgets = QtCore = None

main_window = None

JOINT_EXTRA_Z = {
    "LeftFoot": 15.5,
    "RightFoot": 15.5,
    "LeftHandThumb1": 14.0,
    "RightHandThumb1": 14.0,
}

JOINT_EXTRA_Y = {
    "RightHandThumb1": 40.0,
    "LeftHandThumb1": 40.0,
}



class MathUtils:
    """Helper class containing 3D math, vector, and matrix utility operations."""

    def __init__(self):
        self._IDENT = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]

    def vec_dot(self, a, b):
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

    def vec_cross(self, a, b):
        return (
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        )

    def vec_sub(self, a, b):
        return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

    def vec_unit(self, v):
        l = math.sqrt(self.vec_dot(v, v))
        return (v[0] / l, v[1] / l, v[2] / l) if l >= 1e-9 else None

    def mat_mul(self, a, b):
        return [
            [sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)
        ]

    def mat_transpose(self, m):
        return [[m[j][i] for j in range(3)] for i in range(3)]

    def vec_mat_mul(self, v, m):
        return tuple(sum(v[k] * m[k][j] for k in range(3)) for j in range(3))

    def rot_from_to(self, a, b):
        k, c = self.vec_cross(a, b), self.vec_dot(a, b)
        s2 = self.vec_dot(k, k)
        if s2 < 1e-12:
            if c > 0:
                return self._IDENT
            axis = self.vec_unit(
                self.vec_cross(a, (1.0, 0.0, 0.0))
            ) or self.vec_unit(self.vec_cross(a, (0.0, 1.0, 0.0)))
            return [
                [
                    2.0 * axis[i] * axis[j] - (1.0 if i == j else 0.0)
                    for j in range(3)
                ]
                for i in range(3)
            ]
        kx, ky, kz = k
        kk = [[0.0, -kz, ky], [kz, 0.0, -kx], [-ky, kx, 0.0]]
        kk2 = self.mat_mul(kk, kk)
        f = (1.0 - c) / s2
        return self.mat_transpose(
            [
                [
                    (1.0 if i == j else 0.0) + kk[i][j] + kk2[i][j] * f
                    for j in range(3)
                ]
                for i in range(3)
            ]
        )

    def compute_triad(self, up, left):
        e2 = self.vec_unit(up)
        if not e2:
            return None
        e1 = self.vec_unit(
            self.vec_sub(left, tuple(e2[i] * self.vec_dot(left, e2) for i in range(3)))
        )
        if not e1:
            return None
        e3 = self.vec_cross(e1, e2)
        return [
            [e1[0], e2[0], e3[0]],
            [e1[1], e2[1], e3[1]],
            [e1[2], e2[2], e3[2]],
        ]

    def roll_about(self, w, u, z_target):
        """Rotate the world frame `w` (rows = local axes in world) about the unit
        axis `u` so its local Z axis points toward `z_target` (as seen
        perpendicular to `u`). The direction `u` itself is unchanged, so child
        joints stay where they are. Returns None if the roll is undefined."""
        zw = w[2]
        zp = tuple(zw[i] - u[i] * self.vec_dot(zw, u) for i in range(3))
        zt = tuple(z_target[i] - u[i] * self.vec_dot(z_target, u) for i in range(3))
        nz, nt = self.vec_unit(zp), self.vec_unit(zt)
        if not nz or not nt:
            return None
        c = self.vec_dot(nz, nt)
        s = self.vec_dot(u, self.vec_cross(nz, nt))
        ux, uy, uz = u
        rc = [
            [c + ux * ux * (1 - c), ux * uy * (1 - c) - uz * s, ux * uz * (1 - c) + uy * s],
            [uy * ux * (1 - c) + uz * s, c + uy * uy * (1 - c), uy * uz * (1 - c) - ux * s],
            [uz * ux * (1 - c) - uy * s, uz * uy * (1 - c) + ux * s, c + uz * uz * (1 - c)],
        ]
        return self.mat_mul(w, self.mat_transpose(rc))


class BVHUtils:

    def __init__(self):
        self.ROTATE_ORDER = {"xyz": 0, "yzx": 1, "zxy": 2, "xzy": 3, "yxz": 4, "zyx": 5}
        self.NAMED_FPS = {
            15: "game",
            24: "film",
            25: "pal",
            30: "ntsc",
            48: "show",
            50: "palf",
            60: "ntscf",
        }
        self.NUMERIC_FPS = {
            2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 40, 75, 80, 100, 120, 125, 150, 200, 240,
            250, 300, 375, 400, 500, 600, 750, 1200,
        }
        self.TANGENTS = {
            "linear": oma.MFnAnimCurve.kTangentLinear,
            "clamped": oma.MFnAnimCurve.kTangentClamped,
            "step": oma.MFnAnimCurve.kTangentStep,
        }

    def sanitize_name(self, name):
        clean = re.sub(r"[^A-Za-z0-9_]", "_", name)
        return "_" + clean if not clean or clean[0].isdigit() else clean

    def get_rotate_order_index(self, channels):
        axes = [c[0].lower() for c in channels if c.lower().endswith("rotation")][::-1]
        for a in "xyz":
            if a not in axes:
                axes.append(a)
        return self.ROTATE_ORDER["".join(axes[:3])]

    def set_scene_fps(self, frame_time):
        if frame_time <= 0:
            return False
        fps = 1.0 / frame_time
        nearest = round(fps)
        if abs(fps - nearest) > 0.05:
            return False
        if nearest in self.NAMED_FPS:
            cmds.currentUnit(time=self.NAMED_FPS[nearest])
        elif nearest in self.NUMERIC_FPS:
            cmds.currentUnit(time=f"{nearest}fps")
        return True

    def node_has_position(self, node):
        return any(c.lower().endswith("position") for c in node.channels)

    def node_carries_motion(self, node, data):
        n = data.channel_count
        for i, chan in enumerate(node.channels):
            if chan.lower().endswith("position"):
                if any(
                    abs(v) > 1e-9
                    for v in data.values[node.channel_start + i :: n]
                ):
                    return True
        return False

    def collect_translation_roots(self, node, data, under_pos, out):
        moves = self.node_has_position(node) and self.node_carries_motion(node, data)
        if moves and not under_pos:
            out.add(id(node))
        for child in node.children:
            self.collect_translation_roots(child, data, under_pos or moves, out)

    def resolve_position_mode(self, node, data, mode="auto"):
        if mode == "additive":
            return True
        if mode == "absolute":
            return False
        off_len = math.sqrt(sum(o * o for o in node.offset))
        if off_len < 1e-6:
            return False
        first = [
            data.values[node.channel_start + i]
            for i, c in enumerate(node.channels)
            if c.lower().endswith("position")
        ]
        return math.sqrt(sum(v * v for v in first)) < 0.25 * off_len if first else False

    def get_side_of(self, name):
        toks = [
            t.lower()
            for t in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name)
        ]
        if "left" in toks or "l" in toks:
            return "left"
        if "right" in toks or "r" in toks:
            return "right"
        return None

    def has_xyz_rotation(self, node):
        return {
            c.lower() for c in node.channels if c.lower().endswith("rotation")
        } == {"xrotation", "yrotation", "zrotation"}


class PoseSolver:
    """Solver class for resolving skeletal T-pose orientations."""

    def __init__(self):
        self.math_utils = MathUtils()
        self.bvh_utils = BVHUtils()

    def solve_tpose(self, roots, up_axis="y"):
        if up_axis == "z":
            x_dir, y_dir, z_dir = (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)
        else:
            x_dir, y_dir, z_dir = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
        down = tuple(-c for c in y_dir)
        parent, order, pos, aim = {}, [], {}, {}
        chains = {}

        def walk(n, p, base):
            parent[id(n)] = p
            pos[id(n)] = (
                base[0] + n.offset[0],
                base[1] + n.offset[1],
                base[2] + n.offset[2],
            )
            order.append(n)
            for c in n.children:
                walk(c, n, pos[id(n)])

        for r in roots:
            walk(r, None, (0.0, 0.0, 0.0))

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

        pelvis, tops = None, None
        for n in order:
            kids = [c for c in n.children if not c.is_end_site]
            lefts = [
                c
                for c in kids
                if self.bvh_utils.get_side_of(c.name) == "left"
                and any(
                    re.search(r"foot|ankle|toe", x.name.lower())
                    for x in subtree(c)
                )
            ]
            rights = [
                c
                for c in kids
                if self.bvh_utils.get_side_of(c.name) == "right"
                and any(
                    re.search(r"foot|ankle|toe", x.name.lower())
                    for x in subtree(c)
                )
            ]
            if lefts and rights:
                pelvis, tops = n, {"left": lefts[0], "right": rights[0]}
                break

        spine = []
        if pelvis:
            cur = pelvis
            while True:
                cands = [
                    c
                    for c in cur.children
                    if not c.is_end_site
                    and not self.bvh_utils.get_side_of(c.name)
                    and c not in tops.values()
                ]
                if not cands:
                    break
                nxt = max(cands, key=lambda c: len(subtree(c)))
                spine.append(nxt)
                cur = nxt
                if "head" in nxt.name.lower():
                    break
            for i, j in enumerate(spine):
                child = (
                    spine[i + 1]
                    if i + 1 < len(spine)
                    else next((c for c in j.children if c.is_end_site), None)
                )
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
            toe = next(
                (c for c in toes if re.search(r"toe|ball", c.name.lower())),
                toes[0] if toes else None,
            )
            if toe:
                aim[id(foot)] = (toe, z_dir)

        for n in order:
            side, par = self.bvh_utils.get_side_of(n.name), parent[id(n)]
            if (
                not side
                or n.is_end_site
                or (par and self.bvh_utils.get_side_of(par.name))
                or (tops and n in tops.values())
            ):
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
            triad = self.math_utils.compute_triad(
                self.math_utils.vec_sub(pos[id(spine[-1])], pos[id(pelvis)]),
                self.math_utils.vec_sub(pos[id(tops["left"])], pos[id(tops["right"])]),
            )
            if triad:
                triad = self.math_utils.mat_mul(
                    triad, [list(x_dir), list(y_dir), list(z_dir)]
                )

        def build(ref_W=None, pairs=None):
            S, W = {}, {}
            for n in order:
                par = parent[id(n)]
                wp = W[id(par)] if par else self.math_utils._IDENT
                rot = triad if n is pelvis and triad else self.math_utils._IDENT
                if id(n) in aim:
                    child, target = aim[id(n)]
                    a = self.math_utils.vec_unit(child.offset)
                    if a:
                        rot = self.math_utils.rot_from_to(
                            a,
                            self.math_utils.vec_mat_mul(
                                target, self.math_utils.mat_transpose(wp)
                            ),
                        )
                if rot is not self.math_utils._IDENT and not self.bvh_utils.has_xyz_rotation(n):
                    rot = self.math_utils._IDENT
                wn = self.math_utils.mat_mul(rot, wp)

                # Match this joint's roll to its partner on the other leg.
                if (
                    ref_W
                    and pairs
                    and id(n) in pairs
                    and id(n) in aim
                    and rot is not self.math_utils._IDENT
                ):
                    ref = ref_W.get(id(pairs[id(n)]))
                    if ref:
                        adj = self.math_utils.roll_about(wn, aim[id(n)][1], ref[2])
                        if adj:
                            wn = adj
                            rot = self.math_utils.mat_mul(
                                wn, self.math_utils.mat_transpose(wp)
                            )

                W[id(n)] = wn
                if rot is not self.math_utils._IDENT:
                    S[id(n)] = rot
            return S, W

        S, W = build()
        if (
            "left" in chains
            and "right" in chains
            and len(chains["left"]) == len(chains["right"])
        ):
            # The right leg is the reference; the left leg copies its roll.
            pairs = {id(l): r for l, r in zip(chains["left"], chains["right"])}
            S, W = build(W, pairs)
        return S, []


class BvhNode:
    """Representation of a node/joint within a BVH hierarchy."""

    def __init__(
        self,
        name,
        offset=(0.0, 0.0, 0.0),
        channels=None,
        channel_start=0,
        children=None,
        is_end_site=False,
    ):
        self.name = name
        self.offset = offset
        self.channels = channels if channels is not None else []
        self.channel_start = channel_start
        self.children = children if children is not None else []
        self.is_end_site = is_end_site


class BvhData:
    """Data container storing BVH hierarchy roots, motion values, and frames."""

    def __init__(self, roots, frame_count, frame_time, channel_count, values):
        self.roots = roots
        self.frame_count = frame_count
        self.frame_time = frame_time
        self.channel_count = channel_count
        self.values = values


class BVHReader:
    """Parser class for converting BVH token streams into structured BVH data."""

    def __init__(self, tokens=None):
        self.tokens = tokens if tokens is not None else []
        self.pos = 0
        self.channel_total = 0

    def next(self):
        if self.pos >= len(self.tokens):
            raise ValueError("Unexpected end of BVH hierarchy.")
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def expect(self, word):
        tok = self.next()
        if tok.upper() != word.upper():
            raise ValueError(f"Expected '{word}' but found '{tok}'.")

    def floats(self, count):
        return tuple(float(self.next()) for _ in range(count))

    def parse_hierarchy(self):
        self.expect("HIERARCHY")
        roots = []
        while self.peek() and self.peek().upper() == "ROOT":
            self.next()
            roots.append(self.parse_joint())
        if not roots:
            raise ValueError("No ROOT joint found in BVH file.")
        return roots

    def parse_joint(self):
        name_parts = [self.next()]
        while self.peek() and self.peek() != "{":
            name_parts.append(self.next())
        node = BvhNode(name="_".join(name_parts))
        self.expect("{")
        while True:
            tok = self.next().upper()
            if tok == "OFFSET":
                node.offset = self.floats(3)
            elif tok == "CHANNELS":
                count = int(self.next())
                node.channels = [self.next() for _ in range(count)]
                node.channel_start = self.channel_total
                self.channel_total += count
            elif tok == "JOINT":
                node.children.append(self.parse_joint())
            elif tok == "END":
                self.expect("SITE")
                node.children.append(self.parse_end_site(node.name))
            elif tok == "}":
                return node

    def parse_end_site(self, parent_name):
        node = BvhNode(name=f"{parent_name}_End", is_end_site=True)
        self.expect("{")
        while True:
            tok = self.next().upper()
            if tok == "OFFSET":
                node.offset = self.floats(3)
            elif tok == "}":
                return node

    def parse_file(self, path):
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
        motion = re.search(r"^\s*MOTION\b", text, re.IGNORECASE | re.MULTILINE)
        if not motion:
            raise ValueError("No MOTION section found.")

        tokens = text[: motion.start()].replace("{", " { ").replace("}", " } ").split()
        self.tokens = tokens
        self.pos = 0
        self.channel_total = 0

        roots, channel_count = self.parse_hierarchy(), self.channel_total

        motion_text = text[motion.end() :]
        frames_m = re.search(r"Frames\s*:\s*(\d+)", motion_text, re.IGNORECASE)
        time_m = re.search(
            r"Frame\s+Time\s*:\s*([-+0-9.eE]+)", motion_text, re.IGNORECASE
        )
        if not frames_m or not time_m:
            raise ValueError("Could not read frame specifications from MOTION section.")

        frame_count, frame_time = int(frames_m.group(1)), float(time_m.group(1))
        values = [
            float(v)
            for v in motion_text[max(frames_m.end(), time_m.end()) :].split()
        ]
        available = len(values) // channel_count
        if available < frame_count:
            frame_count = available
        return BvhData(
            roots,
            frame_count,
            frame_time,
            channel_count,
            values[: frame_count * channel_count],
        )


class MayaBVHImporter:
    """Importer class responsible for building Maya joint hierarchies and animating them."""

    def __init__(
        self,
        path=None,
        scale=1.0,
        start_frame=1,
        set_fps=True,
        set_range=True,
        end_sites=True,
        euler_filter=True,
        z_up_to_y=False,
        tangent="linear",
        position_mode="auto",
        start_at_origin=False,
        rest_pose_frame=None,
        tpose=True,
    ):
        self.path = path
        self.scale = scale
        self.start_frame = start_frame
        self.set_fps = set_fps
        self.set_range = set_range
        self.end_sites = end_sites
        self.euler_filter = euler_filter
        self.z_up_to_y = z_up_to_y
        self.tangent = tangent
        self.position_mode = position_mode
        self.start_at_origin = start_at_origin
        self.rest_pose_frame = rest_pose_frame
        self.tpose = tpose
        self.bvh_utils = BVHUtils()
        self.pose_solver = PoseSolver()
        self.reader = BVHReader()

    def _matrix_to_euler(self, matrix, order):
        try:
            return om.MEulerRotation.decompose(matrix, order)
        except AttributeError:
            return om.MTransformationMatrix(matrix).rotation().reorder(order)

    def _unwrap(self, value, previous):
        return value - 2.0 * math.pi * round((value - previous) / (2.0 * math.pi))

    def _bake_rest_pose(
        self,
        node,
        full,
        data,
        rest_frame=None,
        rest_matrix=None,
        extra_z_deg=0.0,
        extra_y_deg = 0.0,
    ):
        n = data.channel_count
        cols = {
            chan.lower()[0]: [
                math.radians(v)
                for v in data.values[node.channel_start + idx :: n]
            ]
            for idx, chan in enumerate(node.channels)
            if chan.lower().endswith("rotation") and chan.lower()[0] in "xyz"
        }
        if set(cols) != {"x", "y", "z"}:
            return None

        order = self.bvh_utils.get_rotate_order_index(node.channels)
        mats = [
            om.MEulerRotation(cols["x"][i], cols["y"][i], cols["z"][i], order).asMatrix()
            for i in range(data.frame_count)
        ]
        if rest_matrix:
            flat = [
                rest_matrix[i][j] if i < 3 and j < 3 else (1.0 if i == j else 0.0)
                for i in range(4)
                for j in range(4)
            ]
            rest = om.MMatrix(flat)
        else:
            rest = mats[rest_frame if rest_frame is not None else 0]

        # Fold the extra Z rotation into the rest pose. jointOrient and the
        # animation deltas are both derived from `rest`, so the orient gets the
        # extra angle and the imported motion stays identical.
        if extra_z_deg:
            extra = om.MEulerRotation(0.0, 0.0, math.radians(extra_z_deg), 0).asMatrix()
            rest = extra * rest
            
        if extra_y_deg:
            extra = om.MEulerRotation(0.0, 0.0, math.radians(extra_y_deg), 0).asMatrix()
            rest = extra * rest


        rest_inv = rest.inverse()

        jo = self._matrix_to_euler(rest, 0)
        ui = om.MAngle.uiUnit()
        cmds.setAttr(
            full + ".jointOrient",
            om.MAngle(jo.x).asUnits(ui),
            om.MAngle(jo.y).asUnits(ui),
            om.MAngle(jo.z).asUnits(ui),
            type="double3",
        )

        out, prev = {"x": [], "y": [], "z": []}, None
        for m in mats:
            e = self._matrix_to_euler(m * rest_inv, order)
            cur = (e.x, e.y, e.z)
            if prev:
                cur = tuple(self._unwrap(c, p) for c, p in zip(cur, prev))
            prev = cur
            for k, val in zip("xyz", cur):
                out[k].append(val)
        return out

    def _build_joints(self, node, parent_path, out):
        if node.is_end_site and not self.end_sites:
            return
        kwargs = {"name": self.bvh_utils.sanitize_name(node.name)}
        if parent_path:
            kwargs["parent"] = parent_path
        created = cmds.createNode("joint", **kwargs)
        full = (parent_path or "") + "|" + created.split("|")[-1]

        ox, oy, oz = (v * self.scale for v in node.offset)
        cmds.setAttr(full + ".translate", ox, oy, oz, type="double3")
        if node.channels:
            cmds.setAttr(
                full + ".rotateOrder", self.bvh_utils.get_rotate_order_index(node.channels)
            )

        out.append((node, full))
        for child in node.children:
            self._build_joints(child, full, out)

    def _apply_animation(
        self,
        node,
        full,
        data,
        times,
        unit,
        tangent,
        recenter_axes=None,
        tpose_rest=None,
    ):
        if not node.channels:
            return None
        additive = self.bvh_utils.resolve_position_mode(node, data, self.position_mode)

        extra_z = JOINT_EXTRA_Z.get(self.bvh_utils.sanitize_name(node.name), 0.0)
        extra_y = JOINT_EXTRA_Y.get(self.bvh_utils.sanitize_name(node.name), 0.0)
        if tpose_rest and id(node) in tpose_rest:
            rest_rot = self._bake_rest_pose(
                node,
                full,
                data,
                rest_matrix=tpose_rest[id(node)],
                extra_z_deg=extra_z,
                extra_y_deg = extra_y,
            )
        elif self.rest_pose_frame is not None:
            rest_rot = self._bake_rest_pose(
                node,
                full,
                data,
                rest_frame=self.rest_pose_frame,
                extra_z_deg=extra_z,
                extra_y_deg=extra_y,
            )
        elif extra_z:
            rest_rot = self._bake_rest_pose(
                node,
                full,
                data,
                rest_matrix=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                extra_z_deg=extra_z,
            )
            
        elif extra_y:
            rest_rot = self._bake_rest_pose(
                node,
                full,
                data,
                rest_matrix=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                extra_y_deg = extra_y,
            )
        else:
            rest_rot = None

        sel = om.MSelectionList()
        sel.add(full)
        dep_fn = om.MFnDependencyNode(sel.getDependNode(0))
        dist_factor, n_ch, rot_curves = om.MDistance.uiToInternal(1.0), data.channel_count, []

        for idx, chan in enumerate(node.channels):
            low, axis = chan.lower(), chan.lower()[0]
            if axis not in "xyz":
                continue
            column = data.values[node.channel_start + idx :: n_ch]

            if low.endswith("position"):
                attr = f"translate{axis.upper()}"
                base = (
                    [v + node.offset["xyz".index(axis)] for v in column]
                    if additive
                    else list(column)
                )
                if recenter_axes and axis in recenter_axes and base:
                    base = [v - base[0] for v in base]
                values = [v * self.scale * dist_factor for v in base]
                curve_type = oma.MFnAnimCurve.kAnimCurveTL
            elif low.endswith("rotation"):
                attr = f"rotate{axis.upper()}"
                values = rest_rot[axis] if rest_rot else [math.radians(v) for v in column]
                curve_type = oma.MFnAnimCurve.kAnimCurveTA
            else:
                continue

            plug = dep_fn.findPlug(attr, False)
            curve_obj = oma.MFnAnimCurve().create(plug, curve_type)
            oma.MFnAnimCurve(curve_obj).addKeys(
                times, om.MDoubleArray(values), tangent, tangent
            )
            if curve_type == oma.MFnAnimCurve.kAnimCurveTA:
                rot_curves.append(om.MFnDependencyNode(curve_obj).name())

        if self.euler_filter and len(rot_curves) == 3:
            try:
                cmds.filterCurve(*rot_curves)
            except RuntimeError:
                pass

    def execute(self):
        if self.path is None:
            picked = cmds.fileDialog2(
                fileMode=1,
                caption="Import BVH",
                fileFilter="BVH Motion Capture (*.bvh);;All Files (*.*)",
            )
            if not picked:
                return []
            self.path = picked[0]
        self.path = os.path.expanduser(self.path)
        if not os.path.isfile(self.path):
            raise FileNotFoundError(self.path)

        data = self.reader.parse_file(self.path)
        if self.set_fps:
            self.bvh_utils.set_scene_fps(data.frame_time)
        unit = om.MTime.uiUnit()
        times = om.MTimeArray(
            [om.MTime(self.start_frame + i, unit) for i in range(data.frame_count)]
        )

        base, root_paths, built = (
            self.bvh_utils.sanitize_name(
                os.path.splitext(os.path.basename(self.path))[0]
            ),
            [],
            [],
        )
        cmds.refresh(suspend=True)
        try:
            group = None
            if self.z_up_to_y:
                group = cmds.ls(
                    cmds.group(empty=True, name=f"{base}_grp"), long=True
                )[0]
                cmds.setAttr(group + ".rotateX", -90)

            for root in data.roots:
                before = len(built)
                self._build_joints(root, group, built)
                root_paths.append(built[before][1])

            tan = self.bvh_utils.TANGENTS.get(
                self.tangent, oma.MFnAnimCurve.kTangentLinear
            )
            trans_roots = set()
            for root in data.roots:
                self.bvh_utils.collect_translation_roots(
                    root, data, False, trans_roots
                )
            horizontal = {"x", "y"} if self.z_up_to_y else {"x", "z"}
            tpose_rest = (
                self.pose_solver.solve_tpose(
                    data.roots, "z" if self.z_up_to_y else "y"
                )[0]
                if self.tpose
                else None
            )

            for node, full in built:
                recenter = (
                    horizontal
                    if (self.start_at_origin and id(node) in trans_roots)
                    else None
                )
                self._apply_animation(
                    node, full, data, times, unit, tan, recenter, tpose_rest
                )
        finally:
            cmds.refresh(suspend=False)

        if self.set_range and data.frame_count:
            end_frame = self.start_frame + data.frame_count - 1
            cmds.playbackOptions(
                minTime=self.start_frame,
                maxTime=end_frame,
                animationStartTime=self.start_frame,
                animationEndTime=end_frame,
            )
            cmds.currentTime(self.start_frame)

        cmds.select(root_paths, replace=True)
        return root_paths

    def _find_under(self, root, short_name):
        """Full path of the joint called `short_name` in `root`'s hierarchy
        (or anywhere in the scene when no root is given)."""
        if not root:
            found = cmds.ls(short_name, type="joint", long=True)
            return found[0] if found else None
        hits = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
        hits.append(cmds.ls(root, long=True)[0])
        for h in hits:
            if h.split("|")[-1].split(":")[-1] == short_name:
                return h
        return None

    @staticmethod
    def _axis_angle_matrix(axis, angle):
        """Maya (row-vector) MMatrix rotating `angle` radians about unit `axis`."""
        x, y, z = axis.x, axis.y, axis.z
        c, s = math.cos(angle), math.sin(angle)
        t = 1.0 - c
        return om.MMatrix([
            c + x * x * t, y * x * t + z * s, z * x * t - y * s, 0.0,
            x * y * t - z * s, c + y * y * t, z * y * t + x * s, 0.0,
            x * z * t + y * s, y * z * t - x * s, c + z * z * t, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ])

    def _lower_arm(self, arm, fore_arm, angle_deg):
        """Rotate `arm` (rotate attr currently zero) in world space so the
        arm -> fore_arm bone points `angle_deg` below horizontal. The rotation
        axis is perpendicular to the bone and world down, so the arm swings
        straight down without twisting. Returns True on success."""
        if not arm or not fore_arm or not angle_deg:
            return False
        p0 = om.MVector(cmds.xform(arm, q=True, ws=True, t=True))
        p1 = om.MVector(cmds.xform(fore_arm, q=True, ws=True, t=True))
        down = om.MVector(0.0, -1.0, 0.0)
        bone = (p1 - p0).normal()
        axis = bone ^ down
        if axis.length() < 1e-6:
            return False
        axis.normalize()

        sel = om.MSelectionList()
        sel.add(arm)
        world = om.MTransformationMatrix(sel.getDagPath(0).inclusiveMatrix())
        w_rot = world.rotation(asQuaternion=True).asMatrix()
        order = cmds.getAttr(arm + ".rotateOrder")
        ui = om.MAngle.uiUnit()
        start = math.degrees(math.asin(max(-1.0, min(1.0, -bone.y))))

        # rotate is zero, so world rotation = jointOrient * parent. A world
        # rotation Rw on top of that is rotate = W * Rw * W^-1. Try both signs
        # and keep the one that lands on the requested angle.
        for sign in (1.0, -1.0):
            rw = self._axis_angle_matrix(axis, math.radians(sign * angle_deg))
            e = self._matrix_to_euler(w_rot * rw * w_rot.inverse(), order)
            cmds.setAttr(
                arm + ".rotate",
                om.MAngle(e.x).asUnits(ui),
                om.MAngle(e.y).asUnits(ui),
                om.MAngle(e.z).asUnits(ui),
                type="double3",
            )
            q1 = om.MVector(cmds.xform(fore_arm, q=True, ws=True, t=True))
            got = math.degrees(math.asin(max(-1.0, min(1.0, -(q1 - p0).normal().y))))
            if abs(got - (start + angle_deg)) < 1.0:
                return True
        cmds.setAttr(arm + ".rotate", 0, 0, 0, type="double3")
        cmds.warning(f"Could not lower {arm} into the A-pose.")
        return False

    def apply_rest_pose(self, root=None, pose="tpose", apose_angle=29.0):
        """Put a Kimodo skeleton into its rest stance at the current frame,
        ready for a HumanIK definition. "tpose" is the solved T-pose;
        "apose" then lowers both arms by `apose_angle` degrees."""
        root = root or self._find_under(None, "Root")
        if not root:
            cmds.warning("No Kimodo 'Root' joint found to pose.")
            return False
        joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
        joints.append(cmds.ls(root, long=True)[0])
        for joint in joints:
            cmds.setAttr(f"{joint}.rotate", 0, 0, 0)

        # Kimodo's right-side bones are rolled 180 degrees relative to the
        # left; this puts the right palm back in line with the left one.
        right_shoulder = self._find_under(root, "RightShoulder")
        if right_shoulder:
            cmds.setAttr(right_shoulder + ".rotate", 180, 0, 0)

        if pose == "apose":
            for side in ("Left", "Right"):
                self._lower_arm(
                    self._find_under(root, f"{side}Arm"),
                    self._find_under(root, f"{side}ForeArm"),
                    apose_angle,
                )
        cmds.select(clear=True)
        return True

    def apply_t_pose(self, root=None):
        return self.apply_rest_pose(root, "tpose")

import maya.cmds as cmds
import maya.mel as mel


class HIKCharacterBuilder:

    # Base HIK slot IDs fallback mapping
    _BASE_FALLBACK_IDS = {
        "Hips": 1,
        "LeftUpLeg": 2,
        "LeftLeg": 3,
        "LeftFoot": 4,
        "RightUpLeg": 5,
        "RightLeg": 6,
        "RightFoot": 7,
        "Spine": 8,
        "LeftArm": 9,
        "LeftForeArm": 10,
        "LeftHand": 11,
        "RightArm": 12,
        "RightForeArm": 13,
        "RightHand": 14,
        "Head": 15,
        "LeftToeBase": 16,
        "RightToeBase": 17,
        "LeftShoulder": 18,
        "RightShoulder": 19,
        "Neck": 20,
    }

    OPTIONAL_MAPPING = {
        "Neck": "Neck",
        "LeftShoulder": "LeftShoulder",
        "RightShoulder": "RightShoulder",
        "LeftToeBase": "LeftToeBase",
        "RightToeBase": "RightToeBase",
    }

    def __init__(
        self,
        char_name="Character1",
        namespace=None,
        root=None,
        include_optional=True,
        lock_definition=True,
    ):
        self.char_name = char_name
        self.namespace = namespace
        self.root = root
        self.include_optional = include_optional
        self.lock_definition = lock_definition

        self.fallback_ids = self._generate_fallback_ids()
        self.mapping = self.build_mapping()

    @classmethod
    def _generate_fallback_ids(cls):
        """Generates full HIK ID lookup table including fingers."""
        ids = cls._BASE_FALLBACK_IDS.copy()
        finger_starts = {
            "Thumb": 50,
            "Index": 54,
            "Middle": 58,
            "Ring": 62,
            "Pinky": 66,
        }
        right_offset = 24

        for side, off in (("Left", 0), ("Right", right_offset)):
            for f, start in finger_starts.items():
                for i in range(4):
                    slot_key = f"{side}Hand{f}{i + 1}"
                    ids[slot_key] = start + off + i
        return ids

    def build_mapping(self):
        m = {"Hips": "Hips", "Head": "Head", "Spine": "Spine1"}

        for side in ("Left", "Right"):
            m[f"{side}Arm"] = f"{side}Arm"
            m[f"{side}ForeArm"] = f"{side}ForeArm"
            m[f"{side}Hand"] = f"{side}Hand"
            m[f"{side}UpLeg"] = f"{side}Leg"
            m[f"{side}Leg"] = f"{side}Shin"
            m[f"{side}Foot"] = f"{side}Foot"

            for finger in ("Thumb", "Index", "Middle", "Ring", "Pinky"):
                base = f"{side}Hand{finger}"
                if finger == "Thumb":
                    m[f"{base}1"] = f"{base}1"
                    m[f"{base}2"] = f"{base}2"
                    m[f"{base}3"] = f"{base}3"
                    m[f"{base}4"] = f"{base}End"
                else:
                    m[f"{base}1"] = f"{base}2"
                    m[f"{base}2"] = f"{base}3"
                    m[f"{base}3"] = f"{base}4"
                    m[f"{base}4"] = f"{base}End"

        if self.include_optional:
            m.update(self.OPTIONAL_MAPPING)

        return m

    def _get_slot_id(self, slot_name):
        try:
            sid = mel.eval(f'hikGetNodeIdFromName("{slot_name}")')
            if sid is not None and int(sid) >= 0:
                return int(sid)
        except RuntimeError:
            pass
        return self.fallback_ids.get(slot_name, -1)

    def _find_joint(self, name):
        """Finds joint by short name, considering namespace and optional root parent."""
        candidates = []
        if self.namespace:
            candidates.append(f"{self.namespace.rstrip(':')}:{name}")
        candidates.append(name)

        for c in candidates:
            if self.root:
                hits = (
                    cmds.listRelatives(
                        self.root,
                        allDescendents=True,
                        type="joint",
                        fullPath=True,
                    )
                    or []
                )
                hits.append(cmds.ls(self.root, long=True)[0])
                for h in hits:
                    if h.split("|")[-1] == c:
                        return h
            else:
                found = cmds.ls(c, type="joint", long=True)
                if found:
                    return found[0]
        return None

    def create(self):
        if cmds.about(batch=True):
            return self._create_batch()
        mel.eval("HIKCharacterControlsTool;")
        char_node = mel.eval(f'hikCreateCharacter("{self.char_name}")')
        char_node = char_node or self.char_name

        assigned, missing_joints, bad_slots = [], [], []

        for slot_name, joint_name in self.mapping.items():
            sid = self._get_slot_id(slot_name)
            if sid < 0:
                bad_slots.append(slot_name)
                continue

            joint = self._find_joint(joint_name)
            if not joint:
                missing_joints.append(f"{slot_name}  <-  {joint_name}")
                continue

            mel.eval(
                f'setCharacterObject("{joint}", "{char_node}", {sid}, 0)'
            )
            assigned.append((slot_name, joint.split("|")[-1]))

        if self.lock_definition:
            try:
                mel.eval("hikToggleLockDefinition();")
            except RuntimeError:
                pass

        try:
            mel.eval("hikUpdateDefinitionUI;")
        except RuntimeError:
            pass

        self._print_report(char_node, assigned, missing_joints, bad_slots)
        return char_node

    def _create_batch(self):
        """create() without the HumanIK window, for mayapy. Builds the
        HIKCharacterNode and connects each joint to its slot directly."""
        if not cmds.pluginInfo("mayaHIK", query=True, loaded=True):
            cmds.loadPlugin("mayaHIK")
        char_node = cmds.createNode("HIKCharacterNode", name=self.char_name)

        assigned, missing_joints, bad_slots = [], [], []
        for slot_name, joint_name in self.mapping.items():
            if not cmds.attributeQuery(slot_name, node=char_node, exists=True):
                bad_slots.append(slot_name)
                continue
            joint = self._find_joint(joint_name)
            if not joint:
                missing_joints.append(f"{slot_name}  <-  {joint_name}")
                continue
            if not cmds.attributeQuery("Character", node=joint, exists=True):
                cmds.addAttr(joint, longName="Character", attributeType="message")
            cmds.connectAttr(joint + ".Character", f"{char_node}.{slot_name}", force=True)
            assigned.append((slot_name, joint.split("|")[-1]))

        if self.lock_definition and cmds.attributeQuery(
            "InputCharacterizationLock", node=char_node, exists=True
        ):
            cmds.setAttr(char_node + ".InputCharacterizationLock", 1)

        self._print_report(char_node, assigned, missing_joints, bad_slots)
        return char_node

    @staticmethod
    def _print_report(char_node, assigned, missing_joints, bad_slots):
        print("=" * 60)
        print(f"HIK character: {char_node}")

class CCCharacterDefinition:
    """Manages creation and joint mapping of a HumanIK Character Definition for Character Creator rigs."""

    FALLBACK_IDS = {
        "Reference": 0, "Hips": 1, "LeftUpLeg": 2, "LeftLeg": 3, "LeftFoot": 4,
        "RightUpLeg": 5, "RightLeg": 6, "RightFoot": 7,
        "Spine": 8, "LeftArm": 9, "LeftForeArm": 10, "LeftHand": 11,
        "RightArm": 12, "RightForeArm": 13, "RightHand": 14,
        "Head": 15, "LeftToeBase": 16, "RightToeBase": 17,
        "LeftShoulder": 18, "RightShoulder": 19, "Neck": 20,
        "Spine1": 23, "Spine2": 24, "Neck1": 32
    }

    _FINGER_START = {"Thumb": 50, "Index": 54, "Middle": 58, "Ring": 62, "Pinky": 66}
    _RIGHT_OFFSET = 24
    for _side, _off in (("Left", 0), ("Right", _RIGHT_OFFSET)):
        for _f, _start in _FINGER_START.items():
            for _i in range(4):
                FALLBACK_IDS["%sHand%s%d" % (_side, _f, _i + 1)] = _start + _off + _i

    def __init__(self, char_name="CC_Character", namespace=None, root=None, auto_lock=True):
        self.char_name = char_name
        self.namespace = namespace.rstrip(":") if namespace else None
        self.root = root
        self.auto_lock = auto_lock

        self.char_node = None
        self.assigned_slots = []
        self.missing_joints = []
        self.bad_slots = []

    def build_cc_mapping(self):
        """Constructs the HIK slot -> Character Creator joint mapping dictionary."""
        mapping = {
            "Reference": "CC_Base_BoneRoot",
            "Hips": "CC_Base_Hip",
            "Spine": "CC_Base_Waist",
            "Spine1": "CC_Base_Spine01",
            "Spine2": "CC_Base_Spine02",
            "Neck": "CC_Base_NeckBone",
            "Head": "CC_Base_Head",
        }

        finger_map = {
            "Thumb": "Thumb",
            "Index": "Index",
            "Middle": "Mid",  
            "Ring": "Ring",
            "Pinky": "Pinky",
        }

        for hik_side, cc_side in [("Left", "L"), ("Right", "R")]:
            # Arms & Legs
            mapping[f"{hik_side}Shoulder"] = f"CC_Base_{cc_side}_Clavicle"
            mapping[f"{hik_side}Arm"] = f"CC_Base_{cc_side}_Upperarm"
            mapping[f"{hik_side}ForeArm"] = f"CC_Base_{cc_side}_Forearm"
            mapping[f"{hik_side}Hand"] = f"CC_Base_{cc_side}_Hand"

            mapping[f"{hik_side}UpLeg"] = f"CC_Base_{cc_side}_Thigh"
            mapping[f"{hik_side}Leg"] = f"CC_Base_{cc_side}_Calf"
            mapping[f"{hik_side}Foot"] = f"CC_Base_{cc_side}_Foot"
            mapping[f"{hik_side}ToeBase"] = f"CC_Base_{cc_side}_ToeBase"

            # Fingers
            for hik_finger, cc_finger in finger_map.items():
                for seg in range(1, 4):
                    hik_slot = f"{hik_side}Hand{hik_finger}{seg}"
                    cc_joint = f"CC_Base_{cc_side}_{cc_finger}{seg}"
                    mapping[hik_slot] = cc_joint

        return mapping

    def get_slot_id(self, slot_name):
        """Queries Maya for the HIK slot ID, falling back to cached dictionary."""
        try:
            sid = mel.eval(f'hikGetNodeIdFromName("{slot_name}")')
            if sid is not None and int(sid) >= 0:
                return int(sid)
        except RuntimeError:
            pass
        return self.FALLBACK_IDS.get(slot_name, -1)

    def find_joint(self, name):
        """Resolves joint name considering namespace and hierarchy root."""
        candidates = []
        if self.namespace:
            candidates.append(f"{self.namespace}:{name}")
        candidates.append(name)

        for c in candidates:
            if self.root:
                hits = cmds.listRelatives(self.root, allDescendents=True, type="joint", fullPath=True) or []
                hits.append(cmds.ls(self.root, long=True)[0])
                for h in hits:
                    if h.split("|")[-1] == c:
                        return h
            else:
                found = cmds.ls(c, type="joint", long=True)
                if found:
                    return found[0]

        if "ToeBase" in name:
            alt_name = name.replace("ToeBase", "PinkyToe1")
            return self.find_joint(alt_name)

        return None

    def create_definition(self):
        mel.eval("HIKCharacterControlsTool;")
        node = mel.eval(f'hikCreateCharacter("{self.char_name}")')
        self.char_node = node or self.char_name
        return self.char_node

    def map_joints(self):
        if not self.char_node:
            self.create_definition()

        mapping = self.build_cc_mapping()
        self.assigned_slots.clear()
        self.missing_joints.clear()
        self.bad_slots.clear()

        for slot_name, joint_name in mapping.items():
            sid = self.get_slot_id(slot_name)
            if sid < 0:
                self.bad_slots.append(slot_name)
                continue

            joint = self.find_joint(joint_name)
            if not joint:
                self.missing_joints.append((slot_name, joint_name))
                continue

            mel.eval(f'setCharacterObject("{joint}", "{self.char_node}", {sid}, 0)')
            self.assigned_slots.append((slot_name, joint.split("|")[-1]))

    def lock(self):
        try:
            mel.eval("hikToggleLockDefinition();")
        except RuntimeError:
            pass

    def refresh_ui(self):
        try:
            mel.eval("hikUpdateDefinitionUI;")
        except RuntimeError:
            pass

    def create(self):
        self.create_definition()
        self.map_joints()

        if self.auto_lock:
            self.lock()

        self.refresh_ui()
        self.print_report()
        return self

    def print_report(self):
        print("=" * 60)
        print(f"HIK Character Created: {self.char_node}")
        print(f"Successfully Assigned {len(self.assigned_slots)} Slots.")
        for s, j in self.assigned_slots:
            print(f"  {s:<22} <- {j}")

        if self.missing_joints:
            print(f"\nMissing/Unmapped Skeleton Joints ({len(self.missing_joints)}):")
            for slot_name, joint_name in self.missing_joints:
                print(f"  {slot_name:<22} <- {joint_name}")

        if self.bad_slots:
            print(f"\nUnknown HIK Slot IDs ({len(self.bad_slots)}): {', '.join(self.bad_slots)}")
        print("=" * 60)

    
if QtWidgets is not None:
    class QtUI(MayaQWidgetBaseMixin, QtWidgets.QWidget):
        def __init__(self, parent=None):
            super(QtUI, self).__init__(parent=parent)
            self.setWindowFlags(QtCore.Qt.Window)
            self.setObjectName("bvh_import")
            self.setWindowTitle("Bvh Import")

            # Window Layouts
            main_layout = QtWidgets.QVBoxLayout(self)
            file_layout = QtWidgets.QHBoxLayout()
            scale_layout = QtWidgets.QHBoxLayout()
            start_frame_layout = QtWidgets.QHBoxLayout()
            zero_pose_frame_layout = QtWidgets.QHBoxLayout()
            kim_ik_layout = QtWidgets.QHBoxLayout()
            cc_ik_layout = QtWidgets.QHBoxLayout()

            # Widgets - File Row
            file_label = QtWidgets.QLabel("File")
            self.path_field = QtWidgets.QLineEdit()
            browse_button = QtWidgets.QPushButton("Browse...")

            file_layout.addWidget(file_label)
            file_layout.addWidget(self.path_field)
            file_layout.addWidget(browse_button)

            # Widgets - Scale Row
            scale_label = QtWidgets.QLabel("Scale")
            self.scale_field = QtWidgets.QDoubleSpinBox()
            self.scale_field.setDecimals(4)
            self.scale_field.setRange(0.0001, 1000.0)
            self.scale_field.setValue(1.0)
            self.scale_field.setSingleStep(0.1)
            scale_layout.addWidget(scale_label)
            scale_layout.addWidget(self.scale_field)

            # Widgets - Start Frame Row
            start_frame_label = QtWidgets.QLabel("Start Frame")
            self.start_frame_field = QtWidgets.QSpinBox()
            self.start_frame_field.setRange(-10000, 10000)
            self.start_frame_field.setValue(1)
            start_frame_layout.addWidget(start_frame_label)
            start_frame_layout.addWidget(self.start_frame_field)

            # Widgets - Zero Pose Frame Row
            zero_pose_frame_label = QtWidgets.QLabel("Zero Pose Frame")
            self.zero_pose_frame_field = QtWidgets.QSpinBox()
            self.zero_pose_frame_field.setRange(-10000, 10000)
            self.zero_pose_frame_field.setValue(-1)
            zero_pose_frame_layout.addWidget(zero_pose_frame_label)
            zero_pose_frame_layout.addWidget(self.zero_pose_frame_field)

            # Check Boxes
            self.fps_checkbox = QtWidgets.QCheckBox("Set scene frame rate from BVH")
            self.fps_checkbox.setChecked(True)

            self.pbr_checkbox = QtWidgets.QCheckBox("Set playback range")
            self.pbr_checkbox.setChecked(True)

            self.jts_checkbox = QtWidgets.QCheckBox("Create End Site Joints")
            self.jts_checkbox.setChecked(True)

            self.elr_checkbox = QtWidgets.QCheckBox("Euler filter rotations")
            self.elr_checkbox.setChecked(True)

            self.zup_checkbox = QtWidgets.QCheckBox("Z-Up")
            self.zup_checkbox.setChecked(False)

            self.org_checkbox = QtWidgets.QCheckBox("Start at Origin")
            self.org_checkbox.setChecked(False)

            # Kimodo setup run as part of Import: rest stance + HumanIK definition
            self.kimodo_checkbox = QtWidgets.QCheckBox("Kimodo: pose + Human_IK on import")
            self.kimodo_checkbox.setChecked(True)

            pose_layout = QtWidgets.QHBoxLayout()
            pose_label = QtWidgets.QLabel("Stance")
            self.tpose_radio = QtWidgets.QRadioButton("T-Pose")
            self.apose_radio = QtWidgets.QRadioButton("A-Pose")
            self.apose_radio.setChecked(True)
            self.apose_angle_field = QtWidgets.QDoubleSpinBox()
            self.apose_angle_field.setRange(0.0, 90.0)
            self.apose_angle_field.setDecimals(1)
            self.apose_angle_field.setSuffix(" deg")
            self.apose_angle_field.setValue(29.0)  # CC3 girlTest3 arms sit ~29 deg below horizontal
            self.apose_angle_field.setToolTip("How far below horizontal the arms go in the A-pose")
            pose_layout.addWidget(pose_label)
            pose_layout.addWidget(self.tpose_radio)
            pose_layout.addWidget(self.apose_radio)
            pose_layout.addWidget(self.apose_angle_field)
            self.apose_radio.toggled.connect(self.apose_angle_field.setEnabled)

            # Add to Layout
            main_layout.addLayout(file_layout)
            main_layout.addLayout(scale_layout)
            main_layout.addLayout(start_frame_layout)
            main_layout.addLayout(zero_pose_frame_layout)
            main_layout.addWidget(self.fps_checkbox)
            main_layout.addWidget(self.pbr_checkbox)
            main_layout.addWidget(self.jts_checkbox)
            main_layout.addWidget(self.elr_checkbox)
            main_layout.addWidget(self.zup_checkbox)
            main_layout.addWidget(self.org_checkbox)
            main_layout.addWidget(self.kimodo_checkbox)
            main_layout.addLayout(pose_layout)

            # Action Buttons
            self.import_button = QtWidgets.QPushButton("Import")
            self.cc_ik_layout_button = QtWidgets.QPushButton("CC3: Human_IK")

            main_layout.addWidget(self.import_button)
            main_layout.addWidget(self.cc_ik_layout_button)

            # Connect Signals
            browse_button.clicked.connect(self.browse_file)
            self.import_button.clicked.connect(self.import_button_onClicked)
            self.cc_ik_layout_button.clicked.connect(self.cc_ik_button_onClicked)

        # Callbacks
        def browse_file(self):
            picked = cmds.fileDialog2(
                fileMode=1,
                caption="Select BVH File",
                fileFilter="BVH Motion Capture (*.bvh);;All Files(*.*)",
            )
            if picked:
                self.path_field.setText(picked[0])

        def kimodo_onClicked(self, root=None):
            importer = MayaBVHImporter()
            pose = "apose" if self.apose_radio.isChecked() else "tpose"
            return importer.apply_rest_pose(root, pose, self.apose_angle_field.value())

        def import_button_onClicked(self):
            path = self.path_field.text().strip()
            if not path:
                return cmds.warning("Choose a .bvh file first.")

            zero_frame = self.zero_pose_frame_field.value()
            rest_pose = zero_frame if zero_frame >= 0 else None

            importer = MayaBVHImporter(
                path=path,
                scale=self.scale_field.value(),
                start_frame=self.start_frame_field.value(),
                set_fps=self.fps_checkbox.isChecked(),
                set_range=self.pbr_checkbox.isChecked(),
                end_sites=self.jts_checkbox.isChecked(),
                euler_filter=self.elr_checkbox.isChecked(),
                z_up_to_y=self.zup_checkbox.isChecked(),
                start_at_origin=self.org_checkbox.isChecked(),
                rest_pose_frame=rest_pose,
                tpose=True,
            )
            root_paths = importer.execute()

            if root_paths and self.kimodo_checkbox.isChecked():
                pose = "apose" if self.apose_radio.isChecked() else "tpose"
                setup_kimodo(root_paths[0], pose, self.apose_angle_field.value())

        def kim_ik_button_onClicked(self, root=None):
            kim_ik_set = HIKCharacterBuilder(root=root)
            kim_ik_set.create()
        
        def cc_ik_button_onClicked(self):
            cc_ik_set = CCCharacterDefinition()
            cc_ik_set.create()

def setup_kimodo(root, pose="apose", apose_angle=29.0, humanik=True):
    """The Kimodo step of Import: put the skeleton into its T-pose or A-pose
    stance at the current frame, then add the Kimodo HumanIK definition.
    Returns the HIK character node (or root without HumanIK), None if skipped."""
    importer = MayaBVHImporter()
    if not importer._find_under(root, "Hips") or not importer._find_under(root, "LeftArm"):
        cmds.warning("Not a Kimodo skeleton; skipped the pose and Human_IK setup.")
        return None
    if not importer.apply_rest_pose(root, pose, apose_angle):
        return None
    if not humanik:
        return root
    return HIKCharacterBuilder(root=root).create()


def show_ui():
    global main_window

    if main_window is not None:
        try:
            main_window.close()
            main_window.deleteLater()
        except RuntimeError:
            pass

    main_window = QtUI()
    main_window.show()


def import_bvh(**kwargs):
    return MayaBVHImporter(**kwargs).execute()


if not cmds.about(batch=True):
    show_ui()
