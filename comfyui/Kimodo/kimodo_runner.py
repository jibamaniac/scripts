"""Kimodo text-to-motion -> BVH, shared by both backends of the ComfyUI nodes.

Imported in-process by the nodes (backend "in_process"), or run as a long-lived
worker under the Kimodo venv (backend "kimodo_venv"):

    <kimodo venv>/python.exe kimodo_runner.py --worker --repo C:\\path\\to\\kimodo

The worker reads one JSON request per line on stdin and writes one JSON reply
per line on stdout. Everything Kimodo prints goes to stderr, so the protocol
channel stays clean. The loaded model is kept between requests.

This mirrors kimodo/scripts/generate.py (model load, model call and the SOMA
BVH export) without importing it, because that module pulls
in the viser demo package.
"""
import gc
import json
import os
import sys
import traceback

_CACHE = {"name": None, "model": None, "resolved": None}
_SKELETONS = {}  # model name -> (export skeleton, device); small, kept after unload


def add_kimodo_to_path(repo_dir):
    """Make `import kimodo` work. repo_dir is the git checkout that contains the
    `kimodo` package folder (the folder you cloned)."""
    repo_dir = os.path.abspath(repo_dir)
    if not os.path.isdir(os.path.join(repo_dir, "kimodo")):
        raise FileNotFoundError(f"No 'kimodo' package folder inside {repo_dir}")
    if repo_dir not in sys.path:
        sys.path.append(repo_dir)
    return repo_dir


def _device():
    import torch

    return "cuda:0" if torch.cuda.is_available() else "cpu"


def unload_model():
    _CACHE.update(name=None, model=None, resolved=None)
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def get_model(model_name):
    """Load (or reuse) a Kimodo model. Only one model is kept at a time."""
    if _CACHE["model"] is not None and _CACHE["name"] == model_name:
        return _CACHE["model"], _CACHE["resolved"]
    unload_model()
    from kimodo import load_model
    from kimodo.skeleton import SOMASkeleton30

    device = _device()
    model, resolved = load_model(model_name, device=device, default_family="Kimodo", return_resolved_name=True)
    skeleton = model.skeleton
    if "somaskel" not in skeleton.name:
        raise ValueError(f"BVH export needs a SOMA model; {resolved} uses skeleton '{skeleton.name}'.")
    if isinstance(skeleton, SOMASkeleton30):
        # The model converts its output to somaskel77, so export against that.
        skeleton = skeleton.somaskel77.to(device)
    _SKELETONS[model_name] = (skeleton, device)
    _CACHE.update(name=model_name, model=model, resolved=resolved)
    print(f"[Kimodo] Loaded model {resolved}", file=sys.stderr, flush=True)
    return model, resolved


def load(model_name):
    model, resolved = get_model(model_name)
    return {"model": resolved, "fps": float(model.fps)}


def _root_keyframe_class():
    """Root2DConstraintSet that can also pin the pelvis height (Kimodo's root_y_pos
    feature) on its frames. Kimodo's own root2d set only pins x/z and heading."""
    from kimodo.constraints import Root2DConstraintSet

    class RootKeyframeSet(Root2DConstraintSet):
        def __init__(self, skeleton, frame_indices, smooth_root_2d, global_root_heading=None, root_y=None):
            super().__init__(skeleton, frame_indices, smooth_root_2d, global_root_heading=global_root_heading)
            self.root_y = root_y

        def update_constraints(self, data_dict, index_dict):
            super().update_constraints(data_dict, index_dict)
            if self.root_y is not None:
                data_dict["root_y_pos"].append(self.root_y)
                index_dict["root_y_pos"].append(self.frame_indices)

        def crop_move(self, start, end):
            mask = (self.frame_indices >= start) & (self.frame_indices < end)
            return RootKeyframeSet(
                self.skeleton,
                self.frame_indices[mask] - start,
                self.smooth_root_2d[mask],
                global_root_heading=None if self.global_root_heading is None else self.global_root_heading[mask],
                root_y=None if self.root_y is None else self.root_y[mask],
            )

        def to(self, device=None, dtype=None):
            super().to(device=device, dtype=dtype)
            if self.root_y is not None:
                self.root_y = self.root_y.to(device=device or self.root_y.device, dtype=dtype or self.root_y.dtype)
            return self

    return RootKeyframeSet


def build_constraints(model, keyframes, total_frames):
    """Turn root keyframes into Kimodo constraint sets.

    Each keyframe is {"time": s, "x": m, "z": m, "heading_deg": opt, "hip_height": opt}
    in Kimodo's world space: metres, Y up, the character starts at the origin
    facing +Z, heading 90 faces +X.
    """
    import math

    import torch

    RootKeyframeSet = _root_keyframe_class()
    device = model.device
    constraints = []
    for k in keyframes:
        frame = int(round(float(k["time"]) * model.fps))
        if not 0 <= frame < total_frames:
            raise ValueError(f"Keyframe at {k['time']} s is outside the motion (0 to {total_frames / model.fps:.2f} s).")
        heading = None
        if k.get("heading_deg") is not None:
            a = math.radians(float(k["heading_deg"]))
            heading = torch.tensor([[math.cos(a), math.sin(a)]], device=device)
        root_y = None
        if k.get("hip_height") is not None:
            root_y = torch.tensor([float(k["hip_height"])], device=device)
        constraints.append(RootKeyframeSet(
            model.skeleton,
            torch.tensor([frame], device=device),
            torch.tensor([[float(k["x"]), float(k["z"])]], device=device),
            global_root_heading=heading,
            root_y=root_y,
        ))
    return constraints


def sample(model_name, segments, out_npz, seed=0, diffusion_steps=100, cfg=2.0,
           num_transition_frames=5, post_processing=True, keep_model_loaded=True, keyframes=None):
    """Run the diffusion model on a list of {"text", "duration", "seed"?} segments
    (played back to back, like the CLI's period-separated prompts), optionally
    constrained by root keyframes, and save the motion as a Kimodo NPZ."""
    from kimodo.exports.motion_io import save_kimodo_npz
    from kimodo.tools import seed_everything

    model, resolved = get_model(model_name)
    try:
        texts = [s["text"] for s in segments]
        num_frames = [int(float(s["duration"]) * model.fps) for s in segments]
        if not texts:
            raise ValueError("Prompt is empty.")
        # Segments with their own seed reseed before they generate, so changing one
        # leaves the segments before it alone. The rest continue from `seed`.
        seeds = [None if s.get("seed") is None else int(s["seed"]) for s in segments]
        constraint_lst = build_constraints(model, keyframes or [], sum(num_frames))
        seed_everything(int(seed))
        output = model(
            texts,
            num_frames,
            constraint_lst=constraint_lst,
            seeds=seeds,
            num_denoising_steps=int(diffusion_steps),
            num_samples=1,
            multi_prompt=True,
            num_transition_frames=int(num_transition_frames),
            post_processing=bool(post_processing),
            cfg_weight=[float(cfg), 2.0],  # [text, constraint]
            return_numpy=True,
        )
        single = {
            k: (v[0] if hasattr(v, "shape") and len(v.shape) > 0 and v.shape[0] == 1 else v)
            for k, v in output.items()
        }
        os.makedirs(os.path.dirname(out_npz) or ".", exist_ok=True)
        save_kimodo_npz(out_npz, single)
        return {
            "model": resolved,
            "fps": float(model.fps),
            "frames": int(single["posed_joints"].shape[0]),
            "npz_path": out_npz,
        }
    finally:
        if not keep_model_loaded:
            unload_model()


def export_bvh(model_name, npz_path, bvh_path, fps, standard_tpose=False):
    """Convert a motion NPZ written by sample() to BVH (same steps as generate.py)."""
    import numpy as np
    import torch

    from kimodo.exports.bvh import save_motion_bvh
    from kimodo.skeleton import global_rots_to_local_rots

    if model_name not in _SKELETONS:
        get_model(model_name)
    skeleton, device = _SKELETONS[model_name]
    with np.load(npz_path) as data:
        joints_pos = torch.from_numpy(np.asarray(data["posed_joints"])).to(device)
        joints_rot = torch.from_numpy(np.asarray(data["global_rot_mats"])).to(device)
    local_rot_mats = global_rots_to_local_rots(joints_rot, skeleton)
    root_positions = joints_pos[:, skeleton.root_idx, :]
    os.makedirs(os.path.dirname(bvh_path) or ".", exist_ok=True)
    save_motion_bvh(bvh_path, local_rot_mats, root_positions, skeleton=skeleton,
                    fps=float(fps), standard_tpose=bool(standard_tpose))
    return {"bvh_path": bvh_path, "frames": int(joints_pos.shape[0])}


COMMANDS = {
    "load": load,
    "sample": sample,
    "export_bvh": export_bvh,
    "unload": lambda: unload_model() or {},
    "ping": lambda: {},
}


def handle(cmd, params):
    if cmd not in COMMANDS:
        raise ValueError(f"Unknown command {cmd!r}")
    return COMMANDS[cmd](**params) or {}


def _worker_loop():
    proto = sys.stdout
    sys.stdout = sys.stderr  # Kimodo's prints must not corrupt the JSON channel.
    proto.write(json.dumps({"ok": True, "ready": True}) + "\n")
    proto.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            reply = {"ok": True, **handle(req.pop("cmd"), req)}
        except Exception as e:
            traceback.print_exc()
            reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        proto.write(json.dumps(reply) + "\n")
        proto.flush()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--repo", required=True, help="Kimodo checkout containing the 'kimodo' package")
    args = parser.parse_args()
    add_kimodo_to_path(args.repo)
    _worker_loop()
