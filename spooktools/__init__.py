"""ComfyUI-SpookTools: server tools for ComfyUI from the browser.

Server-side package. ``routes.register_routes`` wires every feature's HTTP routes onto an aiohttp application;
the repo-root ``__init__.py`` calls it on ComfyUI's PromptServer.
"""
