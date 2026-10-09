"""Convert a BVH file to FBX with Maya running headless (mayapy, no Maya window).

The skeleton and animation are built by Tim's Maya BVH importer
(maya_bvh_import.py, a copy of "Maya/BVH Conversion" in jibamaniac/scripts) with
the same settings its Import button uses, T-pose solve included, then the
joints are exported with Maya's FBX plug-in.

    mayapy maya_bvh_to_fbx.py --bvh in.bvh --fbx out.fbx [--importer script]

Kimodo Save BVH runs this when its fbx option is on.
"""
import argparse
import os
import runpy
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))


def convert(bvh_path, fbx_path, importer_path):
    import maya.cmds as cmds
    import maya.mel as mel

    if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
        cmds.loadPlugin("fbxmaya")
    cmds.file(new=True, force=True)

    # In batch mode the importer script skips its UI and only defines the importer.
    importer = runpy.run_path(importer_path, run_name="maya_bvh_import")
    roots = importer["MayaBVHImporter"](
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

    start = int(cmds.playbackOptions(query=True, minTime=True))
    end = int(cmds.playbackOptions(query=True, maxTime=True))
    cmds.select(roots, replace=True)
    mel.eval("FBXResetExport")
    mel.eval("FBXExportBakeComplexAnimation -v true")
    mel.eval(f"FBXExportBakeComplexStart -v {start}")
    mel.eval(f"FBXExportBakeComplexEnd -v {end}")
    mel.eval("FBXExportUpAxis y")
    mel.eval("FBXExportInAscii -v false")
    fbx_mel = os.path.abspath(fbx_path).replace("\\", "/")
    mel.eval(f'FBXExport -f "{fbx_mel}" -s')
    if not os.path.isfile(fbx_path):
        raise RuntimeError(f"Maya's FBX export did not write {fbx_path}")
    print(f"Wrote {fbx_path} (frames {start}-{end})", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bvh", required=True)
    parser.add_argument("--fbx", required=True)
    parser.add_argument("--importer", default=os.path.join(HERE, "maya_bvh_import.py"),
                        help="Maya BVH import script to use (default: the bundled copy).")
    args = parser.parse_args()

    import maya.standalone
    maya.standalone.initialize(name="python")
    code = 0
    try:
        convert(args.bvh, args.fbx, args.importer)
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
