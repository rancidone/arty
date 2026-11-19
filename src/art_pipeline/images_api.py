"""Explicit OpenAI Images API calls; offline commands never import a client."""
import base64
from contextlib import ExitStack
import inspect
import io
import os
import shlex
import tomllib
from pathlib import Path
import re
from PIL import Image
from .storage import name, now, revision, snapshot, write, sha

DEFAULT_MODEL = "gpt-image-2.6-sunburst"
MODERN = {"gpt-image-2", "gpt-image-2-2026-04-21", "gpt-image-2.5-sunburst",
          "gpt-image-2.5-flare", "gpt-image-2.6-sunburst", "gpt-image-2.6-flare",
          "gpt-image-2.5-sunburst-2026-09-08", "gpt-image-2.5-flare-2026-09-08"}
LEGACY = {"gpt-image-1.5", "gpt-image-1-mini"}


def load_environment():
    """Environment wins, then user config, then the editable checkout's .env.

    Parse assignments as data, never execute shell code or print secret values.
    Called only when constructing a live client, not by offline/dry-run commands.
    """
    user_config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "art-pipeline" / ".env"
    module = Path(__file__).resolve()
    checkout = module.parents[2]
    arty_config = user_config.parent.parent / "arty" / ".env"
    configs = [arty_config, user_config]
    # Wheels live in site-packages, not <checkout>/src/art_pipeline.
    if module.parent == checkout / "src" / "art_pipeline":
        project_file = checkout / "pyproject.toml"
        try:
            project = tomllib.loads(project_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            project = {}
        if project.get("project", {}).get("name") in {"arty", "art-pipeline"}:
            configs.append(checkout / ".env")
    for config in configs:
        if not config.is_file():
            continue
        for number, line in enumerate(config.read_text(encoding="utf-8-sig").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            key, separator, value = line.partition("=")
            key = key.strip()
            if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError(f"Invalid .env assignment in {config.name} at line {number}")
            try:
                values = shlex.split(value, comments=True, posix=True)
            except ValueError:
                raise ValueError(f"Invalid .env quoting in {config.name} at line {number}") from None
            if len(values) > 1:
                raise ValueError(f"Quote whitespace in .env value at line {number}")
            os.environ.setdefault(key, values[0] if values else "")


def create_client(*, timeout=180.0, max_retries=2):
    """Construct the SDK client using explicit environment configuration."""
    load_environment()
    from openai import OpenAI
    return OpenAI(timeout=timeout, max_retries=max_retries)


def api_error_message(error, operation, client):
    """Expose structured SDK rejection details without headers or credentials."""
    status = getattr(error, "status_code", None)
    details = [f"Images API {operation} failed ({type(error).__name__}, HTTP {status or 'unavailable'})"]
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        body = body.get("error", body)
        if isinstance(body, dict):
            for field in ("message", "type", "code", "param"):
                value = body.get(field)
                if isinstance(value, (str, int, float)):
                    details.append(f"{field}: {value}")
    request_id = getattr(error, "request_id", None)
    if isinstance(request_id, str):
        details.append(f"request_id: {request_id}")
    message = "; ".join(details) + "; no revision published"
    # Never dump arbitrary exception text, response headers or request payloads.
    secrets = [getattr(client, "api_key", None)]
    secrets.extend(value for key, value in os.environ.items()
                   if any(word in key.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")))
    for secret in sorted((v for v in secrets if isinstance(v, str) and v), key=len, reverse=True):
        message = message.replace(secret, "[REDACTED]")
    message = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", message)
    return message


def validate_request(model, size, quality):
    if model not in MODERN | LEGACY:
        raise ValueError(f"Unknown model capability profile: {model}; add a documented profile before use")
    qualities = {"auto", "low", "medium", "high"}
    if "2.5" in model or "2.6" in model:
        qualities |= {"xhigh", "max"}
    if quality not in qualities:
        raise ValueError(f"Unsupported quality for {model}: {quality}")
    if size == "auto":
        return
    match = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", size)
    if not match:
        raise ValueError("Size must be auto or WIDTHxHEIGHT")
    w,h = map(int,match.groups())
    if model in MODERN:
        if w%16 or h%16 or not 1/3 <= w/h <= 3 or max(w,h)>3840 or not 655360<=w*h<=8294400:
            raise ValueError("Model requires multiples of 16, ratio 1:3..3:1, edges <=3840, pixels 655360..8294400")
    elif size not in {"1024x1024","1536x1024","1024x1536"}:
        raise ValueError("This model supports 1024x1024, 1536x1024 or 1024x1536")


def request(prompt_file, destination, asset, rev, *, references=(), mask=None,
            model=DEFAULT_MODEL, size="1024x1024", quality="high", background="transparent",
            dry_run=False, client=None, edit=False):
    """One result per explicit call. Reference-guided generation uses the edit endpoint.

    Retries are handled by the SDK (two retries, 180-second timeout). A timeout can
    occur after remote generation; do not assume retrying gives identical artwork.
    """
    validate_request(model,size,quality)
    name(asset); name(rev)
    if edit and not references:
        raise ValueError("Editing requires at least one input image")
    if len(references)>16:
        raise ValueError("At most 16 reference images per request")
    if mask and not references:
        raise ValueError("A mask requires a first edit image")
    if background not in {"transparent","opaque","auto"}:
        raise ValueError("Invalid background")
    from . import raster
    for path in references:
        raster.load(path)
    if mask:
        raster.load(mask)
        with Image.open(mask) as image, Image.open(references[0]) as first:
            if image.format != "PNG" or "A" not in image.getbands() or image.size != first.size:
                raise ValueError("Mask must be PNG with alpha and match first input dimensions")
    prompt = Path(prompt_file).read_text(encoding="utf-8")
    parameters = dict(model=model,prompt=prompt,size=size,quality=quality,background=background,output_format="png",n=1)
    operation = "edit" if references else "generate"
    summary = {"operation":operation,"parameters":parameters,"prompt_sha256":sha(prompt_file),
               "references":[{"filename":Path(p).name,"sha256":sha(p)} for p in references],
               "mask": {"filename":Path(mask).name,"sha256":sha(mask)} if mask else None}
    if dry_run:
        return {"dry_run":True,**summary}
    # Reserve destination and capture all inputs BEFORE the paid request.
    with revision(destination) as root:
        sources = [snapshot(prompt_file,root)]
        refs = [snapshot(p,root) for p in references]
        sources.extend(refs)
        mask_record = snapshot(mask,root) if mask else None
        if mask_record:
            sources.append(mask_record)
        # Roles can reuse the same bytes; identity records are unique by path.
        sources = list({record["file"]: record for record in sources}.values())
        parameters["prompt"] = (root / sources[0]["file"]).read_text(encoding="utf-8")
        if client is None:
            client = create_client()
        method = getattr(client.images,operation)
        # Fail before submitting on an incompatible installed SDK.
        signature = inspect.signature(method)
        if not any(p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()):
            required = set(parameters) | ({"image"} if refs else set()) | ({"mask"} if mask else set())
            if not required <= set(signature.parameters):
                raise ValueError("Installed OpenAI SDK lacks required Images parameters; upgrade it")
        with ExitStack() as files:
            kwargs = dict(parameters)
            if refs:
                kwargs["image"] = [files.enter_context((root/r["file"]).open("rb")) for r in refs]
            if mask_record:
                kwargs["mask"] = files.enter_context((root/mask_record["file"]).open("rb"))
            try:
                response = method(**kwargs)
            except Exception as error:
                raise ValueError(api_error_message(error, operation, client)) from None
        if not response.data or len(response.data)!=1 or not response.data[0].b64_json:
            raise ValueError("Images API did not return the single requested base64 image")
        data = base64.b64decode(response.data[0].b64_json,validate=True)
        # Returned bytes are saved unchanged, even when they fail the requested contract.
        output = root/"source.png"
        output.write_bytes(data)
        validation = {"technical":"pass","errors":[]}
        measured = None
        try:
            from .raster import load,measure
            image = load(output)
            measured = measure(image)
            if size != "auto" and image.size != tuple(map(int,size.split('x'))):
                validation = {"technical":"fail","errors":["API output dimensions differ from request"]}
        except (OSError,ValueError) as error:
            validation = {"technical":"fail","errors":[f"API result failed PNG validation or measurement: {type(error).__name__}"]}
        metadata = {k:getattr(response,k,None) for k in ("created","size","quality","background","output_format","_request_id")}
        usage = getattr(response,"usage",None)
        metadata["usage"] = usage.model_dump() if hasattr(usage,"model_dump") else None
        write(root/"manifest.json",{"schema_version":"art-pipeline.asset.v1","kind":"api","asset":asset,"revision":rev,
                                  "created":now(),"sources":sources,"artifacts":[{"file":"source.png","role":"original_api_bytes","sha256":sha(output),"measurements":measured}],
                                  "request":summary,"response":metadata,"validation":validation,"visual_approval":"pending",
                                  "unknown":["native grid","root","attachments","artistic acceptance"]})
    return {"destination":str(destination),"validation":validation,"output_sha256":sha(Path(destination)/"source.png")}
