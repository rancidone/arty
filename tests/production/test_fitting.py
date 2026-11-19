import pytest
from PIL import Image
from art_pipeline.raster import adapt
from art_pipeline.storage import write,read,sha
from art_pipeline.fitting import fit


def test_crop_mask_rotation_and_placement_preserve_rgba():
    im=Image.new('RGBA',(6,6));im.putpixel((2,2),(10,20,30,127));im.putpixel((4,4),(50,60,70,255))
    with pytest.raises(ValueError,match='discard'):adapt(im,[{'op':'crop','rect':[1,1,3,3]}])
    cut=adapt(im,[{'op':'mask','mode':'keep','rects':[[2,2,1,1]]},{'op':'crop','rect':[2,2,1,1]}])
    assert cut.size==(1,1) and cut.getpixel((0,0))==(10,20,30,127)
    erased=adapt(im,[{'op':'mask','mode':'erase','polygons':[[[1,1],[3,1],[3,3],[1,3]]]}])
    assert erased.getpixel((2,2))==(0,0,0,0) and erased.getpixel((4,4))==(50,60,70,255)
    line=Image.new('RGBA',(2,1));line.putpixel((0,0),(1,2,3,127));line.putpixel((1,0),(4,5,6,255))
    rotated=adapt(line,[{'op':'rotate','degrees_cw':90}])
    assert rotated.size==(1,2) and list(rotated.get_flattened_data())==[(1,2,3,127),(4,5,6,255)]
    with pytest.raises(ValueError):adapt(im,[{'op':'rotate','degrees_cw':float('nan')}])
    with pytest.raises(ValueError):adapt(im,[{'op':'mask','rects':[[5,5,2,2]]}])


def test_fit_builds_immutable_independent_candidates(tmp_path):
    body=Image.new('RGBA',(8,8));body.putpixel((2,4),(100,80,60,255));body.save(tmp_path/'body.png')
    prop=Image.new('RGBA',(2,2),(20,30,40,255));prop.save(tmp_path/'prop.png')
    write(tmp_path/'base.json',{'asset':'fixture','revision':'draft','canvas':[8,8],'root':None,'layers':[{'id':'body','path':'body.png'},{'id':'prop','path':'prop.png'}],'draw_order':['body','prop'],'constraints':{'binary_alpha':True},'artistic_limitations':['human fit review']})
    write(tmp_path/'fit.json',{'build_spec':'base.json','layer':'prop','view_rect':[0,0,8,8],'zoom':2,'candidates':[{'id':'a','operations':[{'op':'place','canvas':[8,8],'offset':[2,4]}]},{'id':'b','operations':[{'op':'nearest','size':[1,1]},{'op':'place','canvas':[8,8],'offset':[3,4]}]}]})
    before=sha(tmp_path/'body.png');fit(tmp_path/'fit.json',tmp_path/'result')
    report=read(tmp_path/'result/fit.json');assert all(c['technical']=='pass' for c in report['candidates'])
    assert Image.open(tmp_path/'result/a/prop.png').getpixel((2,4))==(20,30,40,255)
    assert Image.open(tmp_path/'result/b/prop.png').getpixel((3,4))==(20,30,40,255)
    assert before==sha(tmp_path/'result/a/body.png')==sha(tmp_path/'result/b/body.png')
    assert report['artistic_approval']=='pending' and (tmp_path/'result/comparison.png').exists()
    with pytest.raises(FileExistsError):fit(tmp_path/'fit.json',tmp_path/'result')
