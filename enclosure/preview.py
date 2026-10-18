#!/usr/bin/env python3
"""
Renders preview images of the enclosure parts and how they go together.

    enclosure/.venv/bin/python enclosure/preview.py

Writes PNGs to enclosure/preview/. Uses a small software renderer (numpy + Pillow)
so it needs nothing beyond what build_enclosure.py already uses.
"""

import math
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_enclosure as be  # noqa: E402
from build_enclosure import box, cyl  # noqa: E402

OUT = os.path.join(be.HERE, "preview")

COL = dict(
    frame=(92, 104, 122), cover=(176, 184, 196), panel=(34, 36, 42), led=(70, 200, 150),
    tab=(236, 138, 52), shim=(244, 204, 84), rail=(74, 142, 224), foot=(112, 176, 120),
    screw=(214, 216, 222), esp=(48, 120, 86),
)


def font(size):
    for path in ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/SFNS.ttf",
                 "/Library/Fonts/Arial.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


# ---------------------------------------------------------------- 3D renderer

def camera(direction, up=(0, 1, 0)):
    f = np.array(direction, dtype=float)
    f /= np.linalg.norm(f)
    r = np.cross(f, np.array(up, dtype=float))
    r /= np.linalg.norm(r)
    u = np.cross(r, f)
    return r, u, f


def render(scene, direction, up=(0, 1, 0), size=(1500, 1050), ss=2, margin=0.06, path=None, labels=()):
    """scene: list of (Manifold, rgb). Orthographic, flat shaded, with outline edges."""
    r, u, f = camera(direction, up)
    W, H = size[0] * ss, size[1] * ss
    tris, cols = [], []
    for m, rgb in scene:
        mesh = m.to_mesh()
        v = np.asarray(mesh.vert_properties, dtype=np.float64)[:, :3]
        t = v[np.asarray(mesh.tri_verts, dtype=np.int64)]
        tris.append(t)
        cols.append(np.tile(np.array(rgb, dtype=np.float64), (len(t), 1)))
    T = np.vstack(tris)
    C = np.vstack(cols)
    part = np.concatenate([np.full(len(t), i) for i, t in enumerate(tris)])
    n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    ln = np.linalg.norm(n, axis=1)
    keep = ln > 1e-12
    T, C, part, n, ln = T[keep], C[keep], part[keep], n[keep], ln[keep]
    n = n / ln[:, None]
    facing = n @ f < 0                       # drop faces pointing away from the camera
    T, C, part, n = T[facing], C[facing], part[facing], n[facing]

    P = np.stack([T @ r, T @ u, T @ f], axis=-1)        # camera space
    lo, hi = P[..., :2].reshape(-1, 2).min(0), P[..., :2].reshape(-1, 2).max(0)
    span = hi - lo
    scale = min(W * (1 - 2 * margin) / span[0], H * (1 - 2 * margin) / span[1])
    off = np.array([W, H]) / 2 - scale * (lo + hi) / 2
    X = P[..., 0] * scale + off[0]
    Y = H - (P[..., 1] * scale + off[1])
    Z = P[..., 2]

    light = -f + 0.55 * u - 0.45 * r
    light /= np.linalg.norm(light)
    shade = 0.42 + 0.58 * np.clip(n @ light, 0, 1)

    zbuf = np.full((H, W), np.inf, dtype=np.float32)
    ibuf = np.full((H, W), -1, dtype=np.int32)
    for i in range(len(T)):
        x0, x1, x2 = X[i]
        y0, y1, y2 = Y[i]
        ax, bx = max(int(math.floor(min(x0, x1, x2))), 0), min(int(math.ceil(max(x0, x1, x2))), W - 1)
        ay, by = max(int(math.floor(min(y0, y1, y2))), 0), min(int(math.ceil(max(y0, y1, y2))), H - 1)
        if ax > bx or ay > by:
            continue
        den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(den) < 1e-9:
            continue
        gx = np.arange(ax, bx + 1) + 0.5
        gy = (np.arange(ay, by + 1) + 0.5)[:, None]
        w0 = ((y1 - y2) * (gx - x2) + (x2 - x1) * (gy - y2)) / den
        w1 = ((y2 - y0) * (gx - x2) + (x0 - x2) * (gy - y2)) / den
        w2 = 1 - w0 - w1
        z = w0 * Z[i, 0] + w1 * Z[i, 1] + w2 * Z[i, 2]
        sub = zbuf[ay:by + 1, ax:bx + 1]
        upd = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6) & (z < sub)
        sub[upd] = z[upd]
        ibuf[ay:by + 1, ax:bx + 1][upd] = i

    hit = ibuf >= 0
    img = np.full((H, W, 3), 255.0)
    idx = ibuf[hit]
    img[hit] = C[idx] * shade[idx][:, None]

    # outline: where neighbouring pixels belong to different surfaces
    nb = np.zeros((H, W, 3))
    nb[hit] = n[idx]
    pb = np.full((H, W), -1)
    pb[hit] = part[idx]
    zb = np.where(hit, zbuf, 1e9)
    edge = np.zeros((H, W), dtype=bool)
    for ax_ in (0, 1):
        a = [slice(None), slice(None)]
        b = [slice(None), slice(None)]
        a[ax_], b[ax_] = slice(1, None), slice(None, -1)
        a, b = tuple(a), tuple(b)
        d = (np.sum(nb[a] * nb[b], axis=-1) < 0.90) | (pb[a] != pb[b]) | (np.abs(zb[a] - zb[b]) > 1.2)
        d &= hit[a] | hit[b]
        edge[a] |= d
    img[edge] *= 0.30

    out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).resize(size, Image.LANCZOS)
    if labels:
        dr = ImageDraw.Draw(out)
        fnt = font(22)
        for text, world in labels:
            wpt = np.array(world, dtype=float)
            px = (wpt @ r * scale + off[0]) / ss
            py = (H - (wpt @ u * scale + off[1])) / ss
            tw = dr.textlength(text, font=fnt)
            dr.rounded_rectangle([px - tw / 2 - 8, py - 16, px + tw / 2 + 8, py + 16], 6, fill=(255, 255, 255), outline=(60, 60, 60))
            dr.text((px - tw / 2, py - 12), text, fill=(20, 20, 20), font=fnt)
    if path:
        out.save(path)
    return out


# ---------------------------------------------------------------- assembly

def screw(x, y, z_under_head, length, head_d=5.6, head_h=2.2):
    return cyl(x, y, z_under_head - length, z_under_head, 3.0, 24) + cyl(x, y, z_under_head, z_under_head + head_h, head_d, 32)


def assembly(p, explode=0.0, with_cover=True):
    """Everything in frame coordinates. `explode` spreads the parts out along Z."""
    T = p.panel_t
    zp = p.lip_t + T                       # panel back plane
    proud = zp - p.Z1
    stack = be.shim_stack(p, proud)
    s = []
    s.append((be.build_frame(p), COL["frame"]))
    e1, e2, e3, e4 = (explode * k for k in (1.0, 1.9, 2.5, 3.6))
    s.append((box(-p.panel / 2, p.panel / 2, -p.panel / 2, p.panel / 2, p.lip_t, zp).translate((0, 0, e1)), COL["panel"]))
    for sx in (-1, 1):
        for sy in (-1, 1):
            z = p.Z1
            for t in stack:
                s.append((be.place_corner(be.build_shim(p, t), p, sx, sy, z + e2), COL["shim"]))
                z += t
            if sy > 0:
                s.append((be.place_corner(be.build_tab(p), p, sx, sy, zp + e3), COL["tab"]))
                s.append((screw(sx * p.hx, sy * p.hx, zp + p.tab_t + e3 + explode * 0.5, 12), COL["screw"]))
            else:
                s.append((screw(sx * p.hx, sy * p.hx, zp + p.rail_t + e3 + explode * 0.5, 12), COL["screw"]))
    s.append((be.build_rail(p).translate((0, 0, zp + e3)), COL["rail"]))
    if with_cover:
        cover = be.build_cover(p).mirror((1, 0, 0)).rotate((0, 180, 0)).translate((0, 0, p.Z2 + p.cover_t + e4))
        s.append((cover, COL["cover"]))
        for sx in (-1, 1):
            for sy in (-1, 1):
                s.append((screw(sx * p.xb, sy * p.boss_y, p.Z2 + p.cover_t + e4 + explode * 0.4, 10), COL["screw"]))
    return s


def on_feet(p):
    """Closed frame sitting in its two feet. World: X across, Y up, Z depth."""
    g = be.foot_geometry(p)
    th = math.radians(p.foot_tilt)
    c, sn = math.cos(th), math.sin(th)
    off = (g.D - p.depth_assembled) / 2
    frame_m = np.array([[1, 0, 0, 0],
                        [0, c, -sn, g.A[1] - off * sn + p.O * c],
                        [0, sn, c, g.A[0] + off * c + p.O * sn]], dtype=np.float64)
    scene = [(m.transform(frame_m), col) for m, col in assembly(p)]
    foot = be.build_foot(p)
    for xc in (-75.0, 75.0):
        foot_m = np.array([[0, 0, 1, xc - p.foot_w / 2], [0, 1, 0, 0], [1, 0, 0, 0]], dtype=np.float64)
        scene.append((foot.transform(foot_m), COL["foot"]))
    return scene


# ---------------------------------------------------------------- section drawings

def section_polys(m, yaw=0.0, y0=0.0):
    """Cut `m` with a vertical plane through the Z axis (rotated `yaw` degrees) and
    return loops as (radial distance, z)."""
    cs = m.rotate((0, 0, -yaw)).rotate((90, 0, 0)).slice(y0)
    return [np.column_stack([np.asarray(q)[:, 0], -np.asarray(q)[:, 1]]) for q in cs.to_polygons()]


def draw_section(p, title, yaw, y0, xr, items, notes, path, scale=26):
    """items: list of (Manifold, rgb, outline?)"""
    x0, x1 = xr
    z0, z1 = -4.0, p.Z2 + p.cover_t + 7.0
    pad_l, pad_t, pad_r = 90, 90, 620
    W = int((x1 - x0) * scale) + pad_l + pad_r
    H = int((z1 - z0) * scale) + pad_t + 60
    img = Image.new("RGB", (W, H), (255, 255, 255))
    dr = ImageDraw.Draw(img)

    def px(x, z):          # front of the frame (the print bed) at the bottom
        return (pad_l + (x - x0) * scale, pad_t + (z1 - z) * scale)

    dr.text((pad_l, 22), title, fill=(15, 15, 15), font=font(34))
    dr.line([px(x0, 0), px(x1, 0)], fill=(150, 150, 150), width=2)
    dr.text((px(x0, 0)[0], px(x0, 0)[1] + 8), "front face (sits on the print bed)", fill=(110, 110, 110), font=font(20))
    for m, rgb in items:
        for poly in section_polys(m, yaw, y0):
            keep = (poly[:, 0].max() > x0) and (poly[:, 0].min() < x1)
            if not keep or len(poly) < 3:
                continue
            pts = [px(max(min(a, x1 + 3), x0 - 3), b) for a, b in poly]
            dr.polygon(pts, fill=rgb, outline=(25, 25, 25))
    fnt = font(21)
    order = sorted(notes, key=lambda n: -n[1][1])
    rows, last = [], None
    for text, (ax, az), _ in order:
        tz = az if last is None else min(az, last - 2.4)
        rows.append((text, ax, az, tz))
        last = tz
    for text, ax, az, tz in rows:
        a, t = px(ax, az), (pad_l + (x1 - x0) * scale + 36, pad_t + (z1 - tz) * scale - 12)
        dr.line([a, (t[0] - 8, t[1] + 12)], fill=(190, 40, 40), width=2)
        dr.ellipse([a[0] - 4, a[1] - 4, a[0] + 4, a[1] + 4], fill=(190, 40, 40))
        dr.text(t, text, fill=(20, 20, 20), font=fnt)
    img.save(path)
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    p = be.derive(be.PARAMS)
    frame = be.build_frame(p)

    # 1. frame from the back (the side the panel goes in)
    render([(frame, COL["frame"])], direction=(0.45, -0.55, -0.75), path=os.path.join(OUT, "1_frame_back.png"),
           labels=[("cable notch", (0, -p.O, p.Z2 + 4)), ("cover screw boss", (p.xb - 14, p.boss_y, p.Z2 + 6)),
                   ("tab screw hole + head relief", (-p.hx + 34, -p.hx + 2, p.Z1 + 6)),
                   ("ledge", (20, -(p.Pk + 2.5), p.Z1)), ("panel goes in here", (0, 10, p.lip_t))])
    # 2. frame from the front
    render([(frame, COL["frame"])], direction=(-0.35, -0.3, 1.0), path=os.path.join(OUT, "2_frame_front.png"))
    # 3. exploded view from the back
    render(assembly(p, explode=26.0), direction=(0.62, -0.5, -0.62), size=(1700, 1250),
           path=os.path.join(OUT, "3_exploded_back.png"))
    # 4. assembled, cover off, from the back
    render(assembly(p, with_cover=False), direction=(0.3, -0.45, -0.85), size=(1500, 1250),
           path=os.path.join(OUT, "4_inside_cover_off.png"))
    # 5. closed, standing on its feet
    render(on_feet(p), direction=(-0.75, -0.3, -0.6), path=os.path.join(OUT, "5_on_feet_back.png"))
    render(on_feet(p), direction=(0.6, -0.25, 0.75), path=os.path.join(OUT, "6_on_feet_front.png"))
    render(on_feet(p), direction=(1.0, 0.0, 0.0), size=(1100, 1000), path=os.path.join(OUT, "7_on_feet_side.png"))
    # 6. back cover, outside face
    cover = be.build_cover(p)
    render([(cover, COL["cover"])], direction=(0.25, -0.45, -1.0), path=os.path.join(OUT, "8_back_cover_inside.png"),
           labels=[("wall-hang keyholes", (0, p.key_y - 12, 0)), ("vents", (0, -p.vent_y + 14, 0)), ("cable notch", (0, -p.O + 14, 0)), ("locating rib", (40, p.C - 8, p.cover_t + p.rib_h))])
    # 7. small parts and the rail, as they come off the printer
    render([(be.build_small_parts_plate(p), COL["tab"])], direction=(0.35, 0.55, -0.75), size=(1100, 1200),
           path=os.path.join(OUT, "9_tabs_and_shims_plate.png"))
    render([(be.build_rail(p), COL["rail"])], direction=(0.25, -0.6, -0.75), size=(1700, 800),
           path=os.path.join(OUT, "10_rail_top.png"))
    render([(be.build_rail(p), COL["rail"])], direction=(0.25, -0.6, 0.75), size=(1700, 800),
           path=os.path.join(OUT, "11_rail_underside.png"))

    # 8. section drawings (what touches what)
    T = p.panel_t
    zp = p.lip_t + T
    stack = be.shim_stack(p, zp - p.Z1)
    panel = box(-p.panel / 2, p.panel / 2, -p.panel / 2, p.panel / 2, p.lip_t, zp)
    cover_a = cover.mirror((1, 0, 0)).rotate((0, 180, 0)).translate((0, 0, p.Z2 + p.cover_t))
    items = [(frame, (150, 158, 172)), (panel, (52, 56, 64)), (cover_a, (200, 206, 214))]
    draw_section(p, "Section through a side wall", 0.0, 0.0, (88.0, 106.0), items, [
        ("back cover", (99.0, p.Z2 + p.cover_t / 2), (0, p.Z2 + 4)),
        (f"cavity wall, {p.wall} mm", (p.C + p.wall / 2, p.Z1 + 22), (0, p.Z1 + 26)),
        (f"cavity: {p.cavity_depth:.0f} mm deep for the ESP32 + wiring", (95.0, p.Z1 + 14), (0, p.Z1 + 19)),
        (f"ledge at {p.Z1} mm, {p.band:.0f} mm wide", (p.Pk + 2.5, p.Z1), (0, p.Z1 + 8)),
        (f"panel back stands {zp - p.Z1:.1f} mm proud of the ledge", (93.0, zp), (0, p.Z1 + 2)),
        (f"panel ({T} mm thick here)", (91.0, p.lip_t + T / 2), (0, 9.5)),
        (f"{p.panel_clear} mm gap each side", (p.Pk - p.panel_clear / 2, 6.0), (0, 5.5)),
        (f"lip: covers {(p.panel - 2 * p.L) / 2:.1f} mm of the panel edge", (p.L + 0.3, p.lip_t - 0.4), (0, 1.5)),
    ], os.path.join(OUT, "12_section_wall.png"))

    items = [(frame, (150, 158, 172)), (panel, (52, 56, 64)), (cover_a, (200, 206, 214))]
    z = p.Z1
    for t in stack:
        items.append((be.place_corner(be.build_shim(p, t), p, 1, 1, z), COL["shim"]))
        z += t
    items.append((be.place_corner(be.build_tab(p), p, 1, 1, zp), COL["tab"]))
    items.append((screw(p.hx, p.hx, zp + p.tab_t, 12), COL["screw"]))
    d = p.hx * math.sqrt(2)
    draw_section(p, "Section diagonally through a corner", 45.0, 0.0, (d - 16.0, d + 9.0), items, [
        ("back cover", (d - 6, p.Z2 + p.cover_t / 2), (0, p.Z2 + 4)),
        ("round relief in the corner so the screw head fits", (d + 3.0, p.Z1 + 20), (0, p.Z1 + 26)),
        ("M3 screw, self-taps into the frame", (d, zp + p.tab_t + 1.0), (0, p.Z1 + 20)),
        ("corner tab clamps the panel's back corner", (d - 7.0, zp + p.tab_t / 2), (0, p.Z1 + 14)),
        (f"shims ({' + '.join(str(s) for s in stack)} mm) make up the height", (d + 1.9, p.Z1 + sum(stack) / 2), (0, p.Z1 + 8)),
        (f"screw hole stops {p.Z1 - p.corner_hole_depth:.1f} mm short of the front", (d, p.Z1 - p.corner_hole_depth), (0, 6.0)),
        ("panel", (d - 12.0, p.lip_t + T / 2), (0, 2.0)),
    ], os.path.join(OUT, "13_section_corner.png"))

    cut = p.boss_y
    items = [(frame, (150, 158, 172)), (panel, (52, 56, 64)), (cover_a, (200, 206, 214)),
             (screw(p.xb, p.boss_y, p.Z2 + p.cover_t, 10), COL["screw"])]
    draw_section(p, "Section through a cover screw boss", 0.0, cut, (88.0, 106.0), items, [
        ("M3x10 cover screw", (p.xb, p.Z2 + p.cover_t + 1.0), (0, p.Z2 + 4)),
        ("boss: flush with the pocket wall, so the panel slides past", (p.Pk + 1.2, p.Z1 + 16), (0, p.Z1 + 24)),
        (f"{p.boss_hole_in - p.pilot_d / 2:.2f} mm of plastic inside the hole", (p.xb - 2.2, p.Z2 - 6), (0, p.Z1 + 18)),
    ], os.path.join(OUT, "14_section_boss.png"))
    print("wrote", len(os.listdir(OUT)), "images to", OUT)


if __name__ == "__main__":
    main()
