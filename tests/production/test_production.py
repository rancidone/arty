import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from PIL import Image
from art_pipeline import raster, pipeline
from art_pipeline.storage import sha, read, write, snapshot, local
from art_pipeline.review import review, decision, approval_status
from art_pipeline.images_api import request, validate_request
from art_pipeline.cli import main


@pytest.fixture
def spec(tmp_path):
    # Synthetic independent rear/body/face/front fixture: hair intentionally outside scalp.
    canvas=(8,8)
    colors={'rear':(150,60,20,255),'body':(230,170,90,255),'face':(20,30,40,255),'front':(80,40,10,255)}
    coords={'rear':[(1,1),(3,3)],'body':[(3,3),(4,3),(4,4)],'face':[(4,3)],'front':[(4,3),(6,1)]}
    for key in colors:
        image=Image.new('RGBA',canvas)
        for xy in coords[key]: image.putpixel(xy,colors[key])
        image.save(tmp_path/f'{key}.png')
    reference=Image.new('RGBA',canvas)
    for key in colors:
        for xy in coords[key]: reference.putpixel(xy,colors[key])
    reference.save(tmp_path/'reference.png')
    recipe={'asset':'test-character','revision':'r01','canvas':list(canvas),'root':[4,7],
            'layers':[{'id':k,'path':f'{k}.png','root':[4,7]} for k in colors],
            'draw_order':list(colors),'constraints':{'binary_alpha':True,'rgba':True},'export_2x':True,
            'references':[{'path':'reference.png','status':'human visual authority','comparison':'registered_exact'}],
            'unknown':['attachments','timing']}
    path=tmp_path/'spec.json'; write(path,recipe)
    return path


def build(spec):
    destination=spec.parent/'r01'
    pipeline.build(spec,destination)
    return destination


def test_full_workflow_and_independent_occlusion(spec):
    before=sha(spec.parent/'body.png')
    root=build(spec)
    m=read(root/'manifest.json')
    result=raster.load(root/'preview.png')
    assert result.getpixel((1,1))==(150,60,20,255) # rear outside scalp survives
    assert result.getpixel((3,3))==(230,170,90,255) # base covers rear
    assert result.getpixel((4,3))==(80,40,10,255) # front covers face
    assert result.getpixel((6,1))==(80,40,10,255) # front outside scalp survives
    assert result.getpixel((4,4))==(230,170,90,255)
    assert sha(spec.parent/'body.png')==before==sha(root/'body.png')
    assert pipeline.verify(root)['technical']=='pass'
    assert m['root']==[4,7] and m['visual_approval']=='pending'
    assert raster.same(result,raster.load(spec.parent/'reference.png'))
    zoom=raster.load(root/'preview-2x.png')
    for y in range(8):
        for x in range(8):
            assert all(zoom.getpixel((x*2+dx,y*2+dy))==result.getpixel((x,y)) for dx in (0,1) for dy in (0,1))
    review(root,spec.parent/'review.png')
    assert Image.open(spec.parent/'review.png').height>0
    decision(root,'human tester','Reviewed fixture against saved reference',reference=spec.parent/'reference.png')
    assert approval_status(root)['state']=='approved'
    (root/'front.png').write_bytes((root/'face.png').read_bytes())
    assert pipeline.verify(root)['technical']=='fail'
    assert approval_status(root)['state']=='invalidated'


def test_import_bytes_rgba_and_bad_files(tmp_path):
    image=Image.new('P',(5,3)); image.putpalette([1,2,3]*256); image.info['transparency']=0
    file=tmp_path/'palette.png'; image.save(file)
    pipeline.import_png(file,tmp_path/'import','palette','r01')
    m=read(tmp_path/'import/manifest.json')
    assert sha(local(tmp_path/'import',m['sources'][0]['file']))==sha(file)
    assert raster.load(file).mode=='RGBA'
    assert raster.measure(raster.load(file))['visible_pixels']==0
    with pytest.raises(FileExistsError): pipeline.import_png(file,tmp_path/'import','palette','r01')
    with pytest.raises(ValueError,match='identity'): pipeline.import_png(file,tmp_path/'wrong','palette','r02','0'*64)
    file.write_bytes(b'not png')
    with pytest.raises(OSError): raster.load(file)
    with pytest.raises(OSError): raster.load(tmp_path/'missing.png')


def test_revision_immutability_and_atomic_failure(spec):
    root=build(spec); before=sha(root/'manifest.json')
    with pytest.raises(FileExistsError): pipeline.build(spec,root)
    assert sha(root/'manifest.json')==before
    data=read(spec); data['layers'][0]['path']='missing.png'; write(spec,data)
    with pytest.raises(OSError): pipeline.build(spec,spec.parent/'broken')
    assert not (spec.parent/'broken').exists()
    assert not (spec.parent/'broken.lock').exists()


@pytest.mark.parametrize('field,value', [('canvas',[9,8]),('draw_order',['body']),('root',[5,7])])
def test_registration_and_order_failures(spec,field,value):
    data=read(spec); data[field]=value; write(spec,data)
    if field=='root':
        assert pipeline.verify(build(spec))['technical']=='fail'
    else:
        with pytest.raises(ValueError): build(spec)


def test_exact_partial_alpha_composition():
    lower=Image.new('RGBA',(1,1),(0,0,200,255))
    upper=Image.new('RGBA',(1,1),(200,0,0,128))
    assert raster.compose([lower,upper],[1,1]).getpixel((0,0))==(100,0,100,255)


def test_binary_alpha_palette_noise_bounds(spec):
    image=raster.load(spec.parent/'front.png'); image.putpixel((0,7),(1,2,3,127)); image.save(spec.parent/'front.png')
    d=read(spec); d['constraints'].update(palette=['#000000'],max_isolated_pixels=0,allowed_bounds=[1,0,7,7]); write(spec,d)
    report=pipeline.verify(build(spec))
    assert report['technical']=='fail'
    assert not report['checks']['front:binary_alpha']
    assert not report['checks']['front:palette']
    assert not report['checks']['front:strays']
    assert not report['checks']['front:bounds']


def test_explicit_adaptation_and_no_silent_crop():
    image=Image.new('RGBA',(3,3)); image.putpixel((2,2),(1,2,3,127))
    with pytest.raises(ValueError,match='crop'): raster.adapt(image,[{'op':'place','canvas':[3,3],'offset':[1,0]}])
    placed=raster.adapt(image,[{'op':'place','canvas':[4,4],'offset':[1,1]}])
    assert placed.getpixel((3,3))==(1,2,3,127)
    binary=raster.adapt(placed,[{'op':'alpha_threshold','value':100}])
    assert binary.getpixel((3,3))==(1,2,3,255)
    remapped=raster.adapt(binary,[{'op':'palette_map','colors':{'#010203':'#AABBCC'}}])
    assert remapped.getpixel((3,3))==(170,187,204,255)
    selected=raster.adapt(remapped,[{'op':'retain_rect','rect':[0,0,2,2]}])
    assert selected.size==(4,4) and selected.getbbox() is None
    source=Image.new('RGBA',(4,4)); source.putpixel((3,3),(1,2,3,255))
    sampled=raster.adapt(source,[{'op':'sample_grid','size':[2,2]}])
    assert sampled.getpixel((1,1))==(1,2,3,255)


def test_saved_rebuild(spec):
    root=build(spec)
    (spec.parent/'body.png').unlink()
    pipeline.rebuild(root,spec.parent/'r02','r02')
    assert pipeline.verify(spec.parent/'r02')['technical']=='pass'
    assert sha(root/'preview.png')==sha(spec.parent/'r02/preview.png')
    (root/'front.png').write_bytes(b'corrupt')
    with pytest.raises(ValueError): pipeline.rebuild(root,spec.parent/'r03','r03')


def test_atlas_extract_pack_root(tmp_path):
    frames=[]
    for i in range(3):
        im=Image.new('RGBA',(3,4),(i+1,20,30,255)); im.save(tmp_path/f'{i}.png')
        frames.append({'id':f'pose{i}','path':f'{i}.png','cell':[i%2,i//2]})
    spec=tmp_path/'atlas.json'
    write(spec,{'asset':'test-atlas','revision':'r01','cell':[3,4],'grid':[2,2],'root':[1,3],'frames':frames,'constraints':{'binary_alpha':True},'export_2x':True})
    pipeline.build_atlas(spec,tmp_path/'atlas')
    assert pipeline.verify(tmp_path/'atlas')['technical']=='pass'
    pipeline.extract_cells(tmp_path/'atlas',tmp_path/'cells')
    m=read(tmp_path/'cells/manifest.json')
    assert m['root']==[1,3]
    assert raster.same(raster.load(tmp_path/'cells/cell-0-1.png'),raster.load(tmp_path/'2.png'))
    assert raster.load(tmp_path/'cells/cell-1-1.png').getbbox() is None
    with pytest.raises(ValueError): raster.pack([(Image.new('RGBA',(3,4)),0,0)]*2,[3,4],[2,2])


@pytest.mark.parametrize('model,size', [('gpt-image-2.6-sunburst','512x512'),('gpt-image-1.5','512x512'),('unknown','1024x1024'),('gpt-image-2.6-flare','1025x1024'),('gpt-image-2','3840x3840')])
def test_invalid_model_dimensions(model,size):
    with pytest.raises(ValueError): validate_request(model,size,'high')


def fake_response(data):
    return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(data).decode())],created=123,_request_id='synthetic-request')


def png_bytes(size=(1024,1024)):
    stream=io.BytesIO(); Image.new('RGBA',size,(1,2,3,0)).save(stream,format='PNG'); return stream.getvalue()


def test_api_generate_edit_dry_run_failure_and_output_contract(tmp_path):
    prompt=tmp_path/'prompt.txt'; prompt.write_text('Retain full hair silhouette')
    image=tmp_path/'reference.png'; image.write_bytes(png_bytes((8,8)))
    client=SimpleNamespace(images=SimpleNamespace(generate=Mock(return_value=fake_response(png_bytes())),edit=Mock(return_value=fake_response(png_bytes()))))
    assert request(prompt,tmp_path/'dry','a','r',dry_run=True,client=client)['dry_run']
    client.images.generate.assert_not_called(); assert not (tmp_path/'dry').exists()
    request(prompt,tmp_path/'gen','a','r01',client=client)
    assert (tmp_path/'gen/source.png').read_bytes()==png_bytes()
    assert read(tmp_path/'gen/manifest.json')['request']['parameters']['model']=='gpt-image-2.6-sunburst'
    request(prompt,tmp_path/'edit','a','r02',references=[image,image],mask=image,client=client,edit=True)
    assert len(client.images.edit.call_args.kwargs['image'])==2
    assert 'mask' in client.images.edit.call_args.kwargs
    client.images.generate.side_effect=RuntimeError('SECRET must never be printed')
    with pytest.raises(ValueError,match='RuntimeError') as error:
        request(prompt,tmp_path/'failure','a','r03',client=client)
    assert 'SECRET' not in str(error.value) and not (tmp_path/'failure').exists()
    client.images.generate.side_effect=None; client.images.generate.return_value=fake_response(png_bytes((8,8)))
    result=request(prompt,tmp_path/'bad-dimensions','a','r04',client=client)
    assert result['validation']['technical']=='fail'
    assert pipeline.verify(tmp_path/'bad-dimensions')['technical']=='fail'
    assert (tmp_path/'bad-dimensions/source.png').read_bytes()==png_bytes((8,8))
    with pytest.raises(FileExistsError): request(prompt,tmp_path/'gen','a','r01',client=client)
    assert client.images.generate.call_count==3


def test_approval_gates(spec):
    root=build(spec)
    with pytest.raises(ValueError,match='authority'): decision(root,'tester','good')
    decision(root,'tester','Rejected hair',approved=False)
    assert approval_status(root)['state']=='rejected'
    manifest=read(root/'manifest.json'); manifest['artistic_limitations']=['Hair needs redraw']; write(root/'manifest.json',manifest)
    with pytest.raises(ValueError,match='limitations'): decision(root,'tester','good',reference=spec.parent/'reference.png')
    assert approval_status(root)['state']=='invalidated'


def test_cli_full_workflow(spec,capsys):
    out=spec.parent/'cli-r01'
    assert main(['build',str(spec),'-o',str(out)])==0
    assert main(['verify',str(out)])==0
    assert main(['inspect',str(out)])==0
    assert main(['review',str(out),'-o',str(spec.parent/'cli-review.png')])==0
    assert main(['approve',str(out),'--by','human','--note','Reviewed reference','--reference',str(spec.parent/'reference.png')])==0
    assert main(['rebuild',str(out),'-o',str(spec.parent/'cli-r02'),'--revision','r02'])==0
    assert main(['import',str(spec.parent/'face.png'),'-o',str(spec.parent/'import'),'--asset','face','--revision','r01'])==0
    assert main(['verify',str(spec.parent/'missing')])==2


def test_path_security(tmp_path):
    with pytest.raises(ValueError): local(tmp_path,'../outside')
    with pytest.raises(ValueError): pipeline.import_png(tmp_path/'missing',tmp_path/'out','../bad','r01')
