"""Main chat screen: banner + transcript + prompt bar.

Notebook == session. No separate session layer.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Callable, Dict, List, Optional

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import Screen

from opennote.agents.loop import TurnCancelled, agent_turn
from opennote.chat.ask import AskResult
from opennote.chat.client import ChatError, default_provider, get_client
from opennote.notebooks import Notebook, NotebookManager, current_project
from opennote.retrieval.retriever import Retriever, render_results
from opennote.transcript import append_messages, load_transcript, clear_transcript, save_transcript
from opennote.tui.commands import lookup, make_commands
from opennote.tui.dialogs import HelpDialog, InfoDialog, ask_input, item_list, confirm_dialog
from opennote.tui.theme import DEFAULT, LIGHT, Palette
from opennote.tui.widgets.prompt import MODE_LABELS, MODES, PromptBar, PromptInput
from opennote.tui.widgets.sidebar import SideBar
from opennote.tui.widgets.transcript import Transcript

logger = logging.getLogger("opennote.tui.chat")


class TurnResult(Message):
    def __init__(self, question: str, answer: str, provider_id: str, model: str, usage=None, skill: Optional[str] = None) -> None:
        super().__init__()
        self.question = question
        self.answer = answer
        self.provider_id = provider_id
        self.model = model
        self.usage = usage
        self.skill = skill


class TurnFailed(Message):
    def __init__(self, error: str) -> None:
        super().__init__()
        self.error = error


class TurnCancelledMsg(Message):
    pass


class SearchResultMsg(Message):
    def __init__(self, question: str, text: str) -> None:
        super().__init__()
        self.question = question
        self.text = text


class SearchFailed(Message):
    def __init__(self, error: str) -> None:
        super().__init__()
        self.error = error


class RoundProgress(Message):
    def __init__(self, round_: int, total: int) -> None:
        super().__init__()
        self.round = round_
        self.total = total


class IngestResultMsg(Message):
    def __init__(self, target: str, count: int, fallback: bool = False) -> None:
        super().__init__()
        self.target = target
        self.count = count
        self.fallback = fallback


class IngestFailed(Message):
    def __init__(self, error: str) -> None:
        super().__init__()
        self.error = error


class StudioResultMsg(Message):
    def __init__(self, label: str, detail: str) -> None:
        super().__init__()
        self.label = label
        self.detail = detail


class StudioFailed(Message):
    def __init__(self, error: str) -> None:
        super().__init__()
        self.error = error


class ChatScreen(Screen):
    BINDINGS = [
        Binding("ctrl+c,ctrl+q", "quit", "Quit", priority=True),
        Binding("escape", "interrupt", "Interrupt", priority=True),
        Binding("tab", "cycle_mode", "Cycle mode", priority=True),
        Binding("ctrl+p", "open_palette", "Command palette", priority=True),
    ]

    def __init__(
        self,
        notebook_name: Optional[str] = None,
        palette: Optional[Palette] = None,
        manager: Optional[NotebookManager] = None,
        provider_id: Optional[str] = None,
        client=None,
        retriever=None,
        ingest_fn=None,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        # None means bare launch: auto-open new notebook via prompt
        self.notebook_name = notebook_name
        self.palette = palette or DEFAULT
        self._manager = manager or NotebookManager()
        self._provider_id = provider_id
        self._client = client
        self._retriever = retriever
        self._ingest_fn = ingest_fn
        self._cancel_flag = False
        self.mode = "ask"
        self.notebook: Optional[Notebook] = None
        # Guards the startup dialog against double-push (only one may exist).
        self._startup_pending = False
        # Tracks whether the banner is currently live in the transcript.
        self._banner_live = False
        # Skill armed via /use: injected into the next ask turn, then cleared.
        self._pending_skill: Optional[str] = None

    # -- composition -------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield Transcript(id="transcript")
                yield PromptBar(id="prompt-bar")
            yield SideBar(id="sidebar")

    def _open_palette(self) -> None:
        self.action_open_palette()

    def _reveal_transcript(self) -> None:
        if not self.has_class("has-history"):
            self.add_class("has-history")

    #: Below this terminal width the right sidebar hides (small screens).
    SIDEBAR_MIN_WIDTH = 112

    def on_mount(self) -> None:
        self.prompt = self.query_one("#prompt-bar", PromptBar)
        self.transcript = self.query_one("#transcript", Transcript)
        self.sidebar = self.query_one("#sidebar", SideBar)
        self._fit_sidebar()

    def on_resize(self, event) -> None:
        self._fit_sidebar()

    def _fit_sidebar(self) -> None:
        """Hide the sidebar on narrow terminals; show it when room allows."""
        try:
            width = self.app.size.width if self.app else 200
            self.query_one("#sidebar", SideBar).display = width >= self.SIDEBAR_MIN_WIDTH
        except Exception:
            pass
        self.transcript.palette = self.palette
        self.commands = make_commands(self)
        self.prompt.set_commands(self.commands)
        self._resolve_provider()
        # Notebook resolution: explicit name -> direct open, None -> startup flow (auto new)
        if self.notebook_name:
            self._resolve_notebook_direct(self.notebook_name)
            self._finish_mount()
        else:
            self._startup_auto_new()

    def _show_banner(self) -> None:
        """Add the banner unless already live (prevents duplicates)."""
        if self._banner_live:
            return
        self.transcript.add_banner(self.palette)
        self._banner_live = True

    def _finish_mount(self) -> None:
        self._show_banner()
        msgs = load_transcript(self.notebook) if self.notebook else []
        if msgs:
            self._reveal_transcript()
            self._render_history(msgs)
        self.prompt.set_mode(self.mode)
        self.prompt.focus_input()
        self._sync_meta()

    def _enter_studio(self, _arg: str = "") -> None:
        self.mode = "studio"
        self.prompt.set_mode(self.mode)
        self.transcript.add_info(
            "Studio mode: pick a generator command (/mindmap, /study, /faq, /briefing, /timeline, /suggest, /audio, /video) or type a topic to open the generator menu."
        )
        self._open_studio_menu()

    # -- setup -------------------------------------------------------------

    def _startup_auto_new(self) -> None:
        """Auto-open a new notebook: prompt prefilled with next free name."""
        if self._startup_pending or self.notebook is not None:
            return
        self._startup_pending = True
        suggested = self._manager.next_notebook_name(current_project())
        self._startup_suggested = suggested
        ask_input(
            self.app,
            "New Notebook",
            "Name for the new notebook:",
            placeholder=suggested,
            on_submit=self._on_startup_name,
        )

    def _on_startup_name(self, name: Optional[str]) -> None:
        self._startup_pending = False
        if name is None:
            # Esc -> show picker (open existing / delete / rename)
            self._show_banner()
            self.prompt.set_mode(self.mode)
            self.prompt.focus_input()
            self._sync_meta()
            self._show_notebook_picker()
            return
        name = name.strip() or getattr(self, "_startup_suggested", "") or self._manager.next_notebook_name(current_project())
        self._create_and_open_notebook(name)

    def _resolve_notebook_direct(self, name: str) -> None:
        try:
            self.notebook = self._manager.get(name)
        except (KeyError, ValueError):
            try:
                self.notebook = self._manager.create(name, project=current_project())
            except (FileExistsError, ValueError) as e:
                self.transcript.add_error(f"Cannot create notebook: {e}")
                self.notebook = None
        if self.notebook:
            self.notebook_name = self.notebook.name

    def _create_and_open_notebook(self, name: str) -> None:
        name = name.strip() if name else ""
        if not name:
            name = self._manager.next_notebook_name(current_project())
        try:
            nb = self._manager.create(name, project=current_project())
        except (FileExistsError, ValueError) as e:
            self.transcript.add_error(f"Cannot create notebook: {e}")
            # Fall back to finishing mount with no notebook or show picker
            self.notebook = None
            self._finish_mount()
            return
        self.notebook = nb
        self.notebook_name = nb.name
        self._finish_mount()

    def _resolve_provider(self) -> None:
        if self._client is not None:
            return
        if self._provider_id:
            try:
                self._client = get_client(self._provider_id)
            except (ChatError, ValueError) as e:
                self.transcript.add_error(str(e))
            return
        try:
            self._client = get_client(default_provider())
        except (ChatError, ValueError) as e:
            self.transcript.add_error(str(e))

    def _sync_meta(self) -> None:
        # Persist provider choice into notebook + update prompt
        if self.notebook is not None and self._client is not None:
            self.notebook.provider_id = self._client.provider_id
            self.notebook.model = self._client.model
            try:
                self.notebook.save()
            except Exception:
                pass
        pid = ""
        model = ""
        if self._client:
            pid = self._client.provider_id
            model = self._client.model
        elif self.notebook:
            pid = self.notebook.provider_id
            model = self.notebook.model
        if not pid:
            pid = "no provider"
        self.prompt.set_model(model, pid)
        self._sync_sidebar(model, pid)
        # Opencode-style: ctx readout always visible, even before the first
        # turn (shows window + spend so users can find it immediately).
        try:
            from opennote.context_meter import ContextUsage, context_limit_for, load_spent

            nb_dir = getattr(self.notebook, "directory", None)
            spent = load_spent(nb_dir)
            base = getattr(self, "_last_usage", None)
            if base is None:
                base = ContextUsage(
                    limit=context_limit_for(model),
                    model=model,
                    exact=True,
                    session_spent=spent,
                )
            else:
                base.session_spent = spent if spent >= base.session_spent else base.session_spent
            self.prompt.set_context(base)
        except Exception:
            pass

    def _sync_sidebar(self, model: str = "", pid: str = "") -> None:
        """Refresh the right-side panel: session, context, services, footer."""
        try:
            sidebar = self.query_one("#sidebar", SideBar)
        except Exception:
            return
        # Session: notebook name + created timestamp.
        try:
            if self.notebook is not None:
                sidebar.set_session(self.notebook.name, f"created {self.notebook.created[:19]}")
            else:
                sidebar.set_session("No notebook", "open or create one")
        except Exception:
            pass
        # Context: last usage, or zero baseline so the box is never empty.
        try:
            from opennote.context_meter import ContextUsage, context_limit_for, load_spent

            usage = getattr(self, "_last_usage", None)
            if usage is None:
                nb_dir = getattr(self.notebook, "directory", None)
                usage = ContextUsage(
                    limit=context_limit_for(model),
                    model=model,
                    exact=True,
                    session_spent=load_spent(nb_dir),
                )
            sidebar.set_usage(usage)
        except Exception:
            pass
        # Services: provider/model + currently active skills and plugins.
        try:
            lines = [f"{pid} | {model}" if model else pid]
            try:
                from opennote.skills.registry import SkillRegistry

                skills = SkillRegistry.discover().list()
                lines.append(
                    "skills: " + ", ".join(s.name for s in skills) if skills else "skills: none"
                )
            except Exception:
                pass
            try:
                from opennote.capabilities import get_capabilities
                from opennote.plugins.loader import PluginContext, PluginLoader

                caps = get_capabilities()
                loader = PluginLoader(PluginContext(capabilities=caps, notebook=self.notebook))
                loader.load()
                names = [h._name for h in loader.hooks] + [
                    t for t in loader.tools if not any(t in h.tools for h in loader.hooks)
                ]
                lines.append("plugins: " + ", ".join(names) if names else "plugins: none")
            except Exception:
                pass
            sidebar.set_services(lines)
        except Exception:
            pass
        # Footer: cwd + git branch (left), app version (right).
        try:
            from pathlib import Path

            from opennote import __version__
            from opennote.tui.widgets.sidebar import git_branch

            cwd = Path.cwd()
            branch = git_branch(cwd)
            left = f"{cwd.name}:{branch}" if branch else cwd.name
            sidebar.set_footer(left, f"OpenNote {__version__}")
        except Exception:
            pass

    # -- history rendering -------------------------------------------------

    def _render_history(self, messages: Optional[List[Dict]] = None) -> None:
        if self.notebook is None:
            return
        msgs = messages if messages is not None else load_transcript(self.notebook)
        for msg in msgs:
            role = msg.get("role")
            content = msg.get("content")
            if not isinstance(content, str):
                continue
            if role == "user":
                self.transcript.add_user(content)
            elif role == "assistant":
                self.transcript.add_answer(content)

    # -- prompt wiring -----------------------------------------------------

    def on_prompt_input_submitted(self, event: PromptInput.Submitted) -> None:
        text = event.text.strip()
        self.prompt.clear_input()
        if not text:
            return
        if text.startswith("/"):
            self._handle_slash(text)
            return
        if self.mode == "search":
            self._start_search(text)
        elif self.mode == "studio":
            self._start_studio(text)
        else:
            self._start_ask(text)

    # -- modes -------------------------------------------------------------

    def action_cycle_mode(self) -> None:
        if self.prompt.popup_visible():
            self.prompt.complete_command()
            return
        self.mode = MODES[(MODES.index(self.mode) + 1) % len(MODES)]
        self.prompt.set_mode(self.mode)
        self.transcript.add_info(
            f"Mode: {MODE_LABELS.get(self.mode, self.mode)} "
            "(Tab to switch; ask = grounded agent, search = LLM-free retrieval, studio = artifact generators)"
        )

    # -- ask / search workers ----------------------------------------------

    def _start_ask(self, question: str) -> None:
        if not self._client:
            self.transcript.add_error("No provider configured. Run 'opennote auth add <provider>'.")
            return
        if self.notebook is None:
            self.transcript.add_error("Notebook is not available.")
            return
        # Pop a skill armed via /use (kept armed when the turn can't start).
        skill_block, skill_name = self._pop_pending_skill()
        self._cancel_flag = False
        self.prompt.set_busy("Searching sources...")
        self._run_ask(question, skill_block=skill_block, skill_name=skill_name)

    def _pop_pending_skill(self) -> tuple:
        """Render an armed skill into a model context block (mirrors the agent `skill` tool output)."""
        name = self._pending_skill
        if not name:
            return "", ""
        try:
            from opennote.skills.registry import SkillRegistry
            skill = SkillRegistry.discover().get(name)
        except Exception:
            skill = None
        if skill is None:
            self._pending_skill = None
            self.transcript.add_error(f"Armed skill '{name}' is no longer installed. Continuing without it.")
            return "", ""
        parts = [f"<active_skill name=\"{skill.name}\">", f"# Skill: {skill.name}", f"Description: {skill.description}", "", skill.body]
        if skill.files:
            parts.append("\nBundled files (use via the run_skill_script tool when relevant):")
            for f in skill.files[:30]:
                parts.append(f"  - {f}")
        parts.append(f"Skill directory: {skill.directory}")
        parts.append("</active_skill>")
        self._pending_skill = None
        return "\n".join(parts), skill.name

    @work(thread=True, exclusive=True, group="turn")
    async def _run_ask(self, question: str, skill_block: str = "", skill_name: str = "") -> None:
        notebook = self.notebook
        history = load_transcript(notebook) if notebook else []
        provider_id = self._client.provider_id if self._client else None
        if skill_block:
            question = f"{skill_block}\n\nUser task: {question}"
        try:
            agent = agent_turn(
                notebook,
                question,
                provider_id=provider_id,
                history=history,
                client=self._client,
                retriever=self._retriever,
                should_cancel=lambda: self._cancel_flag,
                on_round=lambda used, total: self.app.call_from_thread(
                    self.post_message, RoundProgress(used, total)
                ),
            )
        except TurnCancelled:
            self.app.call_from_thread(self.post_message, TurnCancelledMsg())
            return
        except Exception as e:
            logger.exception("Agent turn failed")
            self.app.call_from_thread(self.post_message, TurnFailed(str(e)))
            return
        if notebook is not None:
            try:
                append_messages(notebook, agent.messages)
            except Exception as e:
                self.app.call_from_thread(self.post_message, TurnFailed(str(e)))
                return
        result: AskResult = agent.result
        self.app.call_from_thread(
            self.post_message,
            TurnResult(question, result.answer, result.provider_id, result.model, getattr(result, "usage", None), skill_name or None),
        )

    def _start_search(self, question: str) -> None:
        if self.notebook is None:
            self.transcript.add_error("Notebook is not available.")
            return
        self._cancel_flag = False
        self.prompt.set_busy("Retrieving...")
        self._run_search(question)

    @work(thread=True, exclusive=True, group="search")
    async def _run_search(self, question: str) -> None:
        notebook = self.notebook
        try:
            if self._retriever is not None:
                results = self._retriever.search(question)
            else:
                retriever = Retriever(notebook, top_k=5)
                results = retriever.search(question)
            text = render_results(results)
        except Exception as e:
            logger.exception("Search failed")
            self.app.call_from_thread(self.post_message, SearchFailed(str(e)))
            return
        self.app.call_from_thread(self.post_message, SearchResultMsg(question, text))

    # -- studio: artifact generators ---------------------------

    STUDIO_GENERATORS = [
        ("mindmap", "Mind map", "create_mindmap"),
        ("study", "Study guide", "make_study_guide"),
        ("faq", "FAQ", "make_faq"),
        ("briefing", "Briefing", "make_briefing"),
        ("timeline", "Timeline", "make_timeline"),
        ("suggest", "Suggested questions", "make_suggested_questions"),
    ]

    def _start_studio(self, text: str) -> None:
        if self.notebook is None:
            self.transcript.add_error("Notebook is not available.")
            return
        if not text.strip():
            self.transcript.add_info("Enter a topic or question for the generator.")
            return
        self._studio_topic = text.strip()
        self._open_studio_menu()

    def _open_studio_menu(self) -> None:
        topic = getattr(self, "_studio_topic", "") or "your topic"
        items = [
            ("view", f"{'View artifacts':>20}  (in-terminal viewer)"),
        ]
        items += [
            (key, f"{label:>20}  {topic}")
            for key, label, _ in self.STUDIO_GENERATORS
        ]
        items += [
            ("audio", f"{'Narrated audio':>20}  {topic}"),
            ("video", f"{'Narrated video':>20}  {topic}"),
        ]
        item_list(self.app, "Studio: pick a generator", items, on_pick=self._on_studio_picked)

    def _on_studio_picked(self, key: Optional[str]) -> None:
        if not key:
            return
        if self.notebook is None:
            self.transcript.add_error("Notebook is not available.")
            return
        if key == "view":
            self._open_artifact("")
            return
        topic = getattr(self, "_studio_topic", "") or ""
        if key == "audio":
            self._cancel_flag = False
            self.prompt.set_busy("Generating audio...")
            self._run_audio(topic)
            return
        if key == "video":
            self._cancel_flag = False
            self.prompt.set_busy("Generating video...")
            self._run_video(topic)
            return
        self._cancel_flag = False
        self.prompt.set_busy(f"Generating {key}...")
        self._run_studio(key, topic)

    def _start_studio_command(self, kind: str) -> Callable[[str], None]:
        def handler(arg: str) -> None:
            topic = arg.strip()
            if not topic:
                self.transcript.add_info(f"Usage: /{kind} <topic or question>")
                return
            self._studio_topic = topic
            self._on_studio_picked(kind)

        return handler

    def _start_studio_palette(self, kind: str) -> None:
        ask_input(
            self.app,
            "Studio",
            f"Topic or question for the {kind} generator:",
            placeholder="e.g. quarterly results",
            on_submit=self._on_studio_palette_topic(kind),
        )

    def _on_studio_palette_topic(self, kind: str):
        def handler(topic: Optional[str]) -> None:
            if not topic:
                return
            self._studio_topic = topic
            self._on_studio_picked(kind)

        return handler

    @work(thread=True, exclusive=True, group="turn")
    async def _run_studio(self, kind: str, topic: str) -> None:
        notebook = self.notebook
        try:
            detail = self._generate_studio_artifact(kind, topic, notebook)
        except Exception as e:
            logger.exception("Studio generation failed")
            self.app.call_from_thread(self.post_message, StudioFailed(str(e)))
            return
        self.app.call_from_thread(
            self.post_message, StudioResultMsg(kind, detail)
        )

    @work(thread=True, exclusive=True, group="turn")
    async def _run_audio(self, topic: str) -> None:
        notebook = self.notebook
        try:
            from opennote.audio.tts import save_audio_artifact

            art_path = save_audio_artifact(topic, notebook.artifacts_dir, notebook.name)
            detail = str(art_path)
        except Exception as e:
            logger.exception("Audio generation failed")
            self.app.call_from_thread(self.post_message, StudioFailed(str(e)))
            return
        self.app.call_from_thread(self.post_message, StudioResultMsg("audio", detail))

    @work(thread=True, exclusive=True, group="turn")
    async def _run_video(self, topic: str) -> None:
        notebook = self.notebook
        try:
            from opennote.retrieval.retriever import Retriever
            from opennote.video import save_video_artifact

            try:
                retriever = self._retriever or Retriever(notebook, top_k=8)
                results = retriever.search(topic)
            except ValueError:
                self.app.call_from_thread(
                    self.post_message, StudioFailed("No sources indexed yet. Run /ingest first.")
                )
                return
            if self._client is not None:
                script_json = self._slides_script_json(topic, results)
            else:
                script_json = self._fallback_slides_json(topic, results)
            art_path = save_video_artifact(script_json, notebook.artifacts_dir, notebook.name)
            detail = str(art_path)
        except Exception as e:
            logger.exception("Video generation failed")
            self.app.call_from_thread(self.post_message, StudioFailed(str(e)))
            return
        self.app.call_from_thread(self.post_message, StudioResultMsg("video", detail))

    def _slides_context(self, results) -> str:
        parts = []
        for i, r in enumerate(results, start=1):
            fn = r.metadata.get("filename", "unknown")
            chunk = r.content[:800].replace("\n", " ")
            parts.append(f"[{i}] {fn}: {chunk}")
        return "\n".join(parts) if parts else "No context available."

    def _slides_script_json(self, topic: str, results) -> str:
        """Ask the LLM for a slide-deck JSON script grounded in *results*."""
        import json as _json

        context_text = self._slides_context(results)
        prompt = (
            f"You are a slideshow script writer. Using ONLY the context below, write a "
            f"3-5 slide narrated slideshow about: {topic}\n\n"
            f"Return ONLY a JSON array (no markdown fences, no commentary). Each slide must have:\n"
            f'- "title": short slide title\n'
            f'- "bullets": 2-4 short bullet strings\n'
            f'- "narration": 1-2 sentences to narrate, grounded in the context with [n] citations\n\n'
            f"Context:\n{context_text}"
        )
        answer: str = self._client.chat([{"role": "user", "content": prompt}]).content
        text = answer.strip()
        # Tolerate models wrapping JSON in fences.
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        try:
            slides = _json.loads(text)
        except Exception as e:
            raise ValueError(f"Model did not return slide JSON: {e}")
        if not isinstance(slides, list) or not slides:
            raise ValueError("Model returned an empty slide list.")
        return _json.dumps(slides)

    def _fallback_slides_json(self, topic: str, results) -> str:
        """Deterministic slide script from retrieval chunks (no LLM needed)."""
        import json as _json

        slides = []
        for i, r in enumerate(results[:4], start=1):
            fn = r.metadata.get("filename", "unknown")
            snippet = " ".join(r.content.strip().split())[:400]
            slides.append(
                {
                    "title": f"{topic} ({i})",
                    "bullets": [f"Source: {fn}", snippet[:120]],
                    "narration": snippet,
                }
            )
        if not slides:
            slides = [{"title": topic, "bullets": ["No context available."], "narration": topic}]
        return _json.dumps(slides)

    def _generate_studio_artifact(self, kind: str, topic: str, notebook: Notebook) -> str:
        from opennote.artifacts import (
            create_mindmap,
            generate_briefing,
            generate_faq,
            generate_study_guide,
            generate_suggested_questions,
            generate_timeline,
            save_artifact,
        )
        from opennote.retrieval.retriever import Retriever

        try:
            retriever = self._retriever or Retriever(notebook, top_k=8)
            results = retriever.search(topic)
        except ValueError:
            self.transcript.add_error("No sources indexed yet. Run /ingest first.")
            return ""

        context_parts: List[str] = []
        for i, r in enumerate(results, start=1):
            fn = r.metadata.get("filename", "unknown")
            citation = f"[{i}] {fn}"
            chunk = r.content[:800].replace("\n", " ")
            context_parts.append(f"{citation}: {chunk}")
        context_text = "\n".join(context_parts) if context_parts else "No context available."

        prompts = {
            "study": generate_study_guide(topic, context_text),
            "faq": generate_faq(context_text),
            "briefing": generate_briefing(topic, context_text),
            "timeline": generate_timeline(topic, context_text),
            "suggest": generate_suggested_questions(topic, context_text),
        }
        if kind == "mindmap":
            items = [f"{r.metadata.get('filename','unknown')}: {r.content[:60]}" for r in results[:8]]
            if self._client is None:
                art = create_mindmap(topic, items, notebook.directory)
                return str(art.path)
            prompt = "# " + topic + "\n" + "\n".join(f"[{i+1}] {it}" for i, it in enumerate(items))
        elif kind in prompts:
            prompt = prompts[kind]
        else:
            raise ValueError(f"Unsupported kind: {kind}")

        if self._client is not None:
            try:
                answer: str = self._client.chat([{"role": "user", "content": prompt}]).content
            except Exception as e:
                answer = f"LLM error: {e}"
            art = save_artifact(kind=kind, title=topic, body=answer, notebook_dir=notebook.directory)
            return str(art.path)

        fallback_templates = {
            "study": f"Study guide for **{topic}** based on {len(results)} chunks.\n" + "\n".join(
                f"- {r.metadata.get('filename','unknown')}: {r.content[:80]}" for r in results[:5]),
            "faq": f"FAQ for **{topic}** from {len(results)} sources.\n" + "\n".join(
                f"Q: What does {r.metadata.get('filename','unknown')} say about {topic}?\nA: {r.content[:100]}" for r in results[:5]),
            "briefing": f"Briefing for **{topic}** from {len(results)} sources.\n" + "\n".join(
                f"- {r.metadata.get('filename','unknown')}: {r.content[:120]}" for r in results[:5]),
            "timeline": f"Timeline for **{topic}** from {len(results)} sources.\n" + "\n".join(
                f"- {r.metadata.get('filename','unknown')}: {r.content[:80]}" for r in results[:8]),
            "suggest": f"Suggested questions for **{topic}** from {len(results)} sources.\n" + "\n".join(
                f"- Suggested question from {r.metadata.get('filename','unknown')}" for r in results[:5]),
            "mindmap": f"Mind map overview for **{topic}**.\n" + "\n".join(
                f"- {r.metadata.get('filename','unknown')}: {r.content[:50]}" for r in results[:5]),
        }
        body = fallback_templates.get(kind, "")
        art = save_artifact(kind=kind, title=topic, body=body, notebook_dir=notebook.directory)
        return str(art.path)

    def on_turn_result(self, msg: TurnResult) -> None:
        self._notify_done("Answer ready.")
        skill = getattr(msg, "skill", None)
        if skill:
            self.transcript.add_info(f"Skill applied: {skill}")
        self.transcript.add_answer(msg.answer)
        usage = getattr(msg, "usage", None)
        # Persistent opencode-style readout in the prompt bar (survives scroll).
        try:
            self.prompt.set_context(usage)
            self._last_usage = usage
            self.query_one("#sidebar", SideBar).set_usage(usage)
        except Exception:
            pass
        self._sync_meta()
        self.prompt.set_idle()

    def on_turn_failed(self, msg: TurnFailed) -> None:
        self._notify_done(f"Ask failed: {msg.error}")
        self.transcript.add_error(msg.error)
        self.prompt.set_idle()

    def on_turn_cancelled_msg(self, msg: TurnCancelledMsg) -> None:
        self._notify_done("Ask interrupted.")
        self.transcript.add_info("Interrupted.")
        self.prompt.set_idle()

    def on_search_result_msg(self, msg: SearchResultMsg) -> None:
        self._notify_done("Search results ready.")
        self.transcript.add_info(f"Search: {msg.question}")
        self.transcript.write(msg.text)
        self.transcript.write("")
        self.prompt.set_idle()

    def on_search_failed(self, msg: SearchFailed) -> None:
        self._notify_done(f"Search failed: {msg.error}")
        self.transcript.add_error(msg.error)
        self.prompt.set_idle()

    def on_studio_result_msg(self, msg: StudioResultMsg) -> None:
        self._notify_done(f"Studio {msg.label} ready.")
        detail = msg.detail
        # Short <notebook>/artifacts/<file> display for deep absolute paths.
        try:
            from pathlib import Path as _Path

            from opennote.artifacts import short_artifact_display as _short

            nb_dir = getattr(self.notebook, "directory", None)
            if nb_dir is not None and _Path(str(detail)).is_file():
                detail = _short(detail, nb_dir)
        except Exception:
            detail = msg.detail
        self.transcript.add_info(f"Studio {msg.label}: {detail}")
        # Native in-terminal rendering: mind-maps as a tree, every other
        # markdown artifact as Markdown. Audio/video stay path-only.
        if msg.label not in ("audio", "video"):
            try:
                from pathlib import Path as _Path

                from opennote.artifacts import load_artifact as _load
                from opennote.artifacts import parse_mindmap as _parse

                p = _Path(str(msg.detail))
                if p.is_file() and p.suffix == ".md":
                    art = _load(p)
                    if msg.label == "mindmap" or art.kind == "mindmap":
                        self.transcript.add_mindmap(art.title, _parse(art.body, title=art.title))
                        self.transcript.add_info("Tip: /open <file> reopens this map in a collapsible viewer.")
                    else:
                        body = art.body
                        if len(body) > 4000:
                            body = body[:4000].rstrip() + "\n\n…(truncated — /open <file> for the full text)"
                        self.transcript.add_answer(body)
            except Exception:
                pass
        self.prompt.set_idle()

    def on_studio_failed(self, msg: StudioFailed) -> None:
        self._notify_done(f"Studio failed: {msg.error}")
        self.transcript.add_error(f"Studio: {msg.error}")
        self.prompt.set_idle()

    def on_round_progress(self, msg: RoundProgress) -> None:
        if self.prompt.busy:
            self.prompt.update_status(f"Searching sources... round {msg.round}/{msg.total}")

    # -- interrupts --------------------------------------------------------

    def action_interrupt(self) -> None:
        if self.prompt.popup_visible():
            self.prompt.hide_popup()
            return
        if not self.prompt.busy:
            return
        if self.prompt._interrupt_armed:
            self._cancel_flag = True
            self.prompt.update_status("Interrupting...")
        else:
            self.prompt.arm_interrupt()

    def action_quit(self) -> None:
        self.app.exit()

    # -- slash commands ----------------------------------------------------

    def _handle_slash(self, raw: str) -> None:
        parts = raw.strip().split(None, 1)
        name = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        cmd = lookup(name, self.commands)
        if cmd is None:
            self.transcript.add_info(f"Unknown command: {name}  (try /help)")
            return
        cmd.handler(arg)

    # -- command handlers --------------------------------------------------

    def _show_help(self, _arg: str = "") -> None:
        from opennote.tui.dialogs import HelpDialog
        self.app.push_screen(
            HelpDialog('Help', 'Press ctrl+p to see all available actions and commands in any context.')
        )

    def _notify_done(self, message: str) -> None:
        """Toast a background completion (visible even over modal screens)."""
        try:
            self.app.notify(message, title="OpenNote")
        except Exception:
            pass

    def _play_snake(self, _arg: str = "") -> None:
        # Deliberately allowed while busy: this is the waiting-room game.
        from opennote.tui.screens.snake import SnakeScreen
        self.app.push_screen(SnakeScreen(status_fn=lambda: self.prompt.status_text))

    def _clear_transcript(self, _arg: str = "") -> None:
        self._pending_skill = None
        if self.notebook is not None:
            clear_transcript(self.notebook)
        self.transcript.clear()
        self._banner_live = False
        self._show_banner()

    def _switch_provider(self, arg: str = "") -> None:
        arg = arg.strip()
        if not arg:
            self._open_provider_dialog()
            return
        try:
            self._client = get_client(arg)
        except (ChatError, ValueError) as e:
            self.transcript.add_error(str(e))
            return
        self._sync_meta()
        self.transcript.add_info(
            f"Provider switched to {self._client.provider_id} ({self._client.model})."
        )

    def _open_provider_dialog(self) -> None:
        from opennote.auth.config import AuthConfig
        from opennote.auth.keychain import resolve_key
        from opennote.auth.registry import all_providers

        config = AuthConfig()
        items = []
        for p in all_providers():
            settings = config.get(p.id)
            model = settings.model if settings else None
            if not (resolve_key(p.id) and model):
                continue
            items.append((p.id, f"{p.label} | {model}"))
        if not items:
            self.transcript.add_info("No provider configured. Run 'opennote auth add <provider>'.")
            return
        item_list(self.app, "Provider", items, on_pick=self._on_provider_picked)

    def _on_provider_picked(self, pid: Optional[str]) -> None:
        if pid:
            self._switch_provider(pid)

    def _export_transcript(self, _arg: str = "") -> None:
        if self.notebook is None:
            self.transcript.add_info("Nothing to export.")
            return
        msgs = load_transcript(self.notebook)
        if not msgs:
            self.transcript.add_info("Nothing to export.")
            return
        out_dir = self.notebook.directory / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"notebook-{self.notebook.name}.md"
        lines = [
            f"# Notebook {self.notebook.name}",
            f"\n- updated: {self.notebook.updated}\n",
        ]
        for msg in msgs:
            role = msg.get("role")
            content = msg.get("content")
            if not isinstance(content, str):
                continue
            if role == "user":
                lines.append(f"\n## You\n\n{content}")
            elif role == "assistant":
                lines.append(f"\n## Assistant\n\n{content}")
        try:
            path.write_text("\n".join(lines), encoding="utf-8")
        except OSError as e:
            self.transcript.add_error(f"Export failed: {e}")
            return
        self.transcript.add_info(f"Exported {len(msgs)} messages to {path}")

    # -- undo / details -------------------------------------------------

    def _undo_last_turn(self, _arg: str = "") -> None:
        if self.prompt.busy:
            self.transcript.add_error("Busy.")
            return
        if self.notebook is None:
            self.transcript.add_info("Nothing to undo.")
            return
        messages = load_transcript(self.notebook)
        last_user = -1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_user = i
                break
        if last_user == -1:
            self.transcript.add_info("Nothing to undo.")
            return
        dropped = len(messages) - last_user
        save_transcript(self.notebook, messages[:last_user])
        self._pending_skill = None
        self.transcript.clear()
        self._banner_live = False
        self._show_banner()
        self._render_history()
        self.prompt.set_idle()
        self.transcript.add_info(f"Undid the last turn ({dropped} message(s) removed).")

    def _show_details(self, _arg: str = "") -> None:
        lines = ["Notebook:"]
        if self.notebook:
            lines.append(f"  name:      {self.notebook.name}")
            lines.append(f"  directory: {self.notebook.directory}")
            lines.append(f"  embed:     {self.notebook.embed_model}")
            lines.append(f"  project:   {self.notebook.project}")
            lines.append(f"  sources:   {len(self.notebook.sources)}/{5}")
            for s in self.notebook.sources:
                lines.append(f"    - {s}")
            msgs = load_transcript(self.notebook)
            lines.append(f"  messages:  {len(msgs)}")
            lines.append(f"  updated:   {self.notebook.updated[:19] if self.notebook.updated else ''}")
        else:
            lines.append("  (no notebook)")
        lines.append("Client:")
        if self._client:
            lines.append(f"  {self._client.provider_id} | {self._client.model}")
        else:
            lines.append("  (no provider configured)")
        self.app.push_screen(InfoDialog("Details", "\n".join(lines)))

    def _list_skills(self, _arg: str = "") -> None:
        from opennote.skills.registry import SkillRegistry
        reg = SkillRegistry.discover()
        skills = reg.list()
        if not skills:
            self.transcript.add_info("No skills installed.")
            self.transcript.add_info("Install: npx skills add <owner/repo> -a codex  (-> .agents/skills/)")
            return
        for s in skills:
            self.transcript.add_info(f"  {s.name:<25} {s.description[:80]}")
        self.transcript.add_info("Tip: /use <skill> <task> applies a skill to your next answer; /skill <name> shows details.")

    def _use_skill(self, arg: str = "") -> None:
        """Arm a skill for the next ask turn: /use <skill> [task]."""
        from opennote.skills.registry import SkillRegistry
        reg = SkillRegistry.discover()
        text = arg.strip()
        name, _, task = text.partition(" ")
        name, task = name.strip(), task.strip()
        if not name:
            skills = reg.list()
            if not skills:
                self.transcript.add_info("No skills installed.")
                self.transcript.add_info("Install: npx skills add <owner/repo> -a codex  (-> .agents/skills/)")
                return
            items = [(s.name, f"{s.name:<25} {s.description[:60]}") for s in skills]
            item_list(self.app, "Use skill (applies to your next answer)", items, on_pick=self._on_use_skill_picked)
            return
        skill = reg.get(name)
        if skill is None:
            self.transcript.add_error(f"Skill '{name}' not found. Available: {', '.join(reg.names()) or 'none'}")
            return
        self._pending_skill = skill.name
        if not task:
            self.transcript.add_info(f"Skill '{skill.name}' armed — type your question (applies to the next answer).")
            return
        if self.prompt.busy:
            self.transcript.add_info(f"Skill '{skill.name}' armed (busy — applies to your next answer).")
            return
        self._start_ask(task)

    def _on_use_skill_picked(self, name: Optional[str]) -> None:
        if name:
            self._use_skill(name)

    def _show_skill(self, arg: str = "") -> None:
        name = arg.strip()
        if not name:
            self._list_skills("")
            return
        from opennote.skills.registry import SkillRegistry
        reg = SkillRegistry.discover()
        skill = reg.get(name)
        if skill is None:
            self.transcript.add_error(f"Skill '{name}' not found. Try /skills")
            return
        lines = [f"Skill: {skill.name}", f"Description: {skill.description}", f"Directory: {skill.directory}", ""]
        lines.append("How to use:")
        lines.append(f"  /use {skill.name} <your task>  — applies this skill to your next answer")
        scripts = [f for f in (skill.files or []) if f.endswith((".py", ".js", ".sh"))]
        if scripts:
            import os as _os
            allowed = _os.environ.get("OPENNOTE_ALLOW_SKILL_SCRIPTS", "").strip().lower() in ("1", "true", "yes", "on")
            state = "enabled" if allowed else "disabled (set OPENNOTE_ALLOW_SKILL_SCRIPTS=1 to enable)"
            lines.append(f"  Scripts: {state}")
            for f in scripts[:10]:
                lines.append(f"    - {f}")
        lines.append("")
        lines.append(skill.body[:3000])
        if skill.files:
            lines.append("\nBundled files:")
            for f in skill.files[:20]:
                lines.append(f"  {f}")
        self.app.push_screen(InfoDialog(f"Skill: {skill.name}", "\n".join(lines)))

    def _list_plugins(self, _arg: str = "") -> None:
        from opennote.capabilities import get_capabilities
        from opennote.plugins.loader import PluginContext, PluginLoader
        caps = get_capabilities()
        loader = PluginLoader(PluginContext(capabilities=caps, notebook=self.notebook, logger=None))
        loader.load()
        if not loader.hooks and not loader.tools:
            self.transcript.add_info("No plugins loaded.")
            if not getattr(caps, "supermemory_available", False):
                self.transcript.add_info("(tip: set SUPERMEMORY_API_KEY to enable supermemory)")
            return
        for h in loader.hooks:
            tools_list = ", ".join(h.tools.keys()) if h.tools else "(no tools)"
            self.transcript.add_info(f"  {h._name}: [{tools_list}]")
        for tname in loader.tools:
            if not any(tname in h.tools for h in loader.hooks):
                self.transcript.add_info(f"  tool: {tname}")

    def _list_agents(self, _arg: str = "") -> None:
        from opennote.agents.defs import AgentRegistry
        reg = AgentRegistry.discover()
        for a in reg.list():
            hidden = " (hidden)" if a.hidden else ""
            self.transcript.add_info(f"  {a.name:<15} [{a.mode:<8}] {a.description[:70]}{hidden}")

    def _show_agent(self, arg: str = "") -> None:
        name = arg.strip()
        if not name:
            self._list_agents("")
            return
        from opennote.agents.defs import AgentRegistry
        reg = AgentRegistry.discover()
        agent = reg.get(name)
        if agent is None:
            self.transcript.add_error(f"Agent '{name}' not found. Try /agents")
            return
        lines = [f"Agent: {agent.name}", f"Mode: {agent.mode}", f"Description: {agent.description}"]
        if agent.model:
            lines.append(f"Model: {agent.model}")
        if agent.temperature is not None:
            lines.append(f"Temperature: {agent.temperature}")
        if agent.permission:
            lines.append(f"Permission: {agent.permission}")
        lines.append("")
        lines.append(agent.prompt[:3000] if agent.prompt else "(no prompt body)")
        self.app.push_screen(InfoDialog(f"Agent: {agent.name}", "\n".join(lines)))

    def _show_capabilities(self, _arg: str = "") -> None:
        from opennote.capabilities import get_capabilities
        caps = get_capabilities()
        lines = [
            f"web_search: {caps.web_search}",
            f"supermemory: {getattr(caps, 'supermemory_available', False)}",
            f"tts_backend: {caps.tts_backend}",
            f"tts_available: {caps.tts_available}",
            f"video_available: {caps.video_available}",
            f"skills: {getattr(caps, 'skills_available', False)} ({getattr(caps, 'skills_count', 0)})",
            f"plugins: {getattr(caps, 'plugins_loaded', [])}",
            f"skill_scripts: {getattr(caps, 'skill_scripts_allowed', False)}",
            f"agents: {getattr(caps, 'agents_available', [])}",
        ]
        self.app.push_screen(InfoDialog("Capabilities", "\n".join(lines)))

    def _show_context(self, _arg: str = "") -> None:
        """Show the opencode-style Context panel (works before the first turn)."""
        usage = getattr(self, "_last_usage", None)
        if usage is None:
            from opennote.context_meter import ContextUsage, context_limit_for, load_spent

            model = ""
            if self._client:
                model = self._client.model
            elif self.notebook:
                model = self.notebook.model
            nb_dir = getattr(self.notebook, "directory", None)
            usage = ContextUsage(
                limit=context_limit_for(model),
                model=model,
                exact=True,
                session_spent=load_spent(nb_dir),
            )
        self.transcript.add_info(usage.render())

    def _set_mode_ask(self, _arg: str = "") -> None:
        self._set_mode("ask")

    def _set_mode_search(self, _arg: str = "") -> None:
        self._set_mode("search")

    def _set_mode(self, mode: str) -> None:
        self.mode = mode if mode in MODES else "ask"
        self.prompt.set_mode(self.mode)

    def _switch_theme(self, arg: str = "") -> None:
        choice = arg.strip().lower()
        if choice == "light":
            self.app.palette = LIGHT
        elif choice == "dark":
            self.app.palette = DEFAULT
        elif choice:
            self.transcript.add_info("Usage: /theme <dark|light>")
            return
        else:
            self.app.palette = LIGHT if self.app.palette.dark else DEFAULT
        self.app.register_theme(self.app.palette.to_textual())
        self.app.theme = self.app.palette.name
        self.palette = self.app.palette
        self.transcript.add_banner(self.app.palette)

    def _list_sources(self, _arg: str = "") -> None:
        if self.notebook is None:
            return
        try:
            retriever = Retriever(self.notebook)
        except Exception as e:
            self.transcript.add_error(str(e))
            return
        sources = retriever.sources()
        if not sources:
            self.transcript.add_info("No sources indexed yet.")
            return
        for src in sources:
            self.transcript.add_info(f"  {src}")

    def _remove_source(self, arg: str = "") -> None:
        if self.notebook is None:
            self.transcript.add_error("Notebook is not available.")
            return
        arg = arg.strip()
        if not arg:
            # Pick from list
            sources = self.notebook.sources
            if not sources:
                self.transcript.add_info("No sources to remove.")
                return
            items = [(s, s) for s in sources]
            item_list(self.app, "Remove source", items, on_pick=self._on_remove_picked)
            return
        # Try exact or substring match
        matches = [s for s in self.notebook.sources if arg in s]
        if not matches:
            self.transcript.add_error(f"No source matching '{arg}'.")
            return
        if len(matches) > 1:
            items = [(s, s) for s in matches]
            item_list(self.app, "Remove source (multiple matches)", items, on_pick=self._on_remove_picked)
            return
        self._do_remove_source(matches[0])

    def _on_remove_picked(self, source: Optional[str]) -> None:
        if source:
            self._do_remove_source(source)

    def _do_remove_source(self, source: str) -> None:
        def on_confirm(confirmed: Optional[bool]) -> None:
            if not confirmed:
                return
            try:
                from opennote.ingest.pipeline import remove_source
                remove_source(self.notebook, source)
                self.transcript.add_info(f"Removed source: {source}")
            except Exception as e:
                self.transcript.add_error(f"Remove failed: {e}")

        confirm_dialog(self.app, f"Remove source?\n{source}", on_confirm)

    def _open_artifact(self, arg: str = "") -> None:
        if self.notebook is None:
            self.transcript.add_error("Notebook is not available.")
            return
        artifacts_dir = self.notebook.directory / "artifacts"
        if not artifacts_dir.is_dir():
            self.transcript.add_info("No artifacts yet. Use /mindmap, /study, /faq, etc.")
            return
        arg = arg.strip()
        if not arg:
            # No arg: pick from the notebook's artifacts (short names).
            files = sorted(artifacts_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not files:
                self.transcript.add_info("No artifacts yet. Use /mindmap, /study, /faq, etc.")
                return
            from opennote.artifacts import short_artifact_display as _short

            items = [(f.name, _short(f, self.notebook.directory)) for f in files]
            item_list(self.app, "Open artifact", items, on_pick=self._on_artifact_picked)
            return
        candidate = (artifacts_dir / arg).resolve()
        try:
            candidate.relative_to(artifacts_dir.resolve())
        except ValueError:
            self.transcript.add_error("Path escapes the notebook's artifacts directory.")
            return
        target = candidate
        if not target.is_file():
            # Forgiving match: unique substring of the filename.
            matches = [f for f in artifacts_dir.glob("*.md") if arg.lower() in f.name.lower()]
            if len(matches) == 1:
                target = matches[0]
            elif len(matches) > 1:
                from opennote.artifacts import short_artifact_display as _short

                items = [(f.name, _short(f, self.notebook.directory)) for f in matches]
                item_list(self.app, "Open artifact (multiple matches)", items, on_pick=self._on_artifact_picked)
                return
        if not target.exists():
            self.transcript.add_info("No artifacts yet. Use /mindmap, /study, /faq, etc.")
            return
        if target.is_file():
            # Mind-maps render in-terminal; everything else opens externally.
            try:
                from opennote.artifacts import load_artifact as _load
                from opennote.artifacts import parse_mindmap as _parse
                from opennote.artifacts import short_artifact_display as _short
                from opennote.tui.dialogs import MindmapDialog

                art = _load(target)
                if art.kind == "mindmap":
                    root = _parse(art.body, title=art.title)
                    self.app.push_screen(
                        MindmapDialog(art.title, root, _short(target, self.notebook.directory))
                    )
                    return
            except Exception:
                pass
        try:
            if os.name == "nt":
                os.startfile(str(target))  # type: ignore[attr-defined]
            else:
                import subprocess as _sp

                if sys.platform == "darwin":
                    _sp.Popen(["open", str(target)])
                else:
                    _sp.Popen(["xdg-open", str(target)])
        except Exception as e:
            self.transcript.add_error(f"Failed to open {target}: {e}")
            return
        self.transcript.add_info(f"Opened {target}")

    def _on_artifact_picked(self, name: Optional[str]) -> None:
        if name:
            self._open_artifact(name)

    # -- notebooks: 4-action picker ---------------------------------------

    def _show_notebook_picker(self, _arg: str = "") -> None:
        """Top-level notebook menu: open / new / delete / rename."""
        if self.prompt.busy:
            self.transcript.add_error("Busy.")
            return
        items = [
            ("open", "Open existing notebook"),
            ("new", "New notebook"),
            ("delete", "Delete notebook"),
            ("rename", "Rename notebook"),
        ]
        item_list(self.app, "Notebooks", items, on_pick=self._on_notebook_action)

    def _on_notebook_action(self, action: Optional[str]) -> None:
        if not action:
            return
        if action == "open":
            self._open_notebook_dialog()
        elif action == "new":
            self._create_notebook_dialog()
        elif action == "delete":
            self._delete_notebook_dialog()
        elif action == "rename":
            self._rename_notebook_dialog()

    def _open_notebook_dialog(self) -> None:
        notebooks = self._manager.list_for_project(current_project())
        if not notebooks:
            self.transcript.add_info("No notebooks for this directory. Use 'New notebook'.")
            return
        items = [
            (nb.name, f"{'*' if self.notebook and nb.name == self.notebook.name else ' '} {nb.name} | {len(nb.sources)}/5 sources | {len(load_transcript(nb))} msgs")
            for nb in notebooks
        ]
        item_list(self.app, "Open notebook", items, on_pick=self._on_notebook_picked)

    def _on_notebook_picked(self, name: Optional[str]) -> None:
        if name:
            self._switch_notebook(name)

    def _switch_notebook(self, name: str = "") -> bool:
        name = name.strip()
        if not name:
            self.transcript.add_info("Usage: /notebook <name>")
            return False
        if self.prompt.busy:
            self.transcript.add_error("Busy.")
            return False
        try:
            notebook = self._manager.get(name)
        except KeyError:
            self.transcript.add_error(f"Notebook '{name}' does not exist.")
            return False
        except ValueError as e:
            self.transcript.add_error(str(e))
            return False
        self.notebook_name = notebook.name
        self.notebook = notebook
        self.transcript.clear()
        self._banner_live = False
        self._show_banner()
        msgs = load_transcript(notebook)
        if msgs:
            self._reveal_transcript()
            self._render_history(msgs)
        self._sync_meta()
        self.transcript.add_info(f"Notebook: {notebook.name}")
        return True

    def _create_notebook(self, name: str = "") -> None:
        name = name.strip()
        if not name:
            self.transcript.add_info("Usage: /create <name>")
            return
        if self.prompt.busy:
            self.transcript.add_error("Busy.")
            return
        try:
            notebook = self._manager.create(name, project=current_project())
        except (FileExistsError, ValueError) as e:
            self.transcript.add_error(str(e))
            return
        self.notebook_name = notebook.name
        self.notebook = notebook
        self.transcript.clear()
        self._banner_live = False
        self._show_banner()
        self._sync_meta()
        self.transcript.add_info(f"Created notebook: {notebook.name}")
        return

    def _create_notebook_dialog(self) -> None:
        suggested = self._manager.next_notebook_name(current_project())
        ask_input(
            self.app,
            "New Notebook",
            "Name for the new notebook:",
            placeholder=suggested,
            on_submit=self._on_create_notebook_dialog,
        )
        self._create_suggested = suggested

    def _on_create_notebook_dialog(self, name: Optional[str]) -> None:
        if name is None:
            return
        name = name.strip() or getattr(self, "_create_suggested", "")
        if not name:
            return
        self._create_notebook(name)

    def _delete_notebook_dialog(self) -> None:
        notebooks = self._manager.list_for_project(current_project())
        if not notebooks:
            self.transcript.add_info("No notebooks to delete.")
            return
        items = [(nb.name, f"{nb.name} | {len(nb.sources)}/5 sources") for nb in notebooks]
        item_list(self.app, "Delete notebook", items, on_pick=self._on_delete_picked)

    def _on_delete_picked(self, name: Optional[str]) -> None:
        if not name:
            return
        def on_confirm(confirmed: Optional[bool]) -> None:
            if not confirmed:
                return
            try:
                self._manager.delete(name)
                self.transcript.add_info(f"Deleted notebook '{name}'.")
                if self.notebook and self.notebook.name == name:
                    # Switch to next available or clear
                    remaining = self._manager.list_for_project(current_project())
                    if remaining:
                        self._switch_notebook(remaining[0].name)
                    else:
                        self.notebook = None
                        self.transcript.clear()
                        self._banner_live = False
                        self._show_banner()
            except Exception as e:
                self.transcript.add_error(str(e))
        confirm_dialog(self.app, f"Delete notebook '{name}'? This cannot be undone.", on_confirm)

    def _rename_notebook_dialog(self) -> None:
        notebooks = self._manager.list_for_project(current_project())
        if not notebooks:
            self.transcript.add_info("No notebooks to rename.")
            return
        items = [(nb.name, nb.name) for nb in notebooks]
        item_list(self.app, "Rename notebook", items, on_pick=self._on_rename_pick)

    def _on_rename_pick(self, name: Optional[str]) -> None:
        if not name:
            return
        self._rename_old = name
        ask_input(
            self.app,
            "Rename Notebook",
            f"New name for '{name}':",
            placeholder=name,
            on_submit=self._on_rename_submit,
        )

    def _on_rename_submit(self, new_name: Optional[str]) -> None:
        if not new_name:
            return
        old = getattr(self, "_rename_old", "")
        new_name = new_name.strip()
        if not new_name:
            return
        try:
            nb = self._manager.rename(old, new_name)
            self.transcript.add_info(f"Renamed '{old}' to '{new_name}'.")
            if self.notebook and self.notebook.name == old:
                self.notebook = nb
                self.notebook_name = nb.name
        except Exception as e:
            self.transcript.add_error(str(e))

    # -- ingest --------------------------------------------------------

    def _start_ingest(self, arg: str = "") -> None:
        arg = arg.strip()
        if not arg:
            self.transcript.add_info("Usage: /ingest <path|url>")
            return
        if self.notebook is None:
            self.transcript.add_error("Notebook is not available.")
            return
        if self.prompt.busy:
            self.transcript.add_error("Busy.")
            return
        self.prompt.set_busy(f"Indexing {arg}...")
        self._run_ingest(arg)

    @work(thread=True, exclusive=True, group="turn")
    async def _run_ingest(self, target: str) -> None:
        notebook = self.notebook
        try:
            import contextlib
            import io

            # Capture any stdout/stderr / tqdm / docling progress that would
            # otherwise corrupt the Textual alternate screen. Also capture
            # pipeline fallback warnings via a temporary logging handler.
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            fallback_used = False

            class _FallbackHandler(logging.Handler):
                def emit(self, record):
                    nonlocal fallback_used
                    if "falling back" in record.getMessage().lower():
                        fallback_used = True

            _logger = logging.getLogger("opennote.ingest.pipeline")
            _handler = _FallbackHandler()
            _logger.addHandler(_handler)
            try:
                with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                    if self._ingest_fn is not None:
                        count = self._ingest_fn(notebook, target)
                    else:
                        from opennote.ingest.pipeline import ingest as _ingest

                        count = _ingest(notebook, target)
                    # Also detect fallback via captured stderr (print fallback)
                    combined = stdout_buf.getvalue() + stderr_buf.getvalue()
                    if "falling back" in combined.lower():
                        fallback_used = True
            finally:
                _logger.removeHandler(_handler)
        except Exception as e:
            logger.exception("Ingest failed")
            detail = str(e)
            self.app.call_from_thread(self.post_message, IngestFailed(detail))
            return
        self.app.call_from_thread(
            self.post_message, IngestResultMsg(target, int(count), fallback=fallback_used)
        )

    def on_ingest_result_msg(self, msg: IngestResultMsg) -> None:
        self._notify_done(f"Indexed {msg.count} chunk(s) from {msg.target}")
        self.transcript.add_info(f"Indexed {msg.count} chunk(s) from {msg.target}")
        if msg.fallback:
            self.transcript.add_info("Note: local fallback used (Docling missing C++ compiler). Install VS Build Tools or run /ingest with --parser fallback for consistent behavior.")
        self.prompt.set_idle()

    def on_ingest_failed(self, msg: IngestFailed) -> None:
        self._notify_done(f"Ingest failed: {msg.error}")
        self.transcript.add_error(f"Ingest failed: {msg.error}")
        self.prompt.set_idle()

    # -- auth / connect -------------------------------------------------

    def _show_auth(self, _arg: str = "") -> None:
        from opennote.auth.config import AuthConfig
        from opennote.auth.keychain import resolve_key
        from opennote.auth.registry import all_providers

        config = AuthConfig()
        lines = ["Providers:"]
        for p in all_providers():
            settings = config.get(p.id)
            model = settings.model if settings else None
            key = resolve_key(p.id)
            bits = ["key Γ£ô" if key else "no key"]
            bits.append(f"model {model}" if model else "no model")
            marker = " *" if self._client and p.id == self._client.provider_id else ""
            lines.append(f"  {p.id:<10} {', '.join(bits)}{marker}")
        self.app.push_screen(InfoDialog("Auth", "\n".join(lines)))

    def _start_connect(self, arg: str = "") -> None:
        from opennote.auth.registry import all_providers, get_provider

        arg = arg.strip()
        if arg:
            try:
                self._connect_provider(get_provider(arg))
            except ValueError as e:
                self.transcript.add_error(str(e))
            return
        items = [(p.id, f"{p.label} ({p.id})") for p in all_providers()]
        item_list(self.app, "Connect: provider", items, on_pick=self._on_connect_provider_picked)

    def _on_connect_provider_picked(self, pid: Optional[str]) -> None:
        if not pid:
            return
        from opennote.auth.registry import get_provider

        try:
            self._connect_provider(get_provider(pid))
        except ValueError as e:
            self.transcript.add_error(str(e))

    def _connect_provider(self, provider) -> None:
        from opennote.auth.keychain import resolve_key

        if not resolve_key(provider.id):
            ask_input(
                self.app,
                f"Connect {provider.label}",
                f"API key for {provider.id} (or set env {provider.env_var}):",
                placeholder="sk-...",
                password=True,
                on_submit=self._on_connect_key(provider.id),
            )
            return
        self._open_connect_model(provider)

    def _on_connect_key(self, pid: str):
        def handler(key: Optional[str]) -> None:
            if not key:
                return
            from opennote.auth.config import AuthConfig
            from opennote.auth.keychain import mask_key, set_key
            from opennote.auth.registry import get_provider

            try:
                set_key(pid, key)
                AuthConfig().mark_added(pid)
            except Exception as e:
                self.transcript.add_error(str(e))
                return
            self.transcript.add_info(f"Stored API key for {pid} ({mask_key(key)}).")
            self._open_connect_model(get_provider(pid), key)

        return handler

    def _open_connect_model(self, provider, key: Optional[str] = None) -> None:
        from opennote.auth.config import AuthConfig
        from opennote.auth.keychain import resolve_key
        from opennote.auth.models import rank_models
        from opennote.auth.validate import validate_key

        if key is None:
            key = resolve_key(provider.id)
        if not key:
            self.transcript.add_error("No API key stored for this provider.")
            return

        settings = AuthConfig().get(provider.id)

        result = validate_key(provider, key)
        if result.ok:
            models = rank_models(provider, result.models)
        else:
            if result.error == "invalid-key":
                self.transcript.add_error("Invalid API key - could not fetch models.")
            elif result.error == "network":
                self.transcript.add_info(
                    "Could not reach the model catalog - using default models. "
                    "Run 'opennote auth verify <provider>' to refresh."
                )
            else:
                self.transcript.add_info(
                    f"Model catalog returned an error ({result.error}) - using default models."
                )
            models = list(provider.preferred_models)

        if settings and settings.model:
            if settings.model not in models:
                models.insert(0, settings.model)
        if not models:
            self._finish_connect(provider.id, settings.model if settings else None)
            return
        items = [(m, m) for m in models]
        item_list(
            self.app,
            f"Connect {provider.id}: model",
            items,
            on_pick=self._on_connect_model_picked(provider.id),
        )

    def _on_connect_model_picked(self, pid: str):
        def handler(model: Optional[str]) -> None:
            self._finish_connect(pid, model)

        return handler

    def _finish_connect(self, pid: str, model: Optional[str]) -> None:
        from opennote.auth.config import AuthConfig

        config = AuthConfig()
        if model:
            config.set_model(pid, model)
        try:
            client = get_client(pid)
        except (ChatError, ValueError) as e:
            self.transcript.add_error(str(e))
            return
        self._client = client
        self._sync_meta()
        self.transcript.add_info(f"Connected {pid} ({client.model}).")

    def _open_model_dialog(self, _arg: str = "") -> None:
        if self._client is None:
            self.transcript.add_error("No provider configured. Run /connect first.")
            return
        provider_id = self._client.provider_id
        current = getattr(self._client, "model", "")

        from opennote.auth.config import AuthConfig
        from opennote.auth.keychain import resolve_key
        from opennote.auth.models import rank_models
        from opennote.auth.registry import get_provider
        from opennote.auth.validate import validate_key

        try:
            provider = get_provider(provider_id)
        except ValueError as e:
            self.transcript.add_error(str(e))
            return

        models: List[str] = []
        key = resolve_key(provider_id)
        if key:
            result = validate_key(provider, key)
            if result.ok:
                models = rank_models(provider, result.models)
            else:
                models = list(provider.preferred_models)
        else:
            settings = AuthConfig().get(provider_id)
            if settings and settings.model:
                models = [settings.model]
            models += [m for m in provider.preferred_models if m not in models]

        if current and current not in models:
            models.insert(0, current)
        if not models:
            self.transcript.add_info("No models available.")
            return
        items = [(m, f"{'* ' if m == current else ''}{m}") for m in models]
        item_list(
            self.app,
            f"Switch model ({provider_id})",
            items,
            on_pick=self._on_model_picked,
        )

    def _on_model_picked(self, model: Optional[str]) -> None:
        if not model or self._client is None:
            return
        from opennote.auth.config import AuthConfig

        AuthConfig().set_model(self._client.provider_id, model)
        try:
            self._client = get_client(self._client.provider_id)
        except (ChatError, ValueError) as e:
            self.transcript.add_error(str(e))
            return
        self._sync_meta()
        self.transcript.add_info(f"Model switched to {model}.")

    def action_open_palette(self) -> None:
        from opennote.tui.dialogs import CommandPalette

        self.app.push_screen(CommandPalette(self, on_done=self._on_palette_done))

    def _on_palette_done(self, value: Optional[str]) -> None:
        pass
