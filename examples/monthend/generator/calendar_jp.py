from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
JST = ZoneInfo("Asia/Tokyo"); UTC = timezone.utc
COVERAGE_START_DATE = date(2026, 1, 1)          # first business date in the demo data (inclusive)
BASE_DATE = date(2026, 3, 10)                   # fixed demo "today"; snapshot cutoff = BASE_DATE 00:00 JST
HOLIDAYS = {date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 12), date(2026, 2, 11), date(2026, 2, 23)}

def jst(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=JST)

def jst_midnight(d: date):
    return datetime(d.year, d.month, d.day, tzinfo=JST)

COVERAGE_START = jst_midnight(COVERAGE_START_DATE)
COVERAGE_END = jst_midnight(BASE_DATE)          # exclusive

def iso(dt):
    return None if dt is None else dt.astimezone(UTC).isoformat()

def daterange(a: date, b_incl: date):
    d = a
    while d <= b_incl:
        yield d; d += timedelta(days=1)

def day_type(d: date):
    if d in HOLIDAYS: return "HOLIDAY"
    if d.weekday() >= 5: return "REST"
    return "WORK"

def first_of_month_after(d: date):
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)
