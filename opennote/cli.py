"""OpenNote CLI."""

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
from opennote.notebooks import Notebook, NotebookManager, current_project
from opennote.retrieval.retriever import Retriever, render_results
from opennote.transcript import load_transcript

app = typer.Typer(help="OpenNote - grounded, cited Q&A over your own sources.")
manager = NotebookManager()
app.add_typer(auth_app, name="auth")

@app.callback(invoke_without_command=True)
def main(ctx: typer.Context, light: bool = False):
    if ctx.invoked_subcommand is None:
        from opennote.tui.app import main as tui_main

        tui_main(light=light)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

for _stream in (sys.stdout, sys.stderr, sys.stdin):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def _notebook(name: Optional[str], create_if_missing: bool = False) -> Notebook:
    # Explicit name
    if name:
        try:
            return manager.get(name)
        except KeyError:
            if create_if_missing:
                typer.echo(f"Creating notebook '{name}'...")
                return manager.create(name, project=current_project())
            raise typer.BadParameter(
                f"Notebook '{name}' does not exist. Run 'opennote create {name}' first."
            )
        except ValueError as e:
            raise typer.BadParameter(str(e))
    # No name: most recent for this directory
    notebooks = manager.list_for_project(current_project())
    if notebooks:
        return notebooks[0]
    if create_if_missing:
        auto = manager.next_notebook_name(current_project())
        typer.echo(f"Creating notebook '{auto}'...")
        return manager.create(auto, project=current_project())
    raise typer.BadParameter(
        "No notebooks for this directory. Run 'opennote create <name>' or 'opennote ingest <path>'."
    )


@app.command("create")
def create(
    name: str = typer.Argument(..., help="Name of the notebook to create."),
    model: str = typer.Option(
        "BAAI/bge-small-en-v1.5", "--model", "-m", help="Embedding model for this notebook."
    ),
):
    """Create a new notebook."""
    try:
        manager.create(name, embed_model=model, project=current_project())
    except (FileExistsError, ValueError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Created notebook '{name}' (model: {model}).")


@app.command("list")
def list_notebooks(
    all: bool = typer.Option(False, "--all", help="List all notebooks (all directories)."),
):
    """List notebooks."""
    notebooks = manager.list() if all else manager.list_for_project(current_project())
    if not notebooks:
        typer.echo("No notebooks found. Run 'opennote create <name>'.")
        return
    for nb in notebooks:
        n = len(nb.sources)
        proj = nb.project or "(legacy)"
        typer.echo(f"  {nb.name:<20} model={nb.embed_model:<30} sources={n}/5 project={proj}")


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


@app.command("remove")
def remove_source_cmd(
    source: str = typer.Argument(..., help="Source path substring to remove."),
    notebook: Optional[str] = typer.Option(None, "--notebook", "-n", help="Notebook (default: most recent in this dir)."),
):
    """Remove a source from a notebook (frees a slot)."""
    nb = _notebook(notebook)
    # Resolve source: exact match first, then substring
    if source in nb.sources:
        target = source
    else:
        matches = [s for s in nb.sources if source in s]
        if not matches:
            typer.echo(f"Error: no source matching '{source}'.", err=True)
            raise typer.Exit(1)
        if len(matches) > 1:
            typer.echo(f"Error: multiple sources match '{source}':", err=True)
            for m in matches:
                typer.echo(f"  {m}", err=True)
            raise typer.Exit(1)
        target = matches[0]
    from opennote.ingest.pipeline import remove_source

    try:
        remove_source(nb, target)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Removed source '{target}' from notebook '{nb.name}'.")


@app.command("ingest")
def ingest(
    target: str = typer.Argument(
        ".", help="A source file, directory, or http(s) URL to ingest (default: current dir)."
    ),
    notebook: Optional[str] = typer.Option(
        None, "--notebook", "-n", help="Notebook to ingest into (default: most recent in this dir, or auto-create)."
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
        None, "--notebook", "-n", help="Notebook to search (default: most recent in this dir)."
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
        None, "--notebook", "-n", help="Notebook to evaluate (default: most recent in this dir)."
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
        None, "--notebook", "-n", help="Notebook to ask over (default: most recent in this dir)."
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
        None, "--notebook", "-n", help="Notebook to chat over (default: new notebook in this dir)."
    ),
    provider: Optional[str] = typer.Option(
        None, "--provider", "-p", help="LLM provider id (default: first configured)."
    ),
    cont: bool = typer.Option(
        False, "--continue", "--cont", help="Continue the most recent notebook in this directory."
    ),
):
    """Interactive, multi-turn grounded Q&A over a notebook."""
    from opennote.agents.loop import agent_turn
    from opennote.transcript import append_messages

    # Resolve notebook: explicit -n, --continue (most recent), or auto-create new
    if notebook:
        nb = _notebook(notebook)
    elif cont:
        nb = _notebook(None)
    else:
        # Bare chat: create a fresh notebook in this directory
        auto = manager.next_notebook_name(current_project())
        nb = manager.create(auto, project=current_project())
        typer.echo(f"Started notebook '{nb.name}'.")

    try:
        pid = provider or default_provider()
        client = get_client(pid)
    except (ChatError, ValueError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    history = load_transcript(nb)
    if history:
        typer.echo(f"Notebook '{nb.name}' — {len(history)} messages of history loaded.")
    else:
        typer.echo(f"Notebook '{nb.name}' — fresh.")

    typer.echo(f"OpenNote chat over notebook '{nb.name}' — provider: {pid}")
    typer.echo("Slash commands: /help /exit /clear /sources /remove /model <id>")
    typer.echo("=" * 60)

    while True:
        try:
            user_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt, UnicodeDecodeError):
            typer.echo("\nGoodbye!")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            parts = re.split(r"\s+", user_input.strip(), maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""

            if cmd == "/help":
                typer.echo(
                    "  /help                  show this help\n"
                    "  /exit                  end\n"
                    "  /clear                 clear transcript\n"
                    "  /sources               list notebook sources\n"
                    "  /remove                remove a source\n"
                    "  /model ID              switch LLM provider (e.g. /model groq)\n"
                    "  /skills                list installed skills\n"
                    "  /skill NAME            show a skill\n"
                    "  /plugins               list loaded plugins\n"
                    "  /agents                list available agents\n"
                    "  /agent NAME            show an agent\n"
                    "  /capabilities          show runtime capabilities"
                )
            elif cmd == "/exit":
                typer.echo("Goodbye!")
                break
            elif cmd == "/clear":
                from opennote.transcript import clear_transcript

                clear_transcript(nb)
                history = []
                typer.echo("Transcript cleared.")
            elif cmd == "/sources":
                try:
                    retriever = Retriever(nb)
                except ValueError as e:
                    typer.echo(f"Error: {e}", err=True)
                    continue
                for src in retriever.sources():
                    typer.echo(f"  {src}")
            elif cmd == "/remove":
                if not arg:
                    typer.echo("Usage: /remove <source-substring>")
                    continue
                from opennote.ingest.pipeline import remove_source

                matches = [s for s in nb.sources if arg in s]
                if not matches:
                    typer.echo(f"No source matching '{arg}'.")
                    continue
                if len(matches) > 1:
                    typer.echo("Multiple matches:")
                    for m in matches:
                        typer.echo(f"  {m}")
                    continue
                try:
                    remove_source(nb, matches[0])
                    typer.echo(f"Removed {matches[0]}")
                except Exception as e:
                    typer.echo(f"Error: {e}", err=True)
            elif cmd == "/model":
                if not arg:
                    typer.echo("Usage: /model <provider-id>")
                    continue
                try:
                    client = get_client(arg)
                    pid = client.provider_id
                    nb.provider_id = client.provider_id
                    nb.model = client.model
                    nb.save()
                    typer.echo(f"Provider switched to {pid} ({client.model}).")
                except (ChatError, ValueError) as e:
                    typer.echo(f"Error: {e}", err=True)
            elif cmd == "/skills":
                from opennote.skills.registry import SkillRegistry
                reg = SkillRegistry.discover()
                skills = reg.list()
                if not skills:
                    typer.echo("No skills installed. Install: npx skills add <owner/repo> -a codex")
                else:
                    for s in skills:
                        typer.echo(f"  {s.name:<25} {s.description[:80]}")
            elif cmd == "/skill":
                if not arg:
                    typer.echo("Usage: /skill <name>")
                    continue
                from opennote.skills.registry import SkillRegistry as SR2
                reg2 = SR2.discover()
                sk = reg2.get(arg)
                if sk is None:
                    typer.echo(f"Skill '{arg}' not found.")
                    continue
                typer.echo(f"Skill: {sk.name}\nDescription: {sk.description}\nDir: {sk.directory}\n")
                typer.echo(sk.body[:3000])
            elif cmd == "/plugins":
                from opennote.capabilities import get_capabilities as _gc
                from opennote.plugins.loader import PluginContext as _PC, PluginLoader as _PL
                caps2 = _gc()
                loader2 = _PL(_PC(capabilities=caps2, notebook=nb))
                loader2.load()
                if not loader2.hooks and not loader2.tools:
                    typer.echo("No plugins loaded.")
                else:
                    for h in loader2.hooks:
                        typer.echo(f"  {h._name}: {list(h.tools.keys())}")
            elif cmd == "/agents":
                from opennote.agents.defs import AgentRegistry as AR2
                reg3 = AR2.discover()
                for a in reg3.list():
                    typer.echo(f"  {a.name:<15} [{a.mode}] {a.description[:70]}")
            elif cmd == "/agent":
                if not arg:
                    typer.echo("Usage: /agent <name>")
                    continue
                from opennote.agents.defs import AgentRegistry as AR3
                reg4 = AR3.discover()
                ag = reg4.get(arg)
                if ag is None:
                    typer.echo(f"Agent '{arg}' not found.")
                    continue
                typer.echo(f"Agent: {ag.name} [{ag.mode}] {ag.description}\n")
                typer.echo(ag.prompt[:3000] if ag.prompt else "(no prompt)")
            elif cmd == "/capabilities":
                from opennote.capabilities import get_capabilities as _gc2
                c = _gc2()
                typer.echo(f"web_search: {c.web_search}  supermemory: {getattr(c, 'supermemory_available', False)}")
                typer.echo(f"skills: {getattr(c, 'skills_available', False)} ({getattr(c, 'skills_count', 0)})")
                typer.echo(f"plugins: {getattr(c, 'plugins_loaded', [])}")
                typer.echo(f"agents: {getattr(c, 'agents_available', [])}")
            else:
                typer.echo("Unknown command. Type /help for available commands.")
            continue

        typer.echo(f"\n({pid}) Question: {user_input}")
        try:
            agent = agent_turn(
                nb,
                user_input,
                provider_id=pid,
                history=history,
                client=client,
            )
        except Exception as e:
            typer.echo(f"Error: {e}", err=True)
            continue

        history = append_messages(nb, agent.messages)
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
    from opennote.auth.local import add_model, list_models, remove_model, set_active, get_active, validate_name

    home = None

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


@app.command("skills")
def skills_cmd(
    action: str = typer.Argument("list", help="Action: list, show"),
    name: Optional[str] = typer.Argument(None, help="Skill name (for 'show')."),
):
    """List or inspect installed agent skills (SKILL.md)."""
    from opennote.skills.registry import SkillRegistry

    if action == "list":
        reg = SkillRegistry.discover()
        skills = reg.list()
        if not skills:
            typer.echo("No skills installed.")
            typer.echo("Install via: npx skills add <owner/repo> -a codex   (lands in .agents/skills/)")
            typer.echo("Scanned: ./skills/, ./.agents/skills/, ./.claude/skills/, ./.opennote/skills/, ~/.agents/skills/, ~/.claude/skills/, ~/.opennote/skills/")
            return
        for s in skills:
            typer.echo(f"  {s.name:<25} {s.description[:80]}  ({s.directory})")
    elif action == "show":
        if not name:
            typer.echo("Usage: opennote skills show <name>", err=True)
            raise typer.Exit(1)
        reg = SkillRegistry.discover()
        skill = reg.get(name)
        if skill is None:
            typer.echo(f"Skill '{name}' not found. Available: {', '.join(reg.names()) or 'none'}", err=True)
            raise typer.Exit(1)
        typer.echo(f"Name: {skill.name}")
        typer.echo(f"Description: {skill.description}")
        typer.echo(f"Directory: {skill.directory}")
        if skill.frontmatter.get("license"):
            typer.echo(f"License: {skill.frontmatter['license']}")
        if skill.frontmatter.get("compatibility"):
            typer.echo(f"Compatibility: {skill.frontmatter['compatibility']}")
        typer.echo("")
        typer.echo(skill.body[:4000])
        if skill.files:
            typer.echo("\nBundled files:")
            for f in skill.files[:30]:
                typer.echo(f"  {f}")
    else:
        typer.echo(f"Unknown action '{action}'. Use: list, show", err=True)
        raise typer.Exit(1)


@app.command("plugins")
def plugins_cmd(
    action: str = typer.Argument("list", help="Action: list"),
):
    """List installed plugins."""
    from opennote.capabilities import get_capabilities
    from opennote.plugins.loader import PluginContext, PluginLoader

    if action == "list":
        caps = get_capabilities()
        # Trigger loader to populate
        loader = PluginLoader(PluginContext(capabilities=caps))
        loader.load()
        if not loader.hooks and not loader.tools:
            typer.echo("No plugins loaded.")
            if not caps.supermemory_available:
                typer.echo("(tip: set SUPERMEMORY_API_KEY to enable the built-in supermemory plugin)")
            typer.echo("Place Python plugins in .opennote/plugins/*.py or ~/.opennote/plugins/*.py")
            return
        for h in loader.hooks:
            tools_list = ", ".join(h.tools.keys()) if h.tools else "(no tools)"
            typer.echo(f"  {h._name}: tools=[{tools_list}]")
        if not loader.hooks:
            for tname in loader.tools:
                typer.echo(f"  tool: {tname}")
    else:
        typer.echo(f"Unknown action '{action}'. Use: list", err=True)
        raise typer.Exit(1)


@app.command("agents")
def agents_cmd(
    action: str = typer.Argument("list", help="Action: list, show"),
    name: Optional[str] = typer.Argument(None, help="Agent name (for 'show')."),
):
    """List or inspect agent definitions."""
    from opennote.agents.defs import AgentRegistry

    if action == "list":
        reg = AgentRegistry.discover()
        agents = reg.list()
        for a in agents:
            mode = a.mode or "all"
            model = a.model or "-"
            hidden = " (hidden)" if a.hidden else ""
            typer.echo(f"  {a.name:<15} [{mode:<8}] {a.description[:70]}{hidden}")
            if a.model:
                typer.echo(f"      model={model} temp={a.temperature} src={a.source_path or 'builtin'}")
    elif action == "show":
        if not name:
            typer.echo("Usage: opennote agents show <name>", err=True)
            raise typer.Exit(1)
        from opennote.agents.defs import AgentRegistry as AR
        reg = AR.discover()
        agent = reg.get(name)
        if agent is None:
            typer.echo(f"Agent '{name}' not found. Available: {', '.join(reg.names())}", err=True)
            raise typer.Exit(1)
        typer.echo(f"Name: {agent.name}")
        typer.echo(f"Description: {agent.description}")
        typer.echo(f"Mode: {agent.mode}")
        if agent.model:
            typer.echo(f"Model: {agent.model}")
        if agent.temperature is not None:
            typer.echo(f"Temperature: {agent.temperature}")
        if agent.permission:
            typer.echo(f"Permission: {agent.permission}")
        if agent.source_path:
            typer.echo(f"Source: {agent.source_path}")
        typer.echo("")
        typer.echo(agent.prompt[:4000] if agent.prompt else "(no prompt body)")
    else:
        typer.echo(f"Unknown action '{action}'. Use: list, show", err=True)
        raise typer.Exit(1)


@app.command("capabilities")
def capabilities_cmd():
    """Show runtime capabilities (probes for web search, TTS, skills, plugins, etc.)."""
    from opennote.capabilities import get_capabilities

    caps = get_capabilities()
    typer.echo(f"web_search: {caps.web_search}")
    typer.echo(f"supermemory: {getattr(caps, 'supermemory_available', False)}")
    typer.echo(f"tts_backend: {caps.tts_backend}")
    typer.echo(f"tts_available: {caps.tts_available}")
    typer.echo(f"video_available: {caps.video_available}")
    typer.echo(f"skills_available: {getattr(caps, 'skills_available', False)} ({getattr(caps, 'skills_count', 0)})")
    typer.echo(f"plugins_loaded: {getattr(caps, 'plugins_loaded', [])}")
    typer.echo(f"skill_scripts_allowed: {getattr(caps, 'skill_scripts_allowed', False)}")
    typer.echo(f"agents_available: {getattr(caps, 'agents_available', [])}")


@app.command("version")
def version():
    typer.echo(f"opennote {__version__}")


if __name__ == "__main__":
    app()
