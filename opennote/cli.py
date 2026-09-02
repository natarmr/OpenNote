"""OpenNote CLI.

Phase 0: notebook management + ingestion + search. The agent shell and BYOK
auth land in later phases.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

import typer

from opennote import __version__
from opennote.auth.cli import auth_app
from opennote.chat.ask import ask
from opennote.chat.client import ChatError, default_provider, get_client
from opennote.ingest.pipeline import ingest as run_ingest
from opennote.notebooks import Notebook, NotebookManager
from opennote.retrieval.retriever import Retriever, render_results

app = typer.Typer(help="OpenNote - grounded, cited Q&A over your own sources.")
manager = NotebookManager()
app.add_typer(auth_app, name="auth")

@app.callback(invoke_without_command=True)
def main(ctx: typer.Context, light: bool = False):
    """OpenNote - grounded, cited Q&A over your own sources.

    Run bare to launch the terminal UI, or a subcommand (create, ingest,
    search, ask, chat, auth, ...) for the CLI.
    """
    if ctx.invoked_subcommand is None:
        from opennote.tui.app import main as tui_main

        tui_main(light=light)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

# LLM output can contain any Unicode; emit UTF-8 so no character is lost or
# crashes the console (cp1252 defaults on Windows). Unencodable chars, if any,
# are replaced rather than raised.
for _stream in (sys.stdout, sys.stderr, sys.stdin):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def _notebook(name: Optional[str], create_if_missing: bool = False) -> Notebook:
    notebook_name = name or "default"
    try:
        return manager.get(notebook_name)
    except KeyError:
        if create_if_missing:
            typer.echo(f"Creating notebook '{notebook_name}'...")
            return manager.create(notebook_name)
        raise typer.BadParameter(
            f"Notebook '{notebook_name}' does not exist. "
            f"Run 'opennote create {notebook_name}' first."
        )
    except ValueError as e:
        raise typer.BadParameter(str(e))


@app.command("create")
def create(
    name: str = typer.Argument(..., help="Name of the notebook to create."),
    model: str = typer.Option(
        "BAAI/bge-small-en-v1.5", "--model", "-m", help="Embedding model for this notebook."
    ),
):
    """Create a new notebook."""
    try:
        manager.create(name, embed_model=model)
    except (FileExistsError, ValueError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Created notebook '{name}' (model: {model}).")


@app.command("list")
def list_notebooks():
    """List all notebooks."""
    notebooks = manager.list()
    if not notebooks:
        typer.echo("No notebooks found. Run 'opennote create <name>'.")
        return
    for nb in notebooks:
        n = len(nb.sources)
        typer.echo(f"  {nb.name:<20} model={nb.embed_model:<30} sources={n}")


@app.command("delete")
def delete(name: str = typer.Argument(..., help="Name of the notebook to delete.")):
    """Delete a notebook and its data."""
    try:
        manager.delete(name)
    except (KeyError, ValueError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Deleted notebook '{name}'.")


@app.command("rename")
def rename(
    old: str = typer.Argument(..., help="Current notebook name."),
    new: str = typer.Argument(..., help="New notebook name."),
):
    """Rename a notebook."""
    try:
        manager.rename(old, new)
    except (KeyError, FileExistsError, ValueError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Renamed '{old}' to '{new}'.")


@app.command("ingest")
def ingest(
    target: str = typer.Argument(
        ".", help="A source file, directory, or http(s) URL to ingest (default: current dir)."
    ),
    notebook: Optional[str] = typer.Option(
        None, "--notebook", "-n", help="Notebook to ingest into (default: 'default')."
    ),
    parser: str = typer.Option(
        "auto", "--parser", help="PDF parser: auto, docling, or fallback."
    ),
    ocr: bool = typer.Option(False, "--ocr", help="Enable OCR in Docling."),
    chunk_size: int = typer.Option(800, "--chunk-size", help="Target chunk size (chars)."),
    chunk_overlap: int = typer.Option(
        120, "--chunk-overlap", help="Overlap between adjacent chunks."
    ),
    batch_size: int = typer.Option(32, "--batch-size", help="Embedding batch size."),
    device: Optional[str] = typer.Option(
        None, "--device", help="Device for embeddings: cpu, cuda, mps (default: auto)."
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Force re-ingest even if content hash is unchanged."
    ),
):
    """Ingest sources into a notebook (txt/md/docx/html/pdf, or a URL)."""
    nb = _notebook(notebook, create_if_missing=True)
    typer.echo(f"Ingesting into notebook '{nb.name}' from '{target}'...")
    try:
        count = run_ingest(
            nb,
            target,
            parser=parser,
            ocr=ocr,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            batch_size=batch_size,
            device=device,
            force=force,
        )
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Indexed {count} chunk(s).")


@app.command("search")
def search(
    query: str = typer.Argument(..., help="The query to search for."),
    notebook: Optional[str] = typer.Option(
        None, "--notebook", "-n", help="Notebook to search (default: 'default')."
    ),
    top_k: int = typer.Option(3, "--top-k", "-k", help="Number of results to return."),
    source: Optional[str] = typer.Option(
        None, "--source", "-s", help="Restrict results to a source filename."
    ),
):
    """Retrieve (LLM-free) and cite the top chunks for a query."""
    nb = _notebook(notebook)
    try:
        retriever = Retriever(nb, top_k=top_k)
        results = retriever.search(query, source=source)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(render_results(results))


@app.command("golden")
def golden(
    golden_file: Path = typer.Argument(
        ..., help="Path to a golden set (TSV): query, source, optional pages."
    ),
    notebook: Optional[str] = typer.Option(
        None, "--notebook", "-n", help="Notebook to evaluate (default: 'default')."
    ),
    top_k: int = typer.Option(5, "--top-k", "-k", help="Recall@k to measure."),
):
    """Evaluate retrieval recall@k against a golden set."""
    from opennote.retrieval.eval import evaluate, load_golden

    nb = _notebook(notebook)
    try:
        retriever = Retriever(nb, top_k=top_k)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    golden_queries = load_golden(golden_file)
    if not golden_queries:
        typer.echo(f"No queries found in '{golden_file}'.", err=True)
        raise typer.Exit(1)
    summary = evaluate(retriever, golden_queries, top_k=top_k)
    typer.echo(f"\nRecall@{top_k}: {summary.recall_at_k:.2f} "
               f"({summary.total} queries)\n")
    for qr in summary.per_query:
        mark = "HIT " if qr.hit_source else "MISS"
        expected = qr.golden.expected_source
        top = ", ".join(s or "?" for s in qr.top_sources[:top_k]) or "none"
        typer.echo(f"  [{mark}] expected={expected}")
        typer.echo(f"          top={top}")


@app.command("ask")
def ask_cmd(
    question: str = typer.Argument(..., help="The question to ask your sources."),
    notebook: Optional[str] = typer.Option(
        None, "--notebook", "-n", help="Notebook to ask over (default: 'default')."
    ),
    provider: Optional[str] = typer.Option(
        None, "--provider", "-p", help="LLM provider id (default: first configured)."
    ),
    top_k: int = typer.Option(5, "--top-k", "-k", help="Context chunks to retrieve."),
):
    """Grounded, cited Q&A over a notebook's sources."""
    nb = _notebook(notebook)
    try:
        result = ask(nb, question, provider_id=provider, top_k=top_k)
    except (ChatError, ValueError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"\n--- Answer (via {result.provider_id}:{result.model}) ---\n")
    typer.echo(result.answer)
    typer.echo()


@app.command("chat")
def chat_cmd(
    notebook: Optional[str] = typer.Option(
        None, "--notebook", "-n", help="Notebook to chat over (default: 'default')."
    ),
    provider: Optional[str] = typer.Option(
        None, "--provider", "-p", help="LLM provider id (default: first configured)."
    ),
    new_session: bool = typer.Option(
        False, "--new", help="Start a fresh session (do not resume)."
    ),
    resume: Optional[str] = typer.Option(
        None, "--resume", help="Resume a specific session id (default: most recent)."
    ),
):
    """Interactive, multi-turn grounded Q&A over a notebook.

    The model drives retrieval: it decides when to search and can search several
    times before answering. Sessions are persisted per notebook and resume
    automatically. Slash commands: /help /exit /new /sessions /sources /model <id>
    """
    from opennote.agents.loop import agent_turn
    from opennote.agents.session import (
        append_messages,
        list_sessions,
        load_session,
        new_session as create_new_session,
        save_session,
    )

    nb = _notebook(notebook)

    try:
        pid = provider or default_provider()
        client = get_client(pid)
    except (ChatError, ValueError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    # --- session setup ---------------------------------------------------
    if resume:
        session = load_session(nb, resume)
        if session is None:
            typer.echo(f"Error: session '{resume}' not found.", err=True)
            raise typer.Exit(1)
    elif not new_session:
        sessions = list_sessions(nb)
        session = sessions[0] if sessions else None
    else:
        session = None

    if session is None:
        session = create_new_session(nb, client.provider_id, client.model)
        typer.echo(f"Started new session {session['id'][:8]}…")
    else:
        typer.echo(
            f"Resumed session {session['id'][:8]}… "
            f"({len(session['messages'])} messages)"
        )

    typer.echo(f"OpenNote chat over notebook '{nb.name}' — provider: {pid}")
    typer.echo("Slash commands: /help /exit /new /sessions /sources /model <id>")
    typer.echo("=" * 60)

    while True:
        try:
            user_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt, UnicodeDecodeError):
            typer.echo("\nGoodbye!")
            break

        if not user_input:
            continue

        # --- slash commands ----------------------------------------------
        if user_input.startswith("/"):
            parts = re.split(r"\s+", user_input.strip(), maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""

            if cmd == "/help":
                typer.echo(
                    "  /help      show this help\n"
                    "  /exit      end the session\n"
                    "  /new       start a fresh session\n"
                    "  /sessions  list saved sessions\n"
                    "  /sources   list notebook sources\n"
                    "  /model ID  switch LLM provider (e.g. /model groq)"
                )
            elif cmd == "/exit":
                typer.echo("Goodbye!")
                break
            elif cmd == "/new":
                session = create_new_session(nb, client.provider_id, client.model)
                typer.echo(f"Started new session {session['id'][:8]}…")
            elif cmd == "/sessions":
                from opennote.agents.session import list_session_meta

                for s in list_session_meta(nb):
                    marker = "*" if s["id"] == session["id"] else " "
                    typer.echo(
                        f"  {marker} {s['id'][:8]}… model={s.get('model')} "
                        f"msgs={s.get('msg_count', 0)} updated={s.get('updated', '')[:19]}"
                    )
            elif cmd == "/sources":
                try:
                    retriever = Retriever(nb)
                except ValueError as e:
                    typer.echo(f"Error: {e}", err=True)
                    continue
                for src in retriever.sources():
                    typer.echo(f"  {src}")
            elif cmd == "/model":
                if not arg:
                    typer.echo("Usage: /model <provider-id>")
                    continue
                try:
                    client = get_client(arg)
                    pid = client.provider_id
                    session["provider_id"] = client.provider_id
                    session["model"] = client.model
                    save_session(nb, session)
                    typer.echo(f"Provider switched to {pid} ({client.model}).")
                except (ChatError, ValueError) as e:
                    typer.echo(f"Error: {e}", err=True)
            else:
                typer.echo("Unknown command. Type /help for available commands.")
            continue

        # --- regular question ---------------------------------------------
        typer.echo(f"\n({pid}) Question: {user_input}")
        try:
            agent = agent_turn(
                nb,
                user_input,
                provider_id=pid,
                history=session["messages"],
                client=client,
            )
        except Exception as e:
            typer.echo(f"Error: {e}", err=True)
            continue

        session = append_messages(nb, session["id"], agent.messages)
        result = agent.result
        typer.echo(f"\n--- Answer (via {result.provider_id}:{result.model}) ---\n")
        typer.echo(result.answer)
        typer.echo()


@app.command("local")
def local_cmd(
    command: str = typer.Argument(..., help="Command: add, list, use, remove"),
    path: Optional[str] = typer.Argument(None, help="Path to GGUF model file (for `add`)"),
    name: Optional[str] = typer.Argument(None, help="Model name (for `add`, auto-derived if omitted)"),
    n_ctx: int = typer.Option(4096, "--n-ctx", help="Context window size"),
    threads: Optional[int] = typer.Option(None, "--threads", help="Number of CPU threads"),
) -> None:
    """Manage local GGUF models.

    ``opennote local add <path>`` registers a model file.
    ``opennote local list`` shows registered models.
    ``opennote local use <name>`` activates a model.
    ``opennote local remove <name>`` unregisters a model.
    """
    from opennote.auth.local import add_model, list_models, remove_model, set_active, get_active, validate_name

    home = None  # will use OPENNOTE_HOME or ~/.opennote

    if command == "add":
        if not path:
            typer.echo("Usage: opennote local add <path-to-gguf> [name] [--n-ctx N] [--threads N]", err=True)
            raise typer.Exit(1)
        if name is None:
            name = os.path.splitext(os.path.basename(path))[0]
        if not validate_name(name):
            typer.echo(f"Invalid model name '{name}'. Use only letters, digits, dash, dot, underscore.", err=True)
            raise typer.Exit(1)
        try:
            add_model(home, name, path, n_ctx=n_ctx, threads=threads)
            typer.echo(f"Model '{name}' registered.")
        except (FileNotFoundError, ValueError) as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1)

    elif command == "list":
        models = list_models(home)
        if not models:
            typer.echo("No local models registered.")
            return
        active = get_active(home)
        active_name = active["name"] if active else None
        for m in models:
            marker = " *" if m["name"] == active_name else ""
            typer.echo(
                f"{marker} {m['name']}: {m['path']}  (n_ctx={m['n_ctx']}, threads={m.get('threads', 'auto')})"
            )

    elif command == "use":
        if not name:
            typer.echo("Usage: opennote local use <name>", err=True)
            raise typer.Exit(1)
        try:
            set_active(home, name)
            typer.echo(f"Model '{name}' is now active.")
        except (KeyError, ValueError) as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1)

    elif command == "remove":
        if not name:
            typer.echo("Usage: opennote local remove <name>", err=True)
            raise typer.Exit(1)
        try:
            remove_model(home, name)
            typer.echo(f"Model '{name}' unregistered.")
        except KeyError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1)


@app.command("version")
def version():
    """Print the version."""
    typer.echo(f"opennote {__version__}")


if __name__ == "__main__":
    app()