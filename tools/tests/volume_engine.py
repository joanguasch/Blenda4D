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
from blenda4d import coords, generators as G, kinds, volume as V  # noqa: E402

bpy.utils.register_class(kinds.BLENDA4D_OT_add_kind)
coords.register()
G.register()
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


for t in (test_builder_and_mesher, test_splines_points_and_nesting, test_text_and_fog, test_switch_off):
    try:
        t()
    except Exception:
        traceback.print_exc()
        fails.append(f"{t.__name__} raised")
print(f"B4D VOLUME OK {passed[0]} FAILED {len(fails)}")
for f in fails:
    print("  -", f)
sys.exit(1 if fails else 0)
