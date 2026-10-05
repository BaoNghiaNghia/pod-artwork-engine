from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageColor, ImageDraw

from .contracts import (
    GeometryFillRule,
    GeometryKind,
    GeometryPrimitive,
    GeometrySpec,
    GeometryTopologyEvidence,
    PathCommandKind,
)


class GeometryRenderUnavailable(RuntimeError):
    pass


def _color(value: str | None) -> tuple[int, int, int, int] | None:
    if value is None:
        return None
    try:
        return ImageColor.getcolor(value, "RGBA")
    except ValueError as exc:
        raise GeometryRenderUnavailable(f"invalid geometry color: {value}") from exc


def _bbox_pixels(
    primitive: GeometryPrimitive,
    canvas: Image.Image,
) -> tuple[int, int, int, int]:
    if primitive.bbox is None:
        raise GeometryRenderUnavailable(
            f"{primitive.kind.value} primitive requires bbox"
        )
    bbox = primitive.bbox
    left = max(0, min(canvas.width - 1, round(bbox.x * canvas.width)))
    top = max(0, min(canvas.height - 1, round(bbox.y * canvas.height)))
    right = max(
        left + 1,
        min(canvas.width, round((bbox.x + bbox.width) * canvas.width)),
    )
    bottom = max(
        top + 1,
        min(canvas.height, round((bbox.y + bbox.height) * canvas.height)),
    )
    return left, top, right, bottom


def _points_pixels(
    primitive: GeometryPrimitive,
    canvas: Image.Image,
) -> list[tuple[int, int]]:
    return [
        (
            max(0, min(canvas.width - 1, round(point.x * canvas.width))),
            max(0, min(canvas.height - 1, round(point.y * canvas.height))),
        )
        for point in primitive.points
    ]


def _path_subpaths_pixels(
    primitive: GeometryPrimitive,
    canvas: Image.Image,
    *,
    cubic_steps: int = 24,
) -> list[tuple[list[tuple[int, int]], bool]]:
    subpaths: list[tuple[list[tuple[int, int]], bool]] = []
    points: list[tuple[int, int]] = []
    current: tuple[float, float] | None = None
    start: tuple[float, float] | None = None
    closed = False

    def scaled(point) -> tuple[float, float]:
        return (
            max(0.0, min(canvas.width - 1.0, point.x * canvas.width)),
            max(0.0, min(canvas.height - 1.0, point.y * canvas.height)),
        )

    def flush() -> None:
        nonlocal points, current, start, closed
        if points:
            subpaths.append((points, closed))
        points = []
        current = None
        start = None
        closed = False

    for command in primitive.path:
        if command.kind is PathCommandKind.MOVE:
            flush()
            current = scaled(command.points[0])
            start = current
            points = [(round(current[0]), round(current[1]))]
        elif command.kind is PathCommandKind.LINE:
            if current is None:
                raise GeometryRenderUnavailable(
                    "line path command requires a current point"
                )
            current = scaled(command.points[0])
            points.append((round(current[0]), round(current[1])))
        elif command.kind is PathCommandKind.CUBIC:
            if current is None:
                raise GeometryRenderUnavailable(
                    "cubic path command requires a current point"
                )
            c1 = scaled(command.points[0])
            c2 = scaled(command.points[1])
            end = scaled(command.points[2])
            x0, y0 = current
            for step in range(1, cubic_steps + 1):
                t = step / cubic_steps
                mt = 1.0 - t
                x = (
                    mt ** 3 * x0
                    + 3 * mt ** 2 * t * c1[0]
                    + 3 * mt * t ** 2 * c2[0]
                    + t ** 3 * end[0]
                )
                y = (
                    mt ** 3 * y0
                    + 3 * mt ** 2 * t * c1[1]
                    + 3 * mt * t ** 2 * c2[1]
                    + t ** 3 * end[1]
                )
                points.append((round(x), round(y)))
            current = end
        elif command.kind is PathCommandKind.CLOSE:
            if start is None or not points:
                raise GeometryRenderUnavailable(
                    "close path command requires an active subpath"
                )
            start_pixel = (round(start[0]), round(start[1]))
            if points[-1] != start_pixel:
                points.append(start_pixel)
            current = start
            closed = True
    flush()
    return subpaths


def _path_subpath_count(primitive: GeometryPrimitive) -> int:
    if primitive.kind is not GeometryKind.PATH:
        return 0
    return sum(command.kind is PathCommandKind.MOVE for command in primitive.path)


def geometry_topology_evidence(spec: GeometrySpec) -> GeometryTopologyEvidence:
    path_primitives = [
        primitive
        for primitive in spec.primitives
        if primitive.kind is GeometryKind.PATH
    ]
    compound = [
        primitive
        for primitive in path_primitives
        if _path_subpath_count(primitive) > 1
    ]
    fill_rules = sorted(
        {primitive.fill_rule for primitive in path_primitives},
        key=lambda item: item.value,
    )
    return GeometryTopologyEvidence(
        evidence_provider=spec.evidence_provider,
        evidence_version=spec.evidence_version,
        primitive_count=len(spec.primitives),
        path_primitive_count=len(path_primitives),
        subpath_count=sum(_path_subpath_count(item) for item in path_primitives),
        compound_path_count=len(compound),
        evenodd_compound_fill_count=sum(
            item.fill is not None
            and item.fill_rule is GeometryFillRule.EVENODD
            for item in compound
        ),
        fill_rules=fill_rules,
    )


def _validate_primitive(primitive: GeometryPrimitive) -> None:
    if primitive.confidence < 0.80:
        raise GeometryRenderUnavailable(
            f"geometry primitive confidence too low: {primitive.kind.value}"
        )
    if primitive.fill is None and primitive.stroke is None:
        raise GeometryRenderUnavailable(
            f"geometry primitive has no fill or stroke: {primitive.kind.value}"
        )
    if primitive.kind in {GeometryKind.RECT, GeometryKind.ELLIPSE}:
        if primitive.bbox is None:
            raise GeometryRenderUnavailable(
                f"{primitive.kind.value} primitive requires bbox"
            )
    elif primitive.kind is GeometryKind.LINE:
        if len(primitive.points) != 2:
            raise GeometryRenderUnavailable("line primitive requires exactly two points")
        if primitive.stroke is None:
            raise GeometryRenderUnavailable("line primitive requires stroke")
    elif primitive.kind is GeometryKind.POLYGON:
        if len(primitive.points) < 3:
            raise GeometryRenderUnavailable(
                "polygon primitive requires at least three points"
            )
    elif primitive.kind is GeometryKind.PATH:
        if not primitive.path:
            raise GeometryRenderUnavailable("path primitive requires path commands")
        if primitive.path[0].kind is not PathCommandKind.MOVE:
            raise GeometryRenderUnavailable("path primitive must start with move")

        active = False
        closed = False
        drawable_segments = 0
        completed_subpaths: list[tuple[bool, int]] = []
        for command in primitive.path:
            required = {
                PathCommandKind.MOVE: 1,
                PathCommandKind.LINE: 1,
                PathCommandKind.CUBIC: 3,
                PathCommandKind.CLOSE: 0,
            }[command.kind]
            if len(command.points) != required:
                raise GeometryRenderUnavailable(
                    f"{command.kind.value} path command requires {required} points"
                )

            if command.kind is PathCommandKind.MOVE:
                if active:
                    completed_subpaths.append((closed, drawable_segments))
                active = True
                closed = False
                drawable_segments = 0
                continue

            if not active:
                raise GeometryRenderUnavailable(
                    f"{command.kind.value} path command requires an active subpath"
                )
            if closed:
                raise GeometryRenderUnavailable(
                    "a closed subpath must be followed by move or end"
                )

            if command.kind in {PathCommandKind.LINE, PathCommandKind.CUBIC}:
                drawable_segments += 1
            elif command.kind is PathCommandKind.CLOSE:
                if drawable_segments < 2:
                    raise GeometryRenderUnavailable(
                        "closed path subpath requires at least two drawable segments"
                    )
                closed = True

        if active:
            completed_subpaths.append((closed, drawable_segments))

        if not completed_subpaths:
            raise GeometryRenderUnavailable("path primitive contains no subpaths")
        if any(segments < 1 for _, segments in completed_subpaths):
            raise GeometryRenderUnavailable(
                "path subpath requires at least one drawable segment"
            )
        if primitive.fill is not None and any(
            not is_closed for is_closed, _ in completed_subpaths
        ):
            raise GeometryRenderUnavailable(
                "every filled path subpath must be explicitly closed"
            )


def validate_geometry_spec(spec: GeometrySpec) -> None:
    if not spec.primitives:
        raise GeometryRenderUnavailable("geometry spec contains no primitives")
    if spec.confidence < 0.80:
        raise GeometryRenderUnavailable("geometry spec confidence is too low")
    for primitive in spec.primitives:
        _validate_primitive(primitive)


def apply_geometry(
    canvas: Image.Image,
    spec: GeometrySpec,
) -> Image.Image:
    validate_geometry_spec(spec)
    result = canvas.convert("RGBA").copy()
    draw = ImageDraw.Draw(result)

    for primitive in spec.primitives:
        fill = _color(primitive.fill)
        stroke = _color(primitive.stroke)
        width = max(
            1,
            round(primitive.stroke_width_ratio * max(result.width, result.height)),
        )

        if primitive.kind is GeometryKind.RECT:
            draw.rectangle(
                _bbox_pixels(primitive, result),
                fill=fill,
                outline=stroke,
                width=width if stroke else 1,
            )
        elif primitive.kind is GeometryKind.ELLIPSE:
            draw.ellipse(
                _bbox_pixels(primitive, result),
                fill=fill,
                outline=stroke,
                width=width if stroke else 1,
            )
        elif primitive.kind is GeometryKind.LINE:
            draw.line(
                _points_pixels(primitive, result),
                fill=stroke,
                width=width,
                joint="curve",
            )
        elif primitive.kind is GeometryKind.POLYGON:
            points = _points_pixels(primitive, result)
            draw.polygon(points, fill=fill)
            if stroke:
                closed = [*points, points[0]]
                draw.line(closed, fill=stroke, width=width, joint="curve")
        elif primitive.kind is GeometryKind.PATH:
            subpaths = _path_subpaths_pixels(primitive, result)
            if not subpaths:
                raise GeometryRenderUnavailable(
                    "path primitive produced no drawable subpaths"
                )

            if fill is not None:
                if len(subpaths) == 1:
                    points, closed = subpaths[0]
                    if not closed or len(points) < 4:
                        raise GeometryRenderUnavailable(
                            "filled path primitive must be explicitly closed"
                        )
                    draw.polygon(points, fill=fill)
                else:
                    if primitive.fill_rule is not GeometryFillRule.EVENODD:
                        raise GeometryRenderUnavailable(
                            "compound filled path requires evenodd fill rule "
                            "for deterministic rasterization"
                        )
                    mask = Image.new("L", result.size, 0)
                    for points, closed in subpaths:
                        if not closed or len(points) < 4:
                            raise GeometryRenderUnavailable(
                                "compound filled path contains an open or "
                                "degenerate subpath"
                            )
                        contour = Image.new("L", result.size, 0)
                        ImageDraw.Draw(contour).polygon(points, fill=255)
                        mask = ImageChops.difference(mask, contour)
                    fill_layer = Image.new("RGBA", result.size, fill)
                    result.paste(fill_layer, (0, 0), mask)
                    draw = ImageDraw.Draw(result)

            if stroke is not None:
                for points, _closed in subpaths:
                    if len(points) < 2:
                        raise GeometryRenderUnavailable(
                            "path subpath produced too few points"
                        )
                    draw.line(
                        points,
                        fill=stroke,
                        width=width,
                        joint="curve",
                    )
        else:
            raise GeometryRenderUnavailable(
                f"unsupported geometry primitive: {primitive.kind}"
            )

    return result


def render_geometry_master(
    spec: GeometrySpec,
    output_path: Path,
    *,
    canvas_size: tuple[int, int] = (3000, 3000),
) -> Path:
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    canvas = apply_geometry(canvas, spec)
    if canvas.getchannel("A").getbbox() is None:
        raise GeometryRenderUnavailable("geometry renderer produced an empty image")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=True)
    return output_path


def geometry_to_svg(
    spec: GeometrySpec,
    output_path: Path,
    *,
    view_box: tuple[int, int] = (3000, 3000),
) -> Path:
    validate_geometry_spec(spec)
    width, height = view_box

    def svg_color(value: str | None) -> str:
        parsed = _color(value)
        if parsed is None:
            return "none"
        red, green, blue, alpha = parsed
        if alpha == 255:
            return f"#{red:02x}{green:02x}{blue:02x}"
        return f"rgba({red},{green},{blue},{alpha / 255:.4f})"

    elements: list[str] = []
    for primitive in spec.primitives:
        stroke_width = max(
            1,
            round(primitive.stroke_width_ratio * max(width, height)),
        )
        common = (
            f'fill="{svg_color(primitive.fill)}" '
            f'stroke="{svg_color(primitive.stroke)}" '
            f'stroke-width="{stroke_width}"'
        )
        if primitive.kind is GeometryKind.RECT:
            if primitive.bbox is None:
                raise GeometryRenderUnavailable("rect primitive requires bbox")
            bbox = primitive.bbox
            elements.append(
                f'<rect x="{bbox.x * width:.3f}" y="{bbox.y * height:.3f}" '
                f'width="{bbox.width * width:.3f}" height="{bbox.height * height:.3f}" '
                f'{common}/>'
            )
        elif primitive.kind is GeometryKind.ELLIPSE:
            if primitive.bbox is None:
                raise GeometryRenderUnavailable("ellipse primitive requires bbox")
            bbox = primitive.bbox
            cx = (bbox.x + bbox.width / 2) * width
            cy = (bbox.y + bbox.height / 2) * height
            rx = bbox.width * width / 2
            ry = bbox.height * height / 2
            elements.append(
                f'<ellipse cx="{cx:.3f}" cy="{cy:.3f}" rx="{rx:.3f}" ry="{ry:.3f}" '
                f'{common}/>'
            )
        elif primitive.kind is GeometryKind.LINE:
            points = primitive.points
            elements.append(
                f'<line x1="{points[0].x * width:.3f}" y1="{points[0].y * height:.3f}" '
                f'x2="{points[1].x * width:.3f}" y2="{points[1].y * height:.3f}" '
                f'{common}/>'
            )
        elif primitive.kind is GeometryKind.POLYGON:
            points = " ".join(
                f"{point.x * width:.3f},{point.y * height:.3f}"
                for point in primitive.points
            )
            elements.append(f'<polygon points="{points}" {common}/>')
        elif primitive.kind is GeometryKind.PATH:
            commands: list[str] = []
            for command in primitive.path:
                if command.kind is PathCommandKind.MOVE:
                    point = command.points[0]
                    commands.append(
                        f"M {point.x * width:.3f} {point.y * height:.3f}"
                    )
                elif command.kind is PathCommandKind.LINE:
                    point = command.points[0]
                    commands.append(
                        f"L {point.x * width:.3f} {point.y * height:.3f}"
                    )
                elif command.kind is PathCommandKind.CUBIC:
                    c1, c2, end = command.points
                    commands.append(
                        "C "
                        f"{c1.x * width:.3f} {c1.y * height:.3f} "
                        f"{c2.x * width:.3f} {c2.y * height:.3f} "
                        f"{end.x * width:.3f} {end.y * height:.3f}"
                    )
                elif command.kind is PathCommandKind.CLOSE:
                    commands.append("Z")
            elements.append(
                f'<path d="{" ".join(commands)}" '
                f'fill-rule="{primitive.fill_rule.value}" {common}/>'
            )

    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width} {height}" width="{width}" height="{height}">\n'
        + "\n".join(elements)
        + "\n</svg>\n"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(svg, encoding="utf-8")
    return output_path
