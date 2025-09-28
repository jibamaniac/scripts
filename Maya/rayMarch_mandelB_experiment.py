import maya.cmds as cmds
import maya.api.OpenMaya as om
import math

# Camera
if cmds.objExists("cam01"):
    cmds.delete("cam01")

camera_transform, camera_shape = cmds.camera()
camera_transform = cmds.rename(camera_transform, "cam01")

# Set position
cmds.setAttr(camera_transform + ".translateZ", 4)

# Plane
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

# Parameters
max_steps = 100
max_dist = 10.0       
bailout = 2.0         
dist_limit = 100    
grid_scale = 1.5
mb_center = om.MVector(0, 0, 0)  # Mandelbulb center

# Camera Setup
cam_name = "cam01"
if cmds.objExists(cam_name):
    cmds.delete(cam_name)
cam_transform, cam_shape = cmds.camera()
cam_transform = cmds.rename(cam_transform, cam_name)
cmds.setAttr(cam_transform + ".translateZ", 4)
cam_pos = om.MVector(cmds.xform(cam_transform, q=True, ws=True, t=True))

# Plane Setup
plane_name = "projection_plane"
if cmds.objExists(plane_name):
    cmds.delete(plane_name)

plane_transform, plane_shape = cmds.polyPlane(
    name=plane_name,
    width=2, height=2,
    subdivisionsX=300, subdivisionsY=300
)

# Move plane in front of camera
cmds.setAttr(plane_transform + ".rotateX", 90)
cmds.setAttr(plane_transform + ".translateZ", 0.5)  # near Mandelbulb center

# Mandelbulb Distance
def mandelbulbDE(pos, bailout_val=bailout):
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

# Raymarch setup
def raymarch_vertex(vertex_pos):
    pos0 = om.MVector(vertex_pos) * grid_scale
    ray_pos = om.MVector(cam_pos)                      
    ray_dir = (pos0 - cam_pos).normalize()              
    total_dist = 0.0

    for i in range(max_steps):
        d = mandelbulbDE(ray_pos - mb_center)
        if d < 0.001:  # hit surface
            break
        ray_pos += ray_dir * d
        total_dist += d
        if total_dist > max_dist:
            break

    if (pos0 - ray_pos).length() > dist_limit:
        return None

    return (ray_pos.x, ray_pos.y, ray_pos.z)

# Apply Raymarch to Selected Vertices
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
