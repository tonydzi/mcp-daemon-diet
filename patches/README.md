# When the server is stdio-only: patching in a transport switch

Check first - this is often unnecessary. Run the server with `--help`, and grep its source
for `transport`, `sse`, `streamable`, `http`. Most maintained MCP servers already expose
the choice; the framework underneath (FastMCP and its ports) has supported HTTP transports
for a long time, and many servers simply never surfaced the option.

## The shape of the patch

If the server is built on FastMCP, the entire change is usually the final call. Before:

```python
mcp.run()                     # stdio, always
```

After:

```python
import os

transport = os.environ.get("MCP_TRANSPORT", "stdio")     # default unchanged
if transport in ("sse", "streamable-http"):
    mcp.run(transport=transport,
            host=os.environ.get("MCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("MCP_PORT", "8765")))
else:
    mcp.run()
```

Three rules that make such a patch acceptable upstream, and safe to carry locally:

1. **Default to the old behaviour.** With no env set, nothing changes for anyone else.
2. **Default the host to `127.0.0.1`, not `0.0.0.0`.** A transport patch that publishes
   someone's account to their LAN is a vulnerability, not a feature.
3. **Change one thing.** A transport patch that also adds tools cannot be reviewed quickly,
   and quick review is how it gets merged.

Node servers look the same: whatever the SDK's `StdioServerTransport` equivalent is, the
HTTP one sits next to it in the same module.

## Carrying a local patch

Pin the upstream commit you patched against and record it, so the patch either applies or
fails loudly instead of half-applying to a moved file:

```
git -C <server> rev-parse --short HEAD          # write this down
git -C <server> diff > 0001-transport-env.patch
git -C <server> apply --check 0001-transport-env.patch   # must pass on a FRESH clone
```

Generate the patch with a plain shell redirect. PowerShell's `Out-File` / `>` adds a BOM
and CRLF, and `git apply` rejects the result - an hour we have paid more than once. If you
commit patches to a repo, add `*.patch -text` to `.gitattributes` so a Windows clone does
not get them rewritten to CRLF on checkout.

**A pull will revert your patch.** The daemon then falls back to stdio and nothing listens
on the port, silently (GOTCHAS #11). Re-apply after every upstream pull - or spend the ten
minutes to send it upstream, which is what happened to ours: the server we originally
patched now ships env-selectable transport natively, and the patch is dead weight. That is
the good ending.

## Worked example

Our published one, pinned and ready to apply, is in the sibling kit:
[telegram-mcp-kit/patches](https://github.com/tonydzi/telegram-mcp-kit/tree/main/patches)
- `0001-transport-sse-env.patch`, against upstream `chigwell/telegram-mcp` at commit
`a008ac2`. It is not copied here on purpose: one source, and that one is kept current.
