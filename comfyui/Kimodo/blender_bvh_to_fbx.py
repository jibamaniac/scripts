"""Convert a BVH file to FBX inside Blender (run in the background by the node).

    blender --background --factory-startup --python blender_bvh_to_fbx.py -- in.bvh out.fbx centimeters

Exports a skeleton-only FBX with the animation baked on every frame.
"centimeters" keeps Kimodo's numbers as they are and marks the file as
centimetres (Maya's default unit); "meters" divides by 100 and marks the
file as metres. Either way nothing is scaled at the node level, so the
joint translations in the FBX are plain values in the declared unit.
"""
import sys

import bpy


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    bvh_path, fbx_path, units = argv[0], argv[1], argv[2]
    if units not in ("centimeters", "meters"):
        raise SystemExit(f"Unknown units {units!r}")

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    # One Blender unit = 1 cm or 1 m; the FBX exporter writes that as the file unit.
    scene.unit_settings.scale_length = 0.01 if units == "centimeters" else 1.0

    bpy.ops.import_anim.bvh(
        filepath=bvh_path,
        global_scale=1.0 if units == "centimeters" else 0.01,  # Kimodo BVH is in centimetres
        rotate_mode="NATIVE",
        axis_forward="-Z",
        axis_up="Y",
        update_scene_fps=True,
        update_scene_duration=True,
        use_cyclic=False,
    )
    armature = next(o for o in scene.objects if o.type == "ARMATURE")
    action = armature.animation_data.action
    scene.frame_start, scene.frame_end = (int(v) for v in action.frame_range)

    bpy.ops.export_scene.fbx(
        filepath=fbx_path,
        use_selection=False,
        object_types={"ARMATURE"},
        apply_unit_scale=True,
        apply_scale_options="FBX_SCALE_UNITS",
        global_scale=1.0,
        axis_forward="-Z",
        axis_up="Y",
        add_leaf_bones=False,
        armature_nodetype="NULL",
        bake_anim=True,
        bake_anim_use_all_bones=True,
        bake_anim_use_nla_strips=False,
        bake_anim_use_all_actions=False,
        bake_anim_force_startend_keying=True,
        bake_anim_step=1.0,
        bake_anim_simplify_factor=0.0,
    )
    print(f"[Kimodo] Wrote {fbx_path}")


main()
