"""Public command status, bounded measurements, and decision semantics."""
import base64
from collections import Counter
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from PIL import Image

from art_pipeline import pipeline, raster
from art_pipeline.production_cli import main
from art_pipeline.review import approval_status, decision
from art_pipeline.storage import read, write


def build_spec(tmp_path):
    Image.new('RGBA', (2, 2), (255, 0, 0, 128)).save(tmp_path/'source.png')
    spec = tmp_path/'build.json'
    write(spec, {'asset':'test', 'revision':'r01', 'canvas':[2,2], 'root':None,
                 'layers':[{'id':'base','path':'source.png'}], 'draw_order':['base'],
                 'constraints':{'binary_alpha':True}})
    return spec


def test_atlas_failure_has_report_and_nonzero_exit(tmp_path, capsys):
    build_spec(tmp_path)
    spec = tmp_path/'atlas.json'
    write(spec, {'asset':'test','revision':'r01','cell':[2,2],'grid':[1,1],
                 'frames':[{'path':'source.png','cell':[0,0]}],
                 'constraints':{'binary_alpha':True}})
    assert main(['atlas', str(spec), '-o', str(tmp_path/'out')]) == 2
    assert '"technical": "fail"' in capsys.readouterr().out
    assert pipeline.verify(tmp_path/'out')['technical'] == 'fail'


@pytest.mark.parametrize('command', ['generate', 'edit'])
def test_api_contract_failure_retains_bytes_and_exits_nonzero(tmp_path, monkeypatch, command):
    build_spec(tmp_path)
    (tmp_path/'prompt.txt').write_text('test')
    stream = BytesIO()
    Image.new('RGBA', (2, 2)).save(stream, format='PNG')
    response = SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(stream.getvalue()).decode())])
    method = Mock(return_value=response)
    monkeypatch.setattr('art_pipeline.images_api.create_client', lambda: SimpleNamespace(
        images=SimpleNamespace(generate=method, edit=method)))
    args = [command, '--prompt', str(tmp_path/'prompt.txt'), '--asset','test',
            '--revision','r01','-o',str(tmp_path/'out')]
    if command == 'edit':
        args += ['--reference', str(tmp_path/'source.png')]
    assert main(args) == 2
    assert (tmp_path/'out/source.png').read_bytes() == stream.getvalue()
    assert method.call_count == 1


def test_fit_and_inspect_propagate_failed_checks(tmp_path):
    spec = build_spec(tmp_path)
    write(tmp_path/'fit.json', {'build_spec':spec.name,'layer':'base',
        'view_rect':[0,0,2,2], 'candidates':[{'id':'partial','operations':[]},
        {'id':'binary','operations':[{'op':'alpha_threshold','value':128}]}]})
    assert main(['fit', str(tmp_path/'fit.json'), '-o', str(tmp_path/'fit')]) == 2
    assert [c['technical'] for c in read(tmp_path/'fit/fit.json')['candidates']] == ['fail','pass']
    assert main(['inspect',str(tmp_path/'fit/partial')]) == 2


def test_rejection_survives_technical_failure_but_not_mutation(tmp_path):
    pipeline.build(build_spec(tmp_path), tmp_path/'out')
    decision(tmp_path/'out', 'Reviewer', 'Partial alpha is unacceptable', approved=False)
    assert approval_status(tmp_path/'out')['state'] == 'rejected'
    (tmp_path/'out/base.png').write_bytes(b'changed')
    assert approval_status(tmp_path/'out')['state'] == 'invalidated'


@pytest.mark.parametrize('size', [(1,1),(1,257),(257,1),(259,258)])
def test_tiled_measurements_match_reference(size):
    rng = np.random.default_rng(42)
    a = rng.integers(0, 5, (*size[::-1],4), dtype=np.uint8)
    a[:,:,3] = np.choose(a[:,:,3], [0,0,128,255,255])
    image = Image.fromarray(a)
    result = raster.measure(image)
    visible = {(x,y) for y in range(image.height) for x in range(image.width) if a[y,x,3]}
    assert result['visible_pixels'] == len(visible)
    assert result['isolated_pixels'] == sum(not any((x+dx,y+dy) in visible
        for dx,dy in [(1,0),(-1,0),(0,1),(0,-1)]) for x,y in visible)
    alpha = Counter(int(v) for v in a[:,:,3].ravel())
    assert result['alpha_counts'] == {str(v):n for v,n in sorted(alpha.items())}
    assert result['partial_alpha_pixels'] == alpha[128]
    colors = Counter('#%02X%02X%02X' % tuple(p[:3]) for row in a for p in row if p[3])
    assert result['palette'] == dict(sorted(colors.items()))
    assert result['transparent_rgb_noise'] == sum(not p[3] and any(p[:3]) for row in a for p in row)


def test_palette_and_aggregate_budgets(tmp_path, monkeypatch):
    monkeypatch.setattr(raster, 'MAX_PALETTE_COLORS', 1)
    im = Image.new('RGBA',(2,1),'red'); im.putpixel((1,0),(0,0,255,255))
    with pytest.raises(ValueError, match='palette'):
        raster.measure(im)
    monkeypatch.setattr(raster, 'MAX_BUILD_PIXELS', 3)
    spec = build_spec(tmp_path)
    with pytest.raises(ValueError, match='aggregate'):
        pipeline.build(spec, tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_measurement_never_converts_whole_image_to_array(monkeypatch):
    image = Image.new('RGBA',(1024,1024),'red')
    original = np.asarray
    def bounded(obj, *args, **kwargs):
        if isinstance(obj, Image.Image):
            assert max(obj.size) <= raster.MEASURE_TILE + 2
        return original(obj, *args, **kwargs)
    monkeypatch.setattr(np, 'asarray', bounded)
    assert raster.measure(image)['visible_pixels'] == 1024**2


def test_atlas_budget_checked_before_loading_frames(tmp_path, monkeypatch):
    write(tmp_path/'atlas.json', {'asset':'test','revision':'r01','cell':[2,2],
        'grid':[1,1],'frames':[{'path':'missing.png','cell':[0,0]}]})
    monkeypatch.setattr(raster, 'MAX_BUILD_PIXELS', 3)
    with pytest.raises(ValueError, match='aggregate frame'):
        pipeline.build_atlas(tmp_path/'atlas.json', tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_paid_palette_failure_preserves_output(tmp_path, monkeypatch):
    (tmp_path/'prompt.txt').write_text('test')
    stream = BytesIO()
    image = Image.new('RGBA',(1024,1024),'red')
    image.putpixel((0,0),(0,0,255,255))
    image.save(stream,format='PNG')
    method = Mock(return_value=SimpleNamespace(data=[SimpleNamespace(
        b64_json=base64.b64encode(stream.getvalue()).decode())]))
    monkeypatch.setattr('art_pipeline.images_api.create_client', lambda: SimpleNamespace(
        images=SimpleNamespace(generate=method)))
    monkeypatch.setattr(raster, 'MAX_PALETTE_COLORS', 1)
    assert main(['generate','--prompt',str(tmp_path/'prompt.txt'),'--asset','test',
        '--revision','r01','-o',str(tmp_path/'out')]) == 2
    assert (tmp_path/'out/source.png').read_bytes() == stream.getvalue()
    assert method.call_count == 1
    assert read(tmp_path/'out/manifest.json')['validation']['technical'] == 'fail'
