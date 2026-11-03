# VGU Building B6 – 2nd Floor · 360° Walkthrough (Oct 2026)

Static 360° virtual tour (Three.js) of the 2nd-floor corridors of Building B6 and rooms B6-217 / B6-218.
36 Leica scan stations (Cyclone REGISTER 360 panoramas), positioned on the architectural DXF floor plan.

**Live:** enable GitHub Pages (Settings → Pages → *Deploy from a branch* → `main` / root), then open
`https://<user>.github.io/<repo>/`

## Modes
| Mode | What it shows |
|---|---|
| 🚶 Inside Tour | 360° panorama per station, click the floor markers (or W / S) to walk |
| 🗺️ 3D Plan | DXF floor plan in 3D with all stations – click a station to jump in |
| 🧭 Floor Plan | Top-down plan |

## Run locally
```
python -m http.server 8080
```
then open http://localhost:8080 (opening index.html directly from disk does not work – browsers block `fetch` on `file://`).

## Contents
```
index.html            viewer (from e57_pipeline viewer template, adapted for a floor plan)
scan_stations.json    36 stations: plan coordinates (m), rotation quaternion, image paths
floorplan.png/.json   DXF render + its extents in plan metres
photos/               2560 px panoramas (default)
photos_hd/            4096 px panoramas ("4K Ultra" button)
thumbnails/           512 px previews
tools/                build script + config + source DXF + scan→plan transform
```

## Rebuild
```
pip install pillow numpy ezdxf matplotlib shapely
python tools/build_tour_from_panos.py --config tools/tour_config.json
```
`tools/tour_config.json` → `panorama_dir` must point to the Cyclone export folder (`<station>.jpg` + `<station>.txt`).

* Scan → plan transform (`tools/scan_to_plan_transform.json`): rotation 179.4°, scale ×1000 (m→mm), fitted so all
  stations fall inside the corridor hatch (layer `A-FLOR-PATT`) and rooms 217/218. Expected accuracy ≈ ±0.2–0.3 m.
* `pano_yaw_offset_deg = 90`: Cyclone panorama exports are rotated 90° relative to the viewer convention
  (checked on corridor stations S031→S030 and S010→S006).
* `scanner_height_m` (1.6 m) only affects where the floor markers sit inside the panoramas.
