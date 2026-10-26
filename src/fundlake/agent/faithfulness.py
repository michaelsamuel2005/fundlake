"""Numeric faithfulness: every number in an answer must be traceable to a tool result. Counts ungrounded numbers (hallucinations)."""
from __future__ import annotations

import re

ISO = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
IDS = re.compile(r"\b[A-Z]{1,3}\d{2,7}\b")   # F01, S026, T0000081, CP5, CL000012, P000123
NUM = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")


def flatten_numbers(obj, out: list[float] | None = None) -> list[float]:
    out = [] if out is None else out
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, str):
        s = ISO.sub(" ", obj)
        s = IDS.sub(" ", s)
        for m in NUM.findall(s):
            try:
                out.append(float(m.replace(",", "")))
            except ValueError:
                pass
        for m in ISO.findall(obj):
            y, mo, d = m.split("-")
            out += [float(y), float(mo), float(d)]
    elif isinstance(obj, dict):
        for v in obj.values():
            flatten_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            flatten_numbers(v, out)
    return out


def numbers_in_text(text: str) -> list[tuple[str, float, int]]:
    s = ISO.sub(" ", text)
    s = IDS.sub(" ", s)
    res = []
    for m in NUM.findall(s):
        raw = m.replace(",", "")
        dec = len(raw.split(".")[1]) if "." in raw else 0
        res.append((m, float(raw), dec))
    return res


def ungrounded(text: str, tool_results: list) -> list[str]:
    pool = flatten_numbers(tool_results)
    bad = []
    for raw, x, dec in numbers_in_text(text):
        ok = any(abs(round(v, dec) - x) < 1e-9 or abs(round(abs(v), dec) - abs(x)) < 1e-9 for v in pool)
        if not ok:
            bad.append(raw)
    return bad
