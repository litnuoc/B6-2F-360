"""
build_tour_from_panos.py
========================
Builds a static 360 walkthrough (same viewer as e57_pipeline) from
  * Cyclone REGISTER 360 panorama exports  (<station>.jpg + <station>.txt with position/orientation)
  * a scan->plan transform (scan_to_plan_transform.json)
  * a DXF floor plan, shown in the "Dollhouse" / "Floor Plan" modes instead of a 3D mesh.

Usage:
    pip install pillow numpy ezdxf matplotlib
    python tools/build_tour_from_panos.py --config tools/tour_config.json

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


# ---------------------------------------------------------------- viewer
def patch_viewer(template, out, cfg):
    html = Path(template).read_text(encoding="utf-8")
    name = cfg.get("project_name", "360 Walkthrough")
    sub = cfg.get("project_subtitle", "360° Walkthrough")
    html = re.sub(r"<title>.*?</title>", f"<title>{name}</title>", html)
    html = html.replace("VGU Bridge 3D Showcase", name)
    html = html.replace("VGU Cable Bridge &bull; 3D Digital Twin", f"{name} &bull; {sub}")
    html = re.sub(r'<a href="potree_bridge/index.html"[^>]*>.*?</a>', "", html)
    html = re.sub(r'<a href="index.html" class="mp-btn"[^>]*>.*?</a>', "", html)
    html = html.replace("along the bridge deck", "to the next station")
    html = html.replace("'Start of Deck'", "'Start of Walk'")
    html = html.replace("% 9; // loop along main deck", "% state.stations.length; // loop over all stations")
    # --- floor plan instead of mesh / point cloud
    new_loader = """function loadDollhouseModels() {
      // 2D floor plan (DXF render) laid on the floor plane, shown in Dollhouse / Floor Plan modes
      fetch('floorplan.json').then(r => r.json()).then(fp => {
        new THREE.TextureLoader().load(fp.image, (tex) => {
          tex.colorSpace = THREE.SRGBColorSpace;
          tex.anisotropy = renderer.capabilities.getMaxAnisotropy();
          const w = fp.maxX - fp.minX, h = fp.maxY - fp.minY;
          const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h),
                                   new THREE.MeshBasicMaterial({ map: tex, side: THREE.DoubleSide }));
          m.position.set((fp.minX + fp.maxX) / 2, (fp.minY + fp.maxY) / 2, fp.z || 0);
          m.renderOrder = -50;
          state.dollhouseMesh = m;
          m.visible = (state.mode === 'dollhouse' || state.mode === 'floorplan');
          scene.add(m);
        });
      }).catch(() => console.log('No floor plan found'));
    }
    function _unusedOldLoader() {"""
    html = html.replace("function loadDollhouseModels() {", new_loader, 1)
    html = html.replace("if (state.dollhouseMesh) state.dollhouseMesh.visible = isDollhouse && (state.dollhouseLayer === 'mesh');",
                        "if (state.dollhouseMesh) state.dollhouseMesh.visible = isDollhouse;")
    html = html.replace("if (dollhouseLayerDock) dollhouseLayerDock.style.display = isDollhouse ? 'flex' : 'none';",
                        "if (dollhouseLayerDock) dollhouseLayerDock.style.display = 'none';")
    html = html.replace("🏠 Dollhouse", "🗺️ 3D Plan")
    # --- camera framing from station extents (template was hard-coded to the bridge)
    helper = """
    function sceneCenter(zOff = 0) {
      const s = state.stations; if (!s || !s.length) return new THREE.Vector3();
      let x = 0, y = 0, z = 0; s.forEach(t => { x += t.surveyPosition.x; y += t.surveyPosition.y; z += (t.groundZ ?? t.surveyPosition.z - 1.6); });
      return new THREE.Vector3(x / s.length, y / s.length, z / s.length + zOff);
    }
    function sceneExtent() {
      const s = state.stations; if (!s || !s.length) return 30;
      const xs = s.map(t => t.surveyPosition.x), ys = s.map(t => t.surveyPosition.y);
      return Math.max(15, Math.hypot(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) + 10);
    }
    // Build Mattertags (3D Information Hotspots)"""
    html = html.replace("\n    // Build Mattertags (3D Information Hotspots)", helper, 1)
    html = html.replace("const dollhouseCamPos = new THREE.Vector3(curPos.x + 35, curPos.y - 55, curPos.z + 40);",
                        "const _c = sceneCenter(), _e = sceneExtent();\n        const dollhouseCamPos = new THREE.Vector3(_c.x + _e * 0.25, _c.y - _e * 0.7, _c.z + _e * 0.75);")
    html = html.replace("const centerDeck = new THREE.Vector3(-66.5, 171.2, 10.0);", "const centerDeck = sceneCenter(0);")
    html = html.replace("const centerDeck = new THREE.Vector3(-66.5, 171.2, 0);", "const centerDeck = sceneCenter(0);")
    html = html.replace("const topDownPos = new THREE.Vector3(-66.5, 171.2, 140.0);",
                        "const topDownPos = sceneCenter(sceneExtent() * 0.8); topDownPos.y -= 0.01;")
    html = html.replace("controls.minDistance = 20.0;", "controls.minDistance = 3.0;")
    html = html.replace("controls.minDistance = 5.0;", "controls.minDistance = 2.0;")
    html = re.sub(r"const mattertagDefs = \[.*?\n    \];",
                  "const mattertagDefs = " + json.dumps(cfg.get("mattertags", []), ensure_ascii=False) + ";",
                  html, count=1, flags=re.S)
    leftovers = [k for k in ("-66.5, 171.2", "bridge_bpa_colored.glb\"", "potree_bridge") if k in html]
    (out / "index.html").write_text(html, encoding="utf-8")
    return leftovers


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "tour_config.json"))
    ap.add_argument("--skip-images", action="store_true")
    a = ap.parse_args()
    cfg = json.load(open(a.config, encoding="utf-8"))
    base = Path(a.config).parent
    rp = lambda p: (base / p).resolve() if not os.path.isabs(p) else Path(p)
    out = rp(cfg.get("output_dir", ".."))
    for d in ("photos", "photos_hd", "thumbnails"):
        (out / d).mkdir(parents=True, exist_ok=True)

    tr = json.load(open(rp(cfg["transform_file"])))
    th = math.radians(tr["theta_deg"]); sc = tr["scale"]; T = tr["T_mm"]
    u2m = cfg.get("floorplan", {}).get("units_to_m", 0.001)
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
    json.dump(stations, open(out / "scan_stations.json", "w", encoding="utf-8"), indent=2)
    print(f"stations: {len(stations)}")

    meta = render_floorplan(str(rp(cfg["floorplan"]["dxf"])), out, cfg["floorplan"])
    print("floor plan:", meta)
    left = patch_viewer(rp(cfg["viewer_template"]), out, cfg)
    print("viewer written", "(unpatched leftovers: %s)" % left if left else "")


if __name__ == "__main__":
    main()
