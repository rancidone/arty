"""Portable behavior everywhere, plus native Windows filesystem checks in CI."""
import errno
import os
from pathlib import Path
import sys

import pytest
from PIL import Image

from art_pipeline import cli, pipeline, storage
from art_pipeline.fitting import fit
from art_pipeline.storage import name, portable_filename, read, revision, write


@pytest.mark.parametrize('value', ['CON','con.txt','NUL.tar.gz','AUX','PRN',
    'COM1','com9.png','LPT1','lpt9.txt','CONIN$','CONOUT$', 'COM¹',
    'trailing.', 'trailing ', '../escape', 'a:b', 'a\\b', 'a/b', 'a?b', '', 'x'*256])
def test_windows_unsafe_filenames_rejected_everywhere(value):
    with pytest.raises(ValueError):
        portable_filename(value)
    with pytest.raises(ValueError):
        name(value)


def fixture_spec(root, ids=('base',)):
    root.mkdir(parents=True,exist_ok=True)
    Image.new('RGBA',(2,2),'red').save(root/'source é.png')
    spec=root/'build.json'
    write(spec, {'asset':'test','revision':'r01','canvas':[2,2],
        'layers':[{'id':v,'path':'source é.png'} for v in ids], 'draw_order':list(ids)})
    return spec


def test_layer_case_collisions_fail_before_publication(tmp_path):
    spec=fixture_spec(tmp_path,('Body','body'))
    with pytest.raises(ValueError,match='ignoring case'):
        pipeline.build(spec,tmp_path/'out')
    assert not (tmp_path/'out').exists()
    assert not (tmp_path/'out.lock').exists()


def test_candidate_case_collisions_fail_before_publication(tmp_path):
    spec=fixture_spec(tmp_path)
    write(tmp_path/'fit.json',{'build_spec':spec.name,'layer':'base',
        'view_rect':[0,0,2,2],'candidates':[{'id':v,'operations':[]} for v in ('A','a')]})
    with pytest.raises(ValueError,match='uniquely'):
        fit(tmp_path/'fit.json',tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_revision_lock_is_closed_before_publication(tmp_path, monkeypatch):
    original=Path.open
    handles=[]
    def track(path,*args,**kwargs):
        handle=original(path,*args,**kwargs)
        if path.suffix == '.lock': handles.append(handle)
        return handle
    monkeypatch.setattr(Path,'open',track)
    with revision(tmp_path/'out') as stage:
        assert handles and all(h.closed for h in handles)
        (stage/'data').write_bytes(b'ok')
    assert not (tmp_path/'out.lock').exists()
    with pytest.raises(RuntimeError):
        with revision(tmp_path/'failed'):
            raise RuntimeError('test')
    assert not (tmp_path/'failed.lock').exists()
    assert not list(tmp_path.glob('.art-stage-*'))


def test_revision_does_not_remove_another_writers_lock(tmp_path):
    lock=tmp_path/'out.lock'; lock.write_bytes(b'owner')
    with pytest.raises(FileExistsError):
        with revision(tmp_path/'out'): pytest.fail('lock bypassed')
    assert lock.read_bytes() == b'owner'


def test_spaced_unicode_and_deep_paths_roundtrip(tmp_path):
    root=tmp_path/'Project with spaces é' / ('nested-'+'x'*45) / ('deeper-'+'y'*45)
    spec=fixture_spec(root)
    out=root/'revision-r01'
    pipeline.build(spec,out)
    assert pipeline.verify(out)['technical']=='pass'
    manifest=read(out/'manifest.json')
    records=[r for r in manifest['sources'] if r.get('original_filename') == 'source é.png']
    assert len(str(out/records[0]['file'])) > 260
    assert storage.sha(out/records[0]['file']) == storage.sha(root/'source é.png')
    pipeline.rebuild(out,root/'revision-r02','r02')
    assert pipeline.verify(root/'revision-r02')['technical']=='pass'


def test_windows_hardlink_fallback_keeps_no_overwrite(tmp_path, monkeypatch):
    def unsupported(*args): raise OSError(errno.ENOTSUP,'not supported')
    monkeypatch.setattr(cli.os,'link',unsupported)
    monkeypatch.setattr(cli.sys,'platform','win32')
    original_rename=os.rename
    def windows_rename(source,destination):
        # Model Windows refusal on this POSIX test host.
        if Path(destination).exists(): raise FileExistsError(destination)
        original_rename(source,destination)
    monkeypatch.setattr(cli.os,'rename',windows_rename)
    source=tmp_path/'input.png'; source.write_bytes(b'source')
    out=tmp_path/'output.png'
    cli._write_files([(out,b'new')],source,False)
    assert out.read_bytes()==b'new'
    out.unlink()
    def race(source,destination):
        Path(destination).write_bytes(b'other writer')
        windows_rename(source,destination)
    monkeypatch.setattr(cli.os,'rename',race)
    with pytest.raises(FileExistsError): cli._write_files([(out,b'new')],source,False)
    assert out.read_bytes()==b'other writer'
    assert not list(tmp_path.glob('.arty-*'))


def test_permission_failure_cleans_staging(tmp_path, monkeypatch):
    source=tmp_path/'input';source.write_bytes(b'source')
    def denied(*args): raise PermissionError(errno.EACCES,'locked')
    monkeypatch.setattr(cli.os,'replace',denied)
    output=tmp_path/'output'; output.write_bytes(b'original')
    with pytest.raises(PermissionError):cli._write_files([(output,b'new')],source,True)
    assert output.read_bytes()==b'original'
    assert not list(tmp_path.glob('.arty-*'))


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows path semantics')
def test_windows_drive_backslash_and_case_paths(tmp_path):
    spec=fixture_spec(tmp_path/'Space é')
    value=read(spec)
    value['layers'][0]['path']=str((spec.parent/'source é.png').resolve())
    assert '\\' in value['layers'][0]['path']
    assert Path(value['layers'][0]['path']).drive
    write(spec,value)
    pipeline.build(spec,spec.parent/'out')
    assert pipeline.verify(spec.parent/'out')['technical']=='pass'
    source=spec.parent/'source é.png'
    with pytest.raises(cli.PipelineError,match='input image'):
        cli._write_files([(Path(str(source).upper()),b'wrong')],source,True)


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows file sharing')
def test_windows_locked_output_is_preserved(tmp_path):
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,
        wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    output=tmp_path/'output'; output.write_bytes(b'original')
    source=tmp_path/'source';source.write_bytes(b'source')
    handle=kernel.CreateFileW(str(output),0x80000000,1,None,3,0,None)
    assert handle != wintypes.HANDLE(-1).value
    try:
        with pytest.raises(PermissionError):cli._write_files([(output,b'new')],source,True)
    finally:
        kernel.CloseHandle(handle)
    assert output.read_bytes()==b'original'
    assert not list(tmp_path.glob('.arty-*'))


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows atomic rename')
def test_native_windows_publication_without_hardlinks(tmp_path, monkeypatch):
    def unsupported(*args): raise OSError(errno.ENOTSUP,'filesystem has no hard links')
    monkeypatch.setattr(cli.os,'link',unsupported)
    source=tmp_path/'source';source.write_bytes(b'source')
    output=tmp_path/'output'
    cli._write_files([(output,b'new')],source,False)
    assert output.read_bytes()==b'new'
    temporary=tmp_path/'temporary';temporary.write_bytes(b'attempted overwrite')
    with pytest.raises(FileExistsError):cli._publish_new(temporary,output)
    assert output.read_bytes()==b'new'


def test_snapshot_case_collision_is_rejected(tmp_path):
    root=tmp_path/'out';root.mkdir()
    source=tmp_path/'source.png';source.write_bytes(b'same')
    digest=storage.sha(source)
    folder=root/'inputs'/digest;folder.mkdir(parents=True)
    (folder/'SOURCE.PNG').write_bytes(b'same')
    with pytest.raises(ValueError,match='ignoring case'):
        storage.snapshot(source,root)


def test_snapshot_case_check_uses_original_spelling(tmp_path, monkeypatch):
    root=tmp_path/'out'; root.mkdir()
    source=tmp_path/'source.png'; source.write_bytes(b'same')
    folder=root/'inputs'/storage.sha(source); folder.mkdir(parents=True)
    existing=folder/'SOURCE.PNG'; existing.write_bytes(b'same')
    # Windows resolve() can return the on-disk spelling of an existing target.
    original=storage.local
    def canonical_local(root,relative):
        result=original(root,relative)
        return existing if result.name == 'source.png' else result
    monkeypatch.setattr(storage,'local',canonical_local)
    with pytest.raises(ValueError,match='ignoring case'):
        storage.snapshot(source,root)
