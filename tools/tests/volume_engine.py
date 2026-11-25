# Headless test of the volume module: the Volume Builder turns meshes, splines, points and other Volume Builders into
# one volume, rows add, subtract and intersect in list order, every setting changes the result (read through a Volume
# Mesher's surface), objects under them are hidden and given back, and files keep working with the module off.
#   Blender -b --factory-startup --python tools/tests/volume_engine.py
import os
import sys
import traceback

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from blenda4d import coords, generators as G, kinds, moclone_fx_ui as FXUI, volume as V  # noqa: E402

bpy.utils.register_class(kinds.BLENDA4D_OT_add_kind)
coords.register()
G.register()
FXUI.register()                                       # the fields a filter layer can be limited by
V.register()
fails = []
passed = [0]


def check(ok, msg):
    if ok:
        passed[0] += 1
        print("ok", msg, flush=True)
    else:
        fails.append(msg)
        print("FAIL", msg, flush=True)


def update():
    for _ in range(2):
        V.sync_all()
        bpy.context.view_layer.update()


def surface(ob):
    """(points, faces, size x y z, volume-ish sum, smooth faces) of an object's evaluated mesh."""
    update()
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    vs = [v.co.copy() for v in me.vertices]
    size = tuple(round(max(v[i] for v in vs) - min(v[i] for v in vs), 3) for i in range(3)) if vs else (0, 0, 0)
    spread = round(sum(abs(v[0]) + abs(v[1]) + abs(v[2]) for v in vs), 2)
    smooth = sum(1 for p in me.polygons if p.use_smooth)
    out = (len(vs), len(me.polygons), size, spread, smooth)
    ev.to_mesh_clear()
    return out


def grids(ob):
    update()
    ev = ob.evaluated_get(bpy.context.evaluated_depsgraph_get())
    try:
        geo = ev.evaluated_geometry()
        vol = geo.volume
        return [gr.name for gr in vol.grids] if vol is not None else []
    except AttributeError:
        return ["?"]


def clear():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)


def select(objs, active=None):
    for o in bpy.context.scene.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = active or (objs[0] if objs else None)


def cube(name, size=1.0, loc=(0, 0, 0)):
    bpy.ops.mesh.primitive_cube_add(size=size, location=loc)
    ob = bpy.context.object
    ob.name = name
    return ob


def ball(name, radius=0.5, loc=(0, 0, 0)):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, location=loc)
    ob = bpy.context.object
    ob.name = name
    return ob


def add(k, objs=()):
    select(list(objs))
    bpy.ops.blenda4d.add_kind(kind=k)
    ob = bpy.context.view_layer.objects.active
    update()
    return ob


def put(ob, name, v):
    V.put(ob, name, v)


def row_change(ob, row, attr, v, label, mesher):
    before = surface(mesher)
    old = getattr(row, attr)
    setattr(row, attr, v)
    ob.update_tag()
    check(surface(mesher) != before, f"row {label} changes the surface")
    setattr(row, attr, old)
    ob.update_tag()


def test_builder_and_mesher():
    print("== a cube and a ball, built and meshed")
    clear()
    c = cube("Box", 1.0)
    b = ball("Ball", 0.45, (0.6, 0, 0))
    vb = add("volume_builder", [c, b])
    check(V.kind(vb) == "volume_builder" and c.parent == vb and b.parent == vb, "selected objects go under the Volume Builder")
    check([r.obj for r in vb.b4d_vol_items] == [b, c] or [r.obj for r in vb.b4d_vol_items] == [c, b],
          "each child gets a row in the Objects list")
    check("distance" in grids(vb) or grids(vb) == ["?"], f"the builder holds a distance volume ({grids(vb)})")
    check(c.display_type == "WIRE" and c.hide_render and b.display_type == "WIRE", "objects under it show as a wire and don't render")
    vm = add("volume_mesher", [vb])
    check(vb.parent == vm and V.builder_of(vm) == vb, "a Mesher made with a Volume Builder selected takes it")
    check(vb.display_type == "BOUNDS", "the Volume Builder under a Mesher shows as its bounding box (no fog in the way)")
    s = surface(vm)
    check(s[1] > 50, f"the Mesher makes a surface ({s[1]} faces)")
    check(1.3 < s[2][0] < 1.8 and 0.9 < s[2][1] < 1.15, f"its size is the cube and the ball together ({s[2]})")
    # every setting on the builder
    for name, v in (("Voxel Size", 10.0), ("Grow / Shrink", 8.0), ("Smoothing", 3), ("Rounding", 4)):
        before = surface(vm)
        old = V.value(vb, name)
        put(vb, name, v)
        check(surface(vm) != before, f"Volume Builder row {name} changes the surface")
        put(vb, name, old)
    # every setting on the mesher
    for name, v in (("Threshold", 5.0), ("Adaptivity", 0.8), ("Smoothing", 5), ("Smooth Shading", False)):
        before = surface(vm)
        old = V.value(vm, name)
        put(vm, name, v)
        check(surface(vm) != before, f"Volume Mesher row {name} changes the surface")
        put(vm, name, old)
    mat = bpy.data.materials.new("Clay")
    vm.data.materials.append(mat)
    update()
    ev = vm.evaluated_get(bpy.context.evaluated_depsgraph_get())
    check([m.name for m in ev.data.materials if m] == ["Clay"], "the surface takes the Mesher's material")
    grown = V.value(vb, "Grow / Shrink")
    put(vb, "Grow / Shrink", 10.0)
    check(surface(vm)[2][0] > s[2][0] + 0.1, "Grow makes it bigger")
    put(vb, "Grow / Shrink", grown)
    # rows
    i = next(k for k, r in enumerate(vb.b4d_vol_items) if r.obj == b)
    row = vb.b4d_vol_items[i]
    row_change(vb, row, "grow", 10.0, "Grow / Shrink", vm)
    row_change(vb, row, "smooth", 4, "Smoothing", vm)
    row.smooth = 3
    update()
    seen = {"MEAN": surface(vm)}
    for f in ("MEDIAN", "LAPLACIAN", "CURVATURE"):
        row.filter = f
        update()
        seen[f] = surface(vm)
        check(seen[f] != seen["MEAN"], f"row Filter {f.title()} filters differently from Smooth")
    row.filter = "MEAN"
    row.smooth = 0
    update()
    full = surface(vm)
    row.use = False
    update()
    check(surface(vm)[2][0] < full[2][0] - 0.2, "an unticked row is left out (only the cube is left)")
    row.use = True
    update()
    # modes: put the ball second, then subtract and intersect
    vb.b4d_vol_index = i
    if i == 0:
        bpy.ops.blenda4d.volume_move(owner=vb.name, step=1)
    update()
    row = next(r for r in vb.b4d_vol_items if r.obj == b)
    check(vb.b4d_vol_items[1].obj == b, "the move buttons change the list order")
    added = surface(vm)
    row.mode = "SUBTRACT"
    update()
    cut = surface(vm)
    check(cut[2][0] < added[2][0] - 0.2 and cut[1] != added[1], f"Subtract cuts the ball out of the cube ({cut[2]})")
    row.mode = "INTERSECT"
    update()
    both = surface(vm)
    check(both[2][0] < 0.65, f"Intersect keeps only the overlap ({both[2]})")
    row.mode = "ADD"
    update()
    # a moved child moves the surface
    before = surface(vm)
    b.location.x += 0.3
    check(surface(vm) != before, "moving an object under the builder changes the surface")
    # taking a child out gives it back
    b.parent = None
    update()
    check(b.display_type != "WIRE" and not b.hide_render and len(vb.b4d_vol_items) == 1,
          "taken out from under the builder: it shows again and its row goes")


def test_splines_points_and_nesting():
    print("== splines become tubes, points become balls, a builder in a builder")
    clear()
    cu = bpy.data.curves.new("Path", "CURVE")
    cu.dimensions = "3D"
    sp = cu.splines.new("POLY")
    sp.points.add(1)
    sp.points[0].co = (-1.0, 0.0, 0.0, 1.0)
    sp.points[1].co = (1.0, 0.0, 0.0, 1.0)
    path = bpy.data.objects.new("Path", cu)
    bpy.context.collection.objects.link(path)
    me = bpy.data.meshes.new("Dots")
    me.from_pydata([(0.0, 1.0, 0.0), (0.5, 1.0, 0.0)], [], [])
    dots = bpy.data.objects.new("Dots", me)
    bpy.context.collection.objects.link(dots)
    vb = add("volume_builder", [path, dots])
    vm = add("volume_mesher", [vb])
    s = surface(vm)
    check(s[1] > 20 and s[2][0] > 1.8, f"the spline becomes a tube along its length ({s[2]})")
    check(s[2][1] > 1.0, "the points become balls beside it")
    row = next(r for r in vb.b4d_vol_items if r.obj == path)
    row_change(vb, row, "radius", 25.0, "Radius (spline)", vm)
    row = next(r for r in vb.b4d_vol_items if r.obj == dots)
    row_change(vb, row, "radius", 25.0, "Radius (points)", vm)
    inner = add("volume_builder", [cube("Inner Box", 0.5, (0.0, -1.5, 0.0))])
    select([inner])
    inner.parent = vb
    update()
    check(any(r.obj == inner for r in vb.b4d_vol_items), "a Volume Builder under another one gets a row")
    check(surface(vm)[2][1] > s[2][1] + 0.3, "and its volume is part of the outer one")


def test_text_and_fog():
    print("== text, and Render as Fog")
    clear()
    cu = bpy.data.curves.new("Word", "FONT")
    cu.body = "HI"
    word = bpy.data.objects.new("Word", cu)
    bpy.context.collection.objects.link(word)
    vb = add("volume_builder", [word])
    vm = add("volume_mesher", [vb])
    flat = surface(vm)
    check(flat[1] > 50 and flat[2][2] > 0.01, f"flat letters become rounded letters ({flat[1]} faces, {flat[2]})")
    row_change(vb, vb.b4d_vol_items[0], "radius", 6.0, "Radius (text)", vm)
    cu.extrude = 0.2
    update()
    solid = surface(vm)
    check(abs(solid[2][2] - 0.4) < 0.12, f"extruded letters are filled as they are ({solid[2]})")
    # fog
    clear()
    vb = add("volume_builder", [cube("Box", 1.0)])
    vb.b4d_vol_fog = True
    update()
    setm = [n for n in V.mod_of(vb).node_group.nodes if n.bl_idname == "GeometryNodeSetMaterial"]
    check(setm and setm[0].inputs["Material"].default_value.name == V.FOG_MATERIAL,
          "Render as Fog puts the fog material on the volume")
    check("b4d_fog_density" in vb and "b4d_fog_color" in vb, "and its own Fog Density and Fog Color")
    tree = V.mod_of(vb).node_group
    check(any(n.bl_idname == "GeometryNodeVolumeCube" for n in tree.nodes), "the fog is a filled grid over the volume's bounds")
    ev = vb.evaluated_get(bpy.context.evaluated_depsgraph_get())
    check(ev.evaluated_geometry().volume is not None, "the builder holds a volume to render")
    vm = add("volume_mesher", [vb])
    check(surface(vm)[1] > 50, "a Mesher still reads it")
    check(vb.hide_render, "under a Mesher it doesn't render")
    vm.select_set(False)
    vb.parent = None
    update()
    check(not vb.hide_render, "out from under the Mesher it renders again")
    vb.b4d_vol_fog = False
    update()
    check(not any(n.bl_idname == "GeometryNodeVolumeCube" for n in V.mod_of(vb).node_group.nodes), "and the filled grid")


def test_switch_off():
    print("== the file works with the module off")
    clear()
    vb = add("volume_builder", [cube("Box", 1.0)])
    vm = add("volume_mesher", [vb])
    s = surface(vm)
    V.unregister()
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    me = vm.evaluated_get(dg).to_mesh()
    check(len(me.polygons) == s[1], "with the module off the surface is still there (the nodes and drivers stay)")
    vm.evaluated_get(dg).to_mesh_clear()
    V.register()


def test_volume_from_and_viewport():
    print("== Volume From on each row, and how the builder looks in the viewport")
    clear()
    from blenda4d import primitives as P
    plat = P.add_primitive(bpy.context, "platonic")
    update()
    check(len(plat.data.polygons) == 0, "a Platonic's own mesh is empty (its faces come from its modifier)")
    vb = add("volume_builder", [plat])
    row = vb.b4d_vol_items[0]
    check(V._input_shape(plat, row) == "solid", "Auto fills a Platonic instead of taking its points")
    vm = add("volume_mesher", [vb])
    filled = surface(vm)
    check(filled[1] > 200 and min(filled[2]) > 1.5, f"the Platonic is a whole volume ({filled[2]})")
    for src, shape in (("POINTS", "points"), ("EDGES", "tube"), ("SURFACE", "text"), ("FILLED", "solid")):
        row.source = src
        update()
        check(V._input_shape(plat, row) == shape, f"Volume From {src} reads it as {shape}")
        s = surface(vm)
        if src == "FILLED":
            check(s[1] == filled[1], "back to Filled it is the same volume again")
        else:
            check(s[1] > 0 and s != filled, f"Volume From {src} makes a different volume ({s[1]} faces)")
    row.source = "POINTS"
    small = surface(vm)
    row_change(vb, row, "radius", 30.0, "Radius (Volume From Points)", vm)
    row.source = "FILLED"
    vm.select_set(False)
    vb.parent, vm.parent = None, None
    bpy.data.objects.remove(vm)
    update()
    me = bpy.data.meshes.new("Edges")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [(0, 1), (1, 2)], [])
    wire = bpy.data.objects.new("Edges", me)
    bpy.context.collection.objects.link(wire)
    check(V._auto_shape(wire) == "tube", "a mesh with only edges becomes tubes")
    bpy.data.objects.remove(wire)
    # viewport: a surface by default, voxels, or the old fog; only drawn in the viewport, never rendered
    check(vb.b4d_vol_view == "SURFACE", "the builder shows its surface by default")
    nodes = [n.bl_idname for n in V.mod_of(vb).node_group.nodes]
    check("GeometryNodeIsViewport" in nodes and "GeometryNodeGridToMesh" in nodes, "a surface made only for the viewport")
    s = surface(vb)
    check(s[1] > 200, f"the builder itself shows a solid surface ({s[1]} faces)")
    check(V.GRID in grids(vb), "and still carries its distance grid")
    check(grids(vb)[0] == "density", "the grid the viewport draws comes first (not the distance grid as a haze)")
    vb.b4d_vol_view = "VOXELS"
    v = surface(vb)
    check(v[1] > 0 and v[1] % 6 == 0, f"Voxels shows cubes ({v[1] // 6})")
    vb.b4d_vol_view = "FOG"
    check(surface(vb)[1] == 0 and grids(vb)[0] == "density", "Fog shows only the fog, drawn from its density grid")
    vb.b4d_vol_view = "SURFACE"
    update()


def bounds(ob):
    update()
    ev = ob.evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = ev.to_mesh()
    vs = [v.co.copy() for v in me.vertices]
    ev.to_mesh_clear()
    if not vs:
        return None
    return [round(min(v[k] for v in vs), 2) for k in range(3)], [round(max(v[k] for v in vs), 2) for k in range(3)]


def test_filter_layers_and_fields():
    print("== filter layers in the list, limited by fields")
    clear()
    vb = add("volume_builder", [cube("Block", 2.0)])
    base = bounds(vb)
    check(base and abs(base[1][0] - 1.0) < 0.06, f"the block alone ({base})")
    V.put(vb, "Voxel Size", 3.0)
    small = bounds(vb)
    check(small is not None and abs(small[1][0] - 1.0) < 0.05, f"under 4 cm voxels it still shows ({small})")
    V.put(vb, "Voxel Size", 5.0)
    vb.b4d_vol_index = 0
    bpy.ops.blenda4d.volume_add_layer(owner=vb.name, effect="GROW")
    lay = vb.b4d_vol_items[1]
    check(lay.layer == "GROW" and lay.obj is None, "a Grow / Shrink layer goes in under the chosen row")
    lay.grow = 30.0
    vb.update_tag()
    update()
    grown = bounds(vb)
    check(grown[1][0] > 1.25 and grown[0][0] < -1.25, f"with no fields it grows everywhere ({grown})")
    check(len(vb.b4d_vol_items) == 2, "the layer stays in the list while the list follows the children")
    # a spherical field on the +X side: only there it grows
    for o in bpy.context.selected_objects:
        o.select_set(False)
    bpy.ops.blenda4d.add_kind(kind="fd_sphere")
    fld = bpy.context.view_layer.objects.active
    fld.location = (1.2, 0.0, 0.0)
    fld.b4d_fd_radius = 80.0
    lay.fields.add().obj = fld
    update()
    part = bounds(vb)
    check(part[1][0] > 1.2, f"inside the field it grows ({part[1][0]})")
    check(part[0][0] > -1.06, f"away from the field it doesn't ({part[0][0]})")
    fld.location = (-1.2, 0.0, 0.0)
    update()
    moved = bounds(vb)
    check(moved[0][0] < -1.2 and moved[1][0] < 1.06, f"moving the field moves where it grows ({moved[0][0]}, {moved[1][0]})")
    lay.fields[0].opacity = 0.0
    update()
    check(bounds(vb)[0][0] > -1.06, "a field at 0% opacity lets nothing through")
    lay.fields[0].opacity = 100.0
    lay.use = False
    update()
    check(abs(bounds(vb)[0][0] + 1.0) < 0.06, "a layer switched off does nothing")
    lay.use = True
    update()
    # a Smooth layer after it, then the whole thing meshed
    vb.b4d_vol_index = 1
    bpy.ops.blenda4d.volume_add_layer(owner=vb.name, effect="SMOOTH")
    check([r.layer for r in vb.b4d_vol_items] == ["", "GROW", "SMOOTH"], "layers stack in list order")
    sm = vb.b4d_vol_items[2]
    sm.smooth = 6
    vm = add("volume_mesher", [vb])
    a = surface(vm)
    sm.smooth = 0
    vb.update_tag()
    check(surface(vm) != a, "the Smooth layer's Passes change the surface")
    sig = list(vb.get("b4d_vol_sig", []))
    check(any(x.startswith("L:SMOOTH") for x in sig) and any(fld.name in x for x in sig), "layers and their fields are in the signature")
    bpy.ops.blenda4d.volume_remove_layer(owner=vb.name, index=2)
    check([r.layer for r in vb.b4d_vol_items] == ["", "GROW"], "a layer can be taken out")


for t in (test_builder_and_mesher, test_splines_points_and_nesting, test_text_and_fog, test_volume_from_and_viewport, test_filter_layers_and_fields, test_switch_off):
    try:
        t()
    except Exception:
        traceback.print_exc()
        fails.append(f"{t.__name__} raised")
print(f"B4D VOLUME OK {passed[0]} FAILED {len(fails)}")
for f in fails:
    print("  -", f)
sys.exit(1 if fails else 0)
