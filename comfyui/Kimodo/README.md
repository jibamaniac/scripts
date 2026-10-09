# ComfyUI-Kimodo

ComfyUI nodes that turn a text prompt into a `.bvh` (and optionally `.fbx`) motion file with
[Kimodo](https://github.com/nv-tlabs/kimodo), NVIDIA's text-to-motion model.

| Node | Like | Does |
|---|---|---|
| Kimodo Load Model | Load Checkpoint | Picks the SOMA model and backend, loads it once |
| Kimodo Sampler | KSampler | Takes the prompt from any text node (e.g. core `Text (Multiline)`); seconds per sentence, seed, steps, cfg, transition frames, foot-skate cleanup |
| Kimodo Save BVH | Save Image | Writes `ComfyUI/output/kimodo/motion_00001.bvh` (+ optional .npz, and .fbx through Maya); `hand_pose = fist` closes both hands |

Sentences separated by `.` play back to back, each `duration` seconds long.
Sampler defaults are Kimodo's own: duration 5, steps 100, cfg 2.0, transition frames 5, post-processing on.

## 1. Install Kimodo (separately from ComfyUI)

Kimodo runs in its own Python environment; these nodes start it as a background
process, so nothing gets installed into ComfyUI's Python.

1. Follow Kimodo's install guide: <https://github.com/nv-tlabs/kimodo> (clone it, create a venv, `pip install -e .`).
   Low-VRAM Windows alternative: the community installer at <https://github.com/Aero-Ex/kimodo> (NF4 text encoder).
2. Check it works on its own:
   `python -m kimodo.scripts.generate "A person walks forward." --bvh --output test`
3. Model weights are **not** included here. Kimodo downloads them on first use from NVIDIA's
   Hugging Face pages (e.g. [nvidia/Kimodo-SOMA-RP-v1.1](https://huggingface.co/nvidia/Kimodo-SOMA-RP-v1.1))
   under NVIDIA's license, plus its LLM2Vec / Llama 3 text encoder. Log in with `huggingface-cli login`
   if a download is refused.

## 2. Install the nodes

1. Copy this `ComfyUI-Kimodo` folder into `ComfyUI/custom_nodes/`
   (or `git clone` this repo and copy/symlink `comfyui/Kimodo` as `ComfyUI-Kimodo`).
2. Copy `kimodo_config.example.json` to `kimodo_config.json` in the same folder and set:
   - `kimodo_repo`: the Kimodo checkout, i.e. the folder that contains the `kimodo` package folder.
   - `python`: the Python of the venv you installed Kimodo into
     (optional if the venv is `venv/` or `.venv/` inside the checkout or its parent folder).

   Environment variables `KIMODO_REPO` and `KIMODO_PYTHON` override the file.
3. Restart ComfyUI. The nodes are in the `Kimodo` category.
4. Drag `example_workflows/kimodo_text_to_bvh.json` onto the canvas.

If the path is wrong or missing, the Load Model node fails with a message saying what to set.

## FBX export (optional, needs Maya)

Turn on `fbx` on Kimodo Save BVH to also get `motion_00001.fbx` next to the `.bvh` (the BVH is always saved).
It runs Maya in the background with `mayapy` (no Maya window opens), so it needs Maya installed and licensed:

1. Add Maya's mayapy to `kimodo_config.json`, e.g.
   `"mayapy": "C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe"` (or set `KIMODO_MAYAPY`).
2. Save BVH imports the BVH with the Maya BVH import script from this repo
   (`maya_bvh_import.py`, a copy of [`Maya/BVH Conversion`](../../Maya/BVH%20Conversion)) using the
   same settings as its Import button, including its T-pose solve, then exports the joints with Maya's FBX plug-in
   (baked animation, Y up, centimetres).
3. To use a different version of that script, set `"maya_bvh_script"` in `kimodo_config.json` to its path.

The first conversion takes a little while because Maya has to start. Maya's output appears in the
ComfyUI console as `[Kimodo FBX]` lines, and the node shows the last lines if the export fails.
The same script still opens its window when run inside Maya.

## Backends (on Load Model)
- `kimodo_venv` (default): runs Kimodo in the configured Python and keeps the model loaded between runs.
- `in_process`: imports Kimodo into ComfyUI's own Python. Only use this if Kimodo's dependencies
  are installed there too.

## Notes
- BVH export only exists for SOMA models, so the model list is SOMA only.
- Kimodo doesn't animate fingers; they stay in a relaxed rest pose. `hand_pose = fist` on Save BVH
  writes a constant closed fist into the finger channels (everything else is unchanged).
- `standard_tpose` off gives the same rest pose as Kimodo's native BVH export (BONES-SEED rest pose).
- The motion is held as a temp .npz in `ComfyUI/temp/kimodo` until Save BVH converts it.
