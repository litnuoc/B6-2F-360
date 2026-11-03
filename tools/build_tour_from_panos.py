"""
build_tour_from_panos.py
========================
Builds a static 360 walkthrough (same viewer as e57_pipeline) from
  * Cyclone REGISTER 360 panorama exports  (<station>.jpg + <station>.txt with position/orientation)
  * a scan->plan transform (scan_to_plan_transform.json)
  * a DXF floor plan, shown in the "Dollhouse" / "Floor Plan" modes instead of a 3D mesh.

Usage:
    pip install pillow numpy ezdxf matplotlib shapely
    python tools/build_tour_from_panos.py --config tools/tour_config.json      # full build (needs panorama_dir)
    python tools/build_tour_from_panos.py --update-viewer                      # re-use existing scan_stations.json:
        recompute line of sight, area names, walking order and rewrite index.html (no panoramas / no F: drive needed)

scan_stations.json fields added for the viewer:
    visible   – stations in line of sight (floor markers shown inside the panorama)
    links     – registration links = walkable connections (minimap lines)
    walkIndex – position in the walking order (W / S keys, Next / Previous, guided tour)
    area, title, description – DXF area label the station is in (layer A-AREA-IDEN)

Output (the folder you push to GitHub / GitHub Pages):
    index.html, scan_stations.json, floorplan.png, floorplan.json,
    photos/ (web), photos_hd/ (HD), thumbnails/
"""
import argparse, glob, json, math, os, re
from pathlib import Path
import numpy as np
from PIL import Image, ImageFilter

Image.MAX_IMAGE_PIXELS = None
HERE = Path(__file__).parent.resolve()


# ---------------------------------------------------------------- stations
def read_station_txt(path):
    s = open(path, encoding="utf-8", errors="ignore").read()
    pos = [float(v) for v in re.search(r"position\s*=\s*\[([^\]]+)\]", s).group(1).split(",")]
    ori = [float(v) for v in re.search(r"orientation\s*=\s*\[([^\]]+)\]", s).group(1).split(",")]
    return pos, ori  # orientation = quaternion [w, x, y, z]


def qmul(a, b):
    w1, x1, y1, z1 = a; w2, x2, y2, z2 = b
    return [w1*w2 - x1*x2 - y1*y2 - z1*z2, w1*x2 + x1*w2 + y1*z2 - z1*y2,
            w1*y2 - x1*z2 + y1*w2 + z1*x2, w1*z2 + x1*y2 - y1*x2 + z1*w2]


# ---------------------------------------------------------------- panoramas
def fill_nadir(im):
    """Cyclone/RTC360 panoramas are black below ~-60 deg. Replace the black cap with a soft
    fill blended from the surrounding floor, so the bottom of the sphere is not a black hole."""
    a = np.asarray(im).astype(np.float32)
    h, w, _ = a.shape
    dark = (a.max(2) < 6).mean(1)
    rows = np.where(dark > 0.9)[0]
    rows = rows[rows > h // 2]
    if rows.size == 0:
        return im
    b = int(rows.min())                       # first black row
    band = a[max(0, b - h // 40):b - 2]       # floor just above the hole
    col = band.mean(0)                        # per-column floor colour
    col = np.asarray(Image.fromarray(np.clip(col[None], 0, 255).astype(np.uint8))
                     .resize((w // 64, 1), Image.BILINEAR).resize((w, 1), Image.BILINEAR)).astype(np.float32)[0]
    mean = col.mean(0)
    n = h - b
    t = (np.arange(n) / max(1, n - 1))[:, None, None]
    t = t ** 0.7
    fill = col[None] * (1 - t) + mean[None, None] * t
    a[b:] = fill
    # soften the seam
    out = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    seam = out.crop((0, b - h // 60, w, min(h, b + h // 60))).filter(ImageFilter.GaussianBlur(h / 400))
    out.paste(seam, (0, b - h // 60))
    return out


def process_pano(src, name, out, icfg):
    im = Image.open(src)
    hd_w = icfg.get("hd_resolution", 4096)
    im.draft("RGB", (hd_w * 2, hd_w))          # fast JPEG decode at reduced scale
    im = im.convert("RGB")
    im = im.resize((hd_w, hd_w // 2), Image.LANCZOS)
    if icfg.get("fill_nadir", True):
        im = fill_nadir(im)
    im.save(out / "photos_hd" / f"{name}.jpg", quality=icfg.get("hd_quality", 88), optimize=True, progressive=True)
    ww = icfg.get("web_resolution", 2560)
    im.resize((ww, ww // 2), Image.LANCZOS).save(out / "photos" / f"{name}.jpg",
                                                 quality=icfg.get("web_quality", 82), optimize=True, progressive=True)
    tw = icfg.get("thumbnail_resolution", 512)
    im.resize((tw, tw // 2), Image.LANCZOS).save(out / "thumbnails" / f"{name}.jpg", quality=78)


# ---------------------------------------------------------------- floor plan
def render_floorplan(dxf_path, out, fcfg):
    import ezdxf, matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing.config import Configuration, ColorPolicy, BackgroundPolicy
    from ezdxf import path as zpath

    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    s = fcfg.get("units_to_m", 0.001)
    margin = fcfg.get("margin_m", 1.5) / s
    x0, y0, _ = doc.header["$EXTMIN"]; x1, y1, _ = doc.header["$EXTMAX"]
    x0 -= margin; y0 -= margin; x1 += margin; y1 += margin
    px_w = fcfg.get("width_px", 4096)
    px_h = int(px_w * (y1 - y0) / (x1 - x0))
    dpi = 100
    fig = plt.figure(figsize=(px_w / dpi, px_h / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_axis_off()
    bg = fcfg.get("background", "#0b0d13"); fg = fcfg.get("foreground", "#c9d3e0")
    fig.patch.set_facecolor(bg); ax.set_facecolor(bg)
    hl = fcfg.get("highlight_layer", "A-FLOR-PATT")
    # highlighted floor hatch (corridor) drawn underneath
    try:
        from shapely.geometry import Polygon
        from shapely.geometry.polygon import orient
        from matplotlib.path import Path as MPath
        from matplotlib.patches import PathPatch
        for e in msp.query(f'HATCH[layer=="{hl}"]'):
            area = None
            rings = []
            for bp in e.paths:
                v = [(q.x, q.y) for q in zpath.from_hatch_boundary_path(bp).flattening(10)]
                if len(v) > 2:
                    rings.append(Polygon(v).buffer(0))
            for P in sorted(rings, key=lambda g: -g.area):      # even-odd: islands become holes
                area = P if area is None else area.symmetric_difference(P)
            for g in getattr(area, "geoms", [area]):
                if g.is_empty or g.geom_type != "Polygon":
                    continue
                g = orient(g, 1.0)
                verts, codes = [], []
                for ring in [g.exterior, *g.interiors]:
                    c = list(ring.coords)
                    verts += c; codes += [MPath.MOVETO] + [MPath.LINETO] * (len(c) - 2) + [MPath.CLOSEPOLY]
                ax.add_patch(PathPatch(MPath(verts, codes), facecolor=fcfg.get("highlight_color", "#16c6e8"),
                                       alpha=fcfg.get("highlight_alpha", 0.16), lw=0))
    except ImportError:
        print("  (pip install shapely to highlight the corridor hatch)")
    cfg = Configuration(color_policy=ColorPolicy.CUSTOM, custom_fg_color=fg,
                        background_policy=BackgroundPolicy.OFF, lineweight_scaling=0.6)
    ctx = RenderContext(doc)
    Frontend(ctx, MatplotlibBackend(ax), config=cfg).draw_layout(
        msp, finalize=False, filter_func=lambda e: e.dxf.layer != hl)
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_aspect("auto")
    fig.savefig(out / "floorplan.png", dpi=dpi, facecolor=bg)
    plt.close(fig)
    Image.open(out / "floorplan.png").convert("RGB").save(out / "floorplan.png", optimize=True)
    meta = {"image": "floorplan.png", "minX": x0 * s, "minY": y0 * s, "maxX": x1 * s, "maxY": y1 * s,
            "z": fcfg.get("floor_z", 0.0)}
    json.dump(meta, open(out / "floorplan.json", "w"), indent=2)
    return meta



# ---------------------------------------------------------------- line of sight
def load_walls(dxf_path, vcfg, u2m):
    """Wall segments (metres) from the DXF wall layers + wall hatches, with an STRtree index."""
    import ezdxf, shapely
    from shapely.geometry import LineString
    from ezdxf import path as zpath
    doc = ezdxf.readfile(dxf_path); msp = doc.modelspace(); s = u2m
    wl = set(vcfg.get("wall_layers", ["A-WALL", "S-COLS", "I-WALL"]))
    hl = set(vcfg.get("wall_hatch_layers", ["A-WALL-PATT", "S-COLS"]))
    segs = []
    for e in msp.query("LINE"):
        if e.dxf.layer in wl:
            segs.append(LineString([(e.dxf.start.x * s, e.dxf.start.y * s), (e.dxf.end.x * s, e.dxf.end.y * s)]))
    for e in msp.query("INSERT"):
        if e.dxf.layer in wl:
            try:
                for v in e.virtual_entities():
                    if v.dxftype() == "LINE":
                        segs.append(LineString([(v.dxf.start.x * s, v.dxf.start.y * s), (v.dxf.end.x * s, v.dxf.end.y * s)]))
            except Exception:
                pass
    for e in msp.query("HATCH"):
        if e.dxf.layer in hl:
            for bp in e.paths:
                v = [(q.x * s, q.y * s) for q in zpath.from_hatch_boundary_path(bp).flattening(5)]
                if len(v) > 1:
                    segs.append(LineString(v))
    return segs, shapely.STRtree(segs)


def make_clear(walls, tol, end_margin=0.15):
    """clear(a, b) -> True when the straight line a->b (numpy 2D points, metres) crosses no wall."""
    from shapely.geometry import LineString
    segs, tree = walls

    def clear(a, b):
        d = np.linalg.norm(b - a)
        if d < 2 * end_margin + 1e-6:
            return True
        u = (b - a) / d; n = np.array([-u[1], u[0]])
        for off in (0.0, tol, -tol):          # tolerate ~0.25 m position error near wall corners
            seg = LineString([a + u * end_margin + n * off, b - u * end_margin + n * off])
            if not any(segs[k].intersects(seg) for k in tree.query(seg)):
                return True
        return False
    return clear


def compute_visibility(stations, dxf_path, vcfg, u2m, walls=None):
    """Station B is shown inside station A's panorama when the straight line A->B is not blocked
    by a wall in the DXF (doors/glass do not block), or when A-B is a registration link."""
    walls = walls or load_walls(dxf_path, vcfg, u2m)
    tol = vcfg.get("lateral_tolerance_m", 0.25)
    maxd = vcfg.get("max_distance_m", 40.0)
    P = [np.array([st["surveyPosition"]["x"], st["surveyPosition"]["y"]]) for st in stations]

    clear = make_clear(walls, tol)

    ids = [st["id"] for st in stations]
    vis = {i: set() for i in ids}
    for i in range(len(P)):
        for j in range(i + 1, len(P)):
            d = np.linalg.norm(P[j] - P[i])
            if 0.3 < d <= maxd and clear(P[i], P[j]):
                vis[ids[i]].add(ids[j]); vis[ids[j]].add(ids[i])
    for a, b in vcfg.get("links", []):          # registration links: these scans saw each other
        if a in vis and b in vis:
            vis[a].add(b); vis[b].add(a)
    for st in stations:
        st["visible"] = sorted(vis[st["id"]])
    return stations

# ---------------------------------------------------------------- area names
def _nice(text, keep_upper=3):
    """'OPEN STUDY AREA' -> 'Open Study Area'; short ASCII acronyms (BIM, WC) stay upper case."""
    words = []
    for w in text.split(" "):
        if w.isascii() and w.isalpha() and len(w) <= keep_upper and w.isupper() and w not in ("AND", "THE", "OF", "FOR", "LAB"):
            words.append(w)
        else:
            words.append("/".join(x[:1].upper() + x[1:].lower() for x in w.split("/")))
    return " ".join(words)


def read_area_labels(dxf_path, u2m, layer="A-AREA-IDEN", code_re=r"[A-Z]+\d*-[\w.]+"):
    """Room tags of the DXF: a code MTEXT (e.g. 'B6-2.CR4') with the Vietnamese name one line above
    and the English name two lines above (the Vietnamese line is split into fragments by the CAD export)."""
    import ezdxf
    msp = ezdxf.readfile(dxf_path).modelspace()
    dec = lambda t: re.sub(r"\\U\+([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), t)
    T = []
    for e in msp.query(f'MTEXT[layer=="{layer}"]'):
        t = dec(e.plain_text())                 # keep spaces: fragments are joined without separator
        if t.strip():
            T.append((t.strip() if re.fullmatch(code_re, t.strip()) else t, e.dxf.insert.x, e.dxf.insert.y))
    labels = []
    for t, x, y in T:
        if not re.fullmatch(code_re, t):
            continue
        above = [o for o in T if o[0] != t and abs(o[1] - x) < 3000 and 50 < o[2] - y < 700
                 and not re.fullmatch(code_re, o[0])]
        rows = {}
        for o in above:
            rows.setdefault(round((o[2] - y) / 40), []).append(o)
        lines = [" ".join("".join(f[0] for f in sorted(r, key=lambda f: f[1])).split())
                 for _, r in sorted(rows.items())]                     # bottom -> top
        en = _nice(lines[-1]) if lines else ""
        vi = _nice(lines[0], keep_upper=0).capitalize() if len(lines) > 1 else ""
        labels.append({"code": t, "en": en, "vi": vi, "x": x * u2m, "y": y * u2m})
    return labels


def load_floor_area(dxf_path, layer, u2m):
    """Union of the corridor / circulation hatch (e.g. A-FLOR-PATT) in metres, or None."""
    try:
        import ezdxf
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
        from ezdxf import path as zpath
    except ImportError:
        return None
    msp = ezdxf.readfile(dxf_path).modelspace()
    area = None
    for e in msp.query(f'HATCH[layer=="{layer}"]'):
        rings = [Polygon([(q.x * u2m, q.y * u2m) for q in zpath.from_hatch_boundary_path(bp).flattening(10)]).buffer(0)
                 for bp in e.paths if len(bp.vertices if hasattr(bp, "vertices") else [1, 2, 3]) > 2]
        for P in sorted(rings, key=lambda g: -g.area):        # even-odd: islands become holes
            area = P if area is None else area.symmetric_difference(P)
    return area


def name_areas(stations, labels, clear, overrides=None, max_dist=25.0, floor_area=None, area_overrides=None):
    """Each station gets the nearest area label it can see (no wall in between); else the nearest one.
    Stations inside the corridor hatch only take labels that are inside the hatch too (and vice versa),
    so a corridor station does not get the name of a room seen through its door."""
    from shapely.geometry import Point
    overrides = overrides or {}; area_overrides = area_overrides or {}
    inside = (lambda x, y: floor_area.buffer(0.3).covers(Point(x, y))) if floor_area is not None else None
    if inside:
        for L in labels:
            L["in_floor"] = inside(L["x"], L["y"])
    by_code = {L["code"]: L for L in labels}
    for st in stations:
        p = np.array([st["surveyPosition"]["x"], st["surveyPosition"]["y"]])
        pool = labels
        if inside:
            same = [L for L in labels if L["in_floor"] == inside(p[0], p[1])]
            pool = same or labels
        cand = sorted(pool, key=lambda L: np.hypot(L["x"] - p[0], L["y"] - p[1]))
        best = next((L for L in cand if np.hypot(L["x"] - p[0], L["y"] - p[1]) <= max_dist
                     and clear(p, np.array([L["x"], L["y"]]))), cand[0] if cand else None)
        key = f"S{st['id']:03d}"
        if area_overrides.get(key) in by_code:
            best = by_code[area_overrides[key]]
        if best:
            st["area"] = best["code"]
            st["title"] = f"{best['en'] or best['code']} · {best['code']}"
            st["description"] = best["vi"] or best["en"]
        if key in overrides:
            st["description"] = overrides[key]
    return stations


# ---------------------------------------------------------------- walking order
def compute_walk_order(stations, links, start_id=None):
    """Order all stations into one walk (W / S keys, guided tour). Steps along registration links are
    preferred, then steps between stations in line of sight; a step through a wall costs +1000 m.
    Nearest-neighbour start + 2-opt improvement of the open path (first station fixed)."""
    ids = [st["id"] for st in stations]
    P = {st["id"]: np.array([st["surveyPosition"]["x"], st["surveyPosition"]["y"]]) for st in stations}
    vis = {st["id"]: set(st.get("visible", [])) for st in stations}
    L = {i: set() for i in ids}
    for a, b in links:
        if a in L and b in L:
            L[a].add(b); L[b].add(a)

    def cost(a, b):
        d = float(np.linalg.norm(P[a] - P[b]))
        return d if b in L[a] else d + 5.0 if b in vis[a] else d + 1000.0

    start = start_id if start_id in P else min(ids)
    path, rest = [start], set(ids) - {start}
    while rest:
        nxt = min(rest, key=lambda j: (cost(path[-1], j), j)); path.append(nxt); rest.remove(nxt)
    n, improved = len(path), True
    while improved:
        improved = False
        for i in range(1, n - 1):
            for k in range(i + 1, n):
                a, b, c = path[i - 1], path[i], path[k]
                d = path[k + 1] if k + 1 < n else None
                old = cost(a, b) + (cost(c, d) if d is not None else 0)
                new = cost(a, c) + (cost(b, d) if d is not None else 0)
                if new < old - 1e-9:
                    path[i:k + 1] = path[i:k + 1][::-1]; improved = True
    for st in stations:
        st["walkIndex"] = path.index(st["id"])
        st["links"] = sorted(L[st["id"]])
    jumps = [(a, b) for a, b in zip(path, path[1:]) if b not in vis[a] and b not in L[a]]
    return path, jumps


# ---------------------------------------------------------------- viewer
def patch_viewer(template, out, cfg):
    """Fill the {{TOKENS}} of viewer_template_floorplan.html and write index.html."""
    html = Path(template).read_text(encoding="utf-8")
    im = cfg.get("images", {})
    tokens = {
        "PROJECT_NAME": cfg.get("project_name", "360 Walkthrough"),
        "PROJECT_SUBTITLE": cfg.get("project_subtitle", "360° Walkthrough"),
        "BADGE": cfg.get("badge", "360"),
        "HD_LABEL": cfg.get("hd_label", "HD %gK" % (im.get("hd_resolution", 4096) / 1024)),
        "WEB_LABEL": cfg.get("web_label", "Web %gK" % (im.get("web_resolution", 2560) / 1024)),
        "TEXTURE_CACHE_MAX": str(int(cfg.get("texture_cache_max", 6))),
        "MARKER_SCALE": str(cfg.get("marker_scale", 0.2)),
        "BADGE_HEIGHT": str(cfg.get("badge_height_m", 0.12)),
        "MATTERTAGS": json.dumps(cfg.get("mattertags", []), ensure_ascii=False),
    }
    for k, v in tokens.items():
        html = html.replace("{{%s}}" % k, v)
    leftovers = sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", html)))
    (out / "index.html").write_text(html, encoding="utf-8")
    return leftovers


# ---------------------------------------------------------------- main
def finish_stations(stations, cfg, rp, u2m, out):
    """Line of sight, area names and walking order; writes scan_stations.json."""
    dxf = str(rp(cfg["floorplan"]["dxf"]))
    vcfg = cfg.get("visibility", {})
    walls = load_walls(dxf, vcfg, u2m)
    if vcfg.get("enable", True):
        compute_visibility(stations, dxf, vcfg, u2m, walls)
        print("line of sight: avg %.1f visible stations per station"
              % (sum(len(st["visible"]) for st in stations) / len(stations)))
    acfg = cfg.get("area_labels", {})
    if acfg.get("enable", True):
        labels = read_area_labels(dxf, u2m, acfg.get("layer", "A-AREA-IDEN"))
        floor = load_floor_area(dxf, cfg["floorplan"].get("highlight_layer", "A-FLOR-PATT"), u2m)
        name_areas(stations, labels, make_clear(walls, vcfg.get("lateral_tolerance_m", 0.25)),
                   cfg.get("descriptions", {}), acfg.get("max_distance_m", 25.0), floor, acfg.get("overrides", {}))
        print("area names: %d labels, e.g. %s" % (len(labels), stations[0].get("title")))
    path, jumps = compute_walk_order(stations, vcfg.get("links", []), cfg.get("walk_start"))
    print("walking order:", " ".join(f"S{i:03d}" for i in path))
    if jumps:
        print("  steps without line of sight:", ", ".join(f"S{a:03d}->S{b:03d}" for a, b in jumps))
    stations.sort(key=lambda st: st["id"])
    json.dump(stations, open(out / "scan_stations.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"stations: {len(stations)}")
    return stations


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "tour_config.json"))
    ap.add_argument("--skip-images", action="store_true")
    ap.add_argument("--update-viewer", action="store_true",
                    help="re-use <output_dir>/scan_stations.json (no panoramas needed): recompute line of sight, "
                         "area names, walking order and rewrite index.html")
    a = ap.parse_args()
    cfg = json.load(open(a.config, encoding="utf-8"))
    base = Path(a.config).parent
    rp = lambda p: (base / p).resolve() if not os.path.isabs(p) else Path(p)
    out = rp(cfg.get("output_dir", ".."))
    for d in ("photos", "photos_hd", "thumbnails"):
        (out / d).mkdir(parents=True, exist_ok=True)

    u2m = cfg.get("floorplan", {}).get("units_to_m", 0.001)
    if a.update_viewer:
        stations = json.load(open(out / "scan_stations.json", encoding="utf-8"))
        finish_stations(stations, cfg, rp, u2m, out)
        left = patch_viewer(rp(cfg["viewer_template"]), out, cfg)
        print("viewer written", "(unfilled tokens: %s)" % left if left else "")
        return

    tr = json.load(open(rp(cfg["transform_file"])))
    th = math.radians(tr["theta_deg"]); sc = tr["scale"]; T = tr["T_mm"]
    qz = [math.cos(th / 2), 0, 0, math.sin(th / 2)]
    eye = cfg.get("scanner_height_m", 1.6)
    # Cyclone panorama exports put the image centre at a different scanner axis than the viewer expects
    off = math.radians(cfg.get("pano_yaw_offset_deg", 90.0))
    qoff = [math.cos(off / 2), 0, 0, math.sin(off / 2)]

    pano_dir = rp(cfg["panorama_dir"])
    txts = sorted(glob.glob(str(pano_dir / "*.txt")))
    raw = [(Path(t).stem, *read_station_txt(t)) for t in txts]
    zmed = float(np.median([p[2] for _, p, _ in raw]))
    stations = []
    for i, (name, p, q) in enumerate(raw, 1):
        x = (math.cos(th) * p[0] * sc - math.sin(th) * p[1] * sc + T[0]) * u2m
        y = (math.sin(th) * p[0] * sc + math.cos(th) * p[1] * sc + T[1]) * u2m
        z = eye + (p[2] - zmed)
        rot = qmul(qmul(qz, q), qoff)
        sid = re.search(r"S(\d+)$", name)
        num = int(sid.group(1)) if sid else i
        stations.append({
            "id": num, "name": name, "guid": name, "pointCount": 0,
            "surveyPosition": {"x": round(x, 4), "y": round(y, 4), "z": round(z, 4)},
            "groundZ": 0.0, "rotation": [round(v, 9) for v in rot],
            "title": f"Station S{num:03d}",
            "description": cfg.get("descriptions", {}).get(f"S{num:03d}", f"Scan station S{num:03d}"),
            "hasPanorama": True,
            "photo": f"photos/{name}.jpg", "photoHd": f"photos_hd/{name}.jpg",
            "thumbnail": f"thumbnails/{name}.jpg"})
        if not a.skip_images:
            src = pano_dir / f"{name}.jpg"
            print(f"[{i:>2}/{len(raw)}] {name}")
            process_pano(src, name, out, cfg.get("images", {}))
    finish_stations(stations, cfg, rp, u2m, out)

    meta = render_floorplan(str(rp(cfg["floorplan"]["dxf"])), out, cfg["floorplan"])
    print("floor plan:", meta)
    left = patch_viewer(rp(cfg["viewer_template"]), out, cfg)
    print("viewer written", "(unfilled tokens: %s)" % left if left else "")


if __name__ == "__main__":
    main()
