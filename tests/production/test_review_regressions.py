"""Regressions for delivered identities, reused inputs and generated paths."""
import base64
import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from art_pipeline import pipeline
from art_pipeline.images_api import request
from art_pipeline.review import decision, approval_status
from art_pipeline.storage import read, write, sha


def fixture_spec(tmp_path):
    Image.new('RGBA', (2, 2), 'red').save(tmp_path/'source.png')
    spec = tmp_path/'spec.json'
    write(spec, {'asset':'test', 'revision':'r01', 'canvas':[2,2], 'root':None,
        'layers':[{'id':'base','path':'source.png'}], 'draw_order':['base'],
        'references':[{'path':'source.png'}]})
    return spec


@pytest.mark.parametrize('action', ['modify', 'delete'])
def test_auxiliary_identity_changes_invalidate_verification_and_approval(tmp_path, action):
    root = tmp_path/'r01'
    pipeline.build(fixture_spec(tmp_path), root)
    # Generic delivered-file identities remain protected independently of exporters.
    auxiliary = root/'metadata.json'
    write(auxiliary, {'note':'generic delivery metadata'})
    manifest = read(root/'manifest.json')
    manifest['sidecars'] = [{'file':auxiliary.name, 'sha256':sha(auxiliary)}]
    write(root/'manifest.json', manifest)
    assert pipeline.verify(root)['technical'] == 'pass'
    decision(root, 'tester', 'Reviewed fixture', reference=tmp_path/'source.png')
    assert approval_status(root)['state'] == 'approved'
    if action == 'modify':
        auxiliary.write_text('changed')
    else:
        auxiliary.unlink()
    assert pipeline.verify(root)['technical'] == 'fail'
    assert approval_status(root)['state'] == 'invalidated'


@pytest.mark.parametrize('layer', ['preview', 'preview-2x', 'Preview'])
def test_reserved_layer_rejected_before_publish(tmp_path, layer):
    spec = fixture_spec(tmp_path)
    value = read(spec)
    value['layers'][0]['id'] = layer
    value['draw_order'] = [layer]
    write(spec, value)
    with pytest.raises(ValueError, match='reserved'):
        pipeline.build(spec, tmp_path/'out')
    assert not (tmp_path/'out').exists()
    assert not (tmp_path/'out.lock').exists()


def test_repeated_api_inputs_have_unique_identities(tmp_path):
    fixture_spec(tmp_path)
    (tmp_path/'prompt.txt').write_text('test')
    stream = io.BytesIO()
    Image.new('RGBA', (1024, 1024)).save(stream, format='PNG')
    edit = Mock(return_value=SimpleNamespace(data=[SimpleNamespace(
        b64_json=base64.b64encode(stream.getvalue()).decode())]))
    request(tmp_path/'prompt.txt', tmp_path/'api', 'test', 'r01',
            references=[tmp_path/'source.png',tmp_path/'source.png'],
            mask=tmp_path/'source.png', client=SimpleNamespace(images=SimpleNamespace(edit=edit)))
    assert edit.call_count == 1
    assert len(edit.call_args.kwargs['image']) == 2
    assert 'mask' in edit.call_args.kwargs
    assert len(read(tmp_path/'api/manifest.json')['sources']) == 2
    assert pipeline.verify(tmp_path/'api')['technical'] == 'pass'


def test_atlas_resolves_references_and_dependencies_from_spec(tmp_path):
    fixture_spec(tmp_path)
    (tmp_path/'extra.txt').write_text('dependency')
    spec = tmp_path/'atlas.json'
    write(spec, {'asset':'test','revision':'r01','cell':[2,2],'grid':[1,1],
        'frames':[{'path':'source.png','cell':[0,0]}],
        'references':[{'path':'source.png','comparison':'registered_exact'}],
        'dependencies':[{'path':'extra.txt'}]})
    pipeline.build_atlas(spec, tmp_path/'atlas')
    assert pipeline.verify(tmp_path/'atlas')['technical'] == 'pass'
    manifest = read(tmp_path/'atlas/manifest.json')
    assert any(r['sha256'] == sha(tmp_path/'extra.txt') for r in manifest['sources'])
    assert manifest['references'][0]['sha256'] == sha(tmp_path/'source.png')


def test_build_delivers_only_generic_revision_files(tmp_path):
    root = tmp_path/'r01'
    pipeline.build(fixture_spec(tmp_path), root)
    assert {p.name for p in root.iterdir()} == {
        'inputs', 'base.png', 'preview.png', 'manifest.json', 'verification.json'}
    assert 'sidecars' not in read(root/'manifest.json')
