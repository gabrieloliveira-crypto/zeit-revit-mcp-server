# -*- coding: utf-8 -*-
"""Pixel mapping and marker stamping for get_room_views.

Pure functions (no Revit, no MCP) so they can be unit-tested. Coordinates
from Revit are project metres; the exported PNG covers exactly the crop box,
so a project point maps linearly onto the image.
"""

import io
from typing import Dict, List, Tuple

from PIL import Image, ImageDraw, ImageFont

Point = Tuple[int, int]

COLORS = {
    "L": (235, 100, 36),    # luminaires: coral
    "S": (32, 58, 114),     # switch points: navy
    "D": (30, 140, 60),     # doors: green
}


def project_to_pixel(x_m: float, y_m: float, crop: Dict[str, float],
                     size: Tuple[int, int]) -> Point:
    """Map a project point (metres) to image pixels. Image y grows downward."""
    w, h = size
    cw = crop["max_x"] - crop["min_x"]
    ch = crop["max_y"] - crop["min_y"]
    if cw <= 0 or ch <= 0:
        raise ValueError("crop box has no area")
    px = (x_m - crop["min_x"]) / cw * w
    py = (crop["max_y"] - y_m) / ch * h
    return int(round(px)), int(round(py))


def label_items(meta: Dict) -> Dict[str, List[Dict]]:
    """Assign L1.., S1.., D1.. labels in place and return them grouped."""
    groups = {"L": meta.get("luminaires", []),
              "S": meta.get("switch_points", []),
              "D": meta.get("doors", [])}
    for prefix, items in groups.items():
        for i, item in enumerate(items, 1):
            item["label"] = f"{prefix}{i}"
    return groups


def _font(size: int):
    try:
        return ImageFont.truetype("arialbd.ttf", size)
    except Exception:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


def stamp(png_bytes: bytes, crop: Dict[str, float], items: List[Dict],
          prefix: str) -> Tuple[bytes, Tuple[int, int]]:
    """Draw a numbered marker at each item's 'pos' (or 'hinge' for doors).
    Adds 'px' to each item. Returns (annotated PNG bytes, image size)."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)
    color = COLORS.get(prefix, (0, 0, 0))
    r = max(9, img.width // 90)
    font = _font(max(11, int(r * 1.3)))
    for item in items:
        anchor = item.get("pos") or item.get("center")
        if anchor is None:
            continue
        cx, cy = project_to_pixel(anchor["x"], anchor["y"], crop, img.size)
        item["px"] = [cx, cy]
        if prefix == "D" and item.get("hinge"):
            hx, hy = project_to_pixel(item["hinge"]["x"], item["hinge"]["y"], crop, img.size)
            item["hinge_px"] = [hx, hy]
            draw.ellipse([hx - r // 2, hy - r // 2, hx + r // 2, hy + r // 2], fill=color)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=3)
        label = item.get("label", prefix)
        tw = draw.textlength(label, font=font)
        tx, ty = cx + r + 2, cy - r
        draw.rectangle([tx - 2, ty - 1, tx + tw + 2, ty + int(r * 1.6)], fill=(255, 255, 255))
        draw.text((tx, ty), label, fill=color, font=font)
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue(), img.size


def summarize(meta: Dict, groups: Dict[str, List[Dict]]) -> str:
    """Compact human/agent-readable summary. Keeps token cost low."""
    room = meta.get("room", {})
    lines = [
        f"AMBIENTE {room.get('number', '')} {room.get('name', '')} | nivel {room.get('level', '')}"
        f" | area {room.get('area_m2')} m2",
    ]
    ceils = room.get("ceilings") or []
    if ceils:
        lines.append("forro medido (m acima do nivel): " +
                     ", ".join(f"{c['height_m']} ({int(c['coverage']*100)}%)" for c in ceils))
    else:
        lines.append("forro: nenhum Ceiling encontrado sobre o ambiente")
    crop = meta.get("crop_m", {})
    lines.append(f"crop (m, projeto): x {crop.get('min_x')}..{crop.get('max_x')}"
                 f"  y {crop.get('min_y')}..{crop.get('max_y')}")
    lines.append("posicoes abaixo: (x,y) em metros de projeto, z acima do nivel; px = pixel na imagem")

    lines.append(f"PORTAS ({len(groups['D'])}) - marcadas na PLANTA, ponto pequeno = dobradica:")
    for d in groups["D"]:
        into = "abre PARA DENTRO deste ambiente" if d.get("opens_into_this_room") else "abre para fora"
        other = f" -> {d['connects_to']}" if d.get("connects_to") else ""
        lines.append(f"  {d['label']}: {d['family']} {d['width_m']} m{other}; {into};"
                     f" pos ({d['pos']['x']},{d['pos']['y']}) px {d.get('px')};"
                     f" dobradica ({d['hinge']['x']},{d['hinge']['y']}) px {d.get('hinge_px')}")

    lines.append(f"LUMINARIAS ({len(groups['L'])}) - marcadas no FORRO:")
    for l in groups["L"]:
        letter = f" letra='{l['letter']}'" if l.get("letter") else " (sem letra)"
        lines.append(f"  {l['label']}: {l['family']} : {l['type']} z {l['pos']['z']}{letter};"
                     f" pos ({l['pos']['x']},{l['pos']['y']}) px {l.get('px')} id {l['id']}")

    lines.append(f"PONTOS DE INTERRUPTOR ({len(groups['S'])}) - marcados na PLANTA:")
    for s in groups["S"]:
        keys = ", ".join(
            f"{k['type']}[{k['switch_id'] or '-'}] id {k['id']}" for k in s.get("keys", []))
        point = f" ponto '{s['point_type']}'" if s.get("point_type") else ""
        mods = f" modulos: {keys}" if keys else " (sem modulos; Switch ID so no Conjunto)"
        lines.append(f"  {s['label']}:{point} h {s['pos']['z']} m;"
                     f" pos ({s['pos']['x']},{s['pos']['y']}) px {s.get('px')};{mods}")

    furn = meta.get("furniture") or []
    if furn:
        lines.append(f"MOBILIARIO/EQUIPAMENTOS ({len(furn)}):")
        for f in furn[:40]:
            size = f.get("size_m") or {}
            lines.append(f"  {f['category']}: {f['family']} : {f['type']}"
                         f" centro ({f['center']['x']},{f['center']['y']})"
                         f" {size.get('dx')}x{size.get('dy')} m")
    if meta.get("export_error"):
        lines.append("AVISO: " + meta["export_error"])
    return "\n".join(lines)
