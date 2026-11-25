# SPDX-License-Identifier: GPL-3.0-or-later
"""Volumes: the Volume Builder and the Volume Mesher.

- Volume Builder: a mesh object whose Geometry Nodes group turns the objects under it into one volume (a distance
  grid named "distance": negative inside, positive outside). Each object is a row in its Objects list, where it is
  added to, subtracted from or intersected with the rows above, in list order, with its own Grow / Shrink and
  Smoothing. Meshes with faces are filled; meshes without faces (points) and splines become balls and tubes of the
  row's Radius. A Volume Builder under another one is used as it is. Objects under it show as a wire and don't render.
- Volume Mesher: a mesh object that turns the Volume Builder under it back into a surface (Threshold, Adaptivity,
  Smoothing). The Volume Builder under it shows as a wire.

Both groups are built for their object; their own settings are group inputs (animatable, kept when the group is
built again), the rows' settings reach the nodes through drivers, so files work with the add-on off. sync() runs after
every scene update: it keeps the Objects list in step with the children and builds the groups again when the
objects, their order, their mode or their kind change. Lengths are in cm (1 cm = 0.01 m)."""
import bpy

from . import generators as g
from . import kinds
from .primitives import input_slot, set_input, tidy_name

MOD = "Blenda Volume"
SCALE = 0.01
LABELS = {"volume_builder": "Volume Builder", "volume_mesher": "Volume Mesher"}
ICONS = {"volume_builder": "VOLUME_DATA", "volume_mesher": "MESH_DATA"}
TIPS = {"volume_builder": "Turns the objects under it into one volume: added, subtracted or intersected in list order",
        "volume_mesher": "Turns the Volume Builder under it into a mesh"}
GRID = "distance"

# kind -> [(socket, type, default, min, max, description)]
INPUTS = {
    "volume_builder": [
        ("Voxel Size", "FLOAT", 5.0, 0.1, 1000.0, "The size of one cell of the volume (cm): smaller is finer and slower"),
        ("Grow / Shrink", "FLOAT", 0.0, -1000.0, 1000.0, "Make the whole volume thicker (positive) or thinner (cm)"),
        ("Smoothing", "INT", 0, 0, 50, "Round the whole volume off: how many passes"),
        ("Rounding", "INT", 0, 0, 50, "Fill in hollows and creases only, leaving the rest: how many passes"),
    ],
    "volume_mesher": [
        ("Threshold", "FLOAT", 0.0, -1000.0, 1000.0, "Where the surface is taken: positive lies outside the volume's edge (cm)"),
        ("Adaptivity", "FLOAT", 0.0, 0.0, 1.0, "Use fewer, larger polygons where the surface is flat (0 to 1)"),
        ("Smoothing", "INT", 0, 0, 50, "Relax the new surface: how many passes"),
        ("Smooth Shading", "BOOL", True, None, None, "Shade the surface smooth instead of faceted"),
    ],
}
MODES = [("ADD", "Add", "Join this object to the volume above it", "ADD", 0),
         ("SUBTRACT", "Subtract", "Cut this object out of the volume above it", "REMOVE", 1),
         ("INTERSECT", "Intersect", "Keep only where this object and the volume above it overlap", "SELECT_INTERSECT", 2)]
BOOLEAN = {"ADD": "UNION", "SUBTRACT": "DIFFERENCE", "INTERSECT": "INTERSECT"}
INPUT_TYPES = ("MESH", "CURVE", "POINTCLOUD")


def kind(ob):
    k = ob.get("b4d_type") if ob is not None else None
    return k if k in LABELS else None


def enabled(ob):
    return bool(ob.get("_b4d_enabled", True))


def mod_of(ob):
    return ob.modifiers.get(MOD) if ob is not None else None


def slot(ob, name):
    mod = mod_of(ob)
    if mod is None or mod.node_group is None or name not in mod.node_group.interface.items_tree:
        return None
    try:
        return input_slot(mod, mod.node_group.interface.items_tree[name].identifier)
    except (KeyError, AttributeError):
        return None


def value(ob, name):
    s = slot(ob, name)
    return getattr(*s) if s else None


def put(ob, name, v):
    mod = mod_of(ob)
    set_input(mod, mod.node_group.interface.items_tree[name].identifier, v)
    ob.update_tag()


def usable(ob):
    """An object a Volume Builder can read: a mesh, a spline, a point cloud or another Volume Builder."""
    return ob is not None and (kind(ob) == "volume_builder" or (ob.type in INPUT_TYPES and kind(ob) is None))


def rows(builder):
    """The Objects list's rows that are switched on and still under the builder, in list order."""
    kids = set(g.kids_of(builder))
    return [r for r in builder.b4d_vol_items if r.use and r.obj is not None and r.obj in kids and usable(r.obj)]


def builder_of(mesher):
    return next((c for c in g.kids_of(mesher) if kind(c) == "volume_builder"), None)


def _input_shape(ob):
    """How an object is turned into a volume: 'builder', 'solid' (faces), 'points' or 'tube'."""
    if kind(ob) == "volume_builder":
        return "builder"
    if ob.type == "CURVE":
        return "tube"
    if ob.type == "POINTCLOUD" or (ob.type == "MESH" and len(ob.data.polygons) == 0):
        return "points"
    return "solid"


# --- node groups -----------------------------------------------------------------------------------------------

class _G:
    def __init__(self, tree):
        self.t, self.n, self.l = tree, tree.nodes, tree.links

    def node(self, idname, **props):
        nd = self.n.new(idname)
        for k, v in props.items():
            setattr(nd, k, v)
        return nd

    def put(self, v, sock):
        if hasattr(v, "is_output"):
            self.l.new(v, sock)
        else:
            sock.default_value = v

    def m(self, op, a, b=None):
        nd = self.node("ShaderNodeMath", operation=op)
        self.put(a, nd.inputs[0])
        if b is not None:
            self.put(b, nd.inputs[1])
        return nd.outputs[0]

    def cm(self, sock):
        return self.m("MULTIPLY", sock, SCALE)

    def driven(self, owner, path, default):
        """A number that follows owner's property at `path` through a driver."""
        v = self.node("ShaderNodeValue")
        v.outputs[0].default_value = default
        fc = v.outputs[0].driver_add("default_value")
        d = fc.driver
        d.type = "AVERAGE"
        var = d.variables.new()
        var.name = "v"
        var.targets[0].id_type = "OBJECT"
        var.targets[0].id = owner
        var.targets[0].data_path = path
        return v.outputs[0]

    def info(self, ob):
        nd = self.node("GeometryNodeObjectInfo", transform_space="RELATIVE")
        nd.inputs["Object"].default_value = ob
        return nd


def _new_tree(name, k, owner):
    old = bpy.data.node_groups.get(name)
    if old is not None:
        bpy.data.node_groups.remove(old)
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
    tree.is_modifier = True
    tree["b4d_owner"] = owner.name
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    for sname, stype, default, lo, hi, tip in INPUTS[k]:
        s = tree.interface.new_socket(sname, in_out="INPUT", socket_type={"FLOAT": "NodeSocketFloat", "INT": "NodeSocketInt",
                                                                            "BOOL": "NodeSocketBool"}[stype])
        s.default_value = default
        s.description = tip
        if lo is not None:
            s.min_value, s.max_value = lo, hi
    return tree


def _row_grid(G, builder, i, row, voxel):
    """The distance grid of one row, with its Grow / Shrink and Smoothing."""
    ob = row.obj
    shape = _input_shape(ob)
    geo = G.info(ob).outputs["Geometry"]
    if shape == "builder":
        get = G.node("GeometryNodeGetNamedGrid", data_type="FLOAT")
        G.l.new(geo, get.inputs["Volume"])
        get.inputs["Name"].default_value = GRID
        grid = get.outputs["Grid"]
    else:
        real = G.node("GeometryNodeRealizeInstances")
        G.l.new(geo, real.inputs["Geometry"])
        geo = real.outputs[0]
        radius = G.cm(G.driven(builder, f"b4d_vol_items[{i}].radius", row.radius))
        if shape == "tube":
            circle = G.node("GeometryNodeCurvePrimitiveCircle", mode="RADIUS")
            circle.inputs["Resolution"].default_value = 12
            G.l.new(radius, circle.inputs["Radius"])
            tube = G.node("GeometryNodeCurveToMesh")
            G.l.new(geo, tube.inputs["Curve"])
            G.l.new(circle.outputs["Curve"], tube.inputs["Profile Curve"])
            tube.inputs["Fill Caps"].default_value = True
            sdf = G.node("GeometryNodeMeshToSDFGrid")
            G.l.new(tube.outputs["Mesh"], sdf.inputs["Mesh"])
            G.l.new(voxel, sdf.inputs["Voxel Size"])
        elif shape == "points":
            pts = G.node("GeometryNodeMeshToPoints")
            G.l.new(geo, pts.inputs["Mesh"])
            join = G.node("GeometryNodeJoinGeometry")    # a point cloud object passes straight through
            G.l.new(pts.outputs[0], join.inputs[0])
            G.l.new(geo, join.inputs[0])
            sdf = G.node("GeometryNodePointsToSDFGrid")
            G.l.new(join.outputs[0], sdf.inputs["Points"])
            G.l.new(radius, sdf.inputs["Radius"])
            G.l.new(voxel, sdf.inputs["Voxel Size"])
        else:
            sdf = G.node("GeometryNodeMeshToSDFGrid")
            G.l.new(geo, sdf.inputs["Mesh"])
            G.l.new(voxel, sdf.inputs["Voxel Size"])
        if "Band Width" in sdf.inputs:
            sdf.inputs["Band Width"].default_value = 3
        grid = sdf.outputs[0]
    off = G.node("GeometryNodeSDFGridOffset")         # positive Distance grows the surface
    G.l.new(grid, off.inputs["Grid"])
    G.l.new(G.cm(G.driven(builder, f"b4d_vol_items[{i}].grow", row.grow)), off.inputs["Distance"])
    smooth = G.node("GeometryNodeSDFGridMean")
    G.l.new(off.outputs[0], smooth.inputs["Grid"])
    smooth.inputs["Width"].default_value = 1
    G.l.new(G.m("ROUND", G.driven(builder, f"b4d_vol_items[{i}].smooth", row.smooth)), smooth.inputs["Iterations"])
    return smooth.outputs[0]


def build_builder(builder):
    name = f"Blenda Volume Builder [{builder.name}]"
    tree = _new_tree(name, "volume_builder", builder)
    G = _G(tree)
    gin, gout = G.node("NodeGroupInput"), G.node("NodeGroupOutput")
    voxel = G.cm(G.m("MAXIMUM", gin.outputs["Voxel Size"], 0.1))
    grid = None
    for i, row in enumerate(builder.b4d_vol_items):
        if row not in rows(builder):
            continue
        part = _row_grid(G, builder, i, row, voxel)
        if grid is None:                              # the first row starts the volume, whatever its mode
            grid = part
            continue
        op = G.node("GeometryNodeSDFGridBoolean", operation=BOOLEAN[row.mode])
        many = op.inputs[1]                           # several grids go into one socket (Grid 2)
        if row.mode == "SUBTRACT":
            G.l.new(grid, op.inputs[0])
            G.l.new(part, many)
        else:
            G.l.new(part, many)
            G.l.new(grid, many)
        grid = op.outputs["Grid"]
    if grid is None:
        G.l.new(G.node("GeometryNodeJoinGeometry").outputs[0], gout.inputs[0])     # nothing to build: empty
        return tree
    off = G.node("GeometryNodeSDFGridOffset")
    G.l.new(grid, off.inputs["Grid"])
    G.l.new(G.cm(gin.outputs["Grow / Shrink"]), off.inputs["Distance"])
    smooth = G.node("GeometryNodeSDFGridMean")
    G.l.new(off.outputs[0], smooth.inputs["Grid"])
    smooth.inputs["Width"].default_value = 1
    G.l.new(gin.outputs["Smoothing"], smooth.inputs["Iterations"])
    fillet = G.node("GeometryNodeSDFGridFillet")
    G.l.new(smooth.outputs[0], fillet.inputs["Grid"])
    G.l.new(gin.outputs["Rounding"], fillet.inputs["Iterations"])
    store = G.node("GeometryNodeStoreNamedGrid", data_type="FLOAT")
    store.inputs["Name"].default_value = GRID
    G.l.new(G.node("GeometryNodeJoinGeometry").outputs[0], store.inputs["Volume"])
    G.l.new(fillet.outputs[0], store.inputs["Grid"])
    # what the viewport draws: a fog that is thick inside the surface and fades out across it ("density" is the grid
    # Blender shows first); the distance grid stays for the Volume Mesher and other Volume Builders
    fog = G.node("GeometryNodeFieldToGrid", data_type="FLOAT")
    item = fog.grid_items.new("FLOAT", "Density")
    G.l.new(fillet.outputs[0], fog.inputs["Topology"])
    read = G.node("GeometryNodeSampleGrid", data_type="FLOAT")
    G.l.new(fillet.outputs[0], read.inputs["Grid"])
    G.l.new(G.node("GeometryNodeInputPosition").outputs[0], read.inputs["Position"])
    dens = G.node("ShaderNodeMapRange", clamp=True)
    G.l.new(read.outputs["Value"], dens.inputs["Value"])
    G.l.new(G.m("MULTIPLY", voxel, -1.0), dens.inputs["From Min"])
    G.l.new(voxel, dens.inputs["From Max"])
    dens.inputs["To Min"].default_value, dens.inputs["To Max"].default_value = 1.0, 0.0
    G.l.new(dens.outputs["Result"], fog.inputs[item.name] if item.name in fog.inputs else fog.inputs[1])
    shown = G.node("GeometryNodeStoreNamedGrid", data_type="FLOAT")
    shown.inputs["Name"].default_value = "density"
    G.l.new(store.outputs[0], shown.inputs["Volume"])
    G.l.new(fog.outputs[0], shown.inputs["Grid"])
    G.l.new(shown.outputs[0], gout.inputs[0])
    return tree


def build_mesher(mesher):
    name = f"Blenda Volume Mesher [{mesher.name}]"
    tree = _new_tree(name, "volume_mesher", mesher)
    G = _G(tree)
    gin, gout = G.node("NodeGroupInput"), G.node("NodeGroupOutput")
    src = builder_of(mesher)
    if src is None:
        G.l.new(G.node("GeometryNodeJoinGeometry").outputs[0], gout.inputs[0])
        return tree
    get = G.node("GeometryNodeGetNamedGrid", data_type="FLOAT")
    G.l.new(G.info(src).outputs["Geometry"], get.inputs["Volume"])
    get.inputs["Name"].default_value = GRID
    surf = G.node("GeometryNodeGridToMesh")
    G.l.new(get.outputs["Grid"], surf.inputs["Grid"])
    G.l.new(G.cm(gin.outputs["Threshold"]), surf.inputs["Threshold"])
    G.l.new(gin.outputs["Adaptivity"], surf.inputs["Adaptivity"])
    blur = G.node("GeometryNodeBlurAttribute", data_type="FLOAT_VECTOR")
    G.l.new(G.node("GeometryNodeInputPosition").outputs[0], blur.inputs["Value"])
    G.l.new(gin.outputs["Smoothing"], blur.inputs["Iterations"])
    setp = G.node("GeometryNodeSetPosition")
    G.l.new(surf.outputs["Mesh"], setp.inputs["Geometry"])
    G.l.new(blur.outputs[0], setp.inputs["Position"])
    shade = G.node("GeometryNodeSetShadeSmooth")
    G.l.new(setp.outputs[0], shade.inputs["Mesh"])
    G.l.new(gin.outputs["Smooth Shading"], shade.inputs["Shade Smooth"])
    out = shade.outputs[0]
    mat = _material(mesher)
    if mat is not None:                               # a new surface has no material of its own: give it the Mesher's
        setm = G.node("GeometryNodeSetMaterial")
        G.l.new(out, setm.inputs["Geometry"])
        setm.inputs["Material"].default_value = mat
        out = setm.outputs[0]
    G.l.new(out, gout.inputs[0])
    return tree


def _material(ob):
    return next((sl.material for sl in ob.material_slots if sl.material is not None), None)


def _signature(ob):
    if kind(ob) == "volume_builder":
        return [f"{r.obj.name}:{r.mode}:{_input_shape(r.obj)}" for r in rows(ob)] + [str(len(ob.b4d_vol_items))]
    src = builder_of(ob)
    return [src.name if src else "", getattr(_material(ob), "name", "")]


def rebuild(ob):
    k = kind(ob)
    mod = mod_of(ob)
    if mod is None:
        mod = ob.modifiers.new(MOD, "NODES")
    old = {}
    if mod.node_group is not None:
        for it in mod.node_group.interface.items_tree:
            if it.item_type == "SOCKET" and it.in_out == "INPUT":
                try:
                    old[it.name] = getattr(*input_slot(mod, it.identifier))
                except Exception:
                    pass
    tree = build_builder(ob) if k == "volume_builder" else build_mesher(ob)
    mod.node_group = tree
    for name, v in old.items():
        if name in tree.interface.items_tree:
            try:
                set_input(mod, tree.interface.items_tree[name].identifier, v)
            except Exception:
                pass
    ob["b4d_vol_sig"] = _signature(ob)
    ob.update_tag()


def _needs_rebuild(ob):
    mod = mod_of(ob)
    if mod is None or mod.node_group is None or mod.node_group.get("b4d_owner") != ob.name:
        return True
    return list(ob.get("b4d_vol_sig", [])) != _signature(ob)


# --- keeping things in step ------------------------------------------------------------------------------------

def _fill_list(builder):
    """New children get a row (at the end); rows whose object left (or was deleted) go."""
    kids = [c for c in g.kids_of(builder) if usable(c)]
    lst = builder.b4d_vol_items
    for i in reversed(range(len(lst))):
        if lst[i].obj is None or lst[i].obj not in kids:
            lst.remove(i)
    have = {r.obj for r in lst}
    for c in kids:
        if c not in have:
            r = lst.add()
            r.obj = c
            r.radius = max(1.0, value(builder, "Voxel Size") or 5.0) * 2.0
    if builder.b4d_vol_index >= len(lst):
        builder.b4d_vol_index = max(0, len(lst) - 1)


_busy = [False]


def sync(scene):
    if _busy[0] or scene is None:
        return
    _busy[0] = True
    try:
        keep = set()
        obs = [o for o in scene.objects if kind(o)]
        for ob in sorted(obs, key=lambda o: kind(o) != "volume_builder"):     # builders first: meshers read them
            if kind(ob) == "volume_builder":
                _fill_list(ob)
            if _needs_rebuild(ob):
                rebuild(ob)
            if enabled(ob):
                used = [r.obj for r in rows(ob)] if kind(ob) == "volume_builder" else [builder_of(ob)]
                for o in used:
                    if o is not None:
                        keep.add(o.name)
                        _hide(o)
        for o in scene.objects:
            if HIDDEN in o and o.name not in keep:
                _show(o)
    finally:
        _busy[0] = False


HIDDEN = "b4d_vol_hidden"      # how an object looked before a volume object took it (its own key: the generators
                               # bring back what they hid, and would bring these back too)


def _hide(ob):
    if HIDDEN in ob:
        return
    ob[HIDDEN] = {"display": ob.display_type, "render": int(ob.hide_render)}
    ob.display_type = "BOUNDS" if kind(ob) == "volume_builder" else "WIRE"   # a volume drawn as a wire still shows its fog
    ob.hide_render = True


def _show(ob):
    was = ob.get(HIDDEN)
    try:
        display, render = str(was.get("display", "TEXTURED")), bool(was.get("render", 0))
    except (TypeError, AttributeError):
        display, render = "TEXTURED", False
    del ob[HIDDEN]
    try:
        ob.display_type = display
    except TypeError:
        ob.display_type = "TEXTURED"
    ob.hide_render = render


def sync_all():
    sync(bpy.context.scene)


@bpy.app.handlers.persistent
def _on_update(scene, depsgraph=None):
    sync(scene)


# --- making them -----------------------------------------------------------------------------------------------

def create(context, k):
    sel = g._selection(context)
    ob = bpy.data.objects.new(LABELS[k], bpy.data.meshes.new(LABELS[k]))
    context.collection.objects.link(ob)
    ob["b4d_type"] = k
    tidy_name(ob)
    if k == "volume_mesher":
        sel = [o for o in sel if kind(o) == "volume_builder"][:1]     # a Mesher takes one Volume Builder
    else:
        sel = [o for o in sel if usable(o) or o.type == "EMPTY"]
    if sel:
        g.wrap(ob, sel)
    else:
        ob.location = context.scene.cursor.location
    rebuild(ob)
    sync_all()
    return ob


# --- the Objects list ------------------------------------------------------------------------------------------

def _row_changed(self, context):
    sync_all()


class BLENDA4D_PG_volume_item(bpy.types.PropertyGroup):
    obj: bpy.props.PointerProperty(type=bpy.types.Object, name="Object")
    use: bpy.props.BoolProperty(name="Use", default=True, update=_row_changed,
                                description="Leave this object out for a while without taking it out from under the Volume Builder")
    mode: bpy.props.EnumProperty(name="Mode", items=MODES, default="ADD", update=_row_changed,
                                 description="How this object meets the volume made by the rows above it")
    radius: bpy.props.FloatProperty(name="Radius", default=10.0, min=0.0, soft_max=1000.0, precision=1,
                                    description="Splines become tubes and points become balls this thick (cm)")
    grow: bpy.props.FloatProperty(name="Grow / Shrink", default=0.0, soft_min=-100.0, soft_max=100.0, precision=1,
                                  description="Make this object's part thicker (positive) or thinner (cm)")
    smooth: bpy.props.IntProperty(name="Smoothing", default=0, min=0, max=50,
                                  description="Round this object's part off before it meets the rows above: how many passes")


class BLENDA4D_UL_volume_items(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "use", text="")
        sub = row.row()
        sub.active = item.use
        ob = item.obj
        sub.label(text=ob.name if ob else "(gone)", icon=kinds.KINDS.get(kind(ob), {}).get("icon", "OBJECT_DATA")
                  if kind(ob) else {"MESH": "MESH_DATA", "CURVE": "CURVE_DATA", "POINTCLOUD": "POINTCLOUD_DATA"}.get(
                      getattr(ob, "type", ""), "OBJECT_DATA"))
        sub.prop(item, "mode", text="", icon_only=True, emboss=False)


class BLENDA4D_OT_volume_move(bpy.types.Operator):
    """Move the chosen object up or down the list: the order decides what adds to and cuts from what"""
    bl_idname = "blenda4d.volume_move"
    bl_label = "Move in List"
    bl_options = {"REGISTER", "UNDO"}
    owner: bpy.props.StringProperty(options={"HIDDEN"})
    step: bpy.props.IntProperty(options={"HIDDEN"})

    def execute(self, context):
        ob = bpy.data.objects.get(self.owner)
        if ob is None:
            return {"CANCELLED"}
        i, j = ob.b4d_vol_index, ob.b4d_vol_index + self.step
        if not (0 <= i < len(ob.b4d_vol_items) and 0 <= j < len(ob.b4d_vol_items)):
            return {"CANCELLED"}
        ob.b4d_vol_items.move(i, j)
        ob.b4d_vol_index = j
        rebuild(ob)                                   # the drivers point at rows by number
        sync_all()
        return {"FINISHED"}


# --- Attribute Manager pages -----------------------------------------------------------------------------------

def _settings(col, ob):
    for sname, _t, _d, _lo, _hi, _tip in INPUTS[kind(ob)]:
        s = slot(ob, sname)
        if s is not None:
            col.prop(*s, text=sname + (" (cm)" if sname in ("Voxel Size", "Grow / Shrink", "Threshold") else ""))


def page_builder(col, ob):
    col.use_property_split = True
    _settings(col, ob)
    col.separator()
    col.use_property_split = False
    col.label(text="Objects, in the order they meet")
    r = col.row()
    r.template_list("BLENDA4D_UL_volume_items", "", ob, "b4d_vol_items", ob, "b4d_vol_index", rows=4)
    side = r.column(align=True)
    for icon, step in (("TRIA_UP", -1), ("TRIA_DOWN", 1)):
        op = side.operator("blenda4d.volume_move", text="", icon=icon)
        op.owner, op.step = ob.name, step
    lst = ob.b4d_vol_items
    if not len(lst):
        col.label(text="Put meshes, splines, points or other Volume Builders under it", icon="INFO")
        return
    i = ob.b4d_vol_index
    if 0 <= i < len(lst) and lst[i].obj is not None:
        row = lst[i]
        box = col.column()
        box.use_property_split = True
        box.label(text=f"Row: {row.obj.name}")
        box.prop(row, "mode")
        if i == 0:
            box.label(text="The first row starts the volume", icon="INFO")
        if _input_shape(row.obj) in ("tube", "points"):
            box.prop(row, "radius", text="Radius (cm)")
        box.prop(row, "grow", text="Grow / Shrink (cm)")
        box.prop(row, "smooth")
        if _input_shape(row.obj) == "solid" and not _closed(row.obj):
            box.label(text="This mesh has holes: its inside may come out wrong", icon="ERROR")


def _closed(ob):
    """True when every edge of the mesh is shared by two faces (it has an inside)."""
    me = ob.data
    if not hasattr(me, "edges") or not len(me.polygons):
        return False
    count = {}
    for p in me.polygons:
        for k in p.edge_keys:
            count[k] = count.get(k, 0) + 1
    return all(n == 2 for n in count.values())


def page_mesher(col, ob):
    col.use_property_split = True
    _settings(col, ob)
    if builder_of(ob) is None:
        col.label(text="Put a Volume Builder under it", icon="INFO")


# --- registration ----------------------------------------------------------------------------------------------

classes = (BLENDA4D_PG_volume_item, BLENDA4D_UL_volume_items, BLENDA4D_OT_volume_move)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Object.b4d_vol_items = bpy.props.CollectionProperty(type=BLENDA4D_PG_volume_item)
    bpy.types.Object.b4d_vol_index = bpy.props.IntProperty(default=0)
    pages = {"volume_builder": page_builder, "volume_mesher": page_mesher}
    for k in ("volume_builder", "volume_mesher"):
        kinds.add(k, LABELS[k], ICONS[k], create=(lambda k: lambda context: create(context, k))(k),
                  tabs=[("OBJECT", "Object")], pages={"OBJECT": pages[k]}, titles={"OBJECT": LABELS[k]},
                  describe=TIPS[k] + " (selected objects go under it)")
        kinds.create_button("volume", LABELS[k], k)
    if _on_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_update)


def unregister():
    if _on_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_update)
    for n in ("b4d_vol_items", "b4d_vol_index"):
        if hasattr(bpy.types.Object, n):
            delattr(bpy.types.Object, n)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
