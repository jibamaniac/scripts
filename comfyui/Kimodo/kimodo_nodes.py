"""ComfyUI nodes for Kimodo text-to-motion, laid out like a txt2img graph:

  Kimodo Load Model --KIMODO_MODEL--> Kimodo Sampler --KIMODO_MOTION--> Kimodo Save BVH
  any text node     --STRING--------> (prompt)
  Kimodo Segments   --KIMODO_SEGMENTS--> (segments, instead of prompt)
  Kimodo Root Keyframe / Jump Over Box --KIMODO_CONSTRAINTS--> (constraints)

Two backends (picked on the loader):
  kimodo_venv  (default) runs Kimodo in a persistent worker process that uses
               Kimodo's own venv, so nothing has to be installed into ComfyUI's
               Python. The model stays loaded in that worker between runs.
  in_process   imports Kimodo straight into ComfyUI's Python (sys.path append).
               Only works if Kimodo's dependencies are installed there too.

Where Kimodo lives is read from the KIMODO_REPO / KIMODO_PYTHON environment
variables, or from kimodo_config.json next to this file (see README.md).
"""
import collections
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid

import folder_paths

from . import kimodo_runner
from .hand_pose import HAND_POSES, apply_hand_pose

RUNNER_PATH = os.path.abspath(kimodo_runner.__file__)
CONFIG_PATH = os.path.join(os.path.dirname(RUNNER_PATH), "kimodo_config.json")


def _venv_python(folder):
    for rel in (("venv", "Scripts", "python.exe"), (".venv", "Scripts", "python.exe"),
                ("venv", "bin", "python"), (".venv", "bin", "python")):
        path = os.path.join(folder, *rel)
        if os.path.isfile(path):
            return path
    return None


def kimodo_paths():
    """Return (repo, python): the Kimodo checkout (the folder that contains the
    `kimodo` package) and the Python interpreter that has Kimodo installed.

    Env vars KIMODO_REPO / KIMODO_PYTHON win over kimodo_config.json. If no
    python is given, a venv/ or .venv/ in the repo or its parent folder is used.
    """
    cfg = {}
    if os.path.isfile(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    repo = os.environ.get("KIMODO_REPO") or cfg.get("kimodo_repo")
    python = os.environ.get("KIMODO_PYTHON") or cfg.get("python")
    if not repo:
        raise RuntimeError(
            "Kimodo location not set. Copy kimodo_config.example.json to kimodo_config.json in "
            f"{os.path.dirname(CONFIG_PATH)} and set kimodo_repo (and python), "
            "or set the KIMODO_REPO / KIMODO_PYTHON environment variables.")
    repo = os.path.abspath(os.path.expanduser(repo))
    if not os.path.isfile(os.path.join(repo, "kimodo", "__init__.py")):
        raise RuntimeError(f"kimodo_repo must be the Kimodo checkout containing the 'kimodo' package folder; "
                           f"no kimodo/__init__.py in {repo}")
    python = python or _venv_python(repo) or _venv_python(os.path.dirname(repo))
    if not python or not os.path.isfile(python):
        raise RuntimeError(f"Kimodo Python not found ({python!r}). Set \"python\" in kimodo_config.json "
                           "to the python.exe of the venv where Kimodo is installed.")
    return repo, python

# BVH export only exists for SOMA skeletons (see kimodo/scripts/generate.py).
SOMA_MODELS = [
    "kimodo-soma-rp",
    "kimodo-soma-seed",
    "kimodo-soma-rp-v1",
    "kimodo-soma-rp-v1.1",
    "kimodo-soma-seed-v1",
    "kimodo-soma-seed-v1.1",
]


class _KimodoWorker:
    """Long-lived Kimodo process speaking JSON lines (see kimodo_runner.py)."""

    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()
        self.log_tail = collections.deque(maxlen=60)

    def _pump_stderr(self, stream):
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip()
            self.log_tail.append(line)
            print(f"[Kimodo] {line}", flush=True)

    def _start(self):
        repo, python = kimodo_paths()
        env = dict(os.environ)
        for var in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
            env.pop(var, None)
        env["PATH"] = os.path.dirname(python) + os.pathsep + env.get("PATH", "")
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        self.log_tail.clear()
        self.proc = subprocess.Popen(
            [python, "-u", RUNNER_PATH, "--worker", "--repo", repo],
            cwd=repo,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self._pump_stderr, args=(self.proc.stderr,), daemon=True).start()
        self._read_reply()  # wait for {"ready": true}

    def _read_reply(self):
        line = self.proc.stdout.readline()
        if not line:
            self.proc = None
            raise RuntimeError("Kimodo worker exited unexpectedly.\n" + "\n".join(self.log_tail))
        return json.loads(line)

    def request(self, payload):
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            self.proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
            reply = self._read_reply()
        if not reply.get("ok"):
            raise RuntimeError(f"Kimodo failed: {reply.get('error')}\n" + "\n".join(list(self.log_tail)[-20:]))
        return reply

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
        self.proc = None


_WORKER = _KimodoWorker()


def _next_output_stem(filename_prefix):
    """<ComfyUI output>/<prefix>_00001, numbered past any existing .bvh files."""
    out_dir = os.path.abspath(folder_paths.get_output_directory())
    stem = os.path.abspath(os.path.join(out_dir, filename_prefix.replace("\\", "/")))
    if os.path.commonpath([out_dir, stem]) != out_dir:
        raise ValueError("filename_prefix must stay inside ComfyUI's output folder.")
    folder, name = os.path.split(stem)
    os.makedirs(folder, exist_ok=True)
    pattern = re.compile(re.escape(name) + r"_(\d+)\.bvh$")
    used = [int(m.group(1)) for f in os.listdir(folder) if (m := pattern.match(f))]
    return os.path.join(folder, f"{name}_{max(used, default=0) + 1:05}")


def _call(backend, cmd, **params):
    """Run a kimodo_runner command on the chosen backend."""
    if backend == "in_process":
        _WORKER.stop()  # don't hold the model twice on the GPU
        kimodo_runner.add_kimodo_to_path(kimodo_paths()[0])
        return kimodo_runner.handle(cmd, params)
    if kimodo_runner._CACHE["model"] is not None:
        kimodo_runner.unload_model()
    return _WORKER.request({"cmd": cmd, **params})


class KimodoLoadModel:
    CATEGORY = "Kimodo"
    FUNCTION = "load"
    RETURN_TYPES = ("KIMODO_MODEL",)
    RETURN_NAMES = ("model",)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model_name": (SOMA_MODELS, {"default": "kimodo-soma-rp"}),
                "backend": (["kimodo_venv", "in_process"], {
                    "default": "kimodo_venv",
                    "tooltip": "kimodo_venv runs Kimodo in its own venv; in_process needs its deps in ComfyUI's Python.",
                }),
                "keep_model_loaded": ("BOOLEAN", {"default": True}),
            }
        }

    def load(self, model_name, backend, keep_model_loaded):
        info = _call(backend, "load", model_name=model_name)
        return ({"model_name": model_name, "backend": backend, "keep_model_loaded": keep_model_loaded,
                 "resolved": info["model"], "fps": info["fps"]},)


class KimodoSampler:
    CATEGORY = "Kimodo"
    FUNCTION = "sample"
    RETURN_TYPES = ("KIMODO_MOTION",)
    RETURN_NAMES = ("motion",)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("KIMODO_MODEL",),
                # Ranges match the Kimodo demo UI (kimodo/demo/config.py, ui.py): the
                # model is trained on 2-10 s clips and needs real denoising steps.
                "duration": ("FLOAT", {
                    "default": 5.0, "min": 2.0, "max": 10.0, "step": 0.5,
                    "tooltip": "Seconds per sentence (Kimodo supports 2-10).",
                }),
                "seed": ("INT", {"default": 42, "min": 0, "max": 2**31 - 1}),
                "steps": ("INT", {"default": 100, "min": 2, "max": 1000,
                                  "tooltip": "Denoising steps. Kimodo's default is 100; very low values give noisy motion."}),
                "cfg": ("FLOAT", {"default": 2.0, "min": 0.0, "max": 5.0, "step": 0.1,
                                  "tooltip": "Text guidance weight (Kimodo default 2.0)."}),
                "transition_frames": ("INT", {"default": 5, "min": 1, "max": 10,
                                              "tooltip": "Blend frames between sentences (Kimodo default 5)."}),
                "post_processing": ("BOOLEAN", {"default": True, "tooltip": "Kimodo's foot-skate cleanup."}),
            },
            "optional": {
                "prompt": ("STRING", {
                    "forceInput": True,
                    "tooltip": "Text from any text node. Sentences separated by '.' play back to back.",
                }),
                "segments": ("KIMODO_SEGMENTS", {
                    "tooltip": "From Kimodo Segments: one action per line with its own length and seed. "
                               "Used instead of prompt.",
                }),
                "constraints": ("KIMODO_CONSTRAINTS", {
                    "tooltip": "From Kimodo Root Keyframe / Kimodo Jump Over Box.",
                }),
            },
        }

    def sample(self, model, duration, seed, steps, cfg, transition_frames, post_processing,
               prompt=None, segments=None, constraints=None):
        if segments:
            segments = [{**s, "duration": s["duration"] if s.get("duration") is not None else float(duration)}
                        for s in segments]
        else:
            segments = [{"text": t.strip() + ".", "duration": float(duration)}
                        for t in (prompt or "").split(".") if t.strip()]
        if not segments:
            raise ValueError("Kimodo prompt is empty: connect a prompt or Kimodo Segments.")
        if steps < 50:
            print(f"[Kimodo] Warning: steps={steps} is far below Kimodo's default of 100; expect jittery motion.",
                  flush=True)
        tmp_dir = os.path.join(folder_paths.get_temp_directory(), "kimodo")
        os.makedirs(tmp_dir, exist_ok=True)
        result = _call(
            model["backend"], "sample",
            model_name=model["model_name"],
            segments=segments,
            out_npz=os.path.join(tmp_dir, f"motion_{uuid.uuid4().hex}.npz"),
            seed=seed,
            diffusion_steps=steps,
            cfg=cfg,
            num_transition_frames=transition_frames,
            post_processing=post_processing,
            keep_model_loaded=model["keep_model_loaded"],
            keyframes=constraints or [],
        )
        return ({**result, "model_name": model["model_name"], "backend": model["backend"],
                 "texts": [s["text"] for s in segments]},)


def parse_segments(text):
    """One action per line: `prompt | seconds | seed`. Seconds and seed are optional
    (blank or missing = the Sampler's duration / no reseed). Lines starting with # are skipped."""
    segments = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) > 3 or not parts[0]:
            raise ValueError(f"Kimodo Segments line {n}: expected 'prompt | seconds | seed', got {line!r}")
        prompt = parts[0] if parts[0].endswith(".") else parts[0] + "."
        try:
            duration = float(parts[1]) if len(parts) > 1 and parts[1] else None
            seed = int(parts[2]) if len(parts) > 2 and parts[2] else None
        except ValueError:
            raise ValueError(f"Kimodo Segments line {n}: seconds must be a number and seed a whole number: {line!r}")
        if duration is not None and not 0.5 <= duration <= 10.0:
            raise ValueError(f"Kimodo Segments line {n}: {duration} s is outside 0.5-10 s.")
        if duration is not None and duration < 2.0:
            print(f"[Kimodo] Line {n} is {duration} s; Kimodo is trained on 2-10 s clips, so short ones may look rushed.",
                  flush=True)
        segments.append({"text": prompt, "duration": duration, "seed": seed})
    if not segments:
        raise ValueError("Kimodo Segments is empty.")
    return segments


class KimodoSegments:
    CATEGORY = "Kimodo"
    FUNCTION = "build"
    RETURN_TYPES = ("KIMODO_SEGMENTS",)
    RETURN_NAMES = ("segments",)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": ("STRING", {
                    "multiline": True,
                    "default": "A person runs forward and jumps over a box | 3\n"
                               "A person does a forward roll and stands up | 4 | 7",
                    "tooltip": "One action per line: prompt | seconds | seed. Seconds and seed are optional. "
                               "Give a line its own seed to reroll it without changing the lines before it.",
                }),
            }
        }

    def build(self, text):
        return (parse_segments(text),)


def _chain(constraints, *keyframes):
    return (list(constraints or []) + list(keyframes),)


# Positions on these nodes are in centimetres, like the exported BVH (and Maya);
# Kimodo works in metres. Y is up and the character starts at the origin facing +Z;
# heading 0 faces +Z and 90 faces +X.
class KimodoRootKeyframe:
    CATEGORY = "Kimodo"
    FUNCTION = "build"
    RETURN_TYPES = ("KIMODO_CONSTRAINTS",)
    RETURN_NAMES = ("constraints",)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "time": ("FLOAT", {"default": 2.0, "min": 0.0, "max": 600.0, "step": 0.05,
                                   "tooltip": "Seconds from the start of the whole motion."}),
                "x": ("FLOAT", {"default": 0.0, "min": -100000.0, "max": 100000.0, "step": 1.0,
                                "tooltip": "Hips position on the ground, cm."}),
                "z": ("FLOAT", {"default": 200.0, "min": -100000.0, "max": 100000.0, "step": 1.0,
                                "tooltip": "Hips position on the ground, cm. The character starts at 0,0 facing +Z."}),
                "set_heading": ("BOOLEAN", {"default": False}),
                "heading": ("FLOAT", {"default": 0.0, "min": -360.0, "max": 360.0, "step": 1.0,
                                      "tooltip": "Facing direction in degrees: 0 = +Z, 90 = +X."}),
                "set_hip_height": ("BOOLEAN", {"default": False}),
                "hip_height": ("FLOAT", {"default": 95.0, "min": 0.0, "max": 500.0, "step": 1.0,
                                         "tooltip": "Pelvis height above the floor, cm (standing is about 95)."}),
            },
            "optional": {"constraints": ("KIMODO_CONSTRAINTS",)},
        }

    def build(self, time, x, z, set_heading, heading, set_hip_height, hip_height, constraints=None):
        return _chain(constraints, {
            "time": float(time), "x": x / 100.0, "z": z / 100.0,
            "heading_deg": float(heading) if set_heading else None,
            "hip_height": hip_height / 100.0 if set_hip_height else None,
        })


class KimodoJumpOverBox:
    """Takeoff, apex and landing root keyframes for jumping over a box."""

    CATEGORY = "Kimodo"
    FUNCTION = "build"
    RETURN_TYPES = ("KIMODO_CONSTRAINTS",)
    RETURN_NAMES = ("constraints",)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "box_x": ("FLOAT", {"default": 0.0, "min": -100000.0, "max": 100000.0, "step": 1.0,
                                    "tooltip": "Box centre on the ground, cm."}),
                "box_z": ("FLOAT", {"default": 300.0, "min": -100000.0, "max": 100000.0, "step": 1.0,
                                    "tooltip": "Box centre on the ground, cm. The character starts at 0,0 facing +Z."}),
                "box_height": ("FLOAT", {"default": 50.0, "min": 0.0, "max": 300.0, "step": 1.0}),
                "box_depth": ("FLOAT", {"default": 50.0, "min": 0.0, "max": 1000.0, "step": 1.0,
                                        "tooltip": "Box size along the jump direction, cm."}),
                "direction": ("FLOAT", {"default": 0.0, "min": -360.0, "max": 360.0, "step": 1.0,
                                        "tooltip": "Jump direction in degrees: 0 = +Z, 90 = +X."}),
                "takeoff_time": ("FLOAT", {"default": 1.8, "min": 0.0, "max": 600.0, "step": 0.05,
                                           "tooltip": "Seconds from the start of the whole motion."}),
                "landing_time": ("FLOAT", {"default": 2.5, "min": 0.0, "max": 600.0, "step": 0.05}),
                "edge_gap": ("FLOAT", {"default": 50.0, "min": 0.0, "max": 500.0, "step": 1.0,
                                       "tooltip": "Hips distance from the box edge at takeoff and landing, cm."}),
                "pin_apex_height": ("BOOLEAN", {"default": True,
                                                "tooltip": "Pin the pelvis height over the box centre halfway through the jump."}),
                "apex_clearance": ("FLOAT", {"default": 60.0, "min": 0.0, "max": 300.0, "step": 1.0,
                                             "tooltip": "Pelvis height above the box top at the apex, cm."}),
            },
            "optional": {"constraints": ("KIMODO_CONSTRAINTS",)},
        }

    def build(self, box_x, box_z, box_height, box_depth, direction, takeoff_time, landing_time,
              edge_gap, pin_apex_height, apex_clearance, constraints=None):
        if landing_time <= takeoff_time:
            raise ValueError("Kimodo Jump Over Box: landing_time must be after takeoff_time.")
        a = math.radians(direction)
        dx, dz = math.sin(a), math.cos(a)
        reach = box_depth / 2.0 + edge_gap

        def key(t, offset, hip_height=None):
            return {"time": float(t), "x": (box_x + dx * offset) / 100.0, "z": (box_z + dz * offset) / 100.0,
                    "heading_deg": float(direction),
                    "hip_height": None if hip_height is None else hip_height / 100.0}

        apex = key((takeoff_time + landing_time) / 2.0, 0.0,
                   box_height + apex_clearance if pin_apex_height else None)
        return _chain(constraints, key(takeoff_time, -reach), apex, key(landing_time, reach))


class KimodoSaveBVH:
    CATEGORY = "Kimodo"
    FUNCTION = "save"
    OUTPUT_NODE = True
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("bvh_path",)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "motion": ("KIMODO_MOTION",),
                "filename_prefix": ("STRING", {"default": "kimodo/motion"}),
                "standard_tpose": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Rest pose = standard T-pose instead of the BONES-SEED rest pose.",
                }),
                "save_npz": ("BOOLEAN", {"default": False, "tooltip": "Also keep the Kimodo .npz next to the .bvh."}),
                "hand_pose": (HAND_POSES, {
                    "default": "none",
                    "tooltip": "Kimodo doesn't animate fingers. 'fist' holds both hands closed for the whole clip.",
                }),
            }
        }

    def save(self, motion, filename_prefix, standard_tpose, save_npz, hand_pose="none"):
        stem = _next_output_stem(filename_prefix)
        result = _call(
            motion["backend"], "export_bvh",
            model_name=motion["model_name"],
            npz_path=motion["npz_path"],
            bvh_path=stem + ".bvh",
            fps=motion["fps"],
            standard_tpose=standard_tpose,
        )
        apply_hand_pose(result["bvh_path"], hand_pose)
        if save_npz:
            shutil.copyfile(motion["npz_path"], stem + ".npz")
        bvh_path = result["bvh_path"]
        summary = f"{os.path.basename(bvh_path)}: {result['frames']} frames @ {motion['fps']:g} fps ({motion['model']})"
        print(f"[Kimodo] Saved {bvh_path}", flush=True)
        return {"ui": {"text": [summary]}, "result": (bvh_path,)}


NODE_CLASS_MAPPINGS = {
    "KimodoLoadModel": KimodoLoadModel,
    "KimodoSampler": KimodoSampler,
    "KimodoSegments": KimodoSegments,
    "KimodoRootKeyframe": KimodoRootKeyframe,
    "KimodoJumpOverBox": KimodoJumpOverBox,
    "KimodoSaveBVH": KimodoSaveBVH,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "KimodoLoadModel": "Kimodo Load Model",
    "KimodoSampler": "Kimodo Sampler",
    "KimodoSegments": "Kimodo Segments",
    "KimodoRootKeyframe": "Kimodo Root Keyframe",
    "KimodoJumpOverBox": "Kimodo Jump Over Box",
    "KimodoSaveBVH": "Kimodo Save BVH",
}
