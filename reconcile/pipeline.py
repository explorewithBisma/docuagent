"""
reconcile/pipeline.py
----------------------
The whole job in one call, for the page and the tests: two raw tables in, the mapping, the matching and the report out.

    table, header_row = load_table(raw)
    analysis = analyze(table_a, header_a, table_b, header_b, Settings(...))
"""

from dataclasses import dataclass, field

from reconcile import columns as rc, match as rm, prepare as rp, report as rr


@dataclass
class Settings:
    day_window: int = 3
    tolerance: float = 0.005
    sign: str = "auto"                                  # auto | same | opposite | absolute
    overrides_a: dict = field(default_factory=dict)     # {role: column or None}
    overrides_b: dict = field(default_factory=dict)
    day_first_a: str = "auto"                           # auto | day | month
    day_first_b: str = "auto"
    default_day_first: bool = True                      # the locale's habit, used when the dates themselves do not say

    def key(self) -> str:
        return repr((self.day_window, self.tolerance, self.sign, sorted(self.overrides_a.items(), key=str), sorted(self.overrides_b.items(), key=str),
                     self.day_first_a, self.day_first_b, self.default_day_first))


@dataclass
class Analysis:
    map_a: object
    map_b: object
    A: object
    B: object
    result: object
    report: object
    summary: str


def load_table(raw):
    return rc.table_from_raw(raw)


def detect(table, settings_overrides: dict, day_first: str, default_day_first: bool):
    mapping = rc.detect_mapping(table, default_day_first).with_overrides(settings_overrides, list(table.columns))
    if day_first in ("day", "month"):
        mapping.day_first, mapping.day_first_certain = day_first == "day", True
    return mapping


def analyze(name_a, table_a, header_a, name_b, table_b, header_b, settings: Settings = None, accepted=frozenset()) -> Analysis:
    settings = settings or Settings()
    map_a = detect(table_a, settings.overrides_a, settings.day_first_a, settings.default_day_first)
    map_b = detect(table_b, settings.overrides_b, settings.day_first_b, settings.default_day_first)
    A, B = rp.prepare_side(table_a, map_a, header_a), rp.prepare_side(table_b, map_b, header_b)
    result = rm.reconcile(A, B, rm.Options(settings.day_window, settings.tolerance, settings.sign))
    report = rr.build_report(name_a, name_b, table_a, table_b, map_a, map_b, A, B, result, accepted)
    return Analysis(map_a, map_b, A, B, result, report, rr.summary_text(result))
