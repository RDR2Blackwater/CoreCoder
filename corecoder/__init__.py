"""CoreCoder - Minimal AI coding agent inspired by Claude Code's architecture."""

__version__ = "0.6.0"

from corecoder.agent import Agent
from corecoder.config import Config
from corecoder.llm import LLM
from corecoder.mcp import load_mcp_tools, shutdown_mcp_clients
from corecoder.tools import ALL_TOOLS

__all__ = [
    "ALL_TOOLS",
    "LLM",
    "Agent",
    "Config",
    "load_mcp_tools",
    "shutdown_mcp_clients",
    "__version__",
]
