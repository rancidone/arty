# Use arty from Codex

The optional MCP server exposes the local pipeline over stdio using the official
Python MCP SDK. It works with Codex and other clients supporting stdio MCP.
Python 3.11+ is required.

## Install and connect

From this checkout:

```sh
uv sync --extra dev --extra mcp
# Use absolute paths so Codex can launch it from any project.
codex mcp add arty -- /absolute/path/art-pipeline/.venv/bin/arty-mcp
codex mcp get arty
```

For pip installations, use `python -m pip install '.[mcp]'` from the checkout,
then register the absolute path to that environment's `arty-mcp` command.
The normal CLI does not require the MCP extra. MCP tests run when the extra is
installed; run `uv run --extra dev --extra mcp pytest -q` to include them.

Codex stores this registration in `~/.codex/config.toml`. An equivalent
configuration, with a longer timeout for paid image requests, is:

```toml
[mcp_servers.arty]
command = "/absolute/path/art-pipeline/.venv/bin/arty-mcp"
startup_timeout_sec = 20
tool_timeout_sec = 660
```

Use **Restart** in the client's MCP settings after changing configuration, or
start a new Codex session. See the [official Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
Remove the registration with `codex mcp remove arty`.

Relative tool paths default to the server's working directory at startup. Pass
`--workspace /absolute/path/to/project` when launching the server to override
that default. The server's directory may differ from the current chat's repo.

Every tool accepts an optional `workspace` argument to select an absolute,
existing directory for that call. For example:

```json
{
  "input": "docs/art/board.png",
  "workspace": "/absolute/path/to/project"
}
```

Pass this to `analyze_image` to analyze a repo-relative file from that project. The
override applies to all input and output paths in that call and does not change
the default for later calls. Paths **inside** build/atlas/fit JSON specs still
resolve relative to the spec file. Absolute tool paths also work without an
override. `~` is expanded in tool paths and workspace directories. The server
advertises its default directory in its initialization instructions and includes
it in missing-file errors. Restart the MCP connection after updating the server
to discover the new tool arguments.

The workspace is **not a filesystem sandbox**:
absolute paths and paths referenced by build specs can access other local files.
Use this server only with a trusted local client and trusted project files. It
opens no HTTP port. Tool annotations distinguish read-only, local writes, and
external API calls; the client controls tool approval policy.

## Tools and workflow

| Tool | Purpose |
| --- | --- |
| `analyze_image` | Inspect grid evidence; optionally supply native sheet `size` or source-pixel `scale` |
| `convert_image` | Write a new recovered PNG and frame JSON, optionally preview and frame files |
| `import_png` | Preserve source bytes in a new revision |
| `build_asset` | Compose a JSON build spec into a revision |
| `build_atlas` / `extract_cells` | Pack declared frames / extract exact cells |
| `fit_overlay` | Build explicit transform candidates and a comparison board |
| `verify_revision` / `inspect_revision` | Check identities, pixels, recipe and current human decision |
| `rebuild_revision` | Rebuild offline from saved inputs |
| `create_review` | Write a comparison PNG and hash-bound sidecar |
| `request_image` | Plan or execute one generation/edit request; defaults to `dry_run=true` |

Example request to Codex:

> Use arty to analyze examples/slime-enlarged.png, recover its 96x64
> native sheet with 3 columns and 2 rows into work/slime.png, and show me the
> preview. Do not generate new artwork.

For layered assets, have Codex create a spec using the [usage guide](usage.md),
then call `build_asset`, `verify_revision`, and `create_review`. Open the returned
absolute PNG path with the client's image viewer (such as Codex's `view_image`).
Outputs require new paths. Build/fit results may contain failed technical checks;
inspect the returned report rather than treating a completed call as acceptance.
Human artistic approval is intentionally not exposed as an MCP tool. After actual
visual review, use the CLI's `approve` or `reject` command.

## Paid requests and credentials

`request_image` defaults to an offline dry run. Only set `dry_run=false` when the
user has authorized spending credits. A nonempty `references` list selects the
edit endpoint. Live requests upload the prompt, references and optional mask to
OpenAI. These inputs are also saved in the revision, so do not put secrets in them.
No API calls are required for the other tools.

Credentials use the same environment/user configuration as the CLI; see
[User configuration](usage.md#user-configuration). Do not put keys in Codex config
or tool arguments. A source checkout's ignored `.env` fallback works when launched
from another working directory; wheel installs use environment or user config.
The MCP server never loads credentials for dry runs or offline calls.

Live image calls can take several minutes. The SDK's retry/timeout behavior still
applies; a timed-out request may have incurred charges. Never automatically repeat
it. A client timeout does not guarantee that the server or upstream request stopped.
The 660-second example accommodates the default 180-second SDK timeout and retries.

## Validation

`tests/test_mcp.py` launches the actual stdio server using an MCP client, discovers
its typed tools, and exercises offline processing, revision verification/rebuild,
review PNG output paths, errors and safe dry-run defaults. Paid-error handling is
verified with a fake client; the suite spends no API credits.
