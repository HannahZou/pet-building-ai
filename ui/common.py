"""Shared Streamlit helpers: branding, badges, flash messages, tables."""
from __future__ import annotations

from typing import Iterable

import pandas as pd
import streamlit as st

from pawpal.models import BookingStatus as S
from pawpal.workflow import STATUS_LABELS

NAVY, GOLD = "#1f2a44", "#c9a24b"

STATUS_COLORS = {
    S.DRAFT: ("#eef0f5", "#4a5068"),
    S.QUOTED: ("#e8f0fb", "#2a5599"),
    S.PENDING_RESIDENT_APPROVAL: ("#fbf3e3", "#8a6414"),
    S.PENDING_ADMIN_APPROVAL: ("#f4ecfb", "#6a3f99"),
    S.CONFIRMED: ("#e6f5ec", "#1e7a45"),
    S.COMPLETED: ("#e9eef0", "#3e5560"),
    S.CANCELLED: ("#f3f3f3", "#777777"),
    S.REJECTED: ("#fbe9e9", "#a33a3a"),
    S.NEEDS_RESCHEDULING: ("#fdeee4", "#b2531a"),
}

STEPS = [
    (S.DRAFT, "Request"),
    (S.QUOTED, "Quote"),
    (S.PENDING_RESIDENT_APPROVAL, "Your approval"),
    (S.PENDING_ADMIN_APPROVAL, "Manager approval"),
    (S.CONFIRMED, "Confirmed"),
]

CSS = f"""
<style>
  .block-container {{padding-top: 2rem; max-width: 1200px;}}
  .pp-hero {{background: linear-gradient(135deg,{NAVY} 0%,#3b4a73 100%);
            color:#fff; padding:1.4rem 2rem; border-radius:18px; margin-bottom:1.2rem;}}
  .pp-hero h1 {{margin:0; font-size:1.9rem; letter-spacing:.5px; color:#fff;}}
  .pp-hero p {{margin:.3rem 0 0; color:#d8dcec;}}
  .pp-note {{border-left:4px solid {GOLD}; background:#faf6ea; color:#4a3f1f;
            padding:.7rem 1rem; border-radius:8px; margin:.5rem 0 1rem;}}
  .pp-badge {{display:inline-block; padding:.18rem .7rem; border-radius:999px; font-size:.82rem;
             font-weight:600; letter-spacing:.2px;}}
  .pp-steps {{display:flex; gap:.4rem; flex-wrap:wrap; margin:.4rem 0 1rem;}}
  .pp-step {{flex:1 1 120px; text-align:center; padding:.45rem .3rem; border-radius:10px;
            font-size:.8rem; background:#f1f2f6; color:#8a8fa3; border:1px solid #e3e5ec;}}
  .pp-step.done {{background:#e6f5ec; color:#1e7a45; border-color:#bfe3cd;}}
  .pp-step.now {{background:{NAVY}; color:#fff; border-color:{NAVY}; font-weight:600;}}
  .pp-card {{border:1px solid #e6e8ef; border-radius:14px; padding:1rem 1.2rem; background:#fff;}}
  .pp-muted {{color:#7a8094; font-size:.85rem;}}
</style>
"""


def hero(title: str, subtitle: str) -> None:
    st.markdown(CSS + f'<div class="pp-hero"><h1>🐾 {title}</h1><p>{subtitle}</p></div>', unsafe_allow_html=True)


def badge(status: S) -> str:
    bg, fg = STATUS_COLORS[status]
    return f'<span class="pp-badge" style="background:{bg};color:{fg}">{STATUS_LABELS[status]}</span>'


def show_badge(status: S, prefix: str = "") -> None:
    st.markdown(f"{prefix}{badge(status)}", unsafe_allow_html=True)


def progress(status: S) -> None:
    order = [s for s, _ in STEPS]
    if status in order:
        idx = order.index(status)
    elif status == S.NEEDS_RESCHEDULING:
        idx = 1
    elif status == S.COMPLETED:
        idx = len(order)
    else:
        idx = -1
    cells = []
    for i, (_, label) in enumerate(STEPS):
        cls = "done" if i < idx or (status == S.COMPLETED) else ("now" if i == idx else "")
        cells.append(f'<div class="pp-step {cls}">{label}</div>')
    st.markdown(f'<div class="pp-steps">{"".join(cells)}</div>', unsafe_allow_html=True)


def note(html: str) -> None:
    st.markdown(f'<div class="pp-note">{html}</div>', unsafe_allow_html=True)


def flash(message: str, kind: str = "success") -> None:
    st.session_state["_flash"] = (kind, message)


def show_flash() -> None:
    item = st.session_state.pop("_flash", None)
    if item:
        getattr(st, item[0])(item[1])


def table(rows: Iterable[dict], empty: str = "Nothing to show.", height: int | None = None) -> None:
    rows = list(rows)
    if not rows:
        st.info(empty)
        return
    df = pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")} for r in rows])
    kwargs = {"hide_index": True, "use_container_width": True}
    if height:
        kwargs["height"] = height
    st.dataframe(df, **kwargs)


def done(message: str, kind: str = "success") -> None:
    """Show a message after the rerun that follows a successful action."""
    flash(message, kind)
    st.rerun()
