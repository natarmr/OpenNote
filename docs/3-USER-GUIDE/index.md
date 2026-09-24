# User Guide — Every Command with Examples

Two surfaces: the **CLI** (`opennote <command>`) for scripts and one-shots, and the **TUI** (bare `opennote`) for interactive work. Both operate on notebooks in `./.opennote`.

## Notebooks

```bash
opennote create <name> [--model BAAI/bge-small-en-v1.5]
opennote list
opennote rename <old> <new>
opennote delete <name>
```

## Ingest sources (max 5 per notebook)

```bash
opennote ingest paper.pdf --notebook <name>
opennote ingest ./docs/ --notebook <name>          # a directory
opennote ingest https://example.com/post --notebook <name>
opennote ingest paper.pdf -n <name> --parser fallback --ocr --force
opennote remove <source-substring> --notebook <name>   # free a slot
```

## Search and ask (cited)

```bash
opennote search "<query>" --notebook <name> --top-k 3 [--source file.pdf] [--no-bm25]
opennote ask "<question>" --notebook <name> [--provider groq] [--top-k 5] [--multihop]
opennote golden golden.tsv --notebook <name> --top-k 5   # recall@k check (TSV: query, source, pages)
```

## Providers, tunnel endpoints, and local models (BYOK)

```bash
opennote auth add anthropic        # prompts for key, validates live, auto-picks a model
opennote auth list                 # providers, key source, selected models, endpoint overrides
opennote auth models openai        # live chat models; --set <id> to change default
opennote auth verify               # re-validate stored keys
opennote auth remove groq

# Remote tunnel (Colab/Kaggle): OpenAI-compat server (llama-cpp/ollama/vLLM) + tunnel URL
opennote auth endpoint set openai https://abc.trycloudflare.com/v1   # normalises /models → /v1
opennote auth add openai --no-verify                                  # keyless tunnels: enter `not-needed`
opennote auth models openai --set /content/models/qwen.gguf            # pick the tunnel's model id
opennote ask "..." --provider openai   # now routes to the tunnel

opennote local add D:\models\qwen.gguf my-qwen --n-ctx 4096
opennote local list                # * marks active
opennote local use my-qwen
opennote ask "..." --provider local
```

## Studio artifacts and the TUI

```bash
opennote artifacts export --notebook <name>              # JSON dump of saved artifacts
opennote artifacts show <substring> -n <name> --tree     # render a mind-map in-terminal (others as Markdown)
opennote artifacts check -n <name> --topic "<t>"         # live self-check: generates + renders every studio mode

# TUI tip: pasting a https://… URL at the “API key” prompt for /connect is handled as an endpoint, not a key
```

In the TUI (`opennote`), `Tab` cycles `ask → search → studio`:
`/mindmap /study /faq /briefing /timeline /suggest /audio /video` generate,
`/open` views artifacts (mind-maps as a tree, others as Markdown; `artifacts show --tree` on CLI),
`/use <skill> <task>` applies an installed skill for the next answer,
`/model /theme /context /skills /skill /plugins /agents /agent /capabilities` manage the session
(`Tab` autocomplete, `Ctrl+P` palette, `/snake` waiting-room game).

## 15-minute checklist

1. `create` a notebook (1 min) — one topic per notebook.
2. `ingest` one file (3 min) — wait for `Indexed N chunk(s)`.
3. `search` a keyword from the file (2 min) — confirm `[n]` citations appear.
4. `ask` one question (3 min) — verify each claim traces to a citation.
5. TUI: `/mindmap <topic>`, then `/open` (6 min) — first studio artifact.

## Which tool for which task?

| Task | Use |
|---|---|
| Explore with follow-ups | TUI `ask` mode / `opennote chat` |
| One comprehensive answer | `opennote ask` (add `--multihop` for hard questions) |
| Keyword lookup / verify a quote | `opennote search` |
| Study material from sources | Studio generators (`/study`, `/faq`, …) |
| Hands-free review | `/audio` (podcast-style) or `/video` (slideshow) |
| Regression-check retrieval | `opennote golden` |
| Apply an installed skill | `/use <skill> <task>` or `opennote skills show <name>` |

## Common mistakes

| Mistake | Fix |
|---|---|
| One notebook for everything | Separate notebooks per topic |
| Generic answers | Ask about what's in the sources, check citations |
| Huge PDFs ingest slowly | Ingest once; re-runs are hash-skipped |
| Full indexes, can't add sources | `/remove` / `opennote remove` to free one of the 5 slots |
| Keyword-only thinking | Hybrid BM25+vectors is default; tune with `--bm25-alpha` |
