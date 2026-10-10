# ComfyUI-Kimodo

ComfyUI nodes that turn a text prompt into a `.bvh` motion file with
[Kimodo](https://github.com/nv-tlabs/kimodo), NVIDIA's text-to-motion model.

| Node | Like | Does |
|---|---|---|
| Kimodo Load Model | Load Checkpoint | Picks the SOMA model and backend, loads it once |
| Kimodo Sampler | KSampler | Takes the prompt from any text node (e.g. core `Text (Multiline)`); seconds per sentence, seed, steps, cfg, transition frames, foot-skate cleanup |
| Kimodo Save BVH | Save Image | Writes `ComfyUI/output/kimodo/motion_00001.bvh` (+ optional .npz); `hand_pose = fist` closes both hands |
| Kimodo Segments | | One action per line, each with its own length and seed; plugs into the Sampler instead of a prompt |
| Kimodo Root Keyframe | | Pins where the hips are on the ground at a time, and optionally facing and pelvis height |
| Kimodo Jump Over Box | | Takeoff, apex and landing keyframes around a box you place |

Sentences separated by `.` play back to back, each `duration` seconds long.
Sampler defaults are Kimodo's own: duration 5, steps 100, cfg 2.0, transition frames 5, post-processing on.

## Several actions in one motion

Kimodo makes each action in order and starts the next one from the last few frames of the one before
(`transition_frames`), so a chain of actions comes out as one continuous BVH with no stitching needed.

**Kimodo Segments** takes one action per line as `prompt | seconds | seed`:

```
A person runs forward and jumps over a box | 3
A person does a forward roll and stands up | 4 | 7
```

Seconds and seed are optional (blank = the Sampler's `duration`, and no reseed). Give a line its own seed to
reroll that action without changing the ones before it. Kimodo is trained on 2-10 s actions.

**Kimodo Root Keyframe** and **Kimodo Jump Over Box** tell Kimodo where the character has to be. They chain
into each other and plug into the Sampler's `constraints` input. Positions are in centimetres, the same as the
BVH in Maya: Y is up, the character starts at 0,0 facing +Z, and heading 0 faces +Z, 90 faces +X. Times
count from the start of the whole motion.

Jump Over Box pins the hips `edge_gap` cm before the box at `takeoff_time`, over the box centre halfway
through, and `edge_gap` cm past it at `landing_time`, all facing `direction`. With `pin_apex_height` on, the
pelvis is also pinned `apex_clearance` cm above the box top at the apex. Kimodo doesn't see the box itself, so
if a foot clips it, raise `apex_clearance`, and if the jump looks forced, move the times so the run-up and
the prompt agree with them. `example_workflows/kimodo_jump_and_roll.json` has the jump then roll set up.

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
2. Double-click `setup_kimodo.bat` in that folder (Windows). It looks for Kimodo in the usual places
   (next to ComfyUI, your user folder, `X:\kimodo`, `X:\projects\kimodo`, `X:\AI\kimodo`) and asks you to pick
   the folder if it can't find it. It then finds Kimodo's venv Python, checks that it can see Kimodo and torch,
   and writes `kimodo_config.json` next to the nodes. You can also pass the folder: `setup_kimodo.bat E:\projects\kimodo`.

   To do it by hand instead, copy `kimodo_config.example.json` to `kimodo_config.json` in the same folder and set:
   - `kimodo_repo`: the Kimodo checkout, i.e. the folder that contains the `kimodo` package folder.
   - `python`: the Python of the venv you installed Kimodo into
     (optional if the venv is `venv/` or `.venv/` inside the checkout or its parent folder).

   Environment variables `KIMODO_REPO` and `KIMODO_PYTHON` override the file.
3. Restart ComfyUI. The nodes are in the `Kimodo` category.
4. Drag `example_workflows/kimodo_text_to_bvh.json` (or `kimodo_jump_and_roll.json`) onto the canvas.

If the path is wrong or missing, the Load Model node fails with a message saying what to set.

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
