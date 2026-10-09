"""ComfyUI nodes for Kimodo text-to-motion, laid out like a txt2img graph:

  Kimodo Load Model --KIMODO_MODEL--> Kimodo Sampler --KIMODO_MOTION--> Kimodo Save BVH
  any text node     --STRING--------> (prompt)

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
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid

import folder_paths

from . import kimodo_runner

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
                "prompt": ("STRING", {
                    "forceInput": True,
                    "tooltip": "Text from any text node. Sentences separated by '.' play back to back.",
                }),
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
            }
        }

    def sample(self, model, prompt, duration, seed, steps, cfg, transition_frames, post_processing):
        segments = [{"text": t.strip() + ".", "duration": float(duration)}
                    for t in prompt.split(".") if t.strip()]
        if not segments:
            raise ValueError("Kimodo prompt is empty.")
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
        )
        return ({**result, "model_name": model["model_name"], "backend": model["backend"],
                 "texts": [s["text"] for s in segments]},)


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
            }
        }

    def save(self, motion, filename_prefix, standard_tpose, save_npz):
        stem = _next_output_stem(filename_prefix)
        result = _call(
            motion["backend"], "export_bvh",
            model_name=motion["model_name"],
            npz_path=motion["npz_path"],
            bvh_path=stem + ".bvh",
            fps=motion["fps"],
            standard_tpose=standard_tpose,
        )
        if save_npz:
            shutil.copyfile(motion["npz_path"], stem + ".npz")
        bvh_path = result["bvh_path"]
        summary = f"{os.path.basename(bvh_path)}: {result['frames']} frames @ {motion['fps']:g} fps ({motion['model']})"
        print(f"[Kimodo] Saved {bvh_path}", flush=True)
        return {"ui": {"text": [summary]}, "result": (bvh_path,)}


NODE_CLASS_MAPPINGS = {
    "KimodoLoadModel": KimodoLoadModel,
    "KimodoSampler": KimodoSampler,
    "KimodoSaveBVH": KimodoSaveBVH,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "KimodoLoadModel": "Kimodo Load Model",
    "KimodoSampler": "Kimodo Sampler",
    "KimodoSaveBVH": "Kimodo Save BVH",
}
