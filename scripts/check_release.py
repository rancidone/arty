"""Install each distribution outside the checkout, then test its public surface.

Requires built wheel/sdist in --dist (default dist/). Uses public pip dependencies;
no credentials or paid API calls. Temporary environments are removed on exit.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import venv
import zipfile
import tarfile


def run(args, cwd, env):
    subprocess.run([str(a) for a in args], cwd=cwd, env=env, check=True)


def check_contents(archive):
    if archive.suffix == '.whl':
        with zipfile.ZipFile(archive) as z:
            files = z.namelist()
    else:
        with tarfile.open(archive) as t:
            files = t.getnames()
    for filename in files:
        parts = Path(filename).parts
        if any(part.startswith('.env') or part in {'.git','.venv','work','backups','__pycache__'} for part in parts):
            raise ValueError(f'Private/generated path in distribution: {filename}')
    if not any(Path(f).name == 'LICENSE' for f in files):
        raise ValueError('Distribution lacks license')
    if archive.suffix != '.whl':
        for expected in ('docs/specs.md','examples/specs/build.json','scripts/check_release.py','tests/test_mcp.py'):
            if not any(f.endswith('/'+expected) for f in files):
                raise ValueError(f'Source distribution lacks {expected}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, default=Path('dist'))
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    archives = sorted(args.dist.resolve().glob('*.whl')) + sorted(args.dist.resolve().glob('*.tar.gz'))
    if len(archives) != 2 or sum(p.suffix == '.whl' for p in archives) != 1:
        parser.error('Expected exactly one wheel and one .tar.gz source archive')
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    env['PYTHONNOUSERSITE'] = '1'
    for archive in archives:
        print(f'Checking {archive.name}', flush=True)
        check_contents(archive)
        with tempfile.TemporaryDirectory(prefix='arty-release-') as temp:
            root = Path(temp)
            environment = root/'env'
            venv.EnvBuilder(with_pip=True, symlinks=os.name != "nt").create(environment)
            bindir = environment/('Scripts' if os.name == 'nt' else 'bin')
            python = bindir/('python.exe' if os.name == 'nt' else 'python')
            arty = bindir/('arty.exe' if os.name == 'nt' else 'arty')
            mcp = bindir/('arty-mcp.exe' if os.name == 'nt' else 'arty-mcp')
            # Ordinary install must work without optional MCP dependencies.
            run([python,'-m','pip','install',archive],root,env)
            run([python,'-m','pip','check'],root,env)
            run([arty,'--help'],root,env)
            run([arty,'--version'],root,env)
            run([bindir/('art-pipeline.exe' if os.name == 'nt' else 'art-pipeline'),'--version'],root,env)
            run([python,'-c',
                'import art_pipeline; from pathlib import Path; '
                'assert "site-packages" in Path(art_pipeline.__file__).parts, art_pipeline.__file__'],root,env)
            run([python,'-m','pip','install',f'{archive}[dev,mcp]'],root,env)
            run([python,'-m','pip','check'],root,env)
            run([mcp,'--help'],root,env)
            run([bindir/('art-pipeline-mcp.exe' if os.name == 'nt' else 'art-pipeline-mcp'),'--help'],root,env)
            # No src/ or repository pytest config is available in this sandbox.
            shutil.copytree(repo/'tests',root/'tests',ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copytree(repo/'examples',root/'examples')
            (root/'pytest.ini').write_text('[pytest]\ntestpaths = tests\n',encoding='utf-8')
            run([python,'-m','pytest','-q'],root,env)
            commands = [
                ['convert','examples/slime-enlarged.png','-o','work/slime.png','--size','96x64',
                 '--columns','3','--rows','2','--preview','work/preview.png'],
                ['build','examples/specs/build.json','-o','work/build-r01'],
                ['verify','work/build-r01'],
                ['review','work/build-r01','-o','work/build-review.png'],
                ['rebuild','work/build-r01','--revision','native-r02','-o','work/build-r02'],
                ['atlas','examples/specs/atlas.json','-o','work/atlas-r01'],
                ['extract','work/atlas-r01','-o','work/cells-r01'],
                ['fit','examples/specs/fit.json','-o','work/fit-r01'],
            ]
            for command in commands:
                run([arty,*command],root,env)
            run([python,'-c',
                'from art_pipeline.raster import load, same; '
                'assert same(load("work/slime.png"), load("examples/slime-original.png")); '
                'assert same(load("work/build-r01/preview.png"), load("work/build-r02/preview.png")); '
                'assert same(load("work/cells-r01/cell-0-0.png"), load("examples/slime-original.png")); '
                'assert load("work/cells-r01/cell-1-0.png").getchannel("A").getbbox() is None'],root,env)
    print('Wheel and source distribution checks passed.')


if __name__ == '__main__':
    main()
