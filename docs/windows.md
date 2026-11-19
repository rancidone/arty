# Windows setup and verification

## Install without activation or administrator access

Use Git, 64-bit Python 3.11–3.14 and a writable local folder. In PowerShell:

```powershell
git clone https://github.com/rancidone/arty.git
cd arty
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install ".[dev,mcp]"
.\.venv\Scripts\arty.exe convert examples/slime-enlarged.png -o work/slime.png --size 96x64 --columns 3 --rows 2 --preview work/preview.png
.\.venv\Scripts\python.exe -m pytest -q
```

Direct executable invocation avoids PowerShell execution-policy changes. For a
specific Python version use `py -3.11` (or another supported version). No API key
is needed for offline examples or tests. JSON uses `/` in relative paths, or escaped
backslashes (`"C:\\Art\\source.png"`) for absolute paths.

Production commands reject device filenames, trailing periods/spaces, and case
collisions on every platform. Input filenames may contain spaces and Unicode.
Long snapshot paths are tested; use a short checkout/output base if your Windows
installation has legacy path limits or long paths are disabled. An inaccessible
path returns an error rather than silently changing its name.

## Filesystem guarantees

NTFS is the primary Windows target. New files are staged in the destination's
folder and published without overwriting existing files. If hard links are
unavailable, Windows uses its non-replacing rename operation; it does not fall
back to a partially visible copy. Existing files, concurrent writers and locked
outputs are preserved. Revisions use exclusive lock files with closed handles
and publish complete staged directories. A process kill can leave a stale lock;
confirm no writer is running before removing it.

Symlinks are not needed for normal operation. The symlink-specific test skips
only when Windows reports missing symlink privilege; other errors fail the test.
Windows CI exercises locked-file behavior, native rename publication without
hard links, absolute drive paths, escaped backslashes, Unicode/spaced/deep paths,
case collisions, package installation and the actual MCP stdio protocol.

## MCP and configuration

Register the installed server using an absolute executable path:

```powershell
codex mcp add arty -- "$((Get-Location).Path)\.venv\Scripts\arty-mcp.exe" --workspace "$((Get-Location).Path)"
```

The workspace is a path base, not a sandbox. After installation, restart the MCP
connection and call `analyze_image` on an offline fixture from another working
directory. The protocol tests perform this with a real stdio client. Validate the
same workflow in your intended desktop client before advertising that integration.

For paid requests set `$env:OPENAI_API_KEY` in the launching environment or use
`$HOME\.config\arty\.env`. `XDG_CONFIG_HOME`, when set, overrides that config
base. Keys should never be stored in client arguments, specs or the repository.
Offline calls and dry runs do not load credentials.

## Release checks

The CI matrix runs Python 3.11–3.14 on `windows-latest`, including isolated wheel
and source installation, all tests, MCP discovery and offline examples. Native
Windows-only tests cannot be validated by a passing macOS run. Require all Windows
jobs to pass before claiming Windows support. Also exercise the quickstart as a
standard Windows 11 user without Developer Mode. Windows ARM, network shares,
external-drive filesystems and desktop-client-specific process launching remain
unverified until separately exercised.
