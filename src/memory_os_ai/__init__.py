"""Memory OS AI — Adaptive memory system for AI agents.

Universal MCP server for persistent semantic memory. Works with
Claude Code, Codex CLI, VS Code Copilot, ChatGPT, and any MCP-compatible client.
Document ingestion, FAISS search, chat extraction, cross-project linking.
"""

__version__ = "4.0.6.dev0"

__all__ = [
    "__version__",
    "MemoryEngine",
    "ChatExtractor",
    "StorageRouter",
    "TOOL_MODELS",
    "MEMORY_INSTRUCTIONS",
]

def __getattr__(name):
    # Reading the package/version does not require optional v3 document libraries.
    from importlib import import_module
    modules = {"MemoryEngine": "engine", "ChatExtractor": "chat_extractor",
               "StorageRouter": "storage_router", "TOOL_MODELS": "models",
               "MEMORY_INSTRUCTIONS": "instructions"}
    if name not in modules:
        raise AttributeError(name)
    value = getattr(import_module(f"memory_os_ai.{modules[name]}"), name)
    globals()[name] = value
    return value
