"""Spec-driven USDM object factory.

`Factory.make(cls, **attrs)` creates a USDM v4 object with a generated id, instanceType, and every
attribute defined in data/usdm_v4_model.json (defaults: None for 0..1, [] for 0..*).  Unknown attribute
names raise immediately, so typos never silently produce invalid JSON.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .ct import ALIAS_FIELDS, Terminology

SPEC_PATH = Path(__file__).parent / "data" / "usdm_v4_model.json"


def load_spec(path=SPEC_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class Factory:
    def __init__(self, ct: Terminology, spec: dict | None = None):
        self.spec = spec or load_spec()
        self.ct = ct
        self.counters: dict[str, int] = defaultdict(int)

    def new_id(self, cls: str) -> str:
        self.counters[cls] += 1
        return f"{cls}_{self.counters[cls]}"

    def make(self, cls: str, **kw) -> dict:
        if cls not in self.spec:
            raise KeyError(f"unknown USDM class {cls}")
        attrs = self.spec[cls]["attributes"]
        unknown = set(kw) - set(attrs)
        if unknown:
            raise KeyError(f"{cls}: unknown attribute(s) {sorted(unknown)}")
        obj = {"id": kw.pop("id", None) or self.new_id(cls)}
        for name, a in attrs.items():
            if name in ("id", "instanceType"):
                continue
            if name in kw:
                obj[name] = kw[name]
            else:
                obj[name] = [] if a["card"].endswith("*") or a["card"] == "0..2" else None
        obj["instanceType"] = cls
        return obj

    # ---- coded values ---------------------------------------------------------------------------
    def code(self, field: str, value) -> dict:
        code, decode, system, version = self.ct.lookup(field, value)
        return self.make("Code", code=code, codeSystem=system, codeSystemVersion=version, decode=decode)

    def raw_code(self, code, decode, system, version) -> dict:
        return self.make("Code", code=code, codeSystem=system, codeSystemVersion=version, decode=decode)

    def alias(self, field: str, value) -> dict:
        return self.make("AliasCode", standardCode=self.code(field, value), standardCodeAliases=[])

    def coded(self, field: str, value):
        """Code or AliasCode depending on how USDM types the attribute; None for empty."""
        if value in (None, ""):
            return None
        return self.alias(field, value) if field in ALIAS_FIELDS else self.code(field, value)

    def codes(self, field: str, value) -> list:
        return [self.code(field, v) for v in split(value)]

    # ---- small helpers --------------------------------------------------------------------------
    def quantity(self, value, unit=None):
        if value in (None, ""):
            return None
        return self.make("Quantity", value=num(value), unit=self.alias("unit", unit) if unit else None)

    def ext(self, url: str, value) -> dict:
        return self.make("ExtensionAttribute", url=url, valueString=None if value is None else str(value))

    def rule(self, name, text):
        if not text:
            return None
        return self.make("TransitionRule", name=name, label=name, text=str(text))


def split(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [s.strip() for s in str(v).split(";") if s.strip()]


def num(v):
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    try:
        f = float(s)
        return int(f) if f.is_integer() else f
    except ValueError:
        raise ValueError(f"not a number: {v!r}")


def yes(v) -> bool:
    return str(v).strip().lower() in ("y", "yes", "true", "1") if v is not None else False


def chain(items: list[dict]):
    """Set previousId / nextId on an ordered list."""
    for i, it in enumerate(items):
        it["previousId"] = items[i - 1]["id"] if i > 0 else None
        it["nextId"] = items[i + 1]["id"] if i + 1 < len(items) else None
    return items
