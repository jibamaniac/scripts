# ComfyUI-Kimodo

ComfyUI nodes that turn a text prompt into a `.bvh` motion file (and optionally an `.fbx` through Maya) with
[Kimodo](https://github.com/nv-tlabs/kimodo), NVIDIA's text-to-motion model.

| Node | Like | Does |
|---|---|---|
| Kimodo Load Model | Load Checkpoint | Picks the SOMA model and backend, loads it once |
| Kimodo Sampler | KSampler | Takes the prompt from any text node (e.g. core `Text (Multiline)`); seconds per sentence, seed, steps, cfg, transition frames, foot-skate cleanup |
| Kimodo Save BVH | Save Image | Writes `ComfyUI/output/kimodo/motion_00001.bvh` (+ optional .npz); `hand_pose = fist` closes both hands |
| Kimodo FBX Export (T-Pose) | | Turns that BVH into `motion_00001_tpose.fbx` through Maya (optional, needs Maya) |
| Kimodo FBX Export (A-Pose) | | Same, with the arms lowered into an A-pose (29° below horizontal by default) |

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

The two Kimodo FBX Export nodes take the `bvh_path` output of Kimodo Save BVH and write an `.fbx` next to it.
They run Maya in the background with `mayapy` (no Maya window opens), so they need Maya installed and licensed.
Everything else works without Maya.

1. Add Maya's mayapy to `kimodo_config.json`, e.g.
   `"mayapy": "C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe"` (or set the `KIMODO_MAYAPY` environment variable).
   If it's missing, the FBX nodes fail with a message saying what to set.
2. Drag `example_workflows/kimodo_text_to_fbx.json` onto the canvas.

Each export does what the Maya BVH import script ([`Maya/BVH Conversion`](../../Maya/BVH%20Conversion)) does
when you press Import with "Kimodo: pose + Human_IK on import" ticked:
it imports the BVH with the script's default settings (solved rest pose, foot and thumb adjustments), puts the
skeleton in the T-pose or A-pose stance and adds the Kimodo HumanIK definition. Then it exports the
whole scene with Maya's FBX plug-in (baked animation, skeleton definitions, Y up, centimetres).

- `stance_frame` (on by default) keys the stance on frame 0, one frame before the motion, so the FBX carries it.
  Turn it off to export only the motion.
- `humanik` adds the HumanIK definition.
- `apose_angle` (A-Pose node) sets how far the arms sit below horizontal.

The nodes use a bundled copy of the import script, `maya_bvh_import.py`. To use a different version, set
`"maya_bvh_script"` in `kimodo_config.json` to its path. The script still opens its window when run inside Maya.
The first export takes a while because Maya has to start. Maya's output appears in the ComfyUI console
as `[Kimodo FBX]` lines, and the node shows the last lines if the export fails.

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
