# VGU Building B6 – 2nd Floor · 360° Walkthrough (Oct 2026)

Static 360° virtual tour (Three.js) of the 2nd-floor corridors of Building B6 and rooms B6-217 / B6-218.
36 Leica scan stations (Cyclone REGISTER 360 panoramas), positioned on the architectural DXF floor plan.

**Live:** enable GitHub Pages (Settings → Pages → *Deploy from a branch* → `main` / root), then open
`https://<user>.github.io/<repo>/`

## Modes
| Mode | What it shows |
|---|---|
| 🚶 Inside Tour | 360° panorama per station, click the floor markers (stations in line of sight) or W / S to walk along the walking order |
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
tools/                build script + config + viewer template + source DXF + scan→plan transform
```

## Rebuild
```
pip install pillow numpy ezdxf matplotlib shapely
python tools/build_tour_from_panos.py --config tools/tour_config.json
```
Only the viewer / station data changed (no new panoramas)? This re-uses `scan_stations.json` and needs no panorama folder:
```
python tools/build_tour_from_panos.py --update-viewer
```
Edit `tools/viewer_template_floorplan.html` (not `index.html`, which is regenerated).

`tools/tour_config.json` → `panorama_dir` must point to the Cyclone export folder (`<station>.jpg` + `<station>.txt`).

* Scan → plan transform (`tools/scan_to_plan_transform.json`): rotation 179.4°, scale ×1000 (m→mm), fitted so all
  stations fall inside the corridor hatch (layer `A-FLOR-PATT`) and rooms 217/218. Expected accuracy ≈ ±0.2–0.3 m.
* `pano_yaw_offset_deg = 90`: Cyclone panorama exports are rotated 90° relative to the viewer convention
  (checked on corridor stations S031→S030 and S010→S006).
* `scanner_height_m` (1.6 m) only affects where the floor markers sit inside the panoramas.
* **Walking order** (`walkIndex` in `scan_stations.json`): one walk through all stations, built from the registration
  `links` (preferred) and line of sight; used by W / S, Next / Previous, the guided tour and the default view direction.
  Start station: `walk_start`. The minimap draws the `links`.
* **Area names** (`title`, `description`, `area`): DXF room tags on layer `A-AREA-IDEN` (English · code, Vietnamese).
  Stations inside the corridor hatch only take corridor labels. Fix a wrong one with `area_labels.overrides`
  (e.g. `"S024": "B6-218"`); `descriptions` still overrides the description text.
* `texture_cache_max` (default 6): panoramas kept in GPU memory; older ones are freed.
* **Storeys** (`floors`, `station_level`, `floor_height_m` in `tour_config.json`): every storey's DXF is rendered in the
  same frame (`floorplan_L<n>.png`, listed in `floorplan.json`) and stacked `floor_height_m` apart in 🗺️ 3D Plan; the
  floor selector on the right isolates one storey. Re-render after changing a DXF:
  `python tools/build_tour_from_panos.py --update-viewer --render-floors` (≈1–2 min).
