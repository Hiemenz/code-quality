"""Score history: append a JSONL line per `scan` run, and render the trend
across runs. This is what lets you track overall codebase health over time
instead of only seeing a single point-in-time number.
"""

import json

_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def append_entry(path, summary):
    """Append one history record (from a `scan` summary dict) as a JSON line."""
    entry = {
        "timestamp": summary["generated_at"],
        "overall": summary["overall"]["score"],
        "grade": summary["overall"]["grade"],
        "categories": {name: cat["score"] for name, cat in summary["categories"].items()},
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=False) + "\n")
    return entry


def read_entries(path):
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def _sparkline(values):
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = hi - lo or 1
    return "".join(_SPARK_CHARS[min(len(_SPARK_CHARS) - 1, int((v - lo) / span * (len(_SPARK_CHARS) - 1)))]
                   for v in values)


def render_trend_text(entries):
    """Render `entries` (as returned by `read_entries`) as a sparkline + score table."""
    if not entries:
        return "No history entries found."

    lines = ["Score History", ""]
    scores = [e["overall"] for e in entries]
    lines.append(f"  {_sparkline(scores)}  (oldest -> newest, {len(entries)} runs)")
    lines.append("")
    lines.append(f"  {'Timestamp':<26} {'Score':>7} {'Grade':>5} {'Delta':>7}")
    prev = None
    for e in entries:
        delta = "" if prev is None else f"{e['overall'] - prev:+.1f}"
        lines.append(f"  {e['timestamp']:<26} {e['overall']:>7.1f} {e['grade']:>5} {delta:>7}")
        prev = e["overall"]
    return "\n".join(lines)
