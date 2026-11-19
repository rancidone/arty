"""Production commands, alongside the existing analyze/convert CLI."""
import argparse
import json
from pathlib import Path
import sys
from .storage import read, sha, local
from . import pipeline, raster
from .review import review, decision, approval_status

COMMANDS = {'generate','edit','import','build','verify','review','approve','reject','inspect','rebuild','atlas','extract','fit'}


def parser():
    p=argparse.ArgumentParser(prog='arty',description='Local image production; only generate/edit consume API credits.')
    commands=p.add_subparsers(dest='command',required=True)
    for command in ('generate','edit'):
        c=commands.add_parser(command,help='Explicit OpenAI Images request; use --dry-run to inspect without API access')
        c.add_argument('--prompt',type=Path,required=True)
        c.add_argument('-o','--output',type=Path,required=True)
        c.add_argument('--asset',required=True); c.add_argument('--revision',required=True)
        c.add_argument('--reference',type=Path,action='append',default=[])
        c.add_argument('--mask',type=Path)
        c.add_argument('--model',default='gpt-image-2.6-sunburst')
        c.add_argument('--size',default='1024x1024'); c.add_argument('--quality',default='high')
        c.add_argument('--background',choices=['transparent','opaque','auto'],default='transparent')
        c.add_argument('--dry-run',action='store_true')
    c=commands.add_parser('import',help='Preserve existing PNG bytes in an immutable revision')
    c.add_argument('input',type=Path); c.add_argument('-o','--output',type=Path,required=True)
    c.add_argument('--asset',required=True); c.add_argument('--revision',required=True); c.add_argument('--sha256')
    c=commands.add_parser('build',help='Build full-canvas ordered layers from a declarative JSON spec')
    c.add_argument('spec',type=Path); c.add_argument('-o','--output',type=Path,required=True); c.add_argument('--revision')
    for command in ('verify','inspect'):
        c=commands.add_parser(command); c.add_argument('revision_dir',type=Path)
    c=commands.add_parser('review',help='Write a labeled local comparison PNG outside the immutable revision')
    c.add_argument('revision_dir',type=Path); c.add_argument('-o','--output',type=Path,required=True)
    for command in ('approve','reject'):
        c=commands.add_parser(command,help='Record explicit human visual decision against exact hashes')
        c.add_argument('revision_dir',type=Path); c.add_argument('--by',required=True); c.add_argument('--note',required=True)
        c.add_argument('--reference',type=Path)
    c=commands.add_parser('rebuild',help='Rebuild from saved inputs without generation')
    c.add_argument('revision_dir',type=Path); c.add_argument('-o','--output',type=Path,required=True); c.add_argument('--revision',required=True)
    c=commands.add_parser('fit',help='Build explicit placement/scale/mask candidates and a local comparison')
    c.add_argument('spec',type=Path); c.add_argument('-o','--output',type=Path,required=True)
    c=commands.add_parser('atlas',help='Pack declared frame PNGs into an immutable atlas revision')
    c.add_argument('spec',type=Path); c.add_argument('-o','--output',type=Path,required=True)
    c=commands.add_parser('extract',help='Extract exact cells, retaining native root metadata')
    c.add_argument('revision_dir',type=Path); c.add_argument('-o','--output',type=Path,required=True)
    return p


def main(argv):
    args=parser().parse_args(argv)
    try:
        cmd=args.command
        if cmd in ('generate','edit'):
            from .images_api import request
            result=request(args.prompt,args.output,args.asset,args.revision,references=args.reference,mask=args.mask,
                           model=args.model,size=args.size,quality=args.quality,background=args.background,dry_run=args.dry_run,edit=cmd=='edit')
        elif cmd=='import':
            result={'destination':str(pipeline.import_png(args.input,args.output,args.asset,args.revision,args.sha256))}
        elif cmd=='build':
            result={'destination':str(pipeline.build(args.spec,args.output,args.revision))}
            result['verification']=pipeline.verify(args.output)
        elif cmd=='verify':
            result=pipeline.verify(args.revision_dir)
        elif cmd=='review':
            result={'review':str(review(args.revision_dir,args.output))}
        elif cmd in ('approve','reject'):
            result=decision(args.revision_dir,args.by,args.note,cmd=='approve',args.reference)
        elif cmd=='inspect':
            manifest=read(args.revision_dir/'manifest.json')
            history=[]
            for file in sorted(args.revision_dir.parent.glob('*/manifest.json')):
                other=read(file)
                if other.get('asset')==manifest.get('asset'):
                    history.append({'revision':other['revision'],'directory':str(file.parent),'approval':approval_status(file.parent)['state']})
            result={'manifest':manifest,'current_verification':pipeline.verify(args.revision_dir),'approval':approval_status(args.revision_dir),'history':history}
        elif cmd=='rebuild':
            result={'destination':str(pipeline.rebuild(args.revision_dir,args.output,args.revision))}
        elif cmd=='fit':
            from .fitting import fit
            result={'destination':str(fit(args.spec,args.output))}
        elif cmd=='atlas':
            result={'destination':str(pipeline.build_atlas(args.spec,args.output))}
        elif cmd=='extract':
            result={'destination':str(pipeline.extract_cells(args.revision_dir,args.output))}
        # Every command that creates a revision reports its independent readback.
        if cmd in {'import', 'rebuild', 'atlas', 'extract'}:
            result['verification'] = pipeline.verify(args.output)
        if cmd == 'fit':
            result['fitting'] = read(args.output / 'fit.json')
        print(json.dumps(result,indent=2,default=str))
        reports = [result] if cmd == 'verify' else []
        reports += [result[k] for k in ('verification', 'validation', 'current_verification') if k in result]
        if cmd == 'fit':
            reports += result['fitting']['candidates']
        if any(report['technical'] != 'pass' for report in reports):
            return 2
        return 0
    except (OSError,ValueError,KeyError,TypeError) as error:
        print(f'arty: {error}',file=sys.stderr)
        return 2
