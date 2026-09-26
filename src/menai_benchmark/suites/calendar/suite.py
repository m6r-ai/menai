from __future__ import annotations

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

_CASES: list[tuple[str, str, int, int, frozenset[str]]] = [
    ("short_5d",    "2025-03-17",   5,   5, frozenset()),
    ("month_20d",   "2025-03-17",  20,  20, frozenset()),
    ("quarter_60d", "2025-01-06",  60,  60, frozenset({"2025-02-17"})),
    ("year_250d",   "2025-01-06", 250, 250, frozenset({"2025-04-18", "2025-07-04", "2025-11-27"})),
    ("months_600d", "2024-01-01", 400, 600, frozenset({"2024-12-25", "2025-04-18"})),
]


def _format_menai_holidays(holidays: frozenset[str]) -> str:
    """Render a holiday set as a Menai set literal expression."""
    if not holidays:
        return "(set)"

    return "(set " + " ".join(f'"{d}"' for d in sorted(holidays)) + ")"


def _expr(case_input: tuple[str, int, int, frozenset[str]]) -> str:
    """Build the module-driving expression for a calendar case."""
    start, days, cal_days, holidays = case_input
    calendar_def = (
        f'(let* ((calendar (import "calendar"))'
        f' (cal ((:: calendar calendar) "std" "Standard" "5-day" '
        f'(set "mon" "tue" "wed" "thu" "fri") {_format_menai_holidays(holidays)})))'
        ' (dict "add-working-days" (:: calendar add-working-days)'
        ' "add-calendar-days" (:: calendar add-calendar-days)'
        ' "cal" cal))'
    )
    return (
        f'(let* ((mod {calendar_def})'
        ' (add-working-days (dict-get mod "add-working-days"))'
        ' (add-calendar-days (dict-get mod "add-calendar-days"))'
        ' (cal (dict-get mod "cal")))'
        ' (list'
        f'  (add-working-days "{start}" {float(days)} cal)'
        f'  (add-calendar-days "{start}" {days})'
        f'  (add-calendar-days "{start}" {cal_days})))'
    )


class Suite(BenchmarkSuite):
    """Benchmark suite for calendar arithmetic built on dense integer match dispatch."""

    name = "calendar"
    description = (
        "Working-day date arithmetic. The inner loops dispatch through "
        "day-name (7-arm) and days-in-month (12-arm) integer matches. "
        "Each case advances the start date by both working days and a "
        "calendar-day span, crossing month and year boundaries so the "
        "days-in-month jump table is exercised on every step."
    )

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per (start date, working-day count, calendar-day count) triple."""
        return [
            BenchmarkCase(name=name, input=(start, days, cal_days, holidays), iterations=3)
            for name, start, days, cal_days, holidays in _CASES
        ]

    def menai_program(self) -> MenaiProgram:
        """Return the calendar expression, built from the case input tuple."""
        return MenaiProgram(expression=_expr)
