import maya.cmds as cmds
import math

# ---------- Input Parameters ----------
generations = 6
max_radius_display = 10
max_center_y = 10
min_radius_compute = 1e-6

# ---------- Sphere Generation Utilities ----------
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
            if (
                curv_i <= curv_j or
                (i == 0 and (B1 < A1 or C1 < A1 or D1 < A1 or E1 < A1)) or
                (i == 1 and (A1 <= B1 or C1 < B1 or D1 < B1 or E1 < B1)) or
                (i == 2 and (A1 <= C1 or B1 <= C1 or D1 < C1 or E1 < C1)) or
                (i == 3 and (A1 <= D1 or B1 <= D1 or C1 <= D1 or E1 < D1)) or
                (i == 4 and (A1 <= E1 or B1 <= E1 or C1 <= E1 or D1 <= E1))
            ):
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

#Generate spheres
spheres = apollonian(generations, min_radius_compute)

# Flatten list of spheres for filtering
all_spheres = [s for gen in spheres for s in gen]
pscales = [s[3] for s in all_spheres]

# Min/Max pscale
min_pscale = min(pscales)
max_pscale = max(pscales)

# User-defined min/max filter
minRange = 0.0
maxRange = 1.0

#Create Maya spheres
created_spheres = []

for s in all_spheres:
    # Normalize pscale like VEX fit
    fitScale = (s[3] - min_pscale) / (max_pscale - min_pscale)
    
    if fitScale < minRange or fitScale > maxRange:
        continue  # Skip this sphere
    
    # Create sphere in Maya
    name = cmds.polySphere(radius=s[3])[0]
    cmds.move(s[0], s[1], s[2])
    created_spheres.append(name)
