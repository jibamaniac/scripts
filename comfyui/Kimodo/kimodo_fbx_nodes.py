"""Kimodo FBX Export nodes: turn a saved Kimodo BVH into an FBX through Maya.

Maya runs headless (mayapy) with maya_bvh_to_fbx.py, which imports the BVH with
the Maya BVH import script, poses the skeleton in a T-pose or A-pose, adds the
Kimodo HumanIK definition and exports FBX. Needs Maya installed and "mayapy" in
kimodo_config.json (see README.md).
"""
import json
import os
import subprocess

import folder_paths

from .kimodo_nodes import CONFIG_PATH

HERE = os.path.dirname(os.path.abspath(__file__))
FBX_SCRIPT = os.path.join(HERE, "maya_bvh_to_fbx.py")


def maya_paths():
    """Return (mayapy, import script).

    mayapy comes from the KIMODO_MAYAPY env var or "mayapy" in kimodo_config.json.
    The import script defaults to the bundled maya_bvh_import.py; "maya_bvh_script"
    in the config can point at another copy.
    """
    cfg = {}
    if os.path.isfile(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    mayapy = os.environ.get("KIMODO_MAYAPY") or cfg.get("mayapy")
    if not mayapy:
        raise RuntimeError(
            "Kimodo FBX Export needs Maya. Set \"mayapy\" in kimodo_config.json "
            f"({CONFIG_PATH}) to Maya's mayapy.exe, e.g. "
            "C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe, or set KIMODO_MAYAPY.")
    mayapy = os.path.abspath(os.path.expanduser(mayapy))
    if not os.path.isfile(mayapy):
        raise RuntimeError(f"mayapy not found at {mayapy}. Check \"mayapy\" in kimodo_config.json.")
    script = cfg.get("maya_bvh_script") or os.path.join(HERE, "maya_bvh_import.py")
    script = os.path.abspath(os.path.expanduser(script))
    if not os.path.isfile(script):
        raise RuntimeError(f"Maya BVH import script not found at {script}. Check \"maya_bvh_script\".")
    return mayapy, script


def _fbx_path_for(bvh_path, pose):
    """<bvh name>_tpose.fbx next to the BVH, or in output/kimodo if the BVH is elsewhere."""
    out_dir = os.path.abspath(folder_paths.get_output_directory())
    folder, name = os.path.split(os.path.abspath(bvh_path))
    if os.path.commonpath([out_dir, folder]) != out_dir:
        folder = os.path.join(out_dir, "kimodo")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{os.path.splitext(name)[0]}_{pose}.fbx")


def bvh_to_fbx(bvh_path, pose, apose_angle=29.0, humanik=True, stance_frame=True):
    """Run the mayapy conversion. Maya's output goes to ComfyUI's console."""
    if not os.path.isfile(bvh_path):
        raise FileNotFoundError(f"BVH not found: {bvh_path}")
    mayapy, importer = maya_paths()
    fbx_path = _fbx_path_for(bvh_path, pose)
    if os.path.isfile(fbx_path):
        os.remove(fbx_path)
    cmd = [mayapy, FBX_SCRIPT, "--bvh", bvh_path, "--fbx", fbx_path, "--pose", pose,
           "--apose-angle", str(apose_angle), "--importer", importer]
    if not humanik:
        cmd.append("--no-humanik")
    if not stance_frame:
        cmd.append("--no-stance-frame")
    env = dict(os.environ)
    for var in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(var, None)
    env["PYTHONUNBUFFERED"] = "1"
    print(f"[Kimodo] Converting {os.path.basename(bvh_path)} to FBX ({pose}) with {mayapy}", flush=True)
    proc = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    log = proc.stdout.decode("utf-8", errors="replace").splitlines()
    for line in log:
        print(f"[Kimodo FBX] {line}", flush=True)
    if proc.returncode != 0 or not os.path.isfile(fbx_path):
        raise RuntimeError(f"Maya FBX export failed (exit code {proc.returncode}):\n" + "\n".join(log[-25:]))
    print(f"[Kimodo] Saved {fbx_path}", flush=True)
    return fbx_path


_COMMON_INPUTS = {
    "bvh_path": ("STRING", {"forceInput": True, "tooltip": "The bvh_path output of Kimodo Save BVH."}),
    "humanik": ("BOOLEAN", {"default": True, "tooltip": "Add the Kimodo HumanIK character definition."}),
    "stance_frame": ("BOOLEAN", {
        "default": True,
        "tooltip": "Key the stance on the frame before the motion (frame 0), so the FBX carries it.",
    }),
}


class _KimodoFBXExport:
    CATEGORY = "Kimodo"
    FUNCTION = "export"
    OUTPUT_NODE = True
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("fbx_path",)
    POSE = None

    def export(self, bvh_path, humanik, stance_frame, apose_angle=29.0):
        fbx_path = bvh_to_fbx(bvh_path, self.POSE, apose_angle, humanik, stance_frame)
        return {"ui": {"text": [os.path.basename(fbx_path)]}, "result": (fbx_path,)}


class KimodoFBXExportTPose(_KimodoFBXExport):
    POSE = "tpose"

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": dict(_COMMON_INPUTS)}


class KimodoFBXExportAPose(_KimodoFBXExport):
    POSE = "apose"

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            **_COMMON_INPUTS,
            "apose_angle": ("FLOAT", {"default": 29.0, "min": 0.0, "max": 90.0, "step": 0.5,
                                      "tooltip": "Degrees below horizontal for the arms."}),
        }}


NODE_CLASS_MAPPINGS = {
    "KimodoFBXExportTPose": KimodoFBXExportTPose,
    "KimodoFBXExportAPose": KimodoFBXExportAPose,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "KimodoFBXExportTPose": "Kimodo FBX Export (T-Pose)",
    "KimodoFBXExportAPose": "Kimodo FBX Export (A-Pose)",
}
