"""Main chat screen: banner + transcript + prompt bar.

Owns the notebook/session state, runs agent turns and searches in worker
threads, and translates slash commands through the command registry.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Callable, Dict, List, Optional

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.screen import Screen

from opennote.agents.loop import TurnCancelled, agent_turn
from opennote.agents.session import (
    append_messages,
    list_sessions,
    load_session,
    new_session,
    save_session,
)
from opennote.chat.ask import AskResult
from opennote.chat.client import ChatError, default_provider, get_client
from opennote.notebooks import Notebook, NotebookManager
from opennote.retrieval.retriever import Retriever, render_results
from opennote.tui.commands import lookup, make_commands
from opennote.tui.dialogs import HelpDialog, InfoDialog, ask_input, item_list
from opennote.tui.theme import DEFAULT, LIGHT, Palette
from opennote.tui.widgets.prompt import MODE_LABELS, MODES, PromptBar, PromptInput
from opennote.tui.widgets.transcript import Transcript

logger = logging.getLogger("opennote.tui.chat")


class TurnResult(Message):
    """A completed agent turn (worker thread -> UI)."""

    def __init__(self, question: str, answer: str, provider_id: str, model: str) -> None:
        super().__init__()
        self.question = question
        self.answer = answer
        self.provider_id = provider_id
        self.model = model


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
    def __init__(self, target: str, count: int) -> None:
        super().__init__()
        self.target = target
        self.count = count


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
    """The single full-screen chat view (banner, transcript, prompt)."""

    BINDINGS = [
        Binding("ctrl+c,ctrl+q", "quit", "Quit", priority=True),
        Binding("escape", "interrupt", "Interrupt", priority=True),
        Binding("tab", "cycle_mode", "Cycle mode", priority=True),
        Binding("ctrl+p", "open_palette", "Command palette", priority=True),
    ]

    def __init__(
        self,
        notebook_name: str = "default",
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
        self.notebook_name = notebook_name
        self.palette = palette or DEFAULT
        self._manager = manager or NotebookManager()
        self._provider_id = provider_id
        self._client = client
        self._retriever = retriever
        self._ingest_fn = ingest_fn
        self._cancel_flag = False
        self.mode = "ask"

        # Resolve notebook + session eagerly so the screen is deterministic.
        self.notebook: Optional[Notebook] = None
        self.session: Optional[Dict] = None

    # -- composition -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Transcript(id="transcript")
        yield PromptBar(id="prompt-bar")

    def _open_palette(self) -> None:
        self.action_open_palette()

    def _reveal_transcript(self) -> None:
        if not self.has_class("has-history"):
            self.add_class("has-history")

    def on_mount(self) -> None:
        self.prompt = self.query_one("#prompt-bar", PromptBar)
        self.transcript = self.query_one("#transcript", Transcript)
        self.transcript.palette = self.palette
        self.commands = make_commands(self)
        self.prompt.set_commands(self.commands)
        self._resolve_notebook()
        self._resolve_provider()
        self._resolve_session()
        self.transcript.add_banner(self.palette)
        if self.session and self.session.get("messages"):
            self._reveal_transcript()
            self._render_history()
        self.prompt.set_mode(self.mode)
        self.prompt.focus_input()

    def _enter_studio(self, _arg: str = "") -> None:
        self.mode = "studio"
        self.prompt.set_mode(self.mode)
        self.transcript.add_info(
            "Studio mode: pick a generator command (/mindmap, /study, /faq, /briefing, /timeline, /suggest, /audio, /video) or type a topic to open the generator menu."
        )
        self._open_studio_menu()

    # -- setup -------------------------------------------------------------
    def _resolve_notebook(self) -> None:
        # Fresh directory -> fresh notebook: derive name from cwd when default
        if self.notebook_name == "default":
            try:
                from pathlib import Path as _P
                from opennote.notebooks import validate_notebook_name as _v

                cand = _P.cwd().name.strip()
                if cand:
                    try:
                        _v(cand)
                        self.notebook_name = cand
                    except ValueError:
                        pass
            except Exception:
                pass
        try:
            self.notebook = self._manager.get(self.notebook_name)
        except (KeyError, ValueError):
            try:
                self.notebook = self._manager.create(self.notebook_name)
            except (FileExistsError, ValueError) as e:
                self.transcript.add_error(f"Cannot create notebook: {e}")
                self.notebook = None

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

    def _resolve_session(self) -> None:
        if self.notebook is None:
            return
        # Surgical: bare `opennote` always starts fresh session (no auto-resume)
        pid = self._client.provider_id if self._client else ""
        model = self._client.model if self._client else ""
        self.session = new_session(self.notebook, pid, model)
        self._sync_meta()

    def _sync_meta(self) -> None:
        pid = self._client.provider_id if self._client else (self.session or {}).get("provider_id", "")
        model = self._client.model if self._client else (self.session or {}).get("model", "")
        if not pid:
            pid = "no provider"
        self.prompt.set_model(model, pid)

    # -- history rendering -------------------------------------------------

    def _render_history(self) -> None:
        if not self.session:
            return
        for msg in self.session.get("messages", []):
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
        self._cancel_flag = False
        self.prompt.set_busy("Searching sources...")
        self._run_ask(question)

    @work(thread=True, exclusive=True, group="turn")
    async def _run_ask(self, question: str) -> None:
        notebook = self.notebook
        session_id = (self.session or {}).get("id", "")
        history = list((self.session or {}).get("messages", [])) if self.session else []
        provider_id = self._client.provider_id if self._client else None
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
        if self.session is not None:
            try:
                self.session = append_messages(notebook, session_id, agent.messages)
            except Exception as e:
                self.app.call_from_thread(self.post_message, TurnFailed(str(e)))
                return
        result: AskResult = agent.result
        self.app.call_from_thread(
            self.post_message,
            TurnResult(question, result.answer, result.provider_id, result.model),
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

    # -- studio: artifact generators (L66/L80/L82) ---------------------------

    STUDIO_GENERATORS = [
        ("mindmap", "Mind map", "create_mindmap"),
        ("study", "Study guide", "make_study_guide"),
        ("faq", "FAQ", "make_faq"),
        ("briefing", "Briefing", "make_briefing"),
        ("timeline", "Timeline", "make_timeline"),
        ("suggest", "Suggested questions", "make_suggested_questions"),
    ]

    def _start_studio(self, text: str) -> None:
        """Run a studio generator. *text* is the topic/question; the generator
        kind comes from the submenu (or a direct slash command)."""
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
        """Build a slash-command handler that runs generator *kind* directly."""

        def handler(arg: str) -> None:
            topic = arg.strip()
            if not topic:
                self.transcript.add_info(f"Usage: /{kind} <topic or question>")
                return
            self._studio_topic = topic
            self._on_studio_picked(kind)

        return handler

    def _start_studio_palette(self, kind: str) -> None:
        """Palette helper: ask for a topic, then run generator *kind* directly."""
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
            from opennote.video import save_video_artifact

            art_path = save_video_artifact(topic, notebook.artifacts_dir, notebook.name)
            detail = str(art_path)
        except Exception as e:
            logger.exception("Video generation failed")
            self.app.call_from_thread(self.post_message, StudioFailed(str(e)))
            return
        self.app.call_from_thread(self.post_message, StudioResultMsg("video", detail))

    def _generate_studio_artifact(self, kind: str, topic: str, notebook: Notebook) -> str:
        """Run one studio generator with retrieved context and save the artifact.

        Delegates prompt rendering to ``opennote.artifacts`` templates (single owner).
        """
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

        # F2 fix: Retriever construction inside try so empty-notebook shows friendly error
        try:
            retriever = self._retriever or Retriever(notebook, top_k=8)
            results = retriever.search(topic)
        except ValueError:
            self.transcript.add_error("No sources indexed yet. Run /ingest first.")
            return ""

        # Build grounded context with [n] citations and full content for LLM prompts
        context_parts: List[str] = []
        for i, r in enumerate(results, start=1):
            fn = r.metadata.get("filename", "unknown")
            citation = f"[{i}] {fn}"
            chunk = r.content[:800].replace("\n", " ")
            context_parts.append(f"{citation}: {chunk}")
        context_text = "\n".join(context_parts) if context_parts else "No context available."

        # Kind -> template rendering (single owner: artifacts.py)
        prompts = {
            "study": generate_study_guide(topic, context_text),
            "faq": generate_faq(context_text),
            "briefing": generate_briefing(topic, context_text),
            "timeline": generate_timeline(topic, context_text),
            "suggest": generate_suggested_questions(topic, context_text),
        }
        if kind == "mindmap":
            # Mindmap uses hierarchical outline; build via artifacts helper when possible
            items = [f"{r.metadata.get('filename','unknown')}: {r.content[:60]}" for r in results[:8]]
            if self._client is None:
                # LLM-free mindmap fallback via artifacts helper
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

        # LLM-free fallback: consolidated digest table (deduplicated)
        fallback_templates = {
            "study": f"Study guide for **{topic}** based on {len(results)} chunks.\n" + "\n".join(
                f"• {r.metadata.get('filename','unknown')}: {r.content[:80]}" for r in results[:5]),
            "faq": f"FAQ for **{topic}** from {len(results)} sources.\n" + "\n".join(
                f"Q: What does {r.metadata.get('filename','unknown')} say about {topic}?\nA: {r.content[:100]}" for r in results[:5]),
            "briefing": f"Briefing for **{topic}** from {len(results)} sources.\n" + "\n".join(
                f"• {r.metadata.get('filename','unknown')}: {r.content[:120]}" for r in results[:5]),
            "timeline": f"Timeline for **{topic}** from {len(results)} sources.\n" + "\n".join(
                f"- {r.metadata.get('filename','unknown')}: {r.content[:80]}" for r in results[:8]),
            "suggest": f"Suggested questions for **{topic}** from {len(results)} sources.\n" + "\n".join(
                f"• Suggested question from {r.metadata.get('filename','unknown')}" for r in results[:5]),
            "mindmap": f"Mind‑map overview for **{topic}**.\n" + "\n".join(
                f"• {r.metadata.get('filename','unknown')}: {r.content[:50]}" for r in results[:5]),
        }
        body = fallback_templates.get(kind, "")
        art = save_artifact(kind=kind, title=topic, body=body, notebook_dir=notebook.directory)
        return str(art.path)

    def on_turn_result(self, msg: TurnResult) -> None:
        self.transcript.add_answer(msg.answer)
        self._sync_meta()
        self.prompt.set_idle()

    def on_turn_failed(self, msg: TurnFailed) -> None:
        self.transcript.add_error(msg.error)
        self.prompt.set_idle()

    def on_turn_cancelled_msg(self, msg: TurnCancelledMsg) -> None:
        self.transcript.add_info("Interrupted.")
        self.prompt.set_idle()

    def on_search_result_msg(self, msg: SearchResultMsg) -> None:
        self.transcript.add_info(f"Search: {msg.question}")
        self.transcript.write(msg.text)
        self.transcript.write("")
        self.prompt.set_idle()

    def on_search_failed(self, msg: SearchFailed) -> None:
        self.transcript.add_error(msg.error)
        self.prompt.set_idle()

    def on_studio_result_msg(self, msg: StudioResultMsg) -> None:
        self.transcript.add_info(f"Studio {msg.label}: {msg.detail}")
        self.prompt.set_idle()

    def on_studio_failed(self, msg: StudioFailed) -> None:
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

    def _clear_transcript(self, _arg: str = "") -> None:
        self.transcript.clear()
        self.transcript.add_banner(self.palette)

    def _open_sessions_dialog(self, _arg: str = "") -> None:
        if self.notebook is None:
            return
        sessions = list_sessions(self.notebook)
        if not sessions:
            self.transcript.add_info("No saved sessions.")
            return
        items = [
            (
                s["id"],
                f"{'*' if self.session and s['id'] == self.session.get('id') else ' '} "
                f"{s['id'][:8]}… model={s.get('model')} "
                f"msgs={len(s.get('messages', []))} updated={s.get('updated', '')[:19]}",
            )
            for s in sessions
        ]
        item_list(self.app, "Sessions", items, on_pick=self._on_session_picked)

    def _on_session_picked(self, session_id: Optional[str]) -> None:
        if session_id and self.notebook is not None:
            self._resume_session(session_id)

    def _resume_session(self, session_id: str) -> None:
        session_id = session_id.strip()
        if not session_id:
            self.transcript.add_info("Usage: /resume <session-id> (or /sessions)")
            return
        if self.notebook is None:
            return
        loaded = load_session(self.notebook, session_id)
        if loaded is None:
            self.transcript.add_error(f"No session with id '{session_id}'.")
            return
        self.session = loaded
        self.transcript.clear()
        self.transcript.add_banner(self.palette)
        self._render_history()
        self._sync_meta()
        self.transcript.add_info(f"Resumed session {session_id[:8]}…")

    def _resume_last(self, _arg: str = "") -> None:
        if self.notebook is None:
            return
        sessions = list_sessions(self.notebook)
        if not sessions:
            self.transcript.add_info("No saved sessions.")
            return
        self._resume_session(sessions[0]["id"])

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
        self._persist_provider_choice()
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
            items.append((p.id, f"{p.label} · {model}"))
        if not items:
            self.transcript.add_info("No provider configured. Run 'opennote auth add <provider>'.")
            return
        item_list(self.app, "Provider", items, on_pick=self._on_provider_picked)

    def _on_provider_picked(self, pid: Optional[str]) -> None:
        if pid:
            self._switch_provider(pid)

    def _persist_provider_choice(self) -> None:
        if self.session is not None:
            self.session["provider_id"] = self._client.provider_id
            self.session["model"] = self._client.model
            if self.notebook is not None:
                save_session(self.notebook, self.session)

    def _export_session(self, _arg: str = "") -> None:
        if self.notebook is None or self.session is None:
            self.transcript.add_info("Nothing to export.")
            return
        session = self.session
        out_dir = self.notebook.directory / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"session-{session['id']}.md"
        lines = [
            f"# Session {session['id'][:8]}",
            f"\n- provider: {session.get('provider_id', '')} · {session.get('model', '')}",
            f"- updated: {session.get('updated', '')}\n",
        ]
        for msg in session.get("messages", []):
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
        self.transcript.add_info(f"Exported {len(session.get('messages', []))} messages to {path}")

    # -- U4: undo / details -------------------------------------------------

    def _undo_last_turn(self, _arg: str = "") -> None:
        if self.prompt.busy:
            self.transcript.add_error("Busy.")
            return
        if self.notebook is None or self.session is None:
            self.transcript.add_info("Nothing to undo.")
            return
        messages = self.session.get("messages", [])
        last_user = -1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_user = i
                break
        if last_user == -1:
            self.transcript.add_info("Nothing to undo.")
            return
        dropped = len(messages) - last_user
        self.session["messages"] = messages[:last_user]
        save_session(self.notebook, self.session)
        self.transcript.clear()
        self.transcript.add_banner(self.palette)
        self._render_history()
        self.prompt.set_idle()
        self.transcript.add_info(f"Undid the last turn ({dropped} message(s) removed).")

    def _show_details(self, _arg: str = "") -> None:
        lines = ["Session:"]
        if self.session:
            s = self.session
            lines.append(f"  id:      {s.get('id', '')}")
            lines.append(f"  created: {s.get('created', '')[:19]}")
            lines.append(f"  updated: {s.get('updated', '')[:19]}")
            lines.append(f"  provider:{s.get('provider_id', '')} · {s.get('model', '')}")
            lines.append(f"  messages:{len(s.get('messages', []))}")
        else:
            lines.append("  (no session)")
        lines.append("Notebook:")
        if self.notebook:
            lines.append(f"  name:      {self.notebook.name}")
            lines.append(f"  directory: {self.notebook.directory}")
            lines.append(f"  embed:     {self.notebook.embed_model}")
            lines.append(f"  sources:   {len(self.notebook.sources)}")
        else:
            lines.append("  (no notebook)")
        lines.append("Client:")
        if self._client:
            lines.append(f"  {self._client.provider_id} · {self._client.model}")
        else:
            lines.append("  (no provider configured)")
        self.app.push_screen(InfoDialog("Details", "\n".join(lines)))

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

    def _new_session(self, _arg: str = "") -> None:
        if self.notebook is None:
            return
        pid = self._client.provider_id if self._client else ""
        model = self._client.model if self._client else ""
        self.session = new_session(self.notebook, pid, model)
        self.transcript.clear()
        self.transcript.add_banner(self.palette)
        self.prompt.set_idle()

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

    def _open_artifact(self, arg: str = "") -> None:
        """Open an artifact file (or the notebook's artifacts dir) with the
        system default application."""
        if self.notebook is None:
            self.transcript.add_error("Notebook is not available.")
            return
        artifacts_dir = self.notebook.directory / "artifacts"
        target = artifacts_dir
        arg = arg.strip()
        if arg:
            # Only allow paths inside the notebook's artifacts dir (L59-style guard).
            candidate = (artifacts_dir / arg).resolve()
            try:
                candidate.relative_to(artifacts_dir.resolve())
            except ValueError:
                self.transcript.add_error("Path escapes the notebook's artifacts directory.")
                return
            if candidate.is_file():
                target = candidate
        if not target.exists():
            self.transcript.add_info("No artifacts yet. Use /mindmap, /study, /faq, etc.")
            return
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

    # -- U3: notebooks -----------------------------------------------------

    def _open_notebooks_dialog(self, _arg: str = "") -> None:
        if self.prompt.busy:
            self.transcript.add_error("Busy.")
            return
        notebooks = self._manager.list()
        if not notebooks:
            self.transcript.add_info("No notebooks yet. Use /create <name>.")
            return
        items = [
            (
                nb.name,
                f"{'*' if self.notebook and nb.name == self.notebook.name else ' '} "
                f"{nb.name} · {len(nb.sources)} sources",
            )
            for nb in notebooks
        ]
        item_list(self.app, "Notebooks", items, on_pick=self._on_notebook_picked)

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
        self._resolve_session()
        self.transcript.clear()
        self.transcript.add_banner(self.palette)
        self._render_history()
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
            notebook = self._manager.create(name)
        except (FileExistsError, ValueError) as e:
            self.transcript.add_error(str(e))
            return
        self.notebook_name = notebook.name
        self.notebook = notebook
        self._resolve_session()
        self.transcript.clear()
        self.transcript.add_banner(self.palette)
        self.transcript.add_info(f"Created notebook: {notebook.name}")
        return

    def _create_notebook_dialog(self) -> None:
        """Palette helper: ask for a notebook name, then create it."""
        ask_input(
            self.app,
            "New Notebook",
            "Name for the new notebook:",
            placeholder="my-notebook",
            on_submit=self._on_create_notebook_dialog,
        )

    def _on_create_notebook_dialog(self, name: Optional[str]) -> None:
        if name:
            self._create_notebook(name)

    # -- U3: ingest --------------------------------------------------------

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
            if self._ingest_fn is not None:
                count = self._ingest_fn(notebook, target)
            else:
                from opennote.ingest.pipeline import ingest as _ingest

                count = _ingest(notebook, target)
        except Exception as e:
            logger.exception("Ingest failed")
            self.app.call_from_thread(self.post_message, IngestFailed(str(e)))
            return
        self.app.call_from_thread(
            self.post_message, IngestResultMsg(target, int(count))
        )

    def on_ingest_result_msg(self, msg: IngestResultMsg) -> None:
        self.transcript.add_info(f"Indexed {msg.count} chunk(s) from {msg.target}")
        self.prompt.set_idle()

    def on_ingest_failed(self, msg: IngestFailed) -> None:
        self.transcript.add_error(f"Ingest failed: {msg.error}")
        self.prompt.set_idle()

    # -- U3: auth / connect -------------------------------------------------

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
            bits = ["key ✓" if key else "no key"]
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
                self.transcript.add_error("Invalid API key — could not fetch models.")
            elif result.error == "network":
                self.transcript.add_info(
                    "Could not reach the model catalog — using default models. "
                    "Run 'opennote auth verify <provider>' to refresh."
                )
            else:
                self.transcript.add_info(
                    f"Model catalog returned an error ({result.error}) — using default models."
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
        self._persist_provider_choice()
        self._sync_meta()
        self.transcript.add_info(f"Connected {pid} ({client.model}).")

    def _open_model_dialog(self, _arg: str = "") -> None:
        """Pick a model for the current provider (live catalog when reachable)."""
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
        self._persist_provider_choice()
        self._sync_meta()
        self.transcript.add_info(f"Model switched to {model}.")

    def action_open_palette(self) -> None:
        """Open the Ctrl+P command palette."""
        from opennote.tui.dialogs import CommandPalette

        self.app.push_screen(CommandPalette(self, on_done=self._on_palette_done))

    def _on_palette_done(self, value: Optional[str]) -> None:
        """Called when the palette is dismissed."""
        pass