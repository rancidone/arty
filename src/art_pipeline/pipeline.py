"""Import/build revisions and independently verify their saved artifacts."""
from pathlib import Path
import copy
from PIL import Image
from . import raster
from .storage import sha, read, write, snapshot, revision, name, local, now, identities

SCHEMA = "art-pipeline.asset.v1"


def artifact(root, filename, role):
    path = local(root, filename)
    return {"file": filename, "role": role, "sha256": sha(path), "measurements": raster.measure(raster.load(path))}


def import_png(source, destination, asset, rev, expected=None):
    name(asset); name(rev)
    raster.load(source)
    with revision(destination) as root:
        record = snapshot(source, root, expected)
        manifest = {"schema_version": SCHEMA, "kind": "import", "asset": name(asset), "revision": name(rev),
                    "created": now(), "sources": [record], "artifacts": [], "source_measurements": raster.measure(raster.load(source)), "root": None, "visual_approval": "pending",
                    "unknown": ["root", "attachments", "orientation", "timing", "artistic acceptance"]}
        write(root / "manifest.json", manifest)
    return Path(destination)


def build(spec_file, destination, rev=None):
    spec_file = Path(spec_file).resolve()
    spec = read(spec_file)
    name(spec["asset"])
    rev = name(rev or spec["revision"])
    canvas = spec["canvas"]
    raster.checked_size(canvas)
    if spec.get("export_2x"):
        raster.checked_size((canvas[0]*2, canvas[1]*2))
    if len(spec["layers"]) * canvas[0] * canvas[1] > raster.MAX_BUILD_PIXELS:
        raise ValueError("Build exceeds the 32 megapixel aggregate layer budget")
    layer_ids = [name(entry["id"]).casefold() for entry in spec["layers"]]
    if len(set(layer_ids)) != len(layer_ids):
        raise ValueError("Duplicate layer IDs ignoring case")
    for entry in spec["layers"]:
        if name(entry["id"]).casefold() in {"preview", "preview-2x"}:
            raise ValueError("Layer ID is reserved for generated output: " + entry["id"])
    with revision(destination) as root:
        sources = []
        def capture(entry):
            record = snapshot(spec_file.parent / entry["path"], root, entry.get("sha256"))
            if record not in sources:
                sources.append(record)
            return record
        spec_record = snapshot(spec_file, root)
        sources.append(spec_record)
        for entry in spec.get("dependencies", []):
            capture(entry)
        refs = []
        for entry in spec.get("references", []):
            record = capture(entry)
            refs.append({**record, "status": entry.get("status", "unapproved"),
                         "comparison": entry.get("comparison", "visual_only"),
                         "note": entry.get("note")})
        layers, artifacts, recipe_layers = {}, [], []
        for entry in spec["layers"]:
            layer = name(entry["id"])
            if layer in layers:
                raise ValueError("Duplicate layer ID")
            record = capture(entry)
            ops = entry.get("operations", [])
            image = raster.adapt(raster.load(local(root, record["file"])), ops)
            if image.size != tuple(canvas):
                raise ValueError(f"Layer {layer} has {image.size}; expected full canvas {canvas}; declare an explicit transform")
            filename = f"{layer}.png"
            if not ops:
                # Preserve exact source bytes, including the authoritative base.
                local(root, filename).write_bytes(local(root, record["file"]).read_bytes())
            else:
                image.save(local(root, filename))
            layers[layer] = image
            artifacts.append(artifact(root, filename, layer))
            recipe_layers.append({**entry, "source": record["file"], "file": filename,
                                  "root": entry.get("root", spec.get("root")),
                                  "pixel_origin": entry.get("pixel_origin", "source-derived; hidden pixels unknown")})
        order = spec["draw_order"]
        if len(order) != len(layers) or set(order) != set(layers):
            raise ValueError("Draw order must contain every layer exactly once")
        result = raster.compose([layers[k] for k in order], canvas)
        result.save(root / "preview.png")
        artifacts.append(artifact(root, "preview.png", "composition"))
        if spec.get("export_2x"):
            result.resize((canvas[0]*2,canvas[1]*2), Image.Resampling.NEAREST).save(root / "preview-2x.png")
            artifacts.append(artifact(root,"preview-2x.png","nearest_2x"))
        recipe = {**spec, "revision": rev, "layers": recipe_layers}
        manifest = {"schema_version": SCHEMA, "kind": "build", "asset": spec["asset"], "revision": rev,
                    "created": now(), "sources": sources, "artifacts": artifacts, "references": refs,
                    "spec_source": spec_record["file"], "recipe": recipe, "native_canvas": canvas,
                    "root": spec.get("root"), "draw_order": order, "visual_approval": "pending",
                    "artistic_limitations": spec.get("artistic_limitations", []),
                    "unknown": spec.get("unknown", ["attachments", "grips", "orientation", "timing"])}
        write(root / "manifest.json", manifest)
        # Independent readback, not just in-memory composition.
        report = verify(root)
        manifest["validation"] = report
        write(root / "manifest.json", manifest)
        write(root / "verification.json", report)
    return Path(destination)


def manifest_errors(manifest):
    """Reject incomplete or unknown contracts before evaluating file identities."""
    try:
        if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA:
            raise ValueError("Unknown manifest schema")
        kind = manifest.get("kind")
        if kind not in {"import", "build", "api", "extraction"}:
            raise ValueError("Unknown manifest kind")
        name(manifest["asset"]); name(manifest["revision"])
        sources, artifacts = manifest["sources"], manifest["artifacts"]
        if not isinstance(sources, list) or not sources or not isinstance(artifacts, list):
            raise ValueError("Manifest requires source records and an artifact list")
        if kind != "import" and not artifacts:
            raise ValueError("Manifest kind requires artifacts")
        files = set()
        sidecars = manifest.get("sidecars", [])
        if not isinstance(sidecars, list):
            raise ValueError("Invalid sidecar list")
        for record in sources + artifacts + sidecars:
            filename, digest = record["file"], record["sha256"]
            if (not isinstance(filename, str) or not filename or filename in files
                    or not isinstance(digest, str) or len(digest) != 64
                    or any(c not in "0123456789abcdef" for c in digest)):
                raise ValueError("Invalid or duplicate identity record")
            files.add(filename)
        if kind == "build":
            recipe = manifest["recipe"]
            raster.checked_size(recipe["canvas"])
            if (not isinstance(recipe["layers"], list) or not recipe["layers"]
                    or not isinstance(manifest["draw_order"], list) or not manifest["draw_order"]):
                raise ValueError("Build requires layers and draw order")
            for entry in recipe["layers"]:
                name(entry["id"])
                if entry["file"] not in {r["file"] for r in artifacts} or entry["source"] not in {r["file"] for r in sources}:
                    raise ValueError("Build layers must reference declared inputs and artifacts")
            if "preview.png" not in {r["file"] for r in artifacts}:
                raise ValueError("Build requires a declared preview")
        elif kind == "api":
            parameters = manifest["request"]["parameters"]
            if not isinstance(parameters, dict) or not all(k in parameters for k in ("model", "size", "prompt")):
                raise ValueError("API revision requires request parameters")
            if not isinstance(manifest["validation"], dict):
                raise ValueError("API revision requires validation")
            if len(artifacts) != 1 or artifacts[0]["file"] != "source.png":
                raise ValueError("API revision requires source.png")
        return []
    except (KeyError, TypeError, ValueError) as error:
        return [f"Invalid manifest: {error}"]


def verify(root):
    root = Path(root)
    manifest = read(root / "manifest.json")
    checks, errors = {}, []
    def check(key, passed):
        checks[key] = bool(passed)
    structure_errors = manifest_errors(manifest)
    if structure_errors:
        return {"technical": "fail", "checks": {"manifest_structure": False},
                "measurements": {}, "overlap_pixels": {}, "errors": structure_errors,
                "artistic": "pending; technical verification does not assess visual fidelity"}
    check("schema", manifest.get("schema_version") == SCHEMA)
    for record in manifest.get("sources", []) + manifest.get("artifacts", []) + manifest.get("sidecars", []):
        try:
            check("identity:" + record["file"], sha(local(root,record["file"])) == record["sha256"])
        except (OSError,ValueError) as e:
            check("identity:" + record["file"], False)
            errors.append(str(e))
    if manifest.get("kind") == "api":
        check("api_output_contract", manifest.get("validation", {}).get("technical") == "pass")
    # Hash consistency alone does not establish that saved bytes are usable PNGs.
    if manifest["kind"] in {"import", "api", "extraction"}:
        records = manifest["sources"][:1] if manifest["kind"] == "import" else manifest["artifacts"]
        for record in records:
            try:
                image = raster.load(local(root, record["file"]))
                check("png:" + record["file"], True)
                if manifest["kind"] == "api":
                    size = manifest["request"]["parameters"]["size"]
                    check("api_dimensions", size == "auto" or size == f"{image.width}x{image.height}")
            except (OSError, ValueError) as error:
                check("png:" + record["file"], False)
                errors.append(str(error))
    measurements, overlaps = {}, {}
    if manifest.get("kind") == "build":
        try:
            recipe = manifest["recipe"]
            canvas, order = recipe["canvas"], manifest["draw_order"]
            check("recipe_order", order == recipe["draw_order"])
            layers = {}
            for entry in recipe["layers"]:
                key = entry["id"]
                image = raster.load(local(root,entry["file"]))
                layers[key] = image
                measured = raster.measure(image)
                measurements[key] = measured
                check(key+":canvas", list(image.size) == canvas)
                check(key+":root", entry.get("root") == manifest.get("root"))
                check(key+":recipe", raster.same(image, raster.adapt(raster.load(local(root,entry["source"])),entry.get("operations",[]))))
                if not entry.get("operations"):
                    check(key+":source_bytes", sha(local(root,entry["file"])) == sha(local(root,entry["source"])))
                constraints = {**recipe.get("constraints",{}), **entry.get("constraints",{})}
                if constraints.get("rgba"):
                    with Image.open(local(root,entry["file"])) as opened:
                        check(key+":rgba", opened.mode == "RGBA")
                if constraints.get("binary_alpha"):
                    check(key+":binary_alpha", measured["partial_alpha_pixels"] == 0)
                if "palette" in constraints:
                    check(key+":palette", set(measured["palette"]) <= {v.upper() for v in constraints["palette"]})
                if "max_isolated_pixels" in constraints:
                    check(key+":strays", measured["isolated_pixels"] <= constraints["max_isolated_pixels"])
                if "allowed_bounds" in constraints:
                    x,y,w,h = constraints["allowed_bounds"]
                    b = measured["bounds_xywh"]
                    check(key+":bounds", b is None or (b[0]>=x and b[1]>=y and b[0]+b[2]<=x+w and b[1]+b[3]<=y+h))
                if constraints.get("clean_transparent_rgb"):
                    check(key+":transparent_rgb", measured["transparent_rgb_noise"] == 0)
            check("unique_order", len(order) == len(layers) and set(order) == set(layers))
            preview = raster.load(root / "preview.png")
            check("exact_recomposition", raster.same(preview, raster.compose([layers[k] for k in order], canvas)))
            for record in manifest["artifacts"]:
                check("measurements:"+record["file"], raster.measure(raster.load(local(root,record["file"]))) == record["measurements"])
            if recipe.get("export_2x"):
                check("exact_2x", raster.same(raster.load(root/"preview-2x.png"), preview.resize((canvas[0]*2,canvas[1]*2),Image.Resampling.NEAREST)))
            for i,key in enumerate(order):
                for other in order[i+1:]:
                    overlaps[f"{key}/{other}"] = raster.overlap(layers[key],layers[other])
            for ref in manifest.get("references",[]):
                if ref["comparison"] == "registered_exact":
                    check("reference_composition:"+ref["file"], raster.same(preview,raster.load(local(root,ref["file"]))))
            atlas = recipe.get("atlas")
            if atlas:
                cell, grid = atlas["cell"], atlas["grid"]
                check("atlas_geometry", canvas == [cell[0]*grid[0],cell[1]*grid[1]])
                for frame in atlas.get("frames",[]):
                    image = raster.extract(preview, cell, *frame["cell"])
                    if frame.get("grounded_row") is not None:
                        box = image.getchannel("A").getbbox()
                        check("ground:"+str(frame["cell"]), box is not None and box[3]-1 == frame["grounded_row"])
        except (OSError,ValueError,KeyError,TypeError) as e:
            checks["readback"] = False
            errors.append(str(e))
    return {"technical": "pass" if checks and all(checks.values()) else "fail", "checks": checks,
            "measurements": measurements, "overlap_pixels": overlaps, "errors": errors,
            "artistic": "pending; technical verification does not assess visual fidelity"}


def rebuild(root, destination, rev):
    root = Path(root).resolve()
    manifest = read(root/"manifest.json")
    if manifest["kind"] != "build":
        raise ValueError("Only build revisions have a rebuild recipe")
    if verify(root)["technical"] != "pass":
        raise ValueError("Source revision fails verification; rebuild refused")
    # Saved snapshot paths, not original workstation paths.
    spec = copy.deepcopy(manifest["recipe"])
    for entry in spec["layers"]:
        entry["path"] = str(local(root,entry["source"]))
        entry["sha256"] = next(r["sha256"] for r in manifest["sources"] if r["file"] == entry["source"])
    for field in ("dependencies","references"):
        spec[field] = []
    spec["references"] = [{**r,"path":str(local(root,r["file"]))} for r in manifest.get("references",[])]
    spec["dependencies"] = [{"path":str(local(root,r["file"])),"sha256":r["sha256"]} for r in manifest["sources"]]
    import tempfile
    with tempfile.TemporaryDirectory() as temp:
        file = Path(temp)/"rebuild.json"
        write(file,spec)
        return build(file,destination,rev)


def build_atlas(spec_file, destination):
    """Explicit frame packing; no invented frames, roots or timings."""
    import tempfile
    spec_file=Path(spec_file).resolve()
    spec=read(spec_file)
    cell,grid=spec['cell'],spec['grid']
    raster.checked_size(cell)
    raster.checked_size(grid)
    raster.checked_size((cell[0]*grid[0], cell[1]*grid[1]))
    if len(spec['frames']) * cell[0] * cell[1] > raster.MAX_BUILD_PIXELS:
        raise ValueError("Atlas exceeds the 32 megapixel aggregate frame budget")
    frames=[]
    dependencies=[]
    for entry in spec['frames']:
        path=spec_file.parent/entry['path']
        if entry.get('sha256') and sha(path)!=entry['sha256']:
            raise ValueError('Frame source identity mismatch')
        frames.append((raster.load(path),*entry['cell']))
        dependencies.append({'path':str(path),'sha256':sha(path)})
    packed=raster.pack(frames,cell,grid)
    with tempfile.TemporaryDirectory() as temp:
        temp=Path(temp)
        packed.save(temp/'packed.png')
        references = [{**entry, 'path':str((spec_file.parent/entry['path']).resolve())}
                      for entry in spec.get('references', [])]
        dependencies.extend({**entry, 'path':str((spec_file.parent/entry['path']).resolve())}
                            for entry in spec.get('dependencies', []))
        recipe={**spec,'canvas':list(packed.size),'dependencies':dependencies,'references':references,
                'layers':[{'id':'atlas','path':str(temp/'packed.png'),'root':spec.get('root')}],
                'draw_order':['atlas'],'atlas':{'cell':cell,'grid':grid,'frames':spec['frames']}}
        write(temp/'atlas-spec.json',recipe)
        return build(temp/'atlas-spec.json',destination)


def extract_cells(root, destination):
    root=Path(root)
    manifest=read(root/'manifest.json')
    if verify(root)['technical']!='pass':
        raise ValueError('Source revision fails verification')
    atlas=manifest['recipe']['atlas']
    image=raster.load(root/'preview.png')
    with revision(destination) as out:
        records=[]
        for row in range(atlas['grid'][1]):
            for col in range(atlas['grid'][0]):
                filename=f'cell-{col}-{row}.png'
                raster.extract(image,atlas['cell'],col,row).save(out/filename)
                records.append({**artifact(out,filename,'cell'),'cell':[col,row],'root':manifest.get('root')})
        write(out/'manifest.json',{'schema_version':SCHEMA,'kind':'extraction','asset':manifest['asset'],
                                 'revision':Path(destination).name,'created':now(),'sources':[snapshot(root/'preview.png',out)],
                                 'artifacts':records,'root':manifest.get('root'),'visual_approval':'pending','unknown':manifest['unknown']})
    return Path(destination)
