#!/usr/bin/env python3
"""
Parametric generator for the Spotify vinyl display enclosure.

Fits a generic P3 64x64 HUB75 LED panel (192 x 192 mm face) and prints in one
piece on a Prusa MK3 (250 x 210 mm bed).

Run from the project root:
    enclosure/.venv/bin/python enclosure/build_enclosure.py

Writes STL files to enclosure/stl/ and a self-check report to enclosure/report.json.
Change a number in PARAMS (or pass key=value on the command line) and re-run.

Coordinates: X right, Y up, Z from the FRONT face (Z = 0, the face that sits on
the print bed) toward the back. The panel drops in from the back and rests
against a thin front lip. All dimensions are millimetres.

How the fit works, front to back:
    lip      Z 0 .. lip_t            thin rim the panel face rests against
    pocket   lip_t .. Z1             192.8 mm square the panel sits in
    ledge    at Z1                   5 mm wide shelf all round, behind the panel edge
    cavity   Z1 .. Z2                room for the ESP32 and wiring
    cover    sits on the rim at Z2

The panel is deliberately THICKER than the pocket is deep, so its back stands
proud of the ledge. Corner tabs screwed into the ledge clamp it, with stackable
shims under each tab making up the height. That is why the frame does not care
exactly how thick the panel is.
"""

import json
import math
import os
import struct
import sys
from types import SimpleNamespace

import numpy as np
from manifold3d import CrossSection, JoinType, Manifold

HERE = os.path.dirname(os.path.abspath(__file__))

PARAMS = dict(
    # ---- the panel ----
    panel=192.0,            # nominal face size (real ones measure ~0.3 mm under)
    panel_clear=0.4,        # gap per side between panel and pocket wall
    panel_t=14.5,           # body thickness used for checks/previews only (suppliers list 14-15)
    # ---- frame walls ----
    band=5.0,               # ledge width behind the panel edge
    wall=2.4,               # cavity wall thickness
    r_out=4.0,              # outer corner radius
    lip_overlap=1.2,        # how far the front lip reaches in from the pocket wall
    lip_t=1.6,              # lip thickness
    lip_chamfer=0.8,        # 45 degree chamfer on the lip's front inner edge
    front_chamfer=0.8,      # 45 degree chamfer round the outer front edge
    pocket_depth=12.0,      # must stay LESS than the panel thickness
    cavity_depth=36.0,      # ledge to rim
    # ---- screws (all M3 machine screws, head 6 mm or smaller) ----
    pilot_d=2.7,            # hole an M3 screw self-taps into
    clear_d=3.4,            # hole an M3 screw passes through
    corner_inset=2.5,       # tab screw position, measured out from the pocket corner
    corner_hole_depth=10.0,
    head_relief_r=3.3,      # round pocket in the cavity corner so the screw head fits
    boss_y=65.0,            # cover screw bosses sit on the left/right walls at +-this
    boss_w=10.0,
    boss_hole_in=3.4,       # cover screw position, measured out from the pocket wall
    boss_hole_depth=12.0,
    # ---- power cable exit (bottom wall, centre) ----
    notch_w=6.5,
    notch_d=7.5,
    # ---- back cover ----
    cover_t=2.4,
    cover_chamfer=0.6,
    rib_h=2.0,              # locating ribs on the inside of the cover (top and bottom edges)
    rib_t=1.6,
    rib_clear=0.3,
    rib_half_len=88.0,
    key_xs=(-50.0, 0.0, 50.0),  # wall-hang keyholes
    key_y=62.0,
    key_head_d=9.5,
    key_slot_w=4.6,
    key_slot_len=9.0,
    vent_w=3.0,
    vent_l=12.0,
    vent_pitch=8.0,
    vent_n=13,
    vent_y=86.0,
    # ---- corner tabs and shims ----
    tab_reach=9.0,          # how far a tab reaches over the panel corner
    tab_edge_clear=0.4,     # gap between tab and cavity wall
    tab_t=3.0,
    tab_corner_r=2.2,
    lobe_r=3.0,             # round lobe under the screw head
    shims=(0.4, 0.6, 0.8, 1.0, 2.0, 3.0),   # stack to any height in 0.2 mm steps
    shim_hole_d=3.6,
    shim_gap=0.3,           # gap between shim and the panel edge
    # ---- electronics rail (optional, replaces the two bottom tabs) ----
    rail_t=3.4,
    deck_half_w=78.0,
    deck_inner_y=-46.0,
    peg_d=3.6,
    peg_pitch=8.0,
    groove_w=3.8,
    groove_d=1.6,
    win_x0=-70.0, win_x1=-10.0, win_y0=-76.0, win_y1=-56.0,  # opening the ESP32 sits over
    # ---- desk feet ----
    foot_tilt=10.0,         # degrees the frame leans back
    foot_w=22.0,
    foot_base=8.0,          # lowest point of the frame above the desk (cable clearance)
    foot_lip_f=5.0, foot_tf=3.0,
    foot_lip_r=14.0, foot_tr=4.0,
    foot_back=45.0,
    foot_clear=0.8,
    # ---- printer ----
    bed=(250.0, 210.0, 210.0),
    seg=64,                 # facets per full circle
)


def derive(params):
    p = SimpleNamespace(**params)
    p.pocket = p.panel + 2 * p.panel_clear
    p.Pk = p.pocket / 2                      # half pocket
    p.C = p.Pk + p.band                      # half cavity
    p.O = p.C + p.wall                       # half outer
    p.outer = 2 * p.O
    p.L = p.Pk - p.lip_overlap               # half lip opening
    p.Z1 = p.lip_t + p.pocket_depth          # ledge height
    p.Z2 = p.Z1 + p.cavity_depth             # rim height
    p.r_cav = max(p.r_out - p.wall, 0.2)
    p.hx = p.Pk + p.corner_inset             # tab screw centre (abs, both axes)
    p.xb = p.Pk + p.boss_hole_in             # cover screw centre (abs x)
    p.depth_assembled = p.Z2 + p.cover_t
    return p


# ---------------------------------------------------------------- helpers

def rrect(w, h, r, seg=64):
    """Centred rectangle with rounded corners."""
    if r <= 0.01:
        return CrossSection.square((w, h), True)
    return CrossSection.square((w - 2 * r, h - 2 * r), True).offset(r, JoinType.Round, 2.0, seg)


def rect(x0, x1, y0, y1):
    return CrossSection.square((x1 - x0, y1 - y0), False).translate((x0, y0))


def disc(x, y, d, seg=48):
    return CrossSection.circle(d / 2, seg).translate((x, y))


def prism(cs, z0, z1):
    return Manifold.extrude(cs, z1 - z0).translate((0, 0, z0))


def box(x0, x1, y0, y1, z0, z1):
    return Manifold.cube((x1 - x0, y1 - y0, z1 - z0)).translate((x0, y0, z0))


def cyl(x, y, z0, z1, d, seg=48):
    return Manifold.cylinder(z1 - z0, d / 2, d / 2, seg).translate((x, y, z0))


def loft(cs_low, z_low, cs_high, z_high):
    """Convex solid joining two convex outlines at different heights (used for chamfers)."""
    pts = []
    for cs, z in ((cs_low, z_low), (cs_high, z_high)):
        for poly in cs.to_polygons():
            a = np.asarray(poly, dtype=np.float64)
            pts.append(np.column_stack([a, np.full(len(a), z)]))
    return Manifold.hull_points(np.vstack(pts))


def union(items):
    out = items[0]
    for m in items[1:]:
        out = out + m
    return out


# ---------------------------------------------------------------- parts

def build_frame(p):
    outer = rrect(p.outer, p.outer, p.r_out, p.seg)
    c = p.front_chamfer
    low = rrect(p.outer - 2 * c, p.outer - 2 * c, max(p.r_out - c, 0.2), p.seg)
    body = loft(low, 0.0, outer, c) + prism(outer, c, p.Z2)

    e = 0.01
    # lip opening: flares out toward the front so the edge pixels are not shadowed
    lc = p.lip_chamfer
    flare = loft(CrossSection.square((2 * (p.L + lc + e),) * 2, True), -e,
                 CrossSection.square((2 * p.L,) * 2, True), lc)
    body = body - flare - box(-p.L, p.L, -p.L, p.L, lc - e, p.lip_t + e)
    # pocket the panel sits in
    body = body - box(-p.Pk, p.Pk, -p.Pk, p.Pk, p.lip_t, p.Z1 + e)
    # cavity behind the panel
    body = body - prism(rrect(2 * p.C, 2 * p.C, p.r_cav, p.seg), p.Z1, p.Z2 + e)

    # cover screw bosses on the left and right walls (clear of the panel's path)
    for sx in (-1, 1):
        for sy in (-1, 1):
            x0, x1 = sorted((sx * p.Pk, sx * (p.C + 0.6)))
            yc = sy * p.boss_y
            body = body + box(x0, x1, yc - p.boss_w / 2, yc + p.boss_w / 2, p.Z1 - 0.6, p.Z2)
            body = body - cyl(sx * p.xb, yc, p.Z2 - p.boss_hole_depth, p.Z2 + e, p.pilot_d)

    # tab screws at the four corners, plus a round relief so the screw head and
    # the tab's lobe clear the cavity wall
    for sx in (-1, 1):
        for sy in (-1, 1):
            x, y = sx * p.hx, sy * p.hx
            body = body - cyl(x, y, p.Z1, p.Z2 + e, 2 * p.head_relief_r, p.seg)
            body = body - cyl(x, y, p.Z1 - p.corner_hole_depth, p.Z1 + e, p.pilot_d)

    # power cable notch: bottom wall, centre, open at the rim
    body = body - box(-p.notch_w / 2, p.notch_w / 2, -(p.O + 1), -(p.C - 0.6),
                      p.Z2 - p.notch_d, p.Z2 + e)
    return body


def build_cover(p):
    """Back cover. Local Z = 0 is the OUTSIDE face (print it that way down)."""
    outer = rrect(p.outer, p.outer, p.r_out, p.seg)
    c = p.cover_chamfer
    low = rrect(p.outer - 2 * c, p.outer - 2 * c, max(p.r_out - c, 0.2), p.seg)
    plate = loft(low, 0.0, outer, c) + prism(outer, c, p.cover_t)

    cuts = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            cuts.append(disc(sx * p.xb, sy * p.boss_y, p.clear_d))
    for kx in p.key_xs:  # keyhole: big hole to pass the screw head, slot above it to hang on
        cuts.append(disc(kx, p.key_y, p.key_head_d))
        cuts.append(rect(kx - p.key_slot_w / 2, kx + p.key_slot_w / 2, p.key_y, p.key_y + p.key_slot_len))
        cuts.append(disc(kx, p.key_y + p.key_slot_len, p.key_slot_w))
    half = (p.vent_n - 1) / 2
    for i in range(p.vent_n):
        vx = (i - half) * p.vent_pitch
        for sy in (-1, 1):
            cuts.append(rrect(p.vent_w, p.vent_l, p.vent_w / 2 - 0.01, 24).translate((vx, sy * p.vent_y)))
    # cable notch matching the one in the frame's bottom wall
    cuts.append(rect(-p.notch_w / 2, p.notch_w / 2, -(p.O + 1), -(p.O - p.notch_d)))
    cut_cs = cuts[0]
    for cs in cuts[1:]:
        cut_cs = cut_cs + cs
    plate = plate - prism(cut_cs, -1, p.cover_t + 1)
    # locating ribs: drop just inside the top and bottom walls so the cover centres
    # itself. Kept off the left/right walls (bosses), the corners (screw-head
    # reliefs) and the cable notch.
    y1 = p.C - p.rib_clear
    y0 = y1 - p.rib_t
    gap = p.notch_w / 2 + 5.0
    z0, z1 = p.cover_t - 0.2, p.cover_t + p.rib_h
    plate = plate + box(-p.rib_half_len, p.rib_half_len, y0, y1, z0, z1)
    plate = plate + box(-p.rib_half_len, -gap, -y1, -y0, z0, z1)
    plate = plate + box(gap, p.rib_half_len, -y1, -y0, z0, z1)
    return plate


def tab_outline(p):
    """Corner tab outline in corner coordinates: origin = pocket corner,
    +u / +v point outward over the ledge, negative values are over the panel."""
    side = p.tab_reach + p.band - p.tab_edge_clear
    mid = (p.band - p.tab_edge_clear - p.tab_reach) / 2
    sq = rrect(side, side, p.tab_corner_r, 32).translate((mid, mid))
    return sq + disc(p.corner_inset, p.corner_inset, 2 * p.lobe_r)


def build_tab(p, grow=0.0):
    """`grow` fattens the outline; only used by the clearance checks."""
    cs = tab_outline(p)
    if grow:
        cs = cs.offset(grow, JoinType.Round, 2.0, 32)
    cs = cs - disc(p.corner_inset, p.corner_inset, p.clear_d)
    return prism(cs, 0, p.tab_t)


def build_shim(p, t, grow=0.0):
    far = p.tab_reach + 1
    over_panel = rect(-far, p.shim_gap, -far, p.shim_gap)   # keep off the panel itself
    cs = tab_outline(p) - over_panel
    if grow:
        cs = cs.offset(grow, JoinType.Round, 2.0, 32)
    cs = cs - disc(p.corner_inset, p.corner_inset, p.shim_hole_d)
    return prism(cs, 0, t)


def rail_pegs(p):
    """Tie-down hole centres on the electronics rail (frame XY coordinates)."""
    out = []
    n = int(round((2 * (p.deck_half_w - 6)) / p.peg_pitch))
    xs = [-(n * p.peg_pitch) / 2 + i * p.peg_pitch for i in range(n + 1)]
    ys = [-90.0 + k * p.peg_pitch for k in range(6)]
    m = 3.5
    for x in xs:
        for y in ys:
            if y > p.deck_inner_y - 3.5:
                continue
            if p.win_x0 - m < x < p.win_x1 + m and p.win_y0 - m < y < p.win_y1 + m:
                continue
            out.append((x, y))
    return xs, out


def build_rail(p):
    """Electronics rail, built in frame XY with its UNDERSIDE (the face that
    touches the panel back) at Z = 0. Runs along the bottom edge and replaces the
    two bottom corner tabs."""
    e = p.tab_edge_clear
    y_out = -(p.C - e)                       # outer edge, just inside the cavity wall
    y_in = -(p.Pk - p.tab_reach)             # inner edge of the bar
    x_end = p.C - e
    bar = rrect(2 * x_end, y_in - y_out, p.tab_corner_r, 32).translate((0, (y_in + y_out) / 2))
    deck = rrect(2 * p.deck_half_w, p.deck_inner_y - y_out, 3.0, 32).translate(
        (0, (p.deck_inner_y + y_out) / 2))
    cs = bar + deck
    for sx in (-1, 1):
        cs = cs + disc(sx * p.hx, -p.hx, 2 * p.lobe_r)
    for sx in (-1, 1):
        cs = cs - disc(sx * p.hx, -p.hx, p.clear_d)
    xs, pegs = rail_pegs(p)
    for (x, y) in pegs:
        cs = cs - disc(x, y, p.peg_d, 24)
    cs = cs - rrect(p.win_x1 - p.win_x0, p.win_y1 - p.win_y0, 1.5, 24).translate(
        ((p.win_x0 + p.win_x1) / 2, (p.win_y0 + p.win_y1) / 2))
    body = prism(cs, 0, p.rail_t)
    # channels on the underside so a cable tie can run from hole to hole
    for x in xs:
        body = body - box(x - p.groove_w / 2, x + p.groove_w / 2, -94.0, p.deck_inner_y + 1,
                          -0.01, p.groove_d)
    return body


def foot_geometry(p):
    """Side profile of a desk foot. u = depth (front to back), v = up."""
    th = math.radians(p.foot_tilt)
    e_up = np.array([math.sin(th), math.cos(th)])      # along the frame face, upward
    e_d = np.array([math.cos(th), -math.sin(th)])      # front of frame toward its back
    D = p.depth_assembled + p.foot_clear                # slot width
    A = np.array([0.0, p.foot_base + D * math.sin(th)])  # slot floor, front
    B = A + D * e_d                                     # slot floor, rear (lowest point)
    P1 = A - p.foot_tf * e_d + p.foot_lip_f * e_up
    P2 = A + p.foot_lip_f * e_up
    P5 = B + p.foot_lip_r * e_up
    P6 = B + p.foot_tr * e_d + p.foot_lip_r * e_up
    u_back = B[0] + p.foot_back
    u_front = P1[0] - 2.0
    pts = [(u_front, 0.0), (u_back, 0.0), (u_back, 4.0), tuple(P6), tuple(P5),
           tuple(B), tuple(A), tuple(P2), tuple(P1)]
    return SimpleNamespace(pts=pts, A=A, B=B, e_up=e_up, e_d=e_d, D=D)


def polygon(pts):
    """Cross-section from a point list, wound counter-clockwise whichever way it was given."""
    a = np.array(pts, dtype=np.float64)
    x, y = a[:, 0], a[:, 1]
    if np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y) < 0:
        a = a[::-1].copy()
    return CrossSection([a])


def build_foot(p):
    g = foot_geometry(p)
    return prism(polygon(g.pts), 0, p.foot_w)


def build_test_ring(p):
    """Thin slice of the front of the frame: checks the panel fits and the frame fits the bed."""
    return build_frame(p) ^ box(-200, 200, -200, 200, -1, p.lip_t + 2.0)


def build_test_corner(p):
    """One corner of the frame: checks the corner fit, the tab, shims and screw."""
    k = p.O - 32.0
    return build_frame(p) ^ box(k, 200, k, 200, -1, p.Z1 + 8.0)


def build_small_parts_plate(p):
    """Four corner tabs and four of each shim, laid out to print in one go.
    Row 1 = tabs, then one row per shim thickness, thinnest first."""
    pitch = p.tab_reach + p.band + 5.0
    items = []
    rows = [build_tab(p)] + [build_shim(p, t) for t in p.shims]
    for r, part in enumerate(rows):
        for c in range(4):
            items.append(part.translate((c * pitch, -r * pitch, 0)))
    return Manifold.compose(items)


def build_all(p):
    parts = {
        "01_fit_test_ring": build_test_ring(p),
        "02_fit_test_corner": build_test_corner(p),
        "03_frame": build_frame(p),
        "04_back_cover": build_cover(p),
        "05_tabs_and_shims_plate": build_small_parts_plate(p),
        # exported top-face-down so the tie channels print as open grooves
        "06_electronics_rail": build_rail(p).rotate((180, 0, 0)).translate((0, 0, p.rail_t)),
        "07_desk_foot_print_2": build_foot(p),
        "singles/corner_tab": build_tab(p),
    }
    for t in p.shims:
        parts[f"singles/shim_{t:.1f}mm"] = build_shim(p, t)
    return parts


# ---------------------------------------------------------------- assembly helpers

def place_corner(part, p, sx, sy, z):
    """Move a part built in corner coordinates to one of the four frame corners."""
    m = part
    if sx < 0:
        m = m.mirror((1, 0, 0))
    if sy < 0:
        m = m.mirror((0, 1, 0))
    return m.translate((sx * p.Pk, sy * p.Pk, z))


def shim_stack(p, proud):
    """Pick shims adding up to just under `proud`, so the tab bears on the panel
    rather than on the shims. Uses at most one of each size, fewest pieces on a tie."""
    target = proud - 0.1
    best, best_key = (), (1e9, 0)
    sizes = list(p.shims)
    for mask in range(1 << len(sizes)):
        combo = tuple(s for i, s in enumerate(sizes) if mask >> i & 1)
        tot = sum(combo)
        key = (round(target - tot, 6), len(combo))
        if tot <= target + 1e-9 and key < best_key:
            best, best_key = combo, key
    return best


# ---------------------------------------------------------------- export + checks

def write_stl(m, path):
    mesh = m.to_mesh()
    v = np.asarray(mesh.vert_properties, dtype=np.float32)[:, :3]
    f = np.asarray(mesh.tri_verts, dtype=np.int64)
    tri = v[f]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.divide(n, ln, out=np.zeros_like(n), where=ln > 0)
    rec = np.zeros(len(f), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["v"] = n, tri
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"vinyl display enclosure".ljust(80, b"\0"))
        fh.write(struct.pack("<I", len(f)))
        fh.write(rec.tobytes())
    return len(f)


def overhang_area(m, limit=0.75):
    """Area (mm2) of faces that point downward more steeply than a 45 degree
    chamfer, ignoring the face sitting on the bed. Should be ~0 for support-free parts."""
    mesh = m.to_mesh()
    v = np.asarray(mesh.vert_properties, dtype=np.float64)[:, :3]
    tri = v[np.asarray(mesh.tri_verts, dtype=np.int64)]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    area = np.linalg.norm(n, axis=1) / 2
    nz = np.divide(n[:, 2], 2 * area, out=np.zeros(len(n)), where=area > 0)
    zmin = v[:, 2].min()
    bad = (nz < -limit) & (tri[:, :, 2].mean(axis=1) > zmin + 0.05)
    return float(area[bad].sum())


def slice_bounds(m, z):
    b = m.slice(z).bounds()
    return [round(float(x), 3) for x in b]


def run_checks(p, parts):
    rep = {"checks": [], "parts": {}, "ok": True}

    def check(name, ok, detail):
        rep["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            rep["ok"] = False

    frame = parts["03_frame"]
    cover_print = parts["04_back_cover"]
    e = 1e-6

    # 1. every part is a valid closed solid that fits the bed without supports
    for name, m in parts.items():
        bb = m.bounding_box()
        size = [round(bb[3] - bb[0], 2), round(bb[4] - bb[1], 2), round(bb[5] - bb[2], 2)]
        fits = size[0] <= p.bed[0] - 2 and size[1] <= p.bed[1] - 2 and size[2] <= p.bed[2]
        oh = overhang_area(m)
        info = {"size_mm": size, "volume_cm3": round(m.volume() / 1000, 2),
                "triangles": m.num_tri(), "bodies": len(m.decompose()),
                "status": str(m.status()), "overhang_mm2": round(oh, 2), "fits_bed": fits}
        rep["parts"][name] = info
        check(f"{name}: valid solid", str(m.status()) == "Error.NoError" and m.volume() > 0, info["status"])
        check(f"{name}: fits MK3 bed with 1 mm margin each side", fits, f"{size} vs bed {list(p.bed)}")
        check(f"{name}: prints without supports", oh < 1.0, f"{oh:.2f} mm2 of steep overhang")
        check(f"{name}: sits flat on the bed", abs(bb[2]) < 1e-4, f"min z = {bb[2]:.4f}")
    for name in ("03_frame", "04_back_cover", "06_electronics_rail", "07_desk_foot_print_2",
                 "01_fit_test_ring", "02_fit_test_corner", "singles/corner_tab"):
        check(f"{name}: single connected body", rep["parts"][name]["bodies"] == 1,
              f"{rep['parts'][name]['bodies']} bodies")

    # 2. openings measured straight off the solid
    def opening(z):
        polys = sorted((np.asarray(q) for q in frame.slice(z).to_polygons()),
                       key=lambda a: np.ptp(a[:, 0]), reverse=True)
        outer, inner = polys[0], polys[1]      # biggest loop is the outside, next is the opening
        return float(np.ptp(inner[:, 0])), float(np.ptp(outer[:, 0])), float(np.ptp(outer[:, 1]))
    lip_w, out_w, out_h = opening(p.lip_t - 0.3)
    pocket_w, _, _ = opening((p.lip_t + p.Z1) / 2)
    check("outer size", abs(out_w - p.outer) < 0.01 and abs(out_h - p.outer) < 0.01, f"{out_w:.2f} x {out_h:.2f} mm")
    check("lip opening", abs(lip_w - 2 * p.L) < 0.01, f"{lip_w:.2f} mm (panel {p.panel})")
    check("pocket width", abs(pocket_w - p.pocket) < 0.01, f"{pocket_w:.2f} mm")
    check("lip does not hide more than 1 mm of panel edge", (p.panel - lip_w) / 2 <= 1.0 + e,
          f"{(p.panel - lip_w) / 2:.2f} mm per side if centred")
    check("pocket shallower than thinnest plausible panel (13 mm)", p.pocket_depth < 13.0, f"{p.pocket_depth} mm")

    # 3. nothing blocks the panel on its way in from the back, even a full-size one pushed to one side
    for dx, dy in ((0, 0), (p.panel_clear - 0.05, p.panel_clear - 0.05), (-(p.panel_clear - 0.05), p.panel_clear - 0.05)):
        path = box(-p.panel / 2 + dx, p.panel / 2 + dx, -p.panel / 2 + dy, p.panel / 2 + dy, p.lip_t + 0.001, p.Z2 + 5)
        v = (frame ^ path).volume()
        check(f"panel insertion path clear (offset {dx:+.2f},{dy:+.2f})", v < 1e-3, f"overlap {v:.4f} mm3")
    lip_hold = (frame ^ box(-p.panel / 2, p.panel / 2, -p.panel / 2, p.panel / 2, 0, p.lip_t)).volume()
    check("front lip actually retains the panel", lip_hold > 100, f"{lip_hold:.0f} mm3 of lip in front of the panel")

    # 4. corner tabs + shims + screw heads, across the plausible range of panel thickness
    tab, head_h = build_tab(p), 3.0
    for T in (13.0, 14.0, 14.5, 15.0, 18.0, 22.0):
        proud = p.lip_t + T - p.Z1
        stack = shim_stack(p, proud)
        ok_all, worst_gap, detail = True, 9.9, ""
        for sx in (-1, 1):
            for sy in (-1, 1):
                # shims rest on the ledge, so test them fattened by 0.15 mm and lifted a hair:
                # any overlap then means less than 0.15 mm of sideways clearance
                z = p.Z1 + 0.01
                for s in stack:
                    fat = place_corner(build_shim(p, s, grow=0.15), p, sx, sy, z)
                    if (fat ^ frame).volume() > 1e-3:
                        ok_all, detail = False, " shim too close to the wall"
                    z += s
                z_tab = p.lip_t + T            # tab underside rests on the panel back
                head = cyl(sx * p.hx, sy * p.hx, z_tab + p.tab_t, z_tab + p.tab_t + head_h, 6.0)
                for s in (place_corner(tab, p, sx, sy, z_tab), head):
                    if (s ^ frame).volume() > 1e-3:
                        ok_all = False
                    worst_gap = min(worst_gap, s.min_gap(frame, 2.0))
                top = z_tab + p.tab_t + head_h
                if top > p.Z2 - 0.5:
                    ok_all, detail = False, f" screw head reaches {top:.1f} > rim {p.Z2}"
        screw_len = p.tab_t + proud + 7.0
        check(f"tabs/shims/screw heads clear the frame (panel {T} mm)", ok_all and worst_gap >= 0.15,
              f"panel stands {proud:.1f} mm proud, shims {stack or 'none'}, min gap {worst_gap:.2f} mm, "
              f"screw about M3x{math.ceil(screw_len / 2) * 2}{detail}")
        panel = box(-p.panel / 2, p.panel / 2, -p.panel / 2, p.panel / 2, p.lip_t, p.lip_t + T)
        grip = sum((place_corner(tab, p, sx, sy, p.lip_t + T - 0.5) ^ panel).volume() / 0.5
                   for sx in (-1, 1) for sy in (-1, 1)) / 4
        check(f"each tab overlaps the panel corner (panel {T} mm)", grip > 60, f"{grip:.0f} mm2 per tab")
    # shims must not sit on the panel
    sh = place_corner(build_shim(p, 1.0), p, 1, 1, p.Z1)
    panel_max = box(-p.panel / 2 - 0.05, p.panel / 2 + 0.05, -p.panel / 2 - 0.05, p.panel / 2 + 0.05, 0, 100)
    check("shims stay off the panel", (sh ^ panel_max).volume() < 1e-3, "no overlap with panel footprint")

    # 5. screw holes have plastic round them and do not break through
    check("tab screw hole does not break the front face", p.Z1 - p.corner_hole_depth > 2.0,
          f"{p.Z1 - p.corner_hole_depth:.1f} mm left in front")
    check("plastic between tab screw hole and pocket corner", math.hypot(p.corner_inset, p.corner_inset) - p.pilot_d / 2 >= 2.0,
          f"{math.hypot(p.corner_inset, p.corner_inset) - p.pilot_d / 2:.2f} mm")
    check("wall left behind the screw-head relief", p.O - (p.hx + p.head_relief_r) >= 1.5,
          f"{p.O - (p.hx + p.head_relief_r):.2f} mm")
    check("plastic round cover screw hole (inside / outside)", p.boss_hole_in - p.pilot_d / 2 >= 2.0 and p.O - p.xb - p.pilot_d / 2 >= 2.0,
          f"{p.boss_hole_in - p.pilot_d / 2:.2f} / {p.O - p.xb - p.pilot_d / 2:.2f} mm")

    # 6. back cover lines up with the frame
    cover = cover_print.mirror((1, 0, 0)).rotate((0, 180, 0)).translate((0, 0, p.Z2 + p.cover_t))
    cb = cover.bounding_box()
    check("cover plate sits on the rim, ribs drop inside", abs(cb[2] - (p.Z2 - p.rib_h)) < 1e-4 and abs(cb[5] - (p.Z2 + p.cover_t)) < 1e-4,
          f"z {cb[2]:.2f}..{cb[5]:.2f} (rim at {p.Z2})")
    seat = (cover ^ box(-200, 200, -200, 200, p.Z2, p.Z2 + 0.05)).volume() / 0.05
    rim = (frame ^ box(-200, 200, -200, 200, p.Z2 - 0.05, p.Z2)).volume() / 0.05
    check("cover covers the whole rim", seat > rim, f"cover underside {seat:.0f} mm2 vs rim {rim:.0f} mm2")
    ribs = cover ^ box(-200, 200, -200, 200, p.Z2 - p.rib_h - 1, p.Z2 - 0.05)
    rib_gap = ribs.min_gap(frame, 2.0)
    check("cover ribs clear the walls sideways", ribs.volume() > 0 and rib_gap >= 0.2, f"min gap {rib_gap:.2f} mm")
    check("cover does not intersect the frame", (cover ^ frame).volume() < 1e-3, f"{(cover ^ frame).volume():.4f} mm3")
    hole_ok = True
    for sx in (-1, 1):
        for sy in (-1, 1):
            pin = cyl(sx * p.xb, sy * p.boss_y, p.Z2 - 5, p.Z2 + p.cover_t + 1, 2.4)   # an M3 screw shaft
            if (pin ^ cover).volume() > 1e-3 or (pin ^ frame).volume() > 1e-3:
                hole_ok = False
    check("cover holes line up with the frame bosses", hole_ok, "a 2.4 mm pin passes through cover and into each boss hole")
    check("cover screw head stays on the plate", p.O - p.xb >= 3.0 + 0.8, f"hole is {p.O - p.xb:.1f} mm from the edge")
    for kx in p.key_xs:
        head = cyl(kx, p.key_y, p.Z2 - 4, p.Z2 + 0.001, 9.0)
        check(f"room behind keyhole x={kx:+.0f} for a wall screw head", (head ^ frame).volume() < 1e-3, "4 mm clear inside")
    cable = box(-2.5, 2.5, -(p.O + 5), -(p.C - 3), p.Z2 - 5.5, p.Z2 - 0.5)   # a 5 mm cable through the wall
    check("5 mm power cable passes through the notch", (cable ^ frame).volume() < 1e-3, f"notch {p.notch_w} x {p.notch_d} mm")

    # 7. electronics rail in place (nominal panel), alongside the two top tabs
    rail = build_rail(p).translate((0, 0, p.lip_t + p.panel_t))
    check("rail does not intersect the frame", (rail ^ frame).volume() < 1e-3, f"{(rail ^ frame).volume():.4f} mm3")
    check("rail clears the frame walls", rail.min_gap(frame, 2.0) >= 0.15, f"min gap {rail.min_gap(frame, 2.0):.2f} mm")
    pin_ok = all((cyl(sx * p.hx, -p.hx, p.Z1 - 5, p.lip_t + p.panel_t + p.rail_t + 1, 2.4) ^ (rail + frame)).volume() < 1e-3
                 for sx in (-1, 1))
    check("rail end holes line up with the bottom corner screw holes", pin_ok, "2.4 mm pin passes")
    top_tabs = [place_corner(tab, p, sx, 1, p.lip_t + p.panel_t) for sx in (-1, 1)]
    check("rail clears the top tabs", all((t ^ rail).volume() < 1e-3 for t in top_tabs), "no overlap")
    xs, pegs = rail_pegs(p)
    check("rail has tie holes either side of the ESP32 opening",
          any(abs(y - (p.win_y0 - 6)) < 0.1 for _, y in pegs) and any(abs(y - (p.win_y1 + 6)) < 0.1 for _, y in pegs),
          f"{len(pegs)} tie holes, opening {p.win_x1 - p.win_x0:.0f} x {p.win_y1 - p.win_y0:.0f} mm")
    room = p.Z2 - (p.lip_t + p.panel_t + p.rail_t)
    check("height above the rail deck for the ESP32 + plugged jumpers (needs ~26 mm)", room >= 26.0, f"{room:.1f} mm")

    # 8. desk foot: the assembled frame drops into the slot
    g = foot_geometry(p)
    foot = parts["07_desk_foot_print_2"]
    D0 = p.depth_assembled
    off = (g.D - D0) / 2
    a = g.A + off * g.e_d
    quad = np.array([a, a + D0 * g.e_d, a + D0 * g.e_d + 60 * g.e_up, a + 60 * g.e_up]) + 0.02 * g.e_up
    block = prism(polygon(quad), -1, p.foot_w + 1)
    check("frame + cover drops into the foot slot", (block ^ foot).volume() < 1e-3 and block.volume() > 0,
          f"slot {g.D:.1f} mm for a {D0:.1f} mm deep frame")
    cg_u = g.A[0] + 0.5 * D0 * g.e_d[0] + (p.outer / 2) * g.e_up[0]
    fb = foot.bounding_box()
    check("stable on the feet (centre of mass well inside the footprint)", fb[0] + 25 < cg_u < fb[3] - 25,
          f"centre of mass at {cg_u - fb[0]:.0f} mm along a {fb[3] - fb[0]:.0f} mm foot")
    check("foot front lip stays below the LEDs", p.foot_lip_f <= p.band + p.wall + p.lip_overlap - 1.0,
          f"lip {p.foot_lip_f} mm vs {p.band + p.wall + p.lip_overlap:.1f} mm border")
    check("cable clearance under the frame on feet", p.foot_base >= 7.0, f"{p.foot_base} mm")
    return rep


def main(argv):
    params = dict(PARAMS)
    for arg in argv:
        if "=" in arg:
            k, v = arg.split("=", 1)
            if k not in params:
                sys.exit(f"unknown parameter: {k}")
            params[k] = type(params[k])(v) if not isinstance(params[k], tuple) else tuple(float(x) for x in v.split(","))
    p = derive(params)
    parts = build_all(p)
    out = os.path.join(HERE, "stl")
    for name, m in parts.items():
        n = write_stl(m, os.path.join(out, name + ".stl"))
        print(f"wrote stl/{name}.stl  ({n} triangles)")
    rep = run_checks(p, parts)
    rep["derived"] = {k: round(float(getattr(p, k)), 3) for k in
                      ("pocket", "outer", "Z1", "Z2", "depth_assembled", "hx", "xb")}
    with open(os.path.join(HERE, "report.json"), "w") as fh:
        json.dump(rep, fh, indent=1)
    bad = [c for c in rep["checks"] if not c["ok"]]
    print(f"\n{len(rep['checks']) - len(bad)}/{len(rep['checks'])} checks passed")
    for c in bad:
        print("  FAIL:", c["name"], "-", c["detail"])
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
