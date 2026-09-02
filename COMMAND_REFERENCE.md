# OpenNote TUI Command Reference

This document lists all commands available in the OpenNote TUI, organized by category.
Commands can be entered at the prompt (prefixed with `/`) or accessed via the Ctrl+P
palette.

---

## Slash Commands (Prompt)

### Session
| Command | Aliases | Usage | Description |
|---------|---------|-------|-------------|
| `/help` | | `/help` | Show this help dialog |
| `/exit` | `quit`, `q` | `/exit` | Quit the TUI |
| `/new` | | `/new` | Start a fresh session |
| `/clear` | | `/clear` | Clear the transcript |
| `/sessions` | | `/sessions` | List sessions and open resume dialog |
| `/resume` `<id>` | | `/resume abc123` | Resume a specific session |
| `/continue` | | `/continue` | Continue the most recent session |

### Provider
| Command | Usage | Description |
|---------|-------|-------------|
| `/model` `<provider>` | `/model groq` | Switch LLM provider |
| `/auth` | `/auth` | Show provider/key status |
| `/connect` `<provider>` | `/connect groq` | Connect a provider (key + model) |

### Notebook
| Command | Usage | Description |
|---------|-------|-------------|
| `/notebooks` | `/notebooks` | List notebooks and open switch dialog |
| `/notebook` `<name>` | `/notebook my-notebook` | Switch to a notebook |
| `/create` `<name>` | `/create my-notebook` | Create a notebook |
| `/ingest` `<path\|url>` | `/ingest report.md` | Index a file, folder, or URL |

### Studio (Artifact Generators)
| Command | Usage | Description |
|---------|-------|-------------|
| `/studio` | `/studio` | Enter studio mode |
| `/mindmap` `<topic>` | `/mindmap quarterly results` | Generate a mind map |
| `/study` `<topic>` | `/study quarterly results` | Generate a study guide |
| `/faq` `<topic>` | `/faq quarterly results` | Generate an FAQ |
| `/briefing` `<topic>` | `/briefing quarterly results` | Generate a briefing |
| `/timeline` `<topic>` | `/timeline quarterly results` | Generate a timeline |
| `/suggest` `<topic>` | `/suggest quarterly results` | Suggest follow-up questions |
| `/audio` `<text>` | `/audio Once upon a time` | Narrate text as audio |
| `/video` `<topic>` | `/video quarterly results` | Narrate a slideshow video |
| `/open` `[file]` | `/open` / `/open my-report.pdf` | Open an artifact or the artifacts folder |

### Appearance
| Command | Usage | Description |
|---------|-------|-------------|
| `/theme` `<dark\|light>` | `/theme light` | Switch dark/light theme |

### General
| Command | Usage | Description |
|---------|-------|-------------|
| `/palette` | `/palette` | Open the command palette (Ctrl+P) |

### Local (Offline GGUF)
| Command | Usage | Description |
|---------|-------|-------------|
| `opennote local add <path> [name]` | `opennote local add D:\models\qwen.gguf my-model --n-ctx 4096` | Register a GGUF file (validates name, copies metadata to `~/.opennote/local.json`) |
| `opennote local list` | `opennote local list` | List registered local models (`*` marks active) |
| `opennote local use <name>` | `opennote local use my-model` | Set active local model |
| `opennote local remove <name>` | `opennote local remove my-model` | Unregister a model |

---

## Ctrl+P Palette (Command Palette)

**Open:** Press `Ctrl+P` from the chat prompt.

The palette is an searchable list with category sections. You can:
- **Type** to filter entries (matches title, description, section, or keywords).
- **Up/Down** to navigate highlights (skips section headers).
- **Enter** to run the highlighted entry's action or open its submenu.
- **Esc** to dismiss.

### Palette Sections

#### Session
- **New Session** — Start a fresh conversation
- **Switch Session** — Pick from saved sessions
- **Export Session** — Save this conversation as Markdown
- **Undo Last Turn** — Remove the last question and answer

#### Mode
- **Ask Mode** — Grounded Q&A with citations
- **Search Mode** — LLM-free keyword + vector search
- **Studio Mode** — Generate mind maps, guides, audio, video

#### Provider
- **Connect Provider** — Add an API key and pick a model (opens provider picker → key input → model picker)
- **Switch Provider** — Change the active provider
- **Switch Model** — Pick a different model for the current provider (live catalog when reachable, offline fallback)

#### Notebook
- **Switch Notebook** — Open another notebook (sources shown)
- **New Notebook** — Create a notebook (opens a name-input dialog)

#### Studio
- **Mind Map** — Generate a mind map from a topic (opens topic-input dialog)
- **Study Guide** — Generate a study guide from a topic
- **FAQ** — Generate an FAQ from a topic
- **Briefing** — Generate a briefing from a topic
- **Timeline** — Generate a timeline from a topic
- **Suggested Questions** — Get suggested questions on a topic

#### Appearance
- **Toggle Theme** — Switch between dark and light

#### Help
- **Help** — Show keyboard shortcuts and commands

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+P` | Open command palette |
| `Tab` | Cycle modes: Ask → Search → Studio → Ask |
| `Ctrl+C` / `Ctrl+Q` | Quit the TUI |
| `Escape` | Interrupt / dismiss current modal |
| `Ctrl+P` + type + `Enter` | Run palette highlighted entry |
| `Ctrl+P` + `Esc` | Dismiss palette |

---

## How to Use the Main Workflows

### 1. Switching Modes
- Press `Tab` to cycle: **Ask** → **Search** → **Studio** → **Ask**.
- Or enter `/ask`, `/search`, `/studio` at the prompt.

### 2. Connecting a Provider
- `/connect groq` → opens provider picker → enter API key → model picker (live catalog or offline fallback).
- Or from the palette: **Connect Provider** → pick provider → enter key → pick model.

### 3. Creating / Switching Notebooks
- `/notebooks` → pick from list.
- `/create my-notebook` → opens name-input dialog.
- Palette: **New Notebook** → enter name.

### 4. Generating Studio Artifacts
- `/studio` → enters studio mode, shows generator menu.
- Or palette: **Studio Mode** → pick a generator (mind map, study, FAQ, etc.) → enter topic.
- Or palette: individual entries (**Mind Map**, **Study Guide**, etc.) → enter topic.

### 5. Using the Prompt in Studio Mode
- After `/studio` or entering studio mode, type a topic or question.
- Commands like `/mindmap`, `/study`, etc. accept a topic after the command.
- Shift+Enter adds a newline within the prompt.

### 6. Help & Shortcuts
- `/help` → modal dialog with "Press Ctrl+P to see all available actions and commands in any context."
- `Ctrl+P` → full palette with searchable entries and keyboard navigation.

---
*Generated from the OpenNote TUI codebase (336/336 tests passing).*