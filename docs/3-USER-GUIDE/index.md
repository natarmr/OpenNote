# User guide

Two ways in: the **CLI** (`opennote <command>`) for scripts and quick one-offs, and the **TUI** (just `opennote`) for interactive work. Both read and write the same notebooks.

## Notebooks

```bash
opennote create my_notebook
opennote list
opennote rename old_name new_name
opennote delete my_notebook
```

## Bring in sources (up to five per notebook)

```bash
opennote ingest paper.pdf -n my_notebook
opennote ingest ./docs/ -n my_notebook              # a whole folder
opennote ingest https://example.com/post -n my_notebook
opennote ingest paper.pdf -n my_notebook --parser fallback --ocr --force
opennote remove paper --notebook my_notebook        # free a slot by name fragment
```

## Search and ask

```bash
opennote search "attention mechanism" -n my_notebook --top-k 3
opennote ask "What does the paper claim?" -n my_notebook --provider groq
opennote ask "Compare X and Y" -n my_notebook --multihop
opennote golden golden.tsv -n my_notebook --top-k 5   # recall@k check
```

Each result shows a `[n]` citation with the file and page — click nothing, just cross-check the answer against the source.

## Providers, keys, and defaults

```bash
opennote auth add groq              # prompts for a key, validates, picks a model
opennote auth add groq --env GROQ_API_KEY   # read the key from an env var instead
opennote auth list                  # provider, key source, model, endpoint, validation
opennote auth models groq           # live model list; --set <id> changes the stored model
opennote auth verify                # re-check all stored keys
opennote auth remove groq

opennote config                     # resolved opennote.json
opennote config --path              # which config files are loaded
opennote config --effective         # every provider's key source + the active default
```

Keys live in your OS keychain. The fallback order is keychain → the provider's env var (`GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, `MODEL_API_KEY` for Meta). You don't even need `auth add` if the env var is already set.

OpenNote remembers the provider/model you last used, so reopening it returns you there. Switching in a session doesn't silently change your configured default; set `model:` in `opennote.json` if you want a permanent default. And if the key for a remembered provider is removed, OpenNote reports "no provider connected" instead of showing a stale selection.

Custom OpenAI-compatible endpoint (Colab/Kaggle tunnels and similar):

```bash
opennote auth endpoint set openai https://abc.trycloudflare.com/v1
opennote auth add openai --no-verify              # for keyless tunnels
opennote auth models openai --set /content/models/qwen.gguf
opennote ask "..." --provider openai
```

Fully offline with a local GGUF model:

```bash
opennote local add D:\models\qwen.gguf my-qwen --n-ctx 4096
opennote local use my-qwen
opennote ask "..." --provider local
```

## Inside the TUI

`Tab` cycles **ask → search → studio → tutor → socratic → curriculum**. The last three are built-in study agents; picking one arms it automatically. `/reset-agent` returns to plain `ask`.

The provider commands are deliberately separate:

| Command | What it does |
|---|---|
| `/connect [provider]` | Add a key and pick a model (searchable list, shows key source) |
| `/model [provider]` | Switch the active provider |
| `/switch-model` | Switch the model for the current provider |
| `/endpoint` | Set a custom endpoint URL for a provider |
| `/auth [all]` | Show configured providers (add `all` for the full catalog) |

Other useful ones: `/mindmap`, `/study`, `/faq`, `/briefing`, `/timeline`, `/suggest`, `/audio`, `/video` generate artifacts; `/open` views them; `/use <skill> <task>` applies a skill; `/theme`, `/context`, `/skills`, `/plugins`, `/agents` manage the session; `/agent <name>` switches study agent; `/capabilities` shows what's enabled.

`F1` opens help, `Ctrl+P` searches every command, `Esc` interrupts a running turn or closes a dialog.

## Study tools

Three built-in agents — `socratic`, `tutor`, `curriculum` — work across notebooks. Use them from the TUI's `Tab` cycle or `/agent <name>`, or from the CLI:

```bash
opennote learn profile -n my_notebook
opennote learn quiz "transformer attention" -n my_notebook
opennote learn review -n my_notebook
opennote learn grade <card-id> 4 -n my_notebook       # 0-5; reschedules via SM-2
opennote learn assess "What is BM25?" "It ranks by TF-IDF" -n my_notebook
opennote learn plan -n my_notebook
```

All learner state is local JSON under `<notebook>/learning/`.

## Artifacts

```bash
opennote artifacts export -n my_notebook
opennote artifacts show mindmap -n my_notebook --tree
opennote artifacts check -n my_notebook --topic "retrieval"   # generates + renders every studio mode
```

## A 15-minute first run

1. `create` a notebook — one topic per notebook.
2. `ingest` one file — wait for `Indexed N chunk(s)`.
3. `search` a word you know is in it — confirm citations show up.
4. `ask` a question — check that claims trace back to sources.
5. In the TUI, `/mindmap <topic>` then `/open` to see your first artifact.

## Which tool when?

| Goal | Use |
|---|---|
| Explore with follow-ups | TUI `ask` mode or `opennote chat` |
| One thorough answer | `opennote ask` (add `--multihop` for hard ones) |
| Verify a quote or keyword | `opennote search` |
| Study material from sources | `/study`, `/faq`, `/mindmap`, … |
| Hands-free review | `/audio` or `/video` |
| Check retrieval quality | `opennote golden` |
| Apply a skill | `/use <skill> <task>` |
| Practice active recall | `tutor` / `socratic` / `learn` commands |

## Common snags

| It feels like… | Actually… |
|---|---|
| Answers are vague | Ask about what's in the sources, then check the citations |
| Can't add another source | Five per notebook — `remove` one first |
| A remembered provider shows but has no key | Its key was removed; run `/connect` again |
| Big PDFs are slow | Ingest once; unchanged files are skipped next time |
| Keywords miss things | Hybrid BM25+vectors is on by default; tune with `--bm25-alpha` |
