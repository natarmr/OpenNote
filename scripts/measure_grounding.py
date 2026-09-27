"""Measure the claim-coverage distribution the grounding validator actually sees.

L160 added a second condition to ``validate_claim``: the claim's own prose must be
carried by the cited chunk at ``_TEXT_SUPPORT_THRESHOLD``. That threshold was
picked by hand, and the calibration corpus
(``tests/security/test_grounding_calibration.py``) shows whole-sentence coverage
cannot separate abstractive paraphrase from blended fabrication. But that corpus
is synthetic -- hand-written to probe the boundary, not sampled from usage.

This tool answers the question that actually decides L175: **what does the model
really submit?** It runs real agent turns and reports

  * how often the model uses ``submit_grounded_answer`` at all, versus answering
    in prose (the prose path uses a different, untouched validator, so the
    blast radius depends entirely on this ratio);
  * the distribution of claim-coverage values that reach the tier-2 check;
  * what would have happened at each candidate threshold.

It changes no behaviour. It wraps ``_text_coverage``, ``filter_grounded_answer``
and ``validate_freeform_answer`` to record their inputs and returns; the
validator's own logic is untouched and the recorded values are the ones it
actually computed.

Usage:
    py scripts/measure_grounding.py --notebook notebook-1
    py scripts/measure_grounding.py --notebook notebook-1 --provider groq
    py scripts/measure_grounding.py --notebook notebook-1 --questions "q1" "q2"

Costs one LLM call (plus tool rounds) per question, so keep the list short.
"""
from __future__ import annotations

import argparse
import os
import re
import statistics
import sys
from collections import Counter

DEFAULT_QUESTIONS_FILE = os.path.join("tests", "data", "kimi.tsv")

#: Shapes that suggest the model relayed an instruction rather than a finding.
INSTRUCTIONISH = re.compile(
    r"ignore (all |any )?(previous|prior|above)|disregard (the )?(above|prior)"
    r"|you are now|system prompt|new instructions?:|reveal.*prompt",
    re.IGNORECASE,
)


class Recorder:
    """Wraps the validator's entry points without altering their behaviour."""

    def __init__(self):
        self.coverage: list[dict] = []
        self.structured: list[dict] = []
        self.freeform: list[dict] = []
        self._saved = {}

    def install(self):
        import opennote.validation.citation as vc

        real_coverage = vc._text_coverage
        real_filter = vc.filter_grounded_answer
        real_freeform = vc.validate_freeform_answer
        self._saved = {
            "coverage": (vc, "_text_coverage", real_coverage),
            "filter": (vc, "filter_grounded_answer", real_filter),
            "freeform": (vc, "validate_freeform_answer", real_freeform),
        }

        def recording_coverage(source_text, claim_text):
            value = real_coverage(source_text, claim_text)
            self.coverage.append({"claim": claim_text, "coverage": value})
            return value

        def recording_filter(answer, chunks_by_id, **kw):
            filtered, kept, dropped = real_filter(answer, chunks_by_id, **kw)
            self.structured.append(
                {
                    "claims": len(answer.claims),
                    "kept": len(kept),
                    "dropped": len(dropped),
                    "dropped_texts": [c.text for c in dropped],
                    "kept_texts": [c.text for c in kept],
                }
            )
            return filtered, kept, dropped

        def recording_freeform(answer, chunks_by_id):
            verdict = real_freeform(answer, chunks_by_id)
            self.freeform.append({"verdict": verdict, "chars": len(answer)})
            return verdict

        vc._text_coverage = recording_coverage
        vc.filter_grounded_answer = recording_filter
        vc.validate_freeform_answer = recording_freeform

    def restore(self):
        for mod, name, real in self._saved.values():
            setattr(mod, name, real)
        self._saved = {}


def load_questions(args):
    if args.questions:
        return list(args.questions)
    if os.path.exists(DEFAULT_QUESTIONS_FILE):
        out = []
        with open(DEFAULT_QUESTIONS_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(line.split("\t")[0])
        return out
    return []


def percentile(values, q):
    if not values:
        return float("nan")
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[idx]


def report(rec, turns):
    from opennote.validation.citation import _TEXT_SUPPORT_THRESHOLD

    print("\n" + "=" * 72)
    print("MEASURED CLAIM COVERAGE")
    print("=" * 72)

    structured_turns = len(rec.structured)
    prose_turns = len(rec.freeform)
    total_turns = len(turns)
    failed_turns = sum(1 for t in turns if t.get("path") == "no-reply")
    print(f"\nturns run: {total_turns}")
    print(f"  used submit_grounded_answer : {structured_turns}")
    print(f"  answered in prose           : {prose_turns}")
    print(f"  failed / no reply           : {failed_turns}")
    usable = structured_turns + prose_turns
    if usable:
        print(f"  structured-tool rate       : {structured_turns / usable:.0%} "
              f"(of {usable} completed turns)")
    if failed_turns:
        print(f"  note: {failed_turns} turn(s) failed (rate limit / connection) and are")
        print("  excluded from every rate below.")
    print(
        "  (the prose path uses validate_freeform_answer, which the claim-text\n"
        "   check does not touch -- so its blast radius is zero)"
    )

    total_claims = sum(s["claims"] for s in rec.structured)
    total_kept = sum(s["kept"] for s in rec.structured)
    total_dropped = sum(s["dropped"] for s in rec.structured)
    print(f"\nclaims submitted: {total_claims}   kept: {total_kept}   dropped: {total_dropped}")
    if total_claims:
        print(f"  drop rate at shipped {_TEXT_SUPPORT_THRESHOLD}: "
              f"{total_dropped / total_claims:.0%}")

    if rec.freeform:
        verdicts = Counter(f["verdict"] for f in rec.freeform)
        print(f"\nfree-form gate calls: {len(rec.freeform)}  verdicts: {dict(verdicts)}")

    values = [c["coverage"] for c in rec.coverage]
    print(
        f"\ncoverage distribution (n={len(values)} tier-2 evaluations).\n"
        "  A claim is evaluated once per source id whose quote_span matched, so this\n"
        "  can exceed the claim count when a claim cites several sources."
    )
    if not values:
        print("  no data -- the model never submitted a claim, so the tier-2 check")
        print("  never ran. There is nothing to tune.")
        _print_drops(rec)
        return
    print(f"  min={min(values):.2f}  p25={percentile(values, 0.25):.2f}  "
          f"median={statistics.median(values):.2f}  p75={percentile(values, 0.75):.2f}  "
          f"max={max(values):.2f}  mean={statistics.mean(values):.2f}")
    buckets = Counter()
    for v in values:
        buckets[min(9, int(v * 10))] += 1
    for b in range(10):
        lo, hi = b / 10, (b + 1) / 10
        n = buckets.get(b, 0)
        if n:
            bar = "#" * max(1, round(n * 40 / max(buckets.values())))
            print(f"  {lo:.1f}-{hi:.1f}  {n:3}  {bar}")

    print(f"\nverdicts at candidate thresholds (n={len(values)}):")
    print(f"  {'threshold':>9}  {'kept':>5}  {'dropped':>7}   note")
    for t in (0.30, 0.40, 0.50, 0.60, 0.70):
        dropped = sum(1 for v in values if v < t)
        note = "  <-- shipped" if abs(t - _TEXT_SUPPORT_THRESHOLD) < 1e-9 else ""
        print(f"  {t:9.2f}  {len(values) - dropped:5}  {dropped:7}{note}")

    print("\nsample of what the model actually wrote:")
    for c in sorted(rec.coverage, key=lambda d: d["coverage"])[:8]:
        flag = "  <-- instruction-like" if INSTRUCTIONISH.search(c["claim"]) else ""
        print(f"  {c['coverage']:5.2f}  {c['claim'][:88]!r}{flag}")

    _print_drops(rec)


def _print_drops(rec):
    dropped_texts = [t for s in rec.structured for t in s["dropped_texts"]]
    if not dropped_texts:
        return
    print(f"\nclaims the validator actually dropped ({len(dropped_texts)}):")
    for t in dropped_texts:
        flag = "  <-- instruction-like" if INSTRUCTIONISH.search(t) else ""
        print(f"  {t[:100]!r}{flag}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--notebook", default="notebook-1")
    ap.add_argument("--provider", default=None, help="provider id; default = configured default")
    ap.add_argument("--questions", nargs="*", default=None, help="override the question list")
    ap.add_argument("--max-rounds", type=int, default=5)
    args = ap.parse_args(argv)

    from opennote.agents.loop import agent_turn
    from opennote.chat.client import default_provider
    from opennote.notebooks import NotebookManager, resolve_home

    manager = NotebookManager(home=resolve_home())
    try:
        nb = manager.get(args.notebook)
    except Exception as exc:  # noqa: BLE001 - a diagnostic must report, not crash
        print(f"cannot open notebook {args.notebook!r}: {exc}", file=sys.stderr)
        print("available:", [getattr(n, "name", "?") for n in manager.list()], file=sys.stderr)
        return 2

    questions = load_questions(args)
    if not questions:
        print("no questions found", file=sys.stderr)
        return 2
    provider = args.provider or default_provider()
    print(f"notebook : {getattr(nb, 'name', args.notebook)}")
    print(f"provider : {provider}")
    print(f"questions: {len(questions)}")

    rec = Recorder()
    rec.install()
    turns = []
    try:
        for i, q in enumerate(questions, 1):
            before_s, before_f = len(rec.structured), len(rec.freeform)
            try:
                result = agent_turn(nb, q, provider_id=provider, max_rounds=args.max_rounds)
                answer = result.result.answer
            except Exception as exc:  # noqa: BLE001 - one bad turn must not stop the run
                answer = f"<turn failed: {type(exc).__name__}: {exc}>"
            path = "structured" if len(rec.structured) > before_s else (
                "prose" if len(rec.freeform) > before_f else "no-reply")
            print(f"  [{i}/{len(questions)}] {path:10} {answer[:80]!r}")
            turns.append({"question": q, "path": path, "answer": answer})
    finally:
        rec.restore()

    report(rec, turns)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
