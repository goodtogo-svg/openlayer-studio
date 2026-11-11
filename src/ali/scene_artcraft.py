"""Export ali-scene/1 boards to ArtCraft 2D editor scene JSON (pagedraw, version "2.0").

Target format: storytold/artcraft frontend/libs/components/pagedraw/src/lib/stores/SceneState.ts
(exportSceneAsJson / importSceneFromJson, checked at commit 55e6e5c, 2026-11-10).

ArtCraft's 2D canvas is one of three fixed boxes (683x1024, 1024x683, 1024x1024), and its node
types are rectangle/circle/triangle/image/line only. So this export is a downscaled, raster-only
handoff for AI generation work. It is not a native-quality layer reconstruction, and it carries no
editable text or vector data.
"""
import base64
import io
import json
import math
from pathlib import Path

from PIL import Image

from ali.runtime import atomic_json

ARTCRAFT_SCENE_VERSION = "2.0"
CANVASES = {"2:3": (683, 1024), "3:2": (1024, 683), "1:1": (1024, 1024)}


def choose_canvas(width, height):
    """Pick the ArtCraft aspect box closest to the board ratio (log-ratio distance)."""
    ratio = width / height
    return min(CANVASES.items(), key=lambda kv: abs(math.log(ratio / (kv[1][0] / kv[1][1]))))


def _png_data_url(image):
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _image_node(node_id, x, y, w, h, data_url):
    # Field set mirrors exportSceneAsJson so importSceneFromJson restores it unchanged.
    return {
        "id": node_id,
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "fill": "transparent",
        "type": "image",
        "stroke": "transparent",
        "strokeWidth": 0,
        "draggable": True,
        "rotation": 0,
        "scaleX": 1,
        "scaleY": 1,
        "offsetX": 0,
        "offsetY": 0,
        "locked": False,
        "imageDataUrl": data_url,
    }


def export_board(board, max_upscale=1.0):
    """Return (artcraft_scene_dict, report_dict) for one ali-scene board."""
    bw, bh = board["width"], board["height"]
    aspect, (cw, ch) = choose_canvas(bw, bh)
    scale = min(cw / bw, ch / bh, max_upscale)
    ox, oy = round((cw - bw * scale) / 2), round((ch - bh * scale) / 2)
    nodes, skipped = [], []
    for node in sorted((n for n in board["nodes"] if n.get("visible", False)), key=lambda n: n.get("z_index", 0)):
        if node["type"] != "raster":
            skipped.append({"id": node["id"], "type": node["type"], "reason": "ArtCraft 2D editor has no text/vector node; rasterize first if needed"})
            continue
        path = Path(node["asset"]).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        with Image.open(path) as im:
            im = im.convert("RGBA")
            if "bbox" in node:
                x0, y0, x1, y1 = node["bbox"]
            elif im.size == (bw, bh):
                x0, y0, x1, y1 = 0, 0, bw, bh
            else:
                raise ValueError(f"Raster node {node['id']} is {im.size}, not board-sized {(bw, bh)}, and has no bbox")
            w, h = max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale))
            if im.size != (w, h):
                im = im.resize((w, h), Image.LANCZOS)
            nodes.append(_image_node(f"ali-{node['id']}", ox + round(x0 * scale), oy + round(y0 * scale), w, h, _png_data_url(im)))
    scene = {
        "drawNodes": nodes,  # index 0 = bottom, last = top (ArtCraft render order)
        "inpaintLineNodes": [],
        "brushColor": "#000000",
        "brushSize": 5,
        "eraserSize": 16,
        "fillColor": "white",
        "aspectRatioType": aspect,
        "version": ARTCRAFT_SCENE_VERSION,
    }
    report = {
        "board": board.get("id"),
        "source_size_px": [bw, bh],
        "canvas": {"aspectRatioType": aspect, "size_px": [cw, ch], "offset_px": [ox, oy]},
        "scale": scale,
        "letterboxed": (ox, oy) != (0, 0),
        "exported_layers": [n["id"] for n in nodes],
        "skipped_nodes": skipped,
    }
    return scene, report


def generate_artcraft(scene_path, out):
    scene = json.loads(Path(scene_path).read_text())
    if scene.get("schema") != "ali-scene/1":
        raise ValueError("Invalid scene schema")
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    jobs = []
    for i, board in enumerate(scene["boards"], 1):
        target = out / f"page_{i:03d}.artcraft-scene.json"
        if target.exists():
            raise FileExistsError("Refusing to overwrite ArtCraft export")
        data, report = export_board(board)
        target.write_text(json.dumps(data, indent=2), encoding="utf-8")
        jobs.append(dict(report, output=str(target)))
    atomic_json(out / "artcraft-handoff.json", {
        "scene": str(Path(scene_path).resolve()),
        "target": "ArtCraft 2D editor scene JSON v2.0 (pagedraw importSceneFromJson)",
        "jobs": jobs,
        "limitations": [
            "Raster only: text and vector nodes are skipped, not converted",
            "Downscaled to ArtCraft's fixed 1024px-class canvas; not print resolution",
            "Inferred layers keep their provenance only in this handoff file, not inside ArtCraft",
        ],
        "artcraft_verified": False,
    })
    return jobs
