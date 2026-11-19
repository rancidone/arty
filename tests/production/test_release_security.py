"""Regression coverage for the public-release security review."""
import os
from pathlib import Path

import pytest
from PIL import Image

from art_pipeline import images_api, pipeline, raster
from art_pipeline.production_cli import main
from art_pipeline.review import review
from art_pipeline.storage import read, write


def test_wheel_install_does_not_load_parent_environment(tmp_path, monkeypatch):
    module = tmp_path / 'site-packages/art_pipeline/images_api.py'
    module.parent.mkdir(parents=True)
    monkeypatch.setattr(images_api, '__file__', str(module))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.delenv('ART_AUDIT_UNEXPECTED', raising=False)
    monkeypatch.delenv('OPENAI_BASE_URL', raising=False)
    (tmp_path / '.env').write_text('ART_AUDIT_UNEXPECTED=loaded\nOPENAI_BASE_URL=https://attacker.invalid/v1\n')
    # Even a coincidental project marker cannot make a wheel into a checkout.
    (tmp_path / 'pyproject.toml').write_text('[project]\nname="art-pipeline"\n')
    images_api.load_environment()
    assert 'ART_AUDIT_UNEXPECTED' not in os.environ
    assert 'OPENAI_BASE_URL' not in os.environ


@pytest.mark.parametrize('project', [None, '[project]\nname="other"\n', 'invalid toml'])
def test_source_layout_alone_is_not_a_checkout(tmp_path, monkeypatch, project):
    module = tmp_path / 'src/art_pipeline/images_api.py'
    module.parent.mkdir(parents=True)
    if project is not None:
        (tmp_path / 'pyproject.toml').write_text(project)
    (tmp_path / '.env').write_text('ART_AUDIT_UNEXPECTED=loaded\n')
    monkeypatch.setattr(images_api, '__file__', str(module))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.delenv('ART_AUDIT_UNEXPECTED', raising=False)
    images_api.load_environment()
    assert 'ART_AUDIT_UNEXPECTED' not in os.environ


def test_production_load_rejects_animation(tmp_path):
    path = tmp_path / 'animated.png'
    Image.new('RGBA', (2, 2), 'red').save(
        path, save_all=True, append_images=[Image.new('RGBA', (2, 2), 'blue')], duration=100)
    with pytest.raises(ValueError, match='animation'):
        raster.load(path)
    assert main(['import', str(path), '-o', str(tmp_path/'out'), '--asset', 'test', '--revision', 'r01']) == 2
    assert not (tmp_path/'out').exists()


def test_decompression_warning_becomes_clean_cli_error(tmp_path, monkeypatch):
    path = tmp_path / 'large.png'
    Image.new('RGBA', (3, 3)).save(path)
    monkeypatch.setattr(Image, 'MAX_IMAGE_PIXELS', 5)
    with pytest.raises(ValueError, match='decompression'):
        raster.load(path)
    assert main(['import', str(path), '-o', str(tmp_path/'out'), '--asset', 'test', '--revision', 'r01']) == 2
    assert not (tmp_path/'out').exists()


def test_production_load_checks_size_before_decode(tmp_path, monkeypatch):
    path = tmp_path / 'large.png'
    Image.new('RGBA', (4, 4)).save(path)
    monkeypatch.setattr(raster, 'MAX_PIXELS', 15)
    with pytest.raises(ValueError, match='safety limit'):
        raster.load(path)


@pytest.mark.parametrize('operation', [
    lambda im: raster.adapt(im, [{'op': 'nearest', 'size': [11, 10]}]),
    lambda im: raster.adapt(im, [{'op': 'place', 'canvas': [11, 10], 'offset': [0, 0]}]),
    lambda im: raster.adapt(im, [{'op': 'rotate', 'degrees_cw': 45}]),
    lambda im: raster.adapt(im, [{'op': 'sample_grid', 'grid': {
        'width': 11, 'height': 10, 'scale_x': 1, 'scale_y': 1}}]),
    lambda im: raster.compose([im], [11, 10]),
    lambda im: raster.pack([(im, 0, 0)], [8, 8], [2, 2]),
])
def test_oversized_operations_rejected_before_allocation(monkeypatch, operation):
    image = Image.new('RGBA', (8, 8))
    monkeypatch.setattr(raster, 'MAX_PIXELS', 100)
    def forbidden(*args, **kwargs):
        pytest.fail('Unsafe allocation was attempted')
    monkeypatch.setattr(Image, 'new', forbidden)
    monkeypatch.setattr(Image.Image, 'resize', forbidden)
    monkeypatch.setattr(Image.Image, 'rotate', forbidden)
    with pytest.raises(ValueError, match='safety limit'):
        operation(image)


@pytest.mark.parametrize('export_2x,canvas', [(False, [11, 10]), (True, [6, 6])])
def test_build_limits_checked_before_creating_revision(tmp_path, monkeypatch, export_2x, canvas):
    spec = tmp_path/'spec.json'
    write(spec, {'asset': 'test', 'revision': 'r01', 'canvas': canvas, 'export_2x': export_2x})
    monkeypatch.setattr(raster, 'MAX_PIXELS', 100)
    with pytest.raises(ValueError, match='safety limit'):
        pipeline.build(spec, tmp_path/'out')
    assert not (tmp_path/'out').exists()
    assert not (tmp_path/'out.lock').exists()


@pytest.mark.parametrize('manifest', [
    {}, [], {'schema_version': pipeline.SCHEMA, 'kind': 'unknown', 'sources': [], 'artifacts': []},
    *[{'schema_version': pipeline.SCHEMA, 'kind': kind, 'asset': 'test', 'revision': 'r01',
       'sources': [], 'artifacts': []} for kind in ('import', 'build', 'api', 'extraction')],
])
def test_invalid_empty_manifests_fail_verification(tmp_path, manifest):
    write(tmp_path/'manifest.json', manifest)
    assert pipeline.verify(tmp_path)['technical'] == 'fail'
    assert main(['verify', str(tmp_path)]) == 2


@pytest.mark.parametrize('mutation', [
    lambda m: m.update(kind='unknown'),
    lambda m: m.update(sources=[]),
    lambda m: m.update(sources=[{}]),
    lambda m: m.update(sources=[None]),
    lambda m: m['sources'][0].update(sha256='not-a-hash'),
])
def test_malformed_identity_records_fail(tmp_path, mutation):
    source = tmp_path/'source.png'
    Image.new('RGBA', (2, 2)).save(source)
    root = tmp_path/'r01'
    pipeline.import_png(source, root, 'test', 'r01')
    manifest = read(root/'manifest.json')
    mutation(manifest)
    write(root/'manifest.json', manifest)
    assert pipeline.verify(root)['technical'] == 'fail'


@pytest.mark.parametrize('existing', ['sidecar', 'image', 'dangling-sidecar'])
def test_review_preserves_existing_outputs(tmp_path, existing):
    source = tmp_path/'source.png'
    Image.new('RGBA', (2, 2)).save(source)
    root = tmp_path/'r01'
    pipeline.import_png(source, root, 'test', 'r01')
    image = tmp_path/'board.png'
    sidecar = tmp_path/'board.review.json'
    conflict = image if existing == 'image' else sidecar
    if existing == 'dangling-sidecar':
        try:
            conflict.symlink_to(tmp_path/'missing')
        except OSError as error:
            if os.name == 'nt' and getattr(error, 'winerror', None) == 1314:
                pytest.skip('Symlink privilege unavailable; ordinary users do not need it')
            raise
    else:
        conflict.write_bytes(b'EXISTING USER DATA')
    with pytest.raises(FileExistsError):
        review(root, image)
    if existing == 'dangling-sidecar':
        assert conflict.is_symlink()
        assert not (tmp_path/'missing').exists()
    else:
        assert conflict.read_bytes() == b'EXISTING USER DATA'
    assert not (sidecar if existing == 'image' else image).exists()


def test_review_sidecar_race_does_not_overwrite(tmp_path, monkeypatch):
    import art_pipeline.cli as cli
    source = tmp_path/'source.png'
    Image.new('RGBA', (2, 2)).save(source)
    root = tmp_path/'r01'
    pipeline.import_png(source, root, 'test', 'r01')
    sidecar = tmp_path/'board.review.json'
    original_link = cli.os.link
    def racing_link(source, destination):
        if destination == sidecar:
            sidecar.write_text('RACING WRITER')
        return original_link(source, destination)
    monkeypatch.setattr(cli.os, 'link', racing_link)
    with pytest.raises(FileExistsError):
        review(root, tmp_path/'board.png')
    assert sidecar.read_text() == 'RACING WRITER'


def test_api_verification_rechecks_saved_png(tmp_path):
    from art_pipeline.storage import sha
    root = tmp_path/'r01'
    root.mkdir()
    prompt = root/'prompt.txt'
    prompt.write_text('test')
    output = root/'source.png'
    output.write_bytes(b'not a PNG')
    write(root/'manifest.json', {
        'schema_version': pipeline.SCHEMA, 'kind': 'api', 'asset': 'test', 'revision': 'r01',
        'sources': [{'file': 'prompt.txt', 'sha256': sha(prompt)}],
        'artifacts': [{'file': 'source.png', 'sha256': sha(output)}],
        'request': {'parameters': {'model': 'test', 'prompt': 'test', 'size': '2x2'}},
        'validation': {'technical': 'pass'},
    })
    assert pipeline.verify(root)['technical'] == 'fail'
    Image.new('RGBA', (3, 3)).save(output)
    manifest = read(root/'manifest.json')
    manifest['artifacts'][0]['sha256'] = sha(output)
    write(root/'manifest.json', manifest)
    report = pipeline.verify(root)
    assert report['technical'] == 'fail'
    assert not report['checks']['api_dimensions']


def test_review_binds_outputs_to_actual_png(tmp_path):
    from art_pipeline.storage import sha
    source = tmp_path/'source.png'
    Image.new('RGBA', (2, 2)).save(source)
    root = tmp_path/'r01'
    pipeline.import_png(source, root, 'test', 'r01')
    output = tmp_path/'board.png'
    review(root, output)
    assert read(tmp_path/'board.review.json')['review_sha256'] == sha(output)
    with Image.open(output) as image:
        assert image.format == 'PNG'
