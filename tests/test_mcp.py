"""Exercise the real MCP stdio protocol without paid requests."""
import asyncio
import json
from pathlib import Path
import sys

import pytest
from PIL import Image

pytest.importorskip('mcp')
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from art_pipeline.mcp_server import create_server


def data(result):
    assert not result.isError, result
    return result.structuredContent or json.loads(result.content[0].text)


@pytest.mark.parametrize('override_workspace', [False, True])
def test_stdio_workflow_and_safety(tmp_path, override_workspace):
    # Neither the process cwd nor the server's configured default needs to be
    # the repo used by the caller. Decoy input detects a wrong-base success.
    launcher = tmp_path/'launcher'
    launcher.mkdir()
    default_workspace = tmp_path/'configured' if override_workspace else tmp_path
    if override_workspace:
        default_workspace.mkdir()
        Image.new('RGBA', (2, 2), (255, 0, 0, 255)).save(default_workspace/'source.png')
    Image.new('RGBA', (8, 8), (10, 20, 30, 255)).save(tmp_path/'source.png')
    (tmp_path/'prompt.txt').write_text('Synthetic offline prompt')
    (tmp_path/'specs').mkdir()
    (tmp_path/'specs/spec.json').write_text(json.dumps({
        'asset': 'test', 'revision': 'r01', 'canvas': [8, 8], 'root': None,
        'layers': [{'id': 'base', 'path': '../source.png'}], 'draw_order': ['base']}))

    async def run():
        parameters = StdioServerParameters(command=sys.executable,
            args=['-m', 'art_pipeline.mcp_server', '--workspace', str(default_workspace)],
            cwd=str(launcher), env={'PYTHONPATH': str(Path(__file__).resolve().parents[1]/'src'),
                                      'XDG_CONFIG_HOME': str(tmp_path/'config')})
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                initialization = await session.initialize()
                assert initialization.serverInfo.name == 'arty'
                assert str(default_workspace) in initialization.instructions
                tools = {t.name: t for t in (await session.list_tools()).tools}
                assert len(tools) == 12
                assert 'view_png' not in tools
                assert not {'approve', 'reject', 'approve_revision'} & tools.keys()
                assert tools['analyze_image'].annotations.readOnlyHint
                assert tools['request_image'].annotations.openWorldHint
                assert not tools['build_asset'].annotations.destructiveHint
                assert tools['request_image'].inputSchema['properties']['dry_run']['default'] is True
                for tool in tools.values():
                    assert 'workspace' in tool.inputSchema['properties']
                    assert 'workspace' not in tool.inputSchema.get('required', [])

                async def call(name, arguments):
                    if override_workspace:
                        arguments = {**arguments, 'workspace': str(tmp_path)}
                    return await session.call_tool(name, arguments)

                report = data(await call('analyze_image', {'input': 'source.png', 'size': [4, 4]}))
                assert report['sampling']['width'] == 4
                assert report['source']['size'] == [8, 8]
                converted = data(await call('convert_image', {
                    'input': 'source.png', 'output': 'converted.png', 'size': [4, 4],
                    'preview': 'preview.png', 'frames_dir': 'frames'}))
                assert Path(converted['output']) == tmp_path/'converted.png'
                assert (tmp_path/'converted.png').is_file()
                assert Path(converted['preview']) == tmp_path/'preview.png'
                assert list((tmp_path/'frames').glob('*.png'))
                assert (await call('convert_image', {
                    'input': 'source.png', 'output': 'converted.png', 'size': [4, 4]})).isError
                assert (await call('analyze_image', {
                    'input': 'source.png', 'size': [4, 4], 'scale': [2, 2]})).isError
                assert (await call('analyze_image', {'input': 'missing.png'})).isError
                assert (await call('analyze_image', {'input': 'source.png', 'size': [4]})).isError
                built = data(await call('build_asset', {'spec': 'specs/spec.json', 'output': 'r01'}))
                assert Path(built['destination']) == tmp_path/'r01'
                assert built['verification']['technical'] == 'pass'
                assert data(await call('verify_revision', {'revision_dir': 'r01'}))['technical'] == 'pass'
                assert data(await call('inspect_revision', {'revision_dir': 'r01'}))['approval']['state'] == 'pending'
                board = data(await call('create_review', {'revision_dir': 'r01', 'output': 'board.png'}))
                assert Path(board['sidecar']).exists()
                assert Path(board['review']) == tmp_path/'board.png'
                with Image.open(board['review']) as image:
                    assert image.format == 'PNG'
                    image.verify()
                rebuilt = data(await call('rebuild_revision', {
                    'revision_dir': 'r01', 'output': 'r02', 'revision': 'r02'}))
                assert rebuilt['verification']['technical'] == 'pass'
                imported = data(await call('import_png', {
                    'input': 'source.png', 'output': 'imported', 'asset': 'source', 'revision': 'r01'}))
                assert imported['verification']['technical'] == 'pass'
                plan = data(await call('request_image', {
                    'prompt_file': 'prompt.txt', 'output': 'paid', 'asset': 'test', 'revision': 'api-r01'}))
                assert plan['dry_run']
                assert not (tmp_path/'paid').exists()
                # A failing call must leave stdout framing and the session usable.
                assert (await call('analyze_image', {'input': 'missing.png'})).isError
                assert len((await session.list_tools()).tools) == 12
                # An override must not leak into subsequent calls or require
                # mutating the server process's working directory.
                if override_workspace:
                    default = data(await session.call_tool('analyze_image', {
                        'input': 'source.png', 'size': [2, 2]}))
                    assert default['source']['size'] == [2, 2]
                absolute = data(await session.call_tool('analyze_image', {
                    'input': str(tmp_path/'source.png'), 'size': [4, 4]}))
                assert absolute['source']['size'] == [8, 8]
                for invalid in ('relative/repo', str(tmp_path/'missing'), str(tmp_path/'source.png'), ''):
                    result = await session.call_tool('analyze_image', {
                        'input': 'source.png', 'workspace': invalid})
                    assert result.isError
                    assert 'absolute existing directory' in result.content[0].text
                missing = await session.call_tool('analyze_image', {'input': 'missing.png'})
                assert missing.isError
                assert str(default_workspace) in missing.content[0].text
                assert 'workspace' in missing.content[0].text
    asyncio.run(run())


@pytest.mark.parametrize('explicit_workspace', [False, True])
def test_default_and_per_call_override(tmp_path, monkeypatch, explicit_workspace):
    repos = tmp_path/'repos'
    project = repos/'game'
    project.mkdir(parents=True)
    Image.new('RGBA', (8, 8), (10, 20, 30, 255)).save(project/'source.png')
    # Same name at the shared root detects an override that was ignored.
    Image.new('RGBA', (2, 2), (255, 0, 0, 255)).save(repos/'source.png')
    monkeypatch.chdir(repos if not explicit_workspace else tmp_path)
    server = create_server(repos if explicit_workspace else None)

    async def run():
        async def source_size(arguments):
            result = await server.call_tool('analyze_image', {**arguments, 'size': [2, 2]})
            report = json.loads(result[0].text)
            return report['source']['size']

        assert await source_size({'input': 'game/source.png'}) == [8, 8]
        assert await source_size({'input': 'source.png', 'workspace': str(project)}) == [8, 8]
        assert await source_size({'input': 'source.png'}) == [2, 2]
        result = await server.call_tool('convert_image', {
            'input': 'game/source.png', 'output': 'game/converted.png', 'size': [4, 4]})
        converted = json.loads(result[0].text)
        assert Path(converted['output']) == project/'converted.png'
        assert (project/'converted.png').is_file()
    asyncio.run(run())


def test_paid_call_is_explicit_and_errors_sanitized(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from types import SimpleNamespace
    from art_pipeline import images_api
    from mcp.server.fastmcp.exceptions import ToolError
    (tmp_path/'prompt.txt').write_text('Synthetic prompt')
    client = SimpleNamespace(images=SimpleNamespace(generate=Mock(side_effect=RuntimeError('SECRET PRIVATE PAYLOAD'))))
    monkeypatch.setattr(images_api, 'create_client', lambda: client)

    async def run():
        server = create_server(tmp_path)
        await server.call_tool('request_image', {
            'prompt_file': 'prompt.txt', 'output': 'dry', 'asset': 'test', 'revision': 'r01'})
        client.images.generate.assert_not_called()
        with pytest.raises(ToolError) as error:
            await server.call_tool('request_image', {
                'prompt_file': 'prompt.txt', 'output': 'paid', 'asset': 'test', 'revision': 'r01', 'dry_run': False})
        assert 'SECRET' not in str(error.value)
        assert 'RuntimeError' in str(error.value)
        assert client.images.generate.call_count == 1
        assert not (tmp_path/'paid').exists()
    asyncio.run(run())
