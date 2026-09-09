# -*- coding: utf-8 -*-
import io

from PIL import Image

from tools import room_view_render as rvr

CROP = {"min_x": 10.0, "min_y": 20.0, "max_x": 14.0, "max_y": 22.0}  # 4 m x 2 m


def _png(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_project_to_pixel_corners_and_center():
    size = (800, 400)
    assert rvr.project_to_pixel(10.0, 22.0, CROP, size) == (0, 0)        # top-left
    assert rvr.project_to_pixel(14.0, 20.0, CROP, size) == (800, 400)    # bottom-right
    assert rvr.project_to_pixel(12.0, 21.0, CROP, size) == (400, 200)    # centre


def test_labels_and_stamp_add_pixels():
    meta = {
        "luminaires": [{"id": 1, "family": "F", "type": "T", "pos": {"x": 12.0, "y": 21.0, "z": 2.8}, "letter": ""}],
        "switch_points": [],
        "doors": [{"id": 2, "family": "P", "type": "T", "width_m": 0.8,
                   "pos": {"x": 11.0, "y": 20.0, "z": 0}, "hinge": {"x": 10.6, "y": 20.0, "z": 0},
                   "latch": {"x": 11.4, "y": 20.0, "z": 0}, "swing": {"x": 0, "y": 1},
                   "opens_into_this_room": True, "connects_to": "HALL"}],
    }
    groups = rvr.label_items(meta)
    assert groups["L"][0]["label"] == "L1"
    assert groups["D"][0]["label"] == "D1"

    out, size = rvr.stamp(_png(800, 400), CROP, groups["L"], "L")
    assert size == (800, 400)
    assert groups["L"][0]["px"] == [400, 200]
    stamped = Image.open(io.BytesIO(out)).convert("RGB")
    # marker ring is coral: some pixel near the centre must be non-white
    assert any(stamped.getpixel((400 + dx, 200)) != (255, 255, 255) for dx in range(-14, 15))

    out2, _ = rvr.stamp(_png(800, 400), CROP, groups["D"], "D")
    assert groups["D"][0]["hinge_px"] == [120, 400]


def test_summary_mentions_markers_and_swing():
    meta = {
        "room": {"number": "12", "name": "SUITE 01", "level": "N1", "area_m2": 15.2,
                 "ceilings": [{"height_m": 2.8, "coverage": 0.95}]},
        "crop_m": CROP,
        "luminaires": [{"id": 1, "family": "Spot", "type": "7W", "pos": {"x": 12.0, "y": 21.0, "z": 2.8}, "letter": "a"}],
        "switch_points": [{"pos": {"x": 10.2, "y": 20.5, "z": 1.2}, "point_type": "1 Tecla Simples",
                           "keys": [{"id": 5, "type": "Simples", "switch_id": "a", "family": "Modulo", "z": 1.2, "role": "key"}]}],
        "doors": [],
        "furniture": [{"category": "Furniture", "family": "Cama", "type": "Casal",
                       "center": {"x": 12, "y": 21, "z": 0}, "size_m": {"dx": 1.6, "dy": 2.0}}],
    }
    groups = rvr.label_items(meta)
    text = rvr.summarize(meta, groups)
    assert "L1:" in text and "S1:" in text and "1 Tecla Simples" in text
    assert "letra='a'" in text
    assert "Cama" in text
    assert "2.8 (95%)" in text
