"""Run Casefile's read-only MCP server over stdio.

    uv run python -m casefile.mcp_server --user admin@lumora.test

Claude Code:
    claude mcp add casefile -- uv --directory <repo>/backend run python -m casefile.mcp_server \
        --user <email>
Claude Desktop: the same command in claude_desktop_config.json under "mcpServers".
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import structlog

from casefile.config import get_settings
from casefile.db.session import get_sessionmaker
from casefile.mcp_server.server import create_server


def main() -> None:
    # stdout carries the MCP protocol: every log line must go to stderr.
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    parser = argparse.ArgumentParser(description="Casefile read-only MCP server (stdio)")
    parser.add_argument("--user", default=os.environ.get("CASEFILE_MCP_USER", ""), help="Casefile user email")
    args = parser.parse_args()
    if not args.user:
        parser.error("--user (or CASEFILE_MCP_USER) is required: the server acts with that user's visibility")
    settings = get_settings()
    create_server(get_sessionmaker(), args.user, base_url=settings.app_base_url).run("stdio")


if __name__ == "__main__":
    main()
