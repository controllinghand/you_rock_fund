"""NYSE market holiday calendar — no external dependencies."""
import calendar
from datetime import date, timedelta


def _easter(year: int) -> date:
    """Compute Easter Sunday via the Anonymous Gregorian algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed(d: date) -> date:
    """NYSE Saturday → Friday, Sunday → Monday observation rule."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _nth_weekday(year: int, month: int, n: int, weekday: int) -> date:
    """nth occurrence (1-based) of weekday (0=Mon…6=Sun) in given year/month."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + (n - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Last occurrence of weekday (0=Mon…6=Sun) in given year/month."""
    last = date(year, month, calendar.monthrange(year, month)[1])
    offset = (last.weekday() - weekday) % 7
    return last - timedelta(days=offset)


def nyse_holidays(year: int) -> frozenset:
    """Return the set of NYSE market holidays for the given calendar year."""
    return frozenset({
        _observed(date(year, 1, 1)),          # New Year's Day
        _nth_weekday(year, 1, 3, 0),          # MLK Day      — 3rd Mon of Jan
        _nth_weekday(year, 2, 3, 0),          # Presidents'  — 3rd Mon of Feb
        _easter(year) - timedelta(days=2),    # Good Friday
        _last_weekday(year, 5, 0),            # Memorial Day — last Mon of May
        _observed(date(year, 6, 19)),         # Juneteenth
        _observed(date(year, 7, 4)),          # Independence Day
        _nth_weekday(year, 9, 1, 0),          # Labor Day    — 1st Mon of Sep
        _nth_weekday(year, 11, 4, 3),         # Thanksgiving — 4th Thu of Nov
        _observed(date(year, 12, 25)),        # Christmas
    })


def is_market_holiday(d: date | None = None) -> bool:
    if d is None:
        d = date.today()
    return d in nyse_holidays(d.year)


def is_first_trading_day_of_week(d: date | None = None) -> bool:
    """True if d is the first trading day of its week.

    Normal week  → Monday (not a holiday).
    Holiday week → Tuesday (when Monday is a holiday).
    """
    if d is None:
        d = date.today()
    wd = d.weekday()
    if wd == 0:                              # Monday
        return not is_market_holiday(d)
    if wd == 1:                              # Tuesday
        monday = d - timedelta(days=1)
        return is_market_holiday(monday)
    return False


def is_last_trading_day_of_week(d: date | None = None) -> bool:
    """True if d is the last trading day of its (Mon–Fri) week.

    Normal week  → Friday (not a holiday).
    Holiday week → Thursday (when Friday is a holiday, e.g. Good Friday).

    Used by the cash-sweep sell job, which is scheduled Thu+Fri and only acts on
    whichever of those is the week's final trading day — so the parked position is
    always liquidated before the weekend without a Friday order on a holiday.
    """
    if d is None:
        d = date.today()
    wd = d.weekday()
    if wd > 4 or is_market_holiday(d):       # weekend or holiday → not a trading day
        return False
    # Any later weekday this week that is still a trading day? Then d isn't last.
    nxt = d + timedelta(days=1)
    while nxt.weekday() <= 4:                 # Mon–Fri only (stop at Saturday)
        if not is_market_holiday(nxt):
            return False
        nxt += timedelta(days=1)
    return True
