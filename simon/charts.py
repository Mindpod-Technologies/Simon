"""Chart tool: Simon renders matplotlib charts into the workspace.

Charts land in ``workspace/charts/`` and appear in the web UI Artifacts
panel; the tool output carries an ``[artifact:charts/<file>.png]`` marker
so the chat UI renders the image inline in Simon's reply.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from . import docs

log = logging.getLogger(__name__)

CHARTS_DIRNAME = "charts"
_CHART_TYPES = {"line", "bar", "scatter", "pie"}

# Simon's UI palette — charts should look native to the product.
_BG = "#0b1116"
_FG = "#c9d4d8"
_ACCENT = "#3fc6c9"
_SERIES_COLORS = ["#3fc6c9", "#c9a43f", "#5fb86a", "#b678e8", "#e8826a"]


def charts_dir(settings) -> Path:
    d = docs._workspace(settings) / CHARTS_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _render(chart_type: str, title: str, labels: list[str],
            series: list[dict], x_label: str, y_label: str,
            out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 4.5), facecolor=_BG)
    ax.set_facecolor(_BG)

    if chart_type == "pie":
        values = [float(v) for v in series[0]["values"]]
        ax.pie(values, labels=labels, autopct="%1.1f%%",
               colors=_SERIES_COLORS * 3,
               textprops={"color": _FG, "fontsize": 10})
    else:
        x = list(range(len(labels)))
        for i, s in enumerate(series):
            values = [float(v) for v in s["values"]]
            color = _SERIES_COLORS[i % len(_SERIES_COLORS)]
            name = s.get("name") or f"series {i + 1}"
            if chart_type == "line":
                ax.plot(x, values, marker="o", label=name, color=color,
                        linewidth=2)
            elif chart_type == "bar":
                width = 0.8 / max(len(series), 1)
                offset = (i - (len(series) - 1) / 2) * width
                ax.bar([v + offset for v in x], values, width=width,
                       label=name, color=color)
            else:  # scatter
                ax.scatter(x, values, label=name, color=color, s=42)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, color=_FG, fontsize=9, rotation=20,
                           ha="right")
        if len(series) > 1 or series[0].get("name"):
            legend = ax.legend(facecolor=_BG, edgecolor="#16232b",
                               labelcolor=_FG, fontsize=9)
        if x_label:
            ax.set_xlabel(x_label, color=_FG, fontsize=10)
        if y_label:
            ax.set_ylabel(y_label, color=_FG, fontsize=10)

    ax.set_title(title, color=_FG, fontsize=13, pad=12)
    for spine in ax.spines.values():
        spine.set_color("#16232b")
    ax.tick_params(colors=_FG)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110, facecolor=_BG)
    plt.close(fig)


def _create_chart(title: str, chart_type: str, labels: list,
                  series: list, x_label: str = "", y_label: str = "",
                  settings=None) -> str:
    title = (title or "").strip()
    chart_type = (chart_type or "").strip().lower()
    if not title:
        return "Error: create_chart needs a title."
    if chart_type not in _CHART_TYPES:
        return (f"Error: unsupported chart type '{chart_type}' — use one "
                f"of {', '.join(sorted(_CHART_TYPES))}.")
    if not labels or not series:
        return "Error: create_chart needs labels and at least one series."
    try:
        clean_series = [
            {"name": str(s.get("name", "")), "values": list(s["values"])}
            for s in series
        ]
        for s in clean_series:
            if len(s["values"]) != len(labels):
                return ("Error: every series must have exactly "
                        f"{len(labels)} values (one per label); "
                        f"'{s['name'] or 'series'}' has "
                        f"{len(s['values'])}.")
        stem = docs.safe_name(title).replace(" ", "_")
        path = charts_dir(settings) / f"{stem}-{int(time.time())}.png"
        _render(chart_type, title, [str(l) for l in labels], clean_series,
                x_label or "", y_label or "", path)
    except (KeyError, TypeError, ValueError) as exc:
        return f"Error: invalid chart data — {exc}"
    log.info("created chart %s", path.name)
    rel = f"{CHARTS_DIRNAME}/{path.name}"
    return (f"Created chart '{path.name}' — it renders inline here and in "
            f"the Artifacts panel.\n[artifact:{rel}]")


def register_chart_tools(registry, settings) -> None:
    """Register the chart-creation tool on ``registry``."""
    from .tools.base import Tool

    registry.register(Tool(
        name="create_chart",
        description=(
            "Render a chart (line, bar, scatter or pie) as an image that "
            "displays inline in the chat and in the Artifacts panel. Use "
            "whenever the user asks for a chart/graph/plot or when numbers "
            "would be clearer visualised."),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Chart title."},
                "chart_type": {
                    "type": "string",
                    "enum": ["line", "bar", "scatter", "pie"],
                    "description": "Visualisation type."},
                "labels": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Category labels (x-axis / pie slices)."},
                "series": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "values": {"type": "array",
                                       "items": {"type": "number"}},
                        },
                        "required": ["values"],
                    },
                    "description": "One or more data series; each needs "
                                   "one value per label."},
                "x_label": {"type": "string",
                            "description": "Optional x-axis label."},
                "y_label": {"type": "string",
                            "description": "Optional y-axis label."},
            },
            "required": ["title", "chart_type", "labels", "series"],
        },
        func=lambda title, chart_type, labels, series, x_label="",
                    y_label="": _create_chart(
            title, chart_type, labels, series, x_label, y_label,
            settings=settings),
    ))
