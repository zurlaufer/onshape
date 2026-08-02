"""FastMCP server exposing FRC-specific tools to Claude Code.

Naming hazard: this package is called `mcp`, the same name as the pip package
that FastMCP depends on. It only shadows the real one if `src/` itself ends up
on `sys.path`. Always launch the server as a module from the project root:

    python -m src.mcp.server

Running `python src/mcp/server.py` puts `src/mcp/` on the path instead, which
is harmless — but adding `src/` to `sys.path` anywhere would break `import mcp`
in confusing ways. If that ever bites, rename this package to `mcp_server`.
"""
