from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from menai import Menai
from menai_benchmark import BenchmarkCase, BenchmarkSuite, Implementation

_SUITE_DIR = Path(__file__).resolve().parent
_MODULE_SRC = (_SUITE_DIR / "calendar.menai").read_text(encoding="utf-8").strip()

_WEEKDAY_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")

# 5-day working week, Monday to Friday
_WORK_DAYS = frozenset(_WEEKDAY_NAMES[1:6])

_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

_CASES: list[tuple[str, str, int, int, frozenset[str]]] = [
    ("short_5d",    "2025-03-17",   5,   5, frozenset()),
    ("month_20d",   "2025-03-17",  20,  20, frozenset()),
    ("quarter_60d", "2025-01-06",  60,  60, frozenset({"2025-02-17"})),
    ("year_250d",   "2025-01-06", 250, 250, frozenset({"2025-04-18", "2025-07-04", "2025-11-27"})),
    ("months_600d", "2024-01-01", 400, 600, frozenset({"2024-12-25", "2025-04-18"})),
]


def _is_leap_year(year: int) -> bool:
    """Return True if the year is a leap year in the Gregorian calendar."""
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _day_of_week(s: str) -> int:
    """Return the day of week of an ISO date string (0=sunday..6=saturday)."""
    date = dt.date.fromisoformat(s)
    return (date - dt.date(1970, 1, 4)).days % 7


def _days_in_month(year: int, month: int) -> int:
    """Return the number of calendar days in the month."""
    if month == 2 and _is_leap_year(year):
        return 29

    return _DAYS_IN_MONTH[month - 1]


def _parse_date(s: str) -> tuple[int, int, int]:
    """Split an ISO date string into a (year, month, day) integer tuple."""
    year, month, day = s.split("-")
    return int(year), int(month), int(day)


def _format_date(year: int, month: int, day: int) -> str:
    """Format a (year, month, day) triple as a zero-padded ISO date string."""
    month_str = f"0{month}" if month < 10 else str(month)
    day_str = f"0{day}" if day < 10 else str(day)
    return f"{year}-{month_str}-{day_str}"


def _add_one_day(s: str) -> str:
    """Return the ISO date string one calendar day after the given date."""
    year, month, day = _parse_date(s)
    if day < _days_in_month(year, month):
        return _format_date(year, month, day + 1)

    if month < 12:
        return _format_date(year, month + 1, 1)

    return _format_date(year + 1, 1, 1)


def _subtract_one_day(s: str) -> str:
    """Return the ISO date string one calendar day before the given date."""
    year, month, day = _parse_date(s)
    if day > 1:
        return _format_date(year, month, day - 1)

    if month > 1:
        return _format_date(year, month - 1, _days_in_month(year, month - 1))

    return _format_date(year - 1, 12, 31)


def _add_calendar_days(s: str, days: int) -> str:
    """Recursively advance the date by the given number of calendar days."""
    if days == 0:
        return s

    if days > 0:
        return _add_calendar_days(_add_one_day(s), days - 1)

    return _add_calendar_days(_subtract_one_day(s), days + 1)


def _add_one_working_day(s: str, holidays: frozenset[str]) -> str:
    """Return the next date that is a working day, skipping weekends and holidays."""
    next_date = _add_one_day(s)
    if _WEEKDAY_NAMES[_day_of_week(next_date)] in _WORK_DAYS and next_date not in holidays:
        return next_date

    return _add_one_working_day(next_date, holidays)


def _add_working_days(s: str, days: int, holidays: frozenset[str]) -> str:
    """Recursively advance the date by the given number of working days."""
    if days == 0:
        return s

    if days > 0:
        return _add_working_days(_add_one_working_day(s, holidays), days - 1, holidays)

    return _add_working_days(_subtract_working_day(s, holidays), days + 1, holidays)


def _subtract_working_day(s: str, holidays: frozenset[str]) -> str:
    """Return the previous date that is a working day, skipping weekends and holidays."""
    prev_date = _subtract_one_day(s)
    if _WEEKDAY_NAMES[_day_of_week(prev_date)] in _WORK_DAYS and prev_date not in holidays:
        return prev_date

    return _subtract_working_day(prev_date, holidays)


def _format_menai_holidays(holidays: frozenset[str]) -> str:
    """Render a holiday set as a Menai set literal expression."""
    if not holidays:
        return "(set)"

    return "(set " + " ".join(f'"{d}"' for d in sorted(holidays)) + ")"


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

    def implementations(self, menai: Menai) -> list[Implementation]:
        """Return Menai, idiomatic Python, and functional Python implementations."""

        def prepare_menai(case_input: tuple[str, int, int, frozenset[str]]) -> Any:
            """Build the module-driving expression and compile to bytecode (untimed)."""
            start, days, cal_days, holidays = case_input
            calendar_def = (
                f'(let* ((calendar {_MODULE_SRC})'
                f' (cal ((dict-get calendar "calendar") "std" "Standard" "5-day" '
                f'(set "mon" "tue" "wed" "thu" "fri") {_format_menai_holidays(holidays)})))'
                ' (dict "add-working-days" (dict-get calendar "add-working-days")'
                ' "add-calendar-days" (dict-get calendar "add-calendar-days")'
                ' "cal" cal))'
            )
            expr = (
                f'(let* ((mod {calendar_def})'
                ' (add-working-days (dict-get mod "add-working-days"))'
                ' (add-calendar-days (dict-get mod "add-calendar-days"))'
                ' (cal (dict-get mod "cal")))'
                ' (list'
                f'  (add-working-days "{start}" {float(days)} cal)'
                f'  (add-calendar-days "{start}" {days})'
                f'  (add-calendar-days "{start}" {cal_days})))'
            )
            return menai.compile(expr)

        def run_menai(code: Any) -> Any:
            """Execute pre-compiled bytecode (timed)."""
            return menai.execute_raw(code)

        return [
            Implementation(name="Menai", run=run_menai, prepare=prepare_menai),
            Implementation(name="Python (idiomatic)", run=run_python_idiomatic),
            Implementation(name="Python (functional)", run=run_python_functional),
        ]

    def results_equal(self, a: Any, b: Any) -> bool:
        """Return True if both results are equal (working-end, calendar-end) date lists."""
        return a == b


def run_python_idiomatic(case_input: tuple[str, int, int, frozenset[str]]) -> list[str]:
    """Advance dates using datetime.date arithmetic."""
    start, days, cal_days, holidays = case_input
    date = dt.date.fromisoformat(start)
    working = 0
    while working < days:
        date += dt.timedelta(days=1)
        if date.weekday() < 5 and date.isoformat() not in holidays:
            working += 1

    working_end = date.isoformat()
    short_end = (dt.date.fromisoformat(start) + dt.timedelta(days=days)).isoformat()
    long_end = (dt.date.fromisoformat(start) + dt.timedelta(days=cal_days)).isoformat()
    return [working_end, short_end, long_end]


def run_python_functional(case_input: tuple[str, int, int, frozenset[str]]) -> list[str]:
    """Advance dates with the same pure-functional recursion as the Menai code."""
    start, days, cal_days, holidays = case_input
    return [
        _add_working_days(start, days, holidays),
        _add_calendar_days(start, days),
        _add_calendar_days(start, cal_days),
    ]
