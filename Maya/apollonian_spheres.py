import maya.cmds as cmds
import math

# Sphere 
def curv(a):
    return a[-1] - a[-2]

def reg_coords(a):
    n = len(a) - 2
    kappa = curv(a)
    r = 1 / kappa if kappa != 0 else 1e6
    return [[d * r for d in a[:n]], r]

def special_to_cartesian(A, B, C, D, E):
    rt2o2 = math.sqrt(2) / 2
    rt6o2 = math.sqrt(6) / 2
    coords, radius = reg_coords([
        (-B + C + D - E) * rt2o2,
        (B - C + D - E) * rt2o2,
        (B + C - D - E) * rt2o2,
        A - B - C - D - E,
        (B + C + D + E) * rt6o2,
    ])
    return coords + [radius]

basis = [
    [1, 0, 0, 0, 0],
    [0, 1, 0, 0, 0],
    [0, 0, 1, 0, 0],
    [0, 0, 0, 1, 0],
    [0, 0, 0, 0, 1],
]

I_funcs = [
    lambda A, B, C, D, E: [-A, A + B, A + C, A + D, A + E],
    lambda A, B, C, D, E: [B + A, -B, B + C, B + D, B + E],
    lambda A, B, C, D, E: [C + A, C + B, -C, C + D, C + E],
    lambda A, B, C, D, E: [D + A, D + B, D + C, -D, D + E],
    lambda A, B, C, D, E: [E + A, E + B, E + C, E + D, -E],
]

def generate(basis):
    result = []
    rt6o2 = math.sqrt(6) / 2
    for A, B, C, D, E in basis:
        curv_j = (B + C + D + E) * rt6o2 - (A - B - C - D - E)
        for i in range(5):
            A1, B1, C1, D1, E1 = I_funcs[i](A, B, C, D, E)
            curv_i = (B1 + C1 + D1 + E1) * rt6o2 - (A1 - B1 - C1 - D1 - E1)
            if curv_i <= curv_j:
                continue
            result.append([A1, B1, C1, D1, E1])
    return result

def apollonian(generations, min_radius_compute):
    gen = basis
    result = [list(map(lambda s: special_to_cartesian(*s), basis))]
    for g in range(1, generations + 1):
        spheres = []
        next_gen = []
        for s in gen:
            sph = special_to_cartesian(*s)
            if abs(sph[3]) >= min_radius_compute:  
                spheres.append(sph)
                next_gen.append(s)
        if not spheres:
            break
        gen = generate(next_gen)
        result.append(spheres)
    return result

# Sphere Creation
def create_spheres(*args):
    #generations from the slider
    generations = cmds.intSliderGrp("sphereSlider", q=True, value=True)
    min_radius_compute = 1e-6
    
    spheres = apollonian(generations, min_radius_compute)
    all_spheres = []
    for gen in spheres:           
        for s in gen:             
            all_spheres.append(s)

    if not all_spheres:
        cmds.warning("No spheres generated.")
        return
    
    min_pscale = min(s[3] for s in all_spheres)
    max_pscale = max(s[3] for s in all_spheres)
    
    for s in all_spheres:
        fitScale = (s[3] - min_pscale) / (max_pscale - min_pscale)
        if 0.0 <= fitScale <= 1.0:
            name = cmds.polySphere(radius=s[3])[0]
            cmds.move(s[0], s[1], s[2], name)

# UI
def window_setup():
    window_name = "AG_Spheres"
    if cmds.window(window_name, exists=True):
        cmds.deleteUI(window_name)
    
    cmds.window(window_name, title="Apollonian Spheres", widthHeight=(300, 150))
    cmds.columnLayout(adjustableColumn=True, rowSpacing=10)
    
    cmds.text(label="Generations", align="center")
    cmds.intSliderGrp("sphereSlider", field=True, min=0, max=10, value=5, step=1)
    
    cmds.button(label="Create Apollonian Spheres", command=create_spheres, bgc=(0.6, 0.8, 0.6))
    
    cmds.showWindow(window_name)

window_setup()
