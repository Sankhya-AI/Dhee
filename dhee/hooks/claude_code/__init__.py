from dhee.hooks.claude_code.assembler import AssembledContext, DocMatch, assemble, assemble_docs_only
from dhee.hooks.claude_code.chunker import Chunk, chunk_markdown
from dhee.hooks.claude_code.ingest import auto_ingest_project, ingest_file
from dhee.hooks.claude_code.install import ensure_installed, install_hooks, uninstall_hooks
from dhee.hooks.claude_code.renderer import estimate_tokens, render_context
from dhee.hooks.claude_code.signal import extract_signal, has_cognition_signal
