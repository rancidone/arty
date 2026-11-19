# Security reporting

For a suspected vulnerability, use the repository's private
[Report a vulnerability](https://github.com/rancidone/arty/security/advisories/new)
form when available. If private reporting is unavailable, open an issue asking for
a private contact channel without including exploit details, credentials or
private artwork. No response-time or security-support guarantee is currently made.

Arty is a trusted local filesystem tool, including its stdio MCP server. A
workspace selects a path base; it does not restrict filesystem access. Only run
trusted specs and connect trusted clients. Offline commands do not call OpenAI;
live generation/edit calls upload prompts and supplied images and may cost money.
Saved revisions contain those inputs. Human decision records establish local
provenance, not authenticated signatures or protection against a local writer.

Use the latest revision and Python dependencies. Version 0.1 is alpha; report bugs
against the current source. Resource limits reject oversized images and excessive
palettes, but do not impose a global process memory or execution-time quota. Run
untrusted inputs in a separate operating-system sandbox if isolation is required.
