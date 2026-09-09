# -*- coding: UTF-8 -*-
"""
Room Views Module for Revit MCP (Zeit, 2026-09-09)

Exports a cropped floor plan + ceiling plan of ONE room as PNG, plus compact
metadata (doors with swing, luminaires, switches, furniture, measured ceiling
height). Gives the agent "eyes" before positioning lights/switches.

Everything here is ASCII-only on purpose: strings that cross the Routes
transport get corrupted when they carry accents.
"""

from pyrevit import routes, DB
from utils import (
    get_element_name,
    get_element_id_value,
    make_element_id,
    sanitize_string,
    suppress_warnings,
)
from System.Collections.Generic import List
import System
import base64
import json
import logging
import os
import re
import tempfile
import time

logger = logging.getLogger(__name__)

FT_TO_M = 0.3048
M_TO_FT = 1.0 / 0.3048
SQFT_TO_M2 = 0.09290304

_HIDE_CATEGORIES = [
    "OST_Dimensions",
    "OST_TextNotes",
    "OST_GenericAnnotation",
    "OST_Sections",
    "OST_Elev",
    "OST_Callouts",
    "OST_Grids",
    "OST_CLines",
    "OST_Cameras",
    "OST_Viewers",
    "OST_SectionBox",
]

_FURNITURE_CATEGORIES = [
    "OST_Furniture",
    "OST_FurnitureSystems",
    "OST_Casework",
    "OST_PlumbingFixtures",
    "OST_SpecialityEquipment",
    "OST_MechanicalEquipment",
    "OST_ElectricalEquipment",
]


def _strip_accents(text):
    """'SUÍTE' -> 'SUITE' using .NET normalisation (IronPython str is a
    .NET String). Falls back to '?' replacement."""
    try:
        from System.Text import NormalizationForm
        from System.Globalization import CharUnicodeInfo, UnicodeCategory
        if not isinstance(text, unicode):
            text = unicode(text)
        decomposed = text.Normalize(NormalizationForm.FormD)
        chars = [
            ch for ch in decomposed
            if CharUnicodeInfo.GetUnicodeCategory(ch) != UnicodeCategory.NonSpacingMark
        ]
        return "".join(chars).encode("ascii", "replace").decode("ascii")
    except Exception:
        return sanitize_string(text)


def _key(text):
    """Accent/case/space-insensitive key: keeps only [a-z0-9]."""
    try:
        s = _strip_accents(text).lower()
    except Exception:
        s = ""
    return re.sub(r"[^a-z0-9]", "", s)


def _skeleton(text):
    """Consonant skeleton: survives transport mojibake on either side
    ('SUÍTE 01', 'SU?TE 01' and 'SUÃ\x8dTE 01' all give 'st01')."""
    try:
        s = "".join(ch for ch in unicode(text) if ord(ch) < 128).lower()
    except Exception:
        s = ""
    return re.sub(r"[^b-df-hj-np-tv-z0-9]", "", s)


def _param_str(elem, bip):
    try:
        p = elem.get_Parameter(bip)
        if p and p.HasValue:
            v = p.AsString()
            if v is None:
                v = p.AsValueString()
            return sanitize_string(v).strip() if v else ""
    except Exception:
        pass
    return ""


def _param_raw(elem, bip):
    """Parameter text WITHOUT ascii sanitising (for matching only)."""
    try:
        p = elem.get_Parameter(bip)
        if p and p.HasValue:
            v = p.AsString()
            if v is None:
                v = p.AsValueString()
            return v.strip() if v else u""
    except Exception:
        pass
    return u""


def _location_point(elem):
    try:
        loc = elem.Location
        if isinstance(loc, DB.LocationPoint):
            return loc.Point
        if isinstance(loc, DB.LocationCurve):
            return loc.Curve.Evaluate(0.5, True)
    except Exception:
        pass
    try:
        bb = elem.get_BoundingBox(None)
        if bb:
            return (bb.Min + bb.Max) * 0.5
    except Exception:
        pass
    return None


def _xyz_m(p, level_z_ft):
    """Project XYZ (ft) -> dict in metres; z relative to the room's level."""
    return {
        "x": round(p.X * FT_TO_M, 3),
        "y": round(p.Y * FT_TO_M, 3),
        "z": round((p.Z - level_z_ft) * FT_TO_M, 2),
    }


def _bic(name):
    return getattr(DB.BuiltInCategory, name)


def _find_room(doc, room_query):
    rooms = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_Rooms)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    placed = [r for r in rooms if r.Area > 0]

    # By id
    try:
        rid = int(room_query)
        for r in placed:
            if get_element_id_value(r) == rid:
                return r, None
        return None, "Room id {} not found".format(rid)
    except (TypeError, ValueError):
        pass

    q = _key(room_query)
    qs = _skeleton(room_query)
    exact, partial, skel = [], [], []
    for r in placed:
        # Raw (unsanitised) so _strip_accents sees the real accented text.
        raw_name = _param_raw(r, DB.BuiltInParameter.ROOM_NAME)
        raw_number = _param_raw(r, DB.BuiltInParameter.ROOM_NUMBER)
        name = _key(raw_name)
        number = _key(raw_number)
        if q == name or q == number or q == number + name or q == name + number:
            exact.append(r)
        elif q and (q in name or q in number + name):
            partial.append(r)
        elif qs and (qs == _skeleton(raw_name) or qs == _skeleton(raw_number)
                     or qs == _skeleton(raw_number + raw_name)):
            skel.append(r)
    hits = exact or partial or skel
    if len(hits) == 1:
        return hits[0], None
    if not hits:
        return None, "No room matches '{}'".format(sanitize_string(room_query))
    return None, {
        "error": "Ambiguous room query '{}'".format(sanitize_string(room_query)),
        "candidates": [
            {
                "id": get_element_id_value(r),
                "name": get_element_name(r),
                "number": _param_str(r, DB.BuiltInParameter.ROOM_NUMBER),
                "level": get_element_name(doc.GetElement(r.LevelId)) if r.LevelId else "",
            }
            for r in hits[:15]
        ],
    }


def _in_room(room, p, level_z_ft, z_max_m=None):
    """Membership by XY (tested 0.5 m above the level so a wrong room upper
    limit does not exclude ceiling fixtures) AND by height band: from 0.3 m
    below the level up to z_max_m. The band keeps out fixtures of a
    double-height space underneath (e.g. gourmet under Suite 01)."""
    try:
        z_rel = (p.Z - level_z_ft) * FT_TO_M
        if z_rel < -0.3:
            return False
        if z_max_m is not None and z_rel > z_max_m:
            return False
        return room.IsPointInRoom(DB.XYZ(p.X, p.Y, level_z_ft + 0.5 * M_TO_FT))
    except Exception:
        return False


def _ceiling_heights(doc, room_bb, level_z_ft):
    """Bottom heights (m above level) of Ceilings that overlap the room bbox."""
    rx0, ry0 = room_bb.Min.X, room_bb.Min.Y
    rx1, ry1 = room_bb.Max.X, room_bb.Max.Y
    room_area = max((rx1 - rx0) * (ry1 - ry0), 1e-6)
    heights = {}
    ceilings = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_Ceilings)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for c in ceilings:
        try:
            bb = c.get_BoundingBox(None)
            if not bb:
                continue
            ox = max(0.0, min(rx1, bb.Max.X) - max(rx0, bb.Min.X))
            oy = max(0.0, min(ry1, bb.Max.Y) - max(ry0, bb.Min.Y))
            overlap = ox * oy / room_area
            h = (bb.Min.Z - level_z_ft) * FT_TO_M
            if overlap < 0.05 or h < 1.5 or h > 9.0:
                continue
            key = round(h, 2)
            heights[key] = max(heights.get(key, 0.0), round(overlap, 2))
        except Exception:
            continue
    return [{"height_m": h, "coverage": cov} for h, cov in sorted(heights.items())]


def _collect_instances(doc, bic_name):
    try:
        return (
            DB.FilteredElementCollector(doc)
            .OfCategory(_bic(bic_name))
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except Exception:
        return []


def _family_and_type(inst):
    fam, typ = "", ""
    try:
        sym = inst.Symbol
        fam = sanitize_string(sym.Family.Name)
        typ = get_element_name(sym)
    except Exception:
        pass
    return fam, typ


def _is_lighting_parent(e):
    try:
        s = e.SuperComponent
        if s is None:
            return True
        return s.Category is None or s.Category.Name != e.Category.Name
    except Exception:
        return True


def _luminaires(doc, room, level_z_ft, z_max_m):
    out = []
    for e in _collect_instances(doc, "OST_LightingFixtures"):
        try:
            if not _is_lighting_parent(e):
                continue
            p = _location_point(e)
            if p is None or not _in_room(room, p, level_z_ft, z_max_m):
                continue
            fam, typ = _family_and_type(e)
            out.append({
                "id": get_element_id_value(e),
                "family": fam,
                "type": typ,
                "pos": _xyz_m(p, level_z_ft),
                "letter": _param_str(e, DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS),
            })
        except Exception:
            continue
    out.sort(key=lambda d: (-d["pos"]["y"], d["pos"]["x"]))
    return out


def _switches(doc, room, level_z_ft, z_max_m):
    """Lighting devices grouped by XY point. Zeit/BrittoBIM convention: the
    'Conjunto' box (family name may be misspelt 'Conjuto') is the POINT and
    its type describes the keys ('1 Tecla Simples + 2 Paralelo'); optional
    'Modulo' instances stacked at ~1.2 m are the individual keys with an
    editable Switch ID."""
    groups = []
    for e in _collect_instances(doc, "OST_LightingDevices"):
        try:
            fam, typ = _family_and_type(e)
            p = _location_point(e)
            if p is None or not _in_room(room, p, level_z_ft, z_max_m):
                continue
            is_point = "conju" in fam.lower()
            entry = {
                "id": get_element_id_value(e),
                "family": fam,
                "type": typ,
                "role": "point" if is_point else "key",
                "switch_id": _param_str(e, DB.BuiltInParameter.RBS_ELEC_SWITCH_ID_PARAM),
                "z": round((p.Z - level_z_ft) * FT_TO_M, 2),
            }
            placed = False
            for g in groups:
                if abs(g["_x"] - p.X) < 0.2 and abs(g["_y"] - p.Y) < 0.2:  # 6 cm
                    g["keys"].append(entry)
                    placed = True
                    break
            if not placed:
                groups.append({"_x": p.X, "_y": p.Y,
                               "pos": _xyz_m(p, level_z_ft), "keys": [entry]})
        except Exception:
            continue
    for g in groups:
        del g["_x"]
        del g["_y"]
        points = [k for k in g["keys"] if k["role"] == "point"]
        keys = [k for k in g["keys"] if k["role"] == "key"]
        g["point_type"] = points[0]["type"] if points else ""
        g["keys"] = keys
        zs = [k["z"] for k in keys] or [k["z"] for k in points]
        g["pos"]["z"] = round(sum(zs) / len(zs), 2) if zs else 0.0
    groups.sort(key=lambda d: (-d["pos"]["y"], d["pos"]["x"]))
    return groups


def _doors(doc, room, level_z_ft, z_max_m):
    out = []
    try:
        phase = doc.GetElement(room.get_Parameter(DB.BuiltInParameter.ROOM_PHASE).AsElementId())
    except Exception:
        phase = None
    room_id = get_element_id_value(room)
    center = None
    try:
        lp = room.Location
        center = lp.Point if isinstance(lp, DB.LocationPoint) else None
    except Exception:
        pass
    for d in _collect_instances(doc, "OST_Doors"):
        try:
            belongs = False
            if phase is not None:
                for side in (d.get_FromRoom(phase), d.get_ToRoom(phase)):
                    if side is not None and get_element_id_value(side) == room_id:
                        belongs = True
            p = _location_point(d)
            if p is None:
                continue
            z_rel = (p.Z - level_z_ft) * FT_TO_M
            if z_rel < -0.3 or z_rel > z_max_m:
                continue
            if not belongs:
                # Doors hosted on the room boundary do not always report
                # From/To; fall back to proximity (0.4 m into the room).
                f = d.FacingOrientation
                for sign in (1.0, -1.0):
                    q = p + f * (0.4 * M_TO_FT * sign)
                    if _in_room(room, q, level_z_ft, z_max_m):
                        belongs = True
                        break
            if not belongs:
                continue
            fam, typ = _family_and_type(d)
            width_ft = 0.0
            for holder, bip in ((d, DB.BuiltInParameter.DOOR_WIDTH),
                                (d.Symbol, DB.BuiltInParameter.DOOR_WIDTH),
                                (d, DB.BuiltInParameter.FAMILY_WIDTH_PARAM),
                                (d.Symbol, DB.BuiltInParameter.FAMILY_WIDTH_PARAM)):
                try:
                    wp = holder.get_Parameter(bip)
                    if wp and wp.HasValue and wp.AsDouble() > 0:
                        width_ft = wp.AsDouble()
                        break
                except Exception:
                    continue
            if width_ft <= 0:
                try:
                    bbd = d.get_BoundingBox(None)
                    width_ft = max(bbd.Max.X - bbd.Min.X, bbd.Max.Y - bbd.Min.Y)
                except Exception:
                    pass
            f = d.FacingOrientation
            h = d.HandOrientation
            hinge = p - h * (width_ft * 0.5)
            latch = p + h * (width_ft * 0.5)
            # Facing points toward the swing side. Does it swing into this room?
            probe = p + f * (0.5 * M_TO_FT)
            swings_in = _in_room(room, probe, level_z_ft, z_max_m)
            other = ""
            if phase is not None:
                for side in (d.get_FromRoom(phase), d.get_ToRoom(phase)):
                    if side is not None and get_element_id_value(side) != room_id:
                        other = get_element_name(side)
            out.append({
                "id": get_element_id_value(d),
                "family": fam,
                "type": typ,
                "width_m": round(width_ft * FT_TO_M, 2),
                "pos": _xyz_m(p, level_z_ft),
                "hinge": _xyz_m(hinge, level_z_ft),
                "latch": _xyz_m(latch, level_z_ft),
                "swing": {"x": round(f.X, 3), "y": round(f.Y, 3)},
                "opens_into_this_room": bool(swings_in),
                "connects_to": other,
            })
        except Exception:
            continue
    out.sort(key=lambda d: (-d["pos"]["y"], d["pos"]["x"]))
    return out


def _furniture(doc, room, level_z_ft, z_max_m):
    out = []
    for bic_name in _FURNITURE_CATEGORIES:
        for e in _collect_instances(doc, bic_name):
            try:
                if e.SuperComponent is not None:
                    continue
                p = _location_point(e)
                if p is None or not _in_room(room, p, level_z_ft, z_max_m):
                    continue
                fam, typ = _family_and_type(e)
                bb = e.get_BoundingBox(None)
                size = None
                if bb:
                    size = {
                        "dx": round((bb.Max.X - bb.Min.X) * FT_TO_M, 2),
                        "dy": round((bb.Max.Y - bb.Min.Y) * FT_TO_M, 2),
                    }
                    if max(size["dx"], size["dy"]) < 0.3:
                        continue  # handles, knobs, small props
                out.append({
                    "id": get_element_id_value(e),
                    "category": bic_name.replace("OST_", ""),
                    "family": fam,
                    "type": typ,
                    "center": _xyz_m(p, level_z_ft),
                    "size_m": size,
                })
            except Exception:
                continue
    return out


def _view_family_type(doc, family):
    for vft in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType):
        try:
            if vft.ViewFamily == family:
                return vft
        except Exception:
            continue
    return None


def _configure_view(view, crop_min, crop_max, cut_m, top_m):
    """Crop, detail, scale, hidden categories and view range. Never raises."""
    try:
        view.ViewTemplateId = DB.ElementId.InvalidElementId
    except Exception:
        pass
    try:
        view.Scale = 50
    except Exception:
        pass
    try:
        view.DetailLevel = DB.ViewDetailLevel.Fine
    except Exception:
        pass
    try:
        view.CropBoxActive = True
        cb = view.CropBox
        cb.Min = DB.XYZ(crop_min.X, crop_min.Y, cb.Min.Z)
        cb.Max = DB.XYZ(crop_max.X, crop_max.Y, cb.Max.Z)
        view.CropBox = cb
        view.CropBoxVisible = False
    except Exception as e:
        logger.warning("crop failed: %s", e)
    try:
        view.get_Parameter(DB.BuiltInParameter.VIEWER_ANNOTATION_CROP_ACTIVE).Set(0)
    except Exception:
        pass
    for name in _HIDE_CATEGORIES:
        try:
            cid = DB.ElementId(_bic(name))
            if view.CanCategoryBeHidden(cid):
                view.SetCategoryHidden(cid, True)
        except Exception:
            continue
    try:
        vr = view.GetViewRange()
        is_rcp = view.ViewType == DB.ViewType.CeilingPlan
        if is_rcp:
            vr.SetOffset(DB.PlanViewPlane.ViewDepthPlane, (top_m + 0.1) * M_TO_FT)
            vr.SetOffset(DB.PlanViewPlane.TopClipPlane, top_m * M_TO_FT)
            vr.SetOffset(DB.PlanViewPlane.CutPlane, cut_m * M_TO_FT)
            vr.SetOffset(DB.PlanViewPlane.BottomClipPlane, 0.0)
        else:
            vr.SetOffset(DB.PlanViewPlane.TopClipPlane, top_m * M_TO_FT)
            vr.SetOffset(DB.PlanViewPlane.CutPlane, cut_m * M_TO_FT)
            vr.SetOffset(DB.PlanViewPlane.BottomClipPlane, 0.0)
            vr.SetOffset(DB.PlanViewPlane.ViewDepthPlane, 0.0)
        view.SetViewRange(vr)
    except Exception as e:
        logger.warning("view range failed: %s", e)


def _export_views(doc, views, width_px, folder, stamp):
    """Export each view to PNG (exact width, height by aspect). Returns
    {tag: filepath}."""
    result = {}
    for tag, view in views.items():
        prefix = os.path.join(folder, "rv_{}_{}".format(stamp, tag))
        ieo = DB.ImageExportOptions()
        ieo.ExportRange = DB.ExportRange.SetOfViews
        ids = List[DB.ElementId]()
        ids.Add(view.Id)
        ieo.SetViewsAndSheets(ids)
        ieo.FilePath = prefix
        ieo.HLRandWFViewsFileType = DB.ImageFileType.PNG
        ieo.ShadowViewsFileType = DB.ImageFileType.PNG
        ieo.ImageResolution = DB.ImageResolution.DPI_150
        ieo.ZoomType = DB.ZoomFitType.FitToPage
        ieo.FitDirection = DB.FitDirectionType.Horizontal
        ieo.PixelSize = int(width_px)
        ieo.ExportRange = DB.ExportRange.SetOfViews
        doc.ExportImage(ieo)
        base = os.path.basename(prefix)
        cands = [
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if f.startswith(base) and f.lower().endswith(".png")
        ]
        cands.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        if cands:
            result[tag] = cands[0]
    return result


def _handle_room_views(doc, request):
    if not doc:
        return routes.make_response(
            data={"error": "No active Revit document"}, status=503
        )
    try:
        data = json.loads(request.data) if isinstance(request.data, str) else request.data
    except Exception:
        data = {}
    data = data or {}
    room_query = data.get("room")
    if room_query in (None, ""):
        return routes.make_response(data={"error": "room is required"}, status=400)
    width_px = int(data.get("width_px", 1200) or 1200)
    width_px = max(400, min(width_px, 3000))
    margin_ft = float(data.get("margin_mm", 600) or 600) / 304.8
    wanted = data.get("views") or ["plan", "ceiling"]

    room, err = _find_room(doc, room_query)
    if room is None:
        payload = err if isinstance(err, dict) else {"error": err}
        return routes.make_response(data=payload, status=404)

    level = doc.GetElement(room.LevelId) if room.LevelId else None
    level_z = level.Elevation if level is not None else 0.0
    room_bb = room.get_BoundingBox(None)
    if room_bb is None:
        return routes.make_response(data={"error": "Room has no bounding box"}, status=500)

    ceilings = _ceiling_heights(doc, room_bb, level_z)
    ceil_top = max([c["height_m"] for c in ceilings] or [2.6])
    z_max = ceil_top + 0.6

    crop_min = DB.XYZ(room_bb.Min.X - margin_ft, room_bb.Min.Y - margin_ft, 0)
    crop_max = DB.XYZ(room_bb.Max.X + margin_ft, room_bb.Max.Y + margin_ft, 0)

    meta = {
        "room": {
            "id": get_element_id_value(room),
            "name": _param_str(room, DB.BuiltInParameter.ROOM_NAME) or get_element_name(room),
            "number": _param_str(room, DB.BuiltInParameter.ROOM_NUMBER),
            "level": get_element_name(level) if level is not None else "",
            "level_elevation_m": round(level_z * FT_TO_M, 3),
            "area_m2": round(room.Area * SQFT_TO_M2, 2),
            "bbox_m": {
                "min_x": round(room_bb.Min.X * FT_TO_M, 3),
                "min_y": round(room_bb.Min.Y * FT_TO_M, 3),
                "max_x": round(room_bb.Max.X * FT_TO_M, 3),
                "max_y": round(room_bb.Max.Y * FT_TO_M, 3),
            },
            "ceilings": ceilings,
        },
        "crop_m": {
            "min_x": round(crop_min.X * FT_TO_M, 4),
            "min_y": round(crop_min.Y * FT_TO_M, 4),
            "max_x": round(crop_max.X * FT_TO_M, 4),
            "max_y": round(crop_max.Y * FT_TO_M, 4),
        },
        "doors": _doors(doc, room, level_z, z_max),
        "luminaires": _luminaires(doc, room, level_z, z_max),
        "switch_points": _switches(doc, room, level_z, z_max),
        "furniture": _furniture(doc, room, level_z, z_max),
    }

    folder = os.path.join(tempfile.gettempdir(), "RevitMCPRoomViews")
    if not os.path.exists(folder):
        os.makedirs(folder)
    stamp = "{}_{}".format(get_element_id_value(room), int(time.time()))

    images = {}
    export_error = None
    tg = DB.TransactionGroup(doc, "MCP room views")
    tg.Start()
    created = {}
    try:
        t1 = DB.Transaction(doc, "MCP room views: create")
        t1.Start()
        suppress_warnings(t1)
        try:
            if level is None:
                raise Exception("Room has no level; cannot create plan views")
            specs = []
            if "plan" in wanted:
                specs.append(("plan", DB.ViewFamily.FloorPlan, 1.6, 2.3))
            if "ceiling" in wanted:
                specs.append(("ceiling", DB.ViewFamily.CeilingPlan, 1.2, ceil_top + 0.8))
            for tag, family, cut_m, top_m in specs:
                vft = _view_family_type(doc, family)
                if vft is None:
                    raise Exception("No ViewFamilyType for {}".format(tag))
                v = DB.ViewPlan.Create(doc, vft.Id, level.Id)
                try:
                    v.Name = "ZZ_MCP_ROOMVIEW_{}_{}".format(tag.upper(), stamp)
                except Exception:
                    pass
                _configure_view(v, crop_min, crop_max, cut_m, top_m)
                created[tag] = v
            t1.Commit()
        except Exception as e:
            if t1.HasStarted() and not t1.HasEnded():
                t1.RollBack()
            raise

        try:
            files = _export_views(doc, created, width_px, folder, stamp)
        except Exception as e:
            export_error = "ExportImage failed: {}".format(sanitize_string(str(e)))
            files = {}

        t2 = DB.Transaction(doc, "MCP room views: cleanup")
        t2.Start()
        suppress_warnings(t2)
        try:
            ids = List[DB.ElementId]()
            for v in created.values():
                ids.Add(v.Id)
            if ids.Count > 0:
                doc.Delete(ids)
            t2.Commit()
        except Exception:
            if t2.HasStarted() and not t2.HasEnded():
                t2.RollBack()
        tg.Assimilate()

        for tag, path in files.items():
            try:
                with open(path, "rb") as fh:
                    images[tag] = base64.b64encode(fh.read()).decode("utf-8")
            except Exception as e:
                export_error = "Could not read {}: {}".format(tag, sanitize_string(str(e)))
            finally:
                try:
                    os.remove(path)
                except Exception:
                    pass
    except Exception as e:
        try:
            if tg.HasStarted() and not tg.HasEnded():
                tg.RollBack()
        except Exception:
            pass
        return routes.make_response(
            data={"error": "room_views failed: {}".format(sanitize_string(str(e))),
                  "meta": meta},
            status=500,
        )

    meta["images"] = images
    meta["width_px"] = width_px
    if export_error:
        meta["export_error"] = export_error
    return routes.make_response(data=meta)


def register_room_views_routes(api):
    """Register room view routes with the API"""

    @api.route("/room_views/", methods=["POST"])
    def room_views(doc, request):
        """
        Payload: {"room": "<name|number|id>", "width_px": 1200,
                  "margin_mm": 600, "views": ["plan", "ceiling"]}
        Returns PNGs (base64) of a cropped floor plan and ceiling plan of the
        room plus metadata in metres (project coordinates; z above the level).
        Logic lives in _handle_room_views so it can be hot-reloaded.
        """
        return _handle_room_views(doc, request)
