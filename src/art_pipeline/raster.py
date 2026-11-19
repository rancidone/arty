"""Explicit pixel operations. No automatic crop, quantization or head masks."""
from collections import Counter
from PIL import Image, ImageDraw
import math
import warnings
import numpy as np
from .core import Grid, grid_from_size, sample
from .sheet import make_layout, pack_sheet


def pixels(image):
    return image.get_flattened_data() if hasattr(image, "get_flattened_data") else image.getdata()


# Per-image and aggregate decoded-layer budgets (RGBA8 is four bytes/pixel).
MAX_PIXELS = 16_000_000
MAX_BUILD_PIXELS = 32_000_000
MAX_PALETTE_COLORS = 65_536
MEASURE_TILE = 256


def checked_size(size):
    if (len(size) != 2 or any(type(v) is not int or v <= 0 for v in size)
            or size[0] * size[1] > MAX_PIXELS):
        raise ValueError("Image dimensions must be positive integers within the 16 megapixel production safety limit")
    return tuple(size)


def load(path):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if image.format != "PNG":
                    raise ValueError(f"Expected PNG: {path}")
                checked_size(image.size)
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Input must be a single PNG, not an animation")
                image.load()
                return image.convert("RGBA")
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        raise ValueError("PNG exceeds the decompression safety limit") from error


def bounds(image):
    box = image.getchannel("A").getbbox()
    return [box[0], box[1], box[2]-box[0], box[3]-box[1]] if box else None


def measure(image):
    """Measure in bounded tiles; never materialize Python objects per image pixel.

    The palette has a separate bound because its JSON representation is itself
    potentially large. A rejected measurement leaves API bytes available as a draft.
    """
    checked_size(image.size)
    alpha = np.zeros(256, dtype=np.int64)
    colors = Counter()
    visible_pixels = isolated = hidden = 0
    for y in range(0, image.height, MEASURE_TILE):
        for x in range(0, image.width, MEASURE_TILE):
            w, h = min(MEASURE_TILE, image.width-x), min(MEASURE_TILE, image.height-y)
            tile = np.asarray(image.crop((x, y, x+w, y+h)), dtype=np.uint8)
            a = tile[:, :, 3]
            visible = a != 0
            visible_pixels += int(np.count_nonzero(visible))
            alpha += np.bincount(a.ravel(), minlength=256)
            hidden += int(np.count_nonzero(~visible & np.any(tile[:, :, :3] != 0, axis=2)))
            rgb = tile[:, :, :3].astype(np.uint32)
            codes = (rgb[:, :, 0] << 16) | (rgb[:, :, 1] << 8) | rgb[:, :, 2]
            values, counts = np.unique(codes[visible], return_counts=True)
            colors.update({int(v): int(n) for v, n in zip(values, counts)})
            if len(colors) > MAX_PALETTE_COLORS:
                raise ValueError(f"Visible palette exceeds {MAX_PALETTE_COLORS} colors; use pixel-art inputs")
            # One-pixel halo includes neighbors across tile boundaries. Pillow
            # fills outside-image crop pixels with RGBA0.
            halo = np.asarray(image.crop((x-1, y-1, x+w+1, y+h+1)))[:, :, 3] != 0
            neighbors = (halo[:-2, 1:-1] | halo[2:, 1:-1] |
                         halo[1:-1, :-2] | halo[1:-1, 2:])
            isolated += int(np.count_nonzero(visible & ~neighbors))
    return {"dimensions": list(image.size), "bounds_xywh": bounds(image),
            "visible_pixels": visible_pixels,
            "partial_alpha_pixels": int(alpha[1:255].sum()),
            "alpha_counts": {str(k): int(n) for k, n in enumerate(alpha) if n},
            "palette": {f"#{k:06X}": n for k, n in sorted(colors.items())},
            "isolated_pixels": isolated, "transparent_rgb_noise": hidden}


def compose(images, canvas):
    result = Image.new("RGBA", checked_size(canvas))
    for image in images:
        if image.size != tuple(canvas):
            raise ValueError("Layer must retain the full registered canvas")
        result = Image.alpha_composite(result, image)
    return result


def same(a, b):
    return a.size == b.size and a.tobytes() == b.tobytes()


def adapt(image, operations):
    checked_size(image.size)
    image = image.copy()
    for op in operations:
        kind = op["op"]
        if kind == "nearest":
            size = checked_size(op["size"])
            image = image.resize(size, Image.Resampling.NEAREST)
        elif kind == "sample_grid":
            grid = Grid(**op["grid"]) if "grid" in op else grid_from_size(image.size, tuple(op["size"]))
            checked_size((grid.width, grid.height))
            image = sample(image, grid)
        elif kind == "crop":
            x,y,w,h = op["rect"]
            if any(not isinstance(v,int) for v in (x,y,w,h)) or min(x,y)<0 or min(w,h)<=0 or x+w>image.width or y+h>image.height:
                raise ValueError("Crop must be inside the source")
            box = image.getchannel("A").getbbox()
            if not op.get("allow_pixel_loss",False) and box and (box[0]<x or box[1]<y or box[2]>x+w or box[3]>y+h):
                raise ValueError("Crop would discard visible pixels; explicitly allow_pixel_loss or mask first")
            image = image.crop((x,y,x+w,y+h))
        elif kind == "rotate":
            angle = op["degrees_cw"]
            if not isinstance(angle,(int,float)) or not math.isfinite(angle):
                raise ValueError("Rotation must be finite")
            normalized = angle % 360
            if normalized in (0, 180):
                expanded = image.size
            elif normalized in (90, 270):
                expanded = image.size[::-1]
            else:
                radians = math.radians(normalized)
                c, s = abs(math.cos(radians)), abs(math.sin(radians))
                # Conservative bound for Pillow's rounded, expanded canvas.
                expanded = (math.ceil(image.width*c + image.height*s) + 2,
                            math.ceil(image.width*s + image.height*c) + 2)
            checked_size(expanded)
            image = image.rotate(-normalized, resample=Image.Resampling.NEAREST, expand=True)
        elif kind == "mask":
            # Editable pixel-index polygons/rectangles; retain full canvas and RGBA.
            mode = op.get("mode","keep")
            if mode not in {"keep","erase"}:
                raise ValueError("Mask mode must be keep or erase")
            mask = Image.new("1",image.size)
            draw = ImageDraw.Draw(mask)
            for polygon in op.get("polygons",[]):
                if len(polygon)<3 or any(len(p)!=2 or any(not isinstance(v,int) for v in p) or not 0<=p[0]<image.width or not 0<=p[1]<image.height for p in polygon):
                    raise ValueError("Invalid mask polygon")
                draw.polygon([tuple(p) for p in polygon],fill=1)
            for rect in op.get("rects",[]):
                x,y,w,h = rect
                if any(not isinstance(v,int) for v in rect) or min(x,y)<0 or min(w,h)<=0 or x+w>image.width or y+h>image.height:
                    raise ValueError("Invalid mask rectangle")
                draw.rectangle((x,y,x+w-1,y+h-1),fill=1)
            if not op.get("polygons") and not op.get("rects"):
                raise ValueError("Mask needs at least one selection")
            values = np.array(image)
            selected = np.asarray(mask, dtype=bool)
            values[selected != (mode == "keep")] = 0
            image = Image.fromarray(values)
        elif kind == "place":
            canvas, offset = checked_size(op["canvas"]), tuple(op["offset"])
            box = image.getchannel("A").getbbox()
            if box and (box[0]+offset[0] < 0 or box[1]+offset[1] < 0 or
                        box[2]+offset[0] > canvas[0] or box[3]+offset[1] > canvas[1]):
                raise ValueError("Placement would crop visible pixels")
            result = Image.new("RGBA", canvas)
            result.paste(image, offset)  # copy RGBA; do not multiply alpha twice
            image = result
        elif kind == "alpha_threshold":
            threshold = op["value"]
            if not 1 <= threshold <= 255:
                raise ValueError("Alpha threshold must be 1..255")
            image.putalpha(image.getchannel("A").point(lambda a: 255 if a >= threshold else 0))
        elif kind == "retain_rect":
            # Deliberately destructive selection, declared in provenance; canvas unchanged.
            x,y,w,h = op["rect"]
            if min(x,y,w,h) < 0 or x+w > image.width or y+h > image.height:
                raise ValueError("Selection outside canvas")
            result = Image.new("RGBA", image.size)
            result.paste(image.crop((x,y,x+w,y+h)), (x,y))
            image = result
        elif kind == "palette_map":
            mapping = {tuple(bytes.fromhex(k.lstrip('#'))): tuple(bytes.fromhex(v.lstrip('#'))) for k,v in op["colors"].items()}
            values = np.array(image)
            original = values[:, :, :3].copy()
            for source, target in mapping.items():
                if len(source) != 3 or len(target) != 3:
                    raise ValueError("Palette mapping requires six-digit RGB colors")
                values[np.all(original == source, axis=2), :3] = target
            image = Image.fromarray(values)
        else:
            raise ValueError(f"Unknown pixel operation: {kind}")
    return image


def extract(image, cell, column, row):
    w,h = cell
    if min(w,h) <= 0 or min(column,row) < 0 or (column+1)*w > image.width or (row+1)*h > image.height:
        raise ValueError("Invalid cell extraction")
    return image.crop((column*w, row*h, (column+1)*w, (row+1)*h))


def pack(frames, cell, grid):
    w,h = cell
    cols,rows = grid
    checked_size(cell)
    checked_size(grid)
    checked_size((w*cols, h*rows))
    result = Image.new("RGBA", (w*cols, h*rows))
    seen = set()
    for frame, column, row in frames:
        if frame.size != (w,h) or not 0 <= column < cols or not 0 <= row < rows or (column,row) in seen:
            raise ValueError("Invalid or duplicate atlas cell")
        seen.add((column,row))
        result.paste(frame, (column*w,row*h))
    packed, _ = pack_sheet(result, make_layout(result.size, columns=cols, rows=rows))
    return packed


def overlap(a,b):
    if a.size != b.size:
        raise ValueError("Overlap images must have equal dimensions")
    count = 0
    for y in range(0, a.height, MEASURE_TILE):
        for x in range(0, a.width, MEASURE_TILE):
            box = (x, y, min(x+MEASURE_TILE, a.width), min(y+MEASURE_TILE, a.height))
            aa = np.asarray(a.crop(box))[:, :, 3]
            ba = np.asarray(b.crop(box))[:, :, 3]
            count += int(np.count_nonzero((aa != 0) & (ba != 0)))
    return count
