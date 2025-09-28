import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.OpenMaya as om2
import maya.mel as mel

maya_useNewAPI = True

class RayIt_Script:
    
    def __init__(self):
        # GUI
        window_name = "Ray_Window"
        
        if cmds.window(window_name, exists=True):
            cmds.deleteUI(window_name)
    
        cmds.window(window_name, title="Ray Vertices To Target", widthHeight=(300, 525))
        cmds.columnLayout(adjustableColumn=True, rowSpacing=10)
    
        cmds.text(label="Project Vertices to Target", align="center", font="boldLabelFont", backgroundColor=(0.7, 0.7, 0.7))
        cmds.button(label="Select Target Object", command=self.select_target_object, backgroundColor=(0.6, 0.8, 0.6))
    
        cmds.separator(height=10)
        cmds.text(label="Set Vertices To Ray", align="center", font="boldLabelFont", backgroundColor=(0.7, 0.7, 0.7))
        cmds.button(label="Select and Set/Reset Vertices", command=self.select_vertices_button, backgroundColor=(0.6, 0.8, 0.6))
    
        cmds.separator(height=10)
        cmds.text(label="Set Direction : Pick 1", align="center", font="boldLabelFont", backgroundColor=(0.7, 0.7, 0.7))
        cmds.button(label="Set Up Direction", command=self.set_up_direction_button, backgroundColor=(1.0, 0.6, 0.6))
        cmds.button(label="Set Down Direction", command=self.set_down_direction_button, backgroundColor=(1.0, 0.6, 0.6))
    
        cmds.text(label="Set Custom Direction: -1 to 1...Example y at 1 is up")
        cmds.text(label="Note: If rays miss, no movement. Verify values.")
    
        cmds.rowLayout(numberOfColumns=3)
        global x_field, y_field, z_field
        x_field = cmds.floatField(minValue=-10, maxValue=10, precision=4, step=0.01)
        y_field = cmds.floatField(minValue=-10, maxValue=10, precision=4, step=0.01)
        z_field = cmds.floatField(minValue=-10, maxValue=10, precision=4, step=0.01)
        cmds.setParent('..')
        cmds.button(label="Set Custom Direction", command=self.set_custom_direction, backgroundColor=(1.0, 0.6, 0.6))
    
        cmds.separator(height=10)
        cmds.text(label="Project The Vertices", align="center", font="boldLabelFont", backgroundColor=(0.7, 0.7, 0.7))
        cmds.button(label="Position Calculation", command=self.calculate_button, backgroundColor=(0.4, 0.6, 0.8))
    
        cmds.text("New Position Slider")
        cmds.intSliderGrp(min=0, max=100, value=0, step=1, changeCommand=self.lerp_action, dragCommand=self.lerp_action)
    
        cmds.showWindow(window_name)
        

    # Selection Helpers
    def get_selected_objects(self, *args):
        selection = cmds.ls(sl=True)
        return [str(obj) for obj in selection]
    
    def get_selected_vertices(self, *args):
        verts = cmds.ls(selection=True, flatten=True)
        return verts if verts else []
    
    # Mesh Functions
    def get_mesh_fn(self, mesh_name):
        sel_list = om.MSelectionList()
        sel_list.add(mesh_name)
        dag_path = sel_list.getDagPath(0)
        return om.MFnMesh(dag_path)
    
    def get_vertex_positions(self, source, count):
        sel_list = om2.MSelectionList()
        om2.MGlobal.getActiveSelectionList(sel_list)
        dag = om2.MDagPath()
        component = om2.MObject()
        sel_list.getDagPath(0, dag, component)
    
        positions = []
        if not component.isNull():
            vert_itr = om2.MItMeshVertex(dag, component)
            while not vert_itr.isDone():
                pos = vert_itr.position(om2.MSpace.kWorld)
                positions.append((pos.x, pos.y, pos.z))
                vert_itr.next()
        return positions
    
    def calculate_average_normal(self, vertices):
        faces = cmds.polyListComponentConversion(toFace=True)
        cmds.select(faces)
        mel.eval('PolySelectTraverse 2')
        face_normals_info = cmds.polyInfo(faces, faceNormals=True)
    
        normals = [tuple(map(float, f.split()[2:5])) for f in face_normals_info]
        count = len(normals)
        avg_normal = [sum(v[i] for v in normals)/count for i in range(3)]
        cmds.select(vertices)
        return avg_normal
    
    # Ray Intersections
    def get_intersections(self, target_mesh, source, vertex_count, vertices, direction):
        fn_mesh_target = self.get_mesh_fn(target_mesh)
        ray_sources = self.get_vertex_positions(source, vertex_count)
        k_space = om.MSpace.kWorld
        test_both = False
        tolerance = 0.001
        max_param = 999999
    
        vertex_positions = {}
        for i in range(vertex_count):
            ray_source = om.MFloatPoint(*ray_sources[i])
            vtx = vertices[i]
            world_pos = cmds.pointPosition(vtx, world=True)
    
            hit = fn_mesh_target.closestIntersection(ray_source,
                                                     direction,
                                                     k_space,
                                                     max_param,
                                                     test_both,
                                                     tolerance=tolerance)
            if hit and hit[0]:
                hit_point = hit[0]
                vertex_positions[vtx] = (hit_point.x, hit_point.y, hit_point.z)
            else:
                vertex_positions[vtx] = world_pos
        return vertex_positions
    
    # Vertex Movement
    def store_vertex_positions(self, vertex_positions):
        global target_positions, original_positions, vertex_list, orig_data
        orig_data = {}
        vertex_list = list(vertex_positions.keys())
        target_positions = []
        original_positions = []
    
        for vtx, pos in vertex_positions.items():
            orig_pos = cmds.xform(vtx, q=True, ws=True, t=True)
            orig_data[vtx] = (pos, orig_pos)
    
        for vtx, (pos, orig_pos) in orig_data.items():
            target_positions.append(pos)
            original_positions.append(orig_pos)
    
        return target_positions, original_positions
    
    # Direction
    def vec_up(self): return om.MFloatVector(0,1,0)
    def vec_down(self): return om.MFloatVector(0,-1,0)
    
    def set_custom_direction(self, *args):
        global direction
        x = cmds.floatField(x_field, q=True, value=True)
        y = cmds.floatField(y_field, q=True, value=True)
        z = cmds.floatField(z_field, q=True, value=True)
        direction = om.MFloatVector(x, y, z)
        print("Direction set:", direction)
        return direction
    
    # Global Variables
        self.target_obj = []
        self.selected_vertices = []
        self.vertex_list = []
        self.original_positions = []
        self.target_positions = []
        self.direction = self.vec_up()
        self.orig_data = {}
    
    # General Functions
    def calculate_targets(self):
        source_name = "source"
        target_mesh = target_obj[0]
        vertices = self.get_selected_vertices()
        if not vertices:
            cmds.warning("No vertices selected for calculation")
            return False
    
        vertex_count = len(vertices)
        vertex_positions = self.get_intersections(target_mesh, source_name, vertex_count, vertices, direction)
        self.store_vertex_positions(vertex_positions)
        return True
    
    def lerp_positions(self, original_pos, target_pos, bias):
        t = bias / 100.0
        for i, vtx in enumerate(vertex_list):
            new_pos = [original_pos[i][j] + t*(target_pos[i][j] - original_pos[i][j]) for j in range(3)]
            cmds.xform(vtx, ws=True, t=new_pos)
    
    def lerp_action(self, bias):
        self.lerp_positions(original_positions, target_positions, bias)
    
    # GUI Callbacks 
    def set_up_direction_button(self, *args):
        global direction
        direction = self.vec_up()
        cmds.confirmDialog(title="Direction", message="Up direction set")
    
    def set_down_direction_button(self, *args):
        global direction
        direction = self.vec_down()
        cmds.confirmDialog(title="Direction", message="Down direction set")
    
    def select_target_object(self, *args):
        global target_obj
        target_obj = self.get_selected_objects()
        cmds.confirmDialog(title="Target Object", message="Target object selected")
    
    def select_vertices_button(self, *args):
        global selected_vertices
        selected_vertices = self.get_selected_vertices()
        cmds.confirmDialog(title="Vertices Selected", message="Vertices selected")
    
    def calculate_button(self, *args):
        if not target_obj:
            cmds.warning("Please select a target object")
            return
        vertices = self.get_selected_vertices()
        if not vertices:
            cmds.warning("Please select vertices to ray")
            return
        self.calculate_targets()
        cmds.confirmDialog(title="Done", message="Vertex positions calculated")

RayIt_Script()

