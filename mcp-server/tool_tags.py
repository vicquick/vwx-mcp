"""Tool tag taxonomy + workflow presets for vwx-mcp.

Foundation for the FastMCP 3.x Visibility API (mcp.enable/disable(tags=...)).
NOT yet wired into vwx_mcp_server.py — that happens in migration phase P2,
after the bundled -> standalone fastmcp swap (see docs/MIGRATION_fastmcp3.md).

Until then this is a reviewable, testable single source of truth: every one of
the 150 @mcp.tool verbs maps to exactly one primary tag. Presets select which
tags load for a given workflow, cutting tool-overload token cost.

Wiring sketch (P2), in vwx_mcp_server.py main():

    from tool_tags import preset_tags
    import os
    sel = os.environ.get("VWX_TOOLSET", "full")
    if sel != "full":
        mcp.enable(tags=preset_tags(sel), only=True)
"""

# tool name -> primary tag (mirrors the section banners in vwx_mcp_server.py)
TOOL_TAGS = {
    # document
    "ping": "document",
    "get_document_info": "document",
    "save_document": "document",
    "save_document_as": "document",
    "get_document_preferences": "document",
    "set_document_preferences": "document",
    # layers
    "get_layers": "layers",
    "get_layer_info": "layers",
    "create_layer": "layers",
    "delete_layer": "layers",
    "set_active_layer": "layers",
    "get_active_layer": "layers",
    "set_layer_visibility": "layers",
    "rename_layer": "layers",
    "set_layer_scale": "layers",
    # classes
    "get_classes": "classes",
    "create_class": "classes",
    "delete_class": "classes",
    "set_active_class": "classes",
    "set_class_visibility": "classes",
    "rename_class": "classes",
    "set_class_appearance": "classes",
    # query (read-only inspection)
    "get_objects": "query",
    "get_object_info": "query",
    "get_selected_objects": "query",
    "select_objects": "query",
    "deselect_all": "query",
    "get_object_bounds": "query",
    "count_objects": "query",
    "find_objects_by_name": "query",
    "for_each_criteria": "query",
    "offset_polygon": "query",
    "polygon_centroid": "query",
    # manipulate
    "move_object": "manipulate",
    "rotate_object": "manipulate",
    "scale_object": "manipulate",
    "delete_object": "manipulate",
    "duplicate_object": "manipulate",
    "set_object_layer": "manipulate",
    "set_object_class": "manipulate",
    "set_object_name": "manipulate",
    "group_objects": "manipulate",
    "ungroup_object": "manipulate",
    "mirror_object": "manipulate",
    "align_objects": "manipulate",
    "distribute_objects": "manipulate",
    # draw2d
    "draw_line": "draw2d",
    "draw_rectangle": "draw2d",
    "draw_circle": "draw2d",
    "draw_arc": "draw2d",
    "draw_polyline": "draw2d",
    "draw_text": "draw2d",
    "draw_ellipse": "draw2d",
    "draw_dimension": "draw2d",
    "draw_spline": "draw2d",
    "create_linear_dimension": "draw2d",
    "draw_rounded_rect": "draw2d",
    "draw_regular_polygon": "draw2d",
    # draw3d
    "draw_extrude": "draw3d",
    "draw_box": "draw3d",
    "draw_sphere": "draw3d",
    "draw_cone": "draw3d",
    "draw_cylinder": "draw3d",
    "boolean_operation": "draw3d",
    "solid_boolean": "draw3d",
    "set_3d_view": "draw3d",
    # symbols
    "get_symbols": "symbols",
    "place_symbol": "symbols",
    "get_symbol_instances": "symbols",
    "create_symbol_from_objects": "symbols",
    "delete_symbol": "symbols",
    "rename_symbol": "symbols",
    # appearance
    "set_fill_color": "appearance",
    "set_pen_color": "appearance",
    "set_line_weight": "appearance",
    "set_fill_pattern": "appearance",
    "set_opacity": "appearance",
    "get_appearance": "appearance",
    "set_marker": "appearance",
    "set_text_style": "appearance",
    "list_hatches": "appearance",
    "set_hatch_on_object": "appearance",
    "create_static_hatch": "appearance",
    "get_textures": "appearance",
    "apply_texture": "appearance",
    # records
    "get_record_formats": "records",
    "get_object_records": "records",
    "get_record_field": "records",
    "set_record_field": "records",
    "attach_record": "records",
    "detach_record": "records",
    "create_record_format": "records",
    # bim (architectural / ifc / materials / pio / components)
    "get_ifc_entity": "bim",
    "set_ifc_entity": "bim",
    "get_ifc_properties": "bim",
    "set_ifc_property": "bim",
    "export_ifc": "bim",
    "create_wall": "bim",
    "create_space": "bim",
    "get_spaces": "bim",
    "get_walls": "bim",
    "create_pio": "bim",
    "get_pio_parameters": "bim",
    "set_pio_parameter": "bim",
    "create_material": "bim",
    "assign_material": "bim",
    "list_components": "bim",
    "insert_component": "bim",
    # landscape (Baumkataster)
    "get_plants": "landscape",
    "create_plant": "landscape",
    "update_plant": "landscape",
    "get_plant_database": "landscape",
    "batch_update_plants": "landscape",
    "baumkataster_set_fields": "landscape",
    # site
    "get_site_model_info": "site",
    "update_site_model": "site",
    "get_terrain_elevation": "site",
    "send_to_surface": "site",
    "get_z_at_xy": "site",
    # viewports
    "get_viewports": "viewports",
    "create_viewport": "viewports",
    "update_viewport": "viewports",
    "set_viewport_scale": "viewports",
    "set_viewport_crop": "viewports",
    "add_vp_class_override": "viewports",
    "list_vp_class_overrides": "viewports",
    # worksheets
    "get_worksheets": "worksheets",
    "create_worksheet": "worksheets",
    "get_worksheet_data": "worksheets",
    "set_worksheet_cell": "worksheets",
    "recalculate_worksheet": "worksheets",
    # io (export/import)
    "export_pdf": "io",
    "export_dxf": "io",
    "export_image": "io",
    "import_dwg": "io",
    "export_shp": "io",
    "import_image": "io",
    # view
    "zoom_to_fit": "view",
    "zoom_to_selection": "view",
    "set_zoom": "view",
    "refresh_view": "view",
    # geo (georeferencing)
    "set_georeferencing": "geo",
    "get_georeferencing": "geo",
    # escape (power-user escape hatches — keep available by default)
    "execute_script": "escape",
    "run_menu_command": "escape",
    "vwx": "escape",
    "vwx_batch": "escape",
    "list_commands": "escape",
    "set_object_variable": "escape",
    "get_object_variable": "escape",
}

# workflow presets -> set of tags to enable (only=True). "full" = no filtering.
PRESETS = {
    "full": None,  # sentinel: enable everything
    "gis": {"query", "layers", "classes", "appearance", "io", "geo",
            "records", "document", "escape"},
    "modeling": {"draw2d", "draw3d", "manipulate", "bim", "symbols",
                 "appearance", "query", "document", "view"},
    "baumkataster": {"landscape", "records", "query", "layers", "document",
                     "io", "escape"},
    "minimal": {"document", "query", "escape"},
}


def preset_tags(preset: str):
    """Return the tag set for a preset name, or None for 'full'/unknown."""
    return PRESETS.get(preset)


def all_tags():
    """Every distinct tag in use."""
    return sorted(set(TOOL_TAGS.values()))
