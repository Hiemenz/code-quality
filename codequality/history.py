"""Score history: append a JSONL line per `scan` run, and render the trend
across runs. This is what lets you track overall codebase health over time
instead of only seeing a single point-in-time number.
"""

import json

_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def append_entry(path, summary):
    """Append one history record (from a `scan` summary dict) as a JSON line."""
    s = summary["summary"]
    entry = {
        "timestamp": summary["generated_at"],
        "overall": summary["overall"]["score"],
        "grade": summary["overall"]["grade"],
        "categories": {name: cat["score"] for name, cat in summary["categories"].items()},
        "test_loc": s.get("test_loc", 0),
        "source_loc": s.get("source_loc", 0),
        "test_ratio": s.get("test_ratio"),
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


def _format_ratio(ratio):
    return f"{ratio:.2f}" if ratio is not None else "n/a"


def _render_score_section(entries):
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
    return lines


def _test_ratio_delta(ratio, prev_ratio):
    if prev_ratio is None or ratio is None:
        return ""
    return f"{ratio - prev_ratio:+.2f}"


def _render_test_ratio_section(entries):
    lines = ["Test Ratio History (test LOC / source LOC)", ""]
    ratios = [e.get("test_ratio") for e in entries]
    if any(r is not None for r in ratios):
        spark_values = [r if r is not None else 0.0 for r in ratios]
        lines.append(f"  {_sparkline(spark_values)}  (oldest -> newest, {len(entries)} runs)")
        lines.append("")
    lines.append(f"  {'Timestamp':<26} {'Ratio':>7} {'TestLOC':>9} {'SrcLOC':>8} {'Delta':>7}")
    prev_ratio = None
    for e in entries:
        ratio = e.get("test_ratio")
        lines.append(
            f"  {e['timestamp']:<26} {_format_ratio(ratio):>7} "
            f"{e.get('test_loc', 0):>9} {e.get('source_loc', 0):>8} {_test_ratio_delta(ratio, prev_ratio):>7}"
        )
        if ratio is not None:
            prev_ratio = ratio
    return lines


def _render_category_section(entries):
    """Sparkline + delta table per category, for entries that carry category data."""
    all_categories = []
    for e in entries:
        for cat in e.get("categories", {}):
            if cat not in all_categories:
                all_categories.append(cat)
    if not all_categories:
        return []

    lines = ["Category Score History", ""]
    for cat in all_categories:
        values = [e["categories"].get(cat) for e in entries if "categories" in e]
        numeric = [v for v in values if v is not None]
        if not numeric:
            continue
        spark = _sparkline(numeric)
        latest = numeric[-1]
        delta = f"{numeric[-1] - numeric[-2]:+.1f}" if len(numeric) >= 2 else ""
        lines.append(f"  {cat:<14} {spark}  latest {latest:>5.1f}  {delta}")
    return lines


def render_trend_text(entries):
    """Render `entries` (as returned by `read_entries`) as a sparkline + score table,
    followed by per-category sparklines and the test-to-source LOC ratio.
    """
    if not entries:
        return "No history entries found."

    lines = _render_score_section(entries)
    lines.append("")
    category_lines = _render_category_section(entries)
    if category_lines:
        lines.extend(category_lines)
        lines.append("")
    lines.extend(_render_test_ratio_section(entries))
    return "\n".join(lines)
