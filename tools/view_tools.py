# -*- coding: utf-8 -*-
"""View-related tools for capturing and listing Revit views"""

import base64

from mcp.server.fastmcp import Context, Image
from .utils import format_response
from . import room_view_render as rvr


def register_view_tools(mcp, revit_get, revit_post, revit_image):
    """Register view-related tools"""

    @mcp.tool()
    async def get_revit_view(view_name: str, ctx: Context = None):
        """Export a specific Revit view as an image"""
        # revit_image returns an Image object (delivered as image content)
        # or an error string. Never stringify the Image.
        return await revit_image(f"/get_view/{view_name}", ctx)

    @mcp.tool()
    async def get_room_views(
        room: str,
        width_px: int = 1200,
        margin_mm: int = 600,
        ctx: Context = None,
    ):
        """LOOK at one room before positioning lights, switches or lighting
        commands. Returns a cropped FLOOR PLAN and CEILING PLAN of the room
        as images, stamped with markers, plus a compact text summary.

        Markers: L1..Ln = luminaires (on the ceiling plan), S1..Sn = switch
        points (on the floor plan; stacked keys share one point), D1..Dn =
        doors (small dot = hinge side). The summary lists each marker with
        project coordinates in metres, pixel position, current letter
        (Comments on luminaires / Switch ID on keys) and door swing.

        Use this BEFORE deciding "which key commands which luminaire" and
        AGAIN after writing, to verify. Reason in room language (beside the
        door, over the table), not in coordinates.

        Args:
            room: room name, number or element id (accent-insensitive)
            width_px: image width in pixels (400-3000, default 1200)
            margin_mm: crop margin around the room (default 600)
        """
        if ctx:
            await ctx.info(f"Exporting room views for '{room}'...")
        payload = {"room": room, "width_px": width_px, "margin_mm": margin_mm,
                   "views": ["plan", "ceiling"]}
        response = await revit_post("/room_views/", payload, ctx, timeout=180.0)
        if not isinstance(response, dict) or response.get("error"):
            return format_response(response)

        groups = rvr.label_items(response)
        crop = response.get("crop_m", {})
        images = response.get("images", {}) or {}
        out = []
        plan_img = ceiling_img = None
        if "plan" in images:
            raw = base64.b64decode(images["plan"])
            plan_bytes, _ = rvr.stamp(raw, crop, groups["D"], "D")
            plan_bytes, _ = rvr.stamp(plan_bytes, crop, groups["S"], "S")
            plan_img = Image(data=plan_bytes, format="png")
        if "ceiling" in images:
            raw = base64.b64decode(images["ceiling"])
            ceiling_bytes, _ = rvr.stamp(raw, crop, groups["L"], "L")
            ceiling_img = Image(data=ceiling_bytes, format="png")

        out.append(rvr.summarize(response, groups))
        if plan_img is not None:
            out.append("PLANTA BAIXA (S = interruptores, D = portas):")
            out.append(plan_img)
        if ceiling_img is not None:
            out.append("PLANTA DE FORRO (L = luminarias):")
            out.append(ceiling_img)
        return out

    @mcp.tool()
    async def list_revit_views(ctx: Context = None) -> str:
        """Get a list of all exportable views in the current Revit model"""
        response = await revit_get("/list_views/", ctx)
        return format_response(response)

    @mcp.tool()
    async def get_current_view_info(ctx: Context = None) -> str:
        """
        Get detailed information about the currently active view in Revit.

        Returns comprehensive information including:
        - View name, type, and ID
        - Scale and detail level
        - Crop box status
        - View family type
        - View discipline
        - Template status
        """
        if ctx:
            await ctx.info("Getting current view information...")
        response = await revit_get("/current_view_info/", ctx)
        return format_response(response)

    @mcp.tool()
    async def get_current_view_elements(ctx: Context = None) -> str:
        """
        Get all elements visible in the currently active view in Revit.

        Returns detailed information about each element including:
        - Element ID, name, and type
        - Category and category ID
        - Level information (if applicable)
        - Location information (point or curve)
        - Summary statistics grouped by category

        This is useful for understanding what elements are currently visible
        and analyzing the content of the active view.
        """
        if ctx:
            await ctx.info("Getting elements in current view...")
        response = await revit_get("/current_view_elements/", ctx)
        return format_response(response)
