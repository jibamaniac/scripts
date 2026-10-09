"""Convert a Kimodo BVH to FBX with Maya running headless (mayapy, no Maya window).

Does what the Import button of the Maya BVH import script does with
"Kimodo: pose + Human_IK on import" ticked: imports the BVH with its default
settings (solved rest pose and joint extras included), puts the skeleton in its
T-pose or A-pose stance and adds the Kimodo HumanIK definition. Then it exports
the scene with Maya's FBX plug-in.

    mayapy maya_bvh_to_fbx.py --bvh in.bvh --fbx out.fbx --pose apose [--apose-angle 29]
        [--no-humanik] [--no-stance-frame] [--importer script]

The Kimodo FBX Export nodes in ComfyUI run this.
"""
import argparse
import os
import runpy
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))


def convert(bvh_path, fbx_path, importer_path, pose, apose_angle, humanik, stance_frame):
    import maya.cmds as cmds
    import maya.mel as mel

    if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
        cmds.loadPlugin("fbxmaya")
    cmds.file(new=True, force=True)

    # In batch mode the import script skips its UI and only defines its functions.
    script = runpy.run_path(importer_path, run_name="maya_bvh_import")
    roots = script["MayaBVHImporter"](
        path=bvh_path,
        scale=1.0,
        start_frame=1,
        set_fps=True,
        set_range=True,
        end_sites=True,
        euler_filter=True,
        z_up_to_y=False,
        start_at_origin=False,
        rest_pose_frame=None,
        tpose=True,
    ).execute()
    if not roots:
        raise RuntimeError(f"The BVH importer built no skeleton from {bvh_path}")
    root = roots[0]

    start = int(cmds.playbackOptions(query=True, minTime=True))
    end = int(cmds.playbackOptions(query=True, maxTime=True))
    if stance_frame:
        # The stance gets its own key one frame before the motion starts.
        start -= 1
        cmds.currentTime(start)

    if script["setup_kimodo"](root, pose, apose_angle, humanik) is None:
        raise RuntimeError("Kimodo stance / HumanIK setup failed; see the warnings above.")

    if stance_frame:
        joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
        joints.append(cmds.ls(root, long=True)[0])
        cmds.setKeyframe(joints, attribute="rotate", time=start)
        cmds.playbackOptions(minTime=start, animationStartTime=start)

    mel.eval("FBXResetExport")
    mel.eval("FBXExportSkeletonDefinitions -v true")
    mel.eval("FBXExportBakeComplexAnimation -v true")
    mel.eval(f"FBXExportBakeComplexStart -v {start}")
    mel.eval(f"FBXExportBakeComplexEnd -v {end}")
    mel.eval("FBXExportUpAxis y")
    mel.eval("FBXExportInAscii -v false")
    fbx_mel = os.path.abspath(fbx_path).replace("\\", "/")
    mel.eval(f'FBXExport -f "{fbx_mel}"')
    if not os.path.isfile(fbx_path):
        raise RuntimeError(f"Maya's FBX export did not write {fbx_path}")
    print(f"Wrote {fbx_path} ({pose}, frames {start}-{end})", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bvh", required=True)
    parser.add_argument("--fbx", required=True)
    parser.add_argument("--pose", choices=("tpose", "apose"), default="tpose")
    parser.add_argument("--apose-angle", type=float, default=29.0,
                        help="Degrees below horizontal for the arms in the A-pose.")
    parser.add_argument("--no-humanik", action="store_true", help="Skip the HumanIK definition.")
    parser.add_argument("--no-stance-frame", action="store_true",
                        help="Don't key the stance on the frame before the motion.")
    parser.add_argument("--importer", default=os.path.join(HERE, "maya_bvh_import.py"),
                        help="Maya BVH import script to use (default: the bundled copy).")
    args = parser.parse_args()

    import maya.standalone
    maya.standalone.initialize(name="python")
    code = 0
    try:
        convert(args.bvh, args.fbx, args.importer, args.pose, args.apose_angle,
                not args.no_humanik, not args.no_stance_frame)
    except Exception:
        traceback.print_exc()
        code = 1
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            maya.standalone.uninitialize()
        except Exception:
            pass
    # Skip Python's own shutdown: mayapy can hang or crash there after uninitialize.
    os._exit(code)


if __name__ == "__main__":
    main()
