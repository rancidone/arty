"""Explicit fitting auditions: same artwork, recorded transforms, local comparison."""
import copy
from pathlib import Path
import tempfile
from PIL import Image, ImageDraw
from .pipeline import build, verify
from .storage import read, write, name, revision, snapshot, sha, local
from .raster import load, checked_size


def fit(spec_file, destination):
    spec_file = Path(spec_file).resolve()
    spec = read(spec_file)
    base_file = (spec_file.parent/spec['build_spec']).resolve()
    base = read(base_file)
    candidates = spec['candidates']
    ids = [name(c['id']) for c in candidates]
    if not 1<=len(ids)<=12 or len({v.casefold() for v in ids})!=len(ids):
        raise ValueError('Fitting requires 1..12 uniquely named candidates')
    target = spec['layer']
    if target not in {e['id'] for e in base['layers']}:
        raise ValueError('Unknown fitting layer')
    x,y,w,h = spec['view_rect']
    zoom = spec.get('zoom',4)
    if any(not isinstance(v,int) for v in (x,y,w,h,zoom)) or min(x,y)<0 or min(w,h,zoom)<=0 or zoom>16 or x+w>base['canvas'][0] or y+h>base['canvas'][1] or w*h*zoom*zoom>4000000:
        raise ValueError('Invalid fitting view')
    with revision(destination) as out:
        original_spec = snapshot(spec_file,out)
        panels=[]; reports=[]
        for candidate in candidates:
            recipe=copy.deepcopy(base)
            recipe['revision']=candidate['id']
            for field in ('layers','references','dependencies'):
                for entry in recipe.get(field,[]):
                    entry['path']=str((base_file.parent/entry['path']).resolve())
            for entry in recipe['layers']:
                if entry['id']==target:
                    entry['operations']=copy.deepcopy(candidate['operations'])
            with tempfile.TemporaryDirectory() as temp:
                file=Path(temp)/'fit-build.json';write(file,recipe)
                build(file,out/candidate['id'])
            report=verify(out/candidate['id'])
            reports.append({'id':candidate['id'],'technical':report['technical'],'operations':candidate['operations'],
                            'preview_sha256':sha(out/candidate['id']/'preview.png')})
            crop=load(out/candidate['id']/'preview.png').crop((x,y,x+w,y+h)).resize((w*zoom,h*zoom),Image.Resampling.NEAREST)
            panels.append((candidate.get('label',candidate['id']),crop))
        cols=min(3,len(panels)); rows=(len(panels)+cols-1)//cols
        pw,ph=w*zoom+24,h*zoom+52
        board=Image.new('RGB',checked_size((cols*pw,rows*ph)),(40,42,48));draw=ImageDraw.Draw(board)
        for i,(label,image) in enumerate(panels):
            px,py=(i%cols)*pw,(i//cols)*ph
            draw.text((px+12,py+8),label,fill='white')
            bg=Image.new('RGBA',image.size,(105,105,105,255));bg.alpha_composite(image)
            board.paste(bg.convert('RGB'),(px+12,py+32))
        board.save(out/'comparison.png')
        write(out/'fit.json',{'schema':'art-pipeline.fitting.v1','source_spec':original_spec,'layer':target,
                             'view_rect':spec['view_rect'],'zoom':zoom,'candidates':reports,
                             'comparison_sha256':sha(out/'comparison.png'),'artistic_approval':'pending',
                             'note':'Display crops only; native candidates preserve full canvases. No automatic selection or artistic score.'})
    return Path(destination)
