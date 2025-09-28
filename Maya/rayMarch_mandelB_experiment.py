import maya.cmds as cmds
import maya.api.OpenMaya as om
import math

# --- Camera ---
if cmds.objExists("cam01"):
    cmds.delete("cam01")

camera_transform, camera_shape = cmds.camera()
camera_transform = cmds.rename(camera_transform, "cam01")

# Set position
cmds.setAttr(camera_transform + ".translateZ", 4)

# --- Plane ---
if cmds.objExists("projection_plane"):
    cmds.delete("projection_plane")

plane_transform, plane_shape = cmds.polyPlane(
    name="projection_plane",
    width=1, height=1,
    subdivisionsX=20, subdivisionsY=20
)

# Rotate plane to face camera
cmds.setAttr(plane_transform + ".rotateX", 90)
cmds.setAttr(plane_transform + ".translateZ", 3.02)

# -------- Parameters --------
MAX_STEPS = 100
MAX_DIST = 10.0       
BAILOUT = 2.0         
DIST_LIMIT = 100    
GRID_SCALE = 1.5
MB_CENTER = om.MVector(0, 0, 0)  # Mandelbulb center

# -------- Camera Setup --------
CAM_NAME = "cam01"
if cmds.objExists(CAM_NAME):
    cmds.delete(CAM_NAME)
cam_transform, cam_shape = cmds.camera()
cam_transform = cmds.rename(cam_transform, CAM_NAME)
cmds.setAttr(cam_transform + ".translateZ", 4)
cam_pos = om.MVector(cmds.xform(cam_transform, q=True, ws=True, t=True))

# -------- Plane Setup --------
PLANE_NAME = "projection_plane"
if cmds.objExists(PLANE_NAME):
    cmds.delete(PLANE_NAME)

plane_transform, plane_shape = cmds.polyPlane(
    name=PLANE_NAME,
    width=2, height=2,
    subdivisionsX=300, subdivisionsY=300
)

# Move plane in front of camera
cmds.setAttr(plane_transform + ".rotateX", 90)
cmds.setAttr(plane_transform + ".translateZ", 0.5)  # near Mandelbulb center

# -------- Mandelbulb Distance Estimator --------
def mandelbulbDE(pos, bailout_val=BAILOUT):
    z = om.MVector(pos)
    dr = 1.0
    r = 0.0
    power = 8
    for i in range(100):
        r = z.length()
        if r > bailout_val:
            break
        if r == 0:
            r = 1e-6
        theta = math.acos(max(min(z.z / r, 1.0), -1.0))
        phi = math.atan2(z.y, z.x)
        zr = pow(r, power)
        dr = pow(r, power-1.0) * power * dr + 1.0
        theta *= power
        phi *= power
        z = zr * om.MVector(math.sin(theta)*math.cos(phi),
                            math.sin(theta)*math.sin(phi),
                            math.cos(theta))
        z += pos
    if r == 0: r = 1e-6
    return 0.5 * math.log(r) * r / dr

# -------- Raymarch one vertex --------
def raymarch_vertex(vertex_pos):
    pos0 = om.MVector(vertex_pos) * GRID_SCALE
    ray_pos = om.MVector(cam_pos)                      
    ray_dir = (pos0 - cam_pos).normalize()              
    total_dist = 0.0

    for i in range(MAX_STEPS):
        d = mandelbulbDE(ray_pos - MB_CENTER)
        if d < 0.001:  # hit surface
            break
        ray_pos += ray_dir * d
        total_dist += d
        if total_dist > MAX_DIST:
            break

    if (pos0 - ray_pos).length() > DIST_LIMIT:
        return None

    return (ray_pos.x, ray_pos.y, ray_pos.z)

# -------- Apply Raymarch to Selected Vertices --------
sel = cmds.ls(selection=True, flatten=True)
if not sel:
    cmds.select(plane_transform + ".vtx[*]")
    sel = cmds.ls(selection=True, flatten=True)

# Convert selection to vertices if needed
if not any(".vtx" in s for s in sel):
    sel = cmds.polyListComponentConversion(sel, toVertex=True)
    sel = cmds.ls(sel, flatten=True)

moved = 0
for vtx in sel:
    pos = cmds.pointPosition(vtx, world=True)
    new_pos = raymarch_vertex(pos)
    if new_pos:
        cmds.xform(vtx, worldSpace=True, translation=new_pos)
        moved += 1

print("Moved vertices:", moved, "of", len(sel))
