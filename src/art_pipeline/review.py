"""Local visual review and hash-bound explicit human decisions."""
from pathlib import Path
import json
import hashlib
from io import BytesIO
from PIL import Image, ImageChops, ImageDraw
from . import raster
from .pipeline import verify
from .storage import read, write, sha, local, identities, now


def review(root, destination):
    root, destination = Path(root), Path(destination)
    manifest = read(root/"manifest.json")
    sidecar = destination.with_suffix(".review.json")
    if destination.resolve() == sidecar.resolve():
        raise ValueError("Review image and sidecar paths collide")
    for path in (destination, sidecar):
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"Review output exists: {path}")
        if path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Write review outside immutable revision")
    panels = []
    for ref in manifest.get("references",[]):
        panels.append((f'Reference ({ref["status"]}); {ref["comparison"]}',raster.load(local(root,ref["file"]))))
    if manifest["kind"] == "build":
        candidate = raster.load(root/"preview.png")
        panels.append(("Native candidate (1:1)",candidate))
        bounds = candidate.getchannel("A").getbbox()
        crop = candidate.crop(bounds) if bounds else candidate
        # Bound the display crop before enlarging; never allocate a huge 4x image
        # merely to reduce it again below.
        zoom = min(4, 776 / crop.width, 800 / crop.height)
        display_size = (max(1, int(crop.width*zoom)), max(1, int(crop.height*zoom)))
        panels.append((f"Candidate visible crop; nearest {zoom:g}x",
                       crop.resize(raster.checked_size(display_size), Image.Resampling.NEAREST)))
        recipe = manifest["recipe"]
        layers = {e["id"]:raster.load(local(root,e["file"])) for e in recipe["layers"]}
        reconstructed = raster.compose([layers[k] for k in manifest["draw_order"]],recipe["canvas"])
        panels.append(("Reconstructed from delivered layers",reconstructed))
        for ref in manifest.get("references",[]):
            if ref["comparison"] in {"registered_exact","registered_comparable"}:
                reference = raster.load(local(root,ref["file"]))
                if reference.size != candidate.size:
                    raise ValueError("Registered comparison dimensions differ")
                diff = ImageChops.difference(candidate,reference)
                # Make RGB and alpha differences visible independently.
                rgb = diff.convert("RGB")
                alpha = diff.getchannel('A')
                rgb = ImageChops.lighter(rgb,Image.merge('RGB',(alpha,alpha,alpha)))
                panels.append(("Exact registered RGBA difference",rgb.convert("RGBA")))
    else:
        records = manifest["artifacts"] or manifest["sources"]
        panels.append(("Preserved source; native registration unknown",raster.load(local(root,records[0]["file"]))))
    # Keep 1:1 native panels. Large reference views are explicitly labeled display reductions.
    width = 800
    heights=[]
    shown=[]
    for label,image in panels:
        if image.width > width-24 or image.height > 800:
            scale = min((width-24)/image.width,800/image.height)
            image = image.resize((max(1,int(image.width*scale)),max(1,int(image.height*scale))),Image.Resampling.NEAREST)
            label += " (display reduction; source unchanged)"
        shown.append((label,image)); heights.append(image.height+58)
    board = Image.new("RGB",raster.checked_size((width,sum(heights)+70)),(35,37,43))
    draw=ImageDraw.Draw(board)
    draw.text((12,10),f'{manifest["asset"]} / {manifest["revision"]}',fill="white")
    draw.text((12,30),"ARTISTIC ACCEPTANCE PENDING - pixel checks do not prove fidelity",fill="#ffd080")
    y=65
    for (label,image),height in zip(shown,heights):
        draw.text((12,y),label,fill="white")
        background = Image.new("RGBA",image.size,(100,100,100,255))
        checker=ImageDraw.Draw(background)
        for cy in range(0,image.height,12):
            for cx in range(0,image.width,12):
                if (cx//12+cy//12)%2: checker.rectangle((cx,cy,cx+11,cy+11),fill=(130,130,130,255))
        background.alpha_composite(image)
        board.paste(background.convert("RGB"),(12,y+26))
        y+=height
    stream = BytesIO()
    board.save(stream, format="PNG")
    image_bytes = stream.getvalue()
    metadata = {"manifest_sha256": sha(root/"manifest.json"),
                "artifact_hashes": identities(root, manifest),
                "review_sha256": hashlib.sha256(image_bytes).hexdigest(),
                "revision": manifest["revision"]}
    # Stage both files and publish without replacing existing files, even in a race.
    from .cli import _write_files
    _write_files([(destination, image_bytes),
                  (sidecar, (json.dumps(metadata, indent=2) + "\n").encode())],
                 root/"manifest.json", overwrite=False)
    return destination


def decision(root, reviewer, note, approved=True, reference=None):
    root = Path(root)
    manifest = read(root/"manifest.json")
    report = verify(root)
    if approved and report["technical"] != "pass":
        raise ValueError("Technical verification failed; approval refused")
    if approved and manifest.get("artistic_limitations"):
        raise ValueError("Unresolved artistic limitations; build a new revision with reviewed corrections before approval")
    if not reviewer.strip() or not note.strip():
        raise ValueError("Reviewer and explicit visual decision note required")
    if reference:
        reference_sha = sha(reference)
        if reference_sha not in {r["sha256"] for r in manifest.get("references",[])}:
            raise ValueError("Visual authority must be a saved reference in this revision")
    else:
        reference_sha = None
    if approved and not reference_sha:
        raise ValueError("Approval requires an explicit saved visual authority (--reference)")
    decisions=root.parent/(root.name+".decisions")
    decisions.mkdir(exist_ok=True)
    value={"state":"approved" if approved else "rejected","reviewer":reviewer,"note":note,"created":now(),
           "manifest_sha256":sha(root/"manifest.json"),"artifact_hashes":identities(root,manifest),"reference_sha256":reference_sha}
    import uuid
    path=decisions/(value['created'].replace(':','-')+'-'+uuid.uuid4().hex+'.json')
    with path.open('x',encoding='utf-8') as handle:
        json.dump(value,handle,indent=2)
    return value


def approval_status(root):
    root=Path(root)
    decisions=root.parent/(root.name+".decisions")
    files=sorted(decisions.glob('*.json'))
    if not files:
        return {"state":"pending"}
    value=read(files[-1])
    try:
        valid = value["manifest_sha256"]==sha(root/"manifest.json") and value["artifact_hashes"]==identities(root,read(root/"manifest.json"))
        if value["state"] == "approved":
            valid = valid and verify(root)["technical"] == "pass"
    except (OSError,ValueError,KeyError):
        valid=False
    return {**value,"state":value["state"] if valid else "invalidated"}
