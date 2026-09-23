from datetime import date, timedelta
import calendar


def date_range_for(preset: str, custom_start=None, custom_end=None):
    """Return (start_date, end_date) inclusive for a named preset."""
    today = date.today()

    if preset == "today":
        return today, today
    if preset == "yesterday":
        y = today - timedelta(days=1)
        return y, y
    if preset == "this_week":
        start = today - timedelta(days=today.weekday())
        return start, today
    if preset == "this_month":
        return today.replace(day=1), today
    if preset == "last_month":
        first_this = today.replace(day=1)
        last_month_end = first_this - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return last_month_start, last_month_end
    if preset == "this_year":
        return today.replace(month=1, day=1), today
    if preset == "custom" and custom_start and custom_end:
        return custom_start, custom_end

    # default: this month
    return today.replace(day=1), today


def month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def month_label(period_key: str) -> str:
    year, month = period_key.split("-")
    return f"{calendar.month_name[int(month)]} {year}"


def last_n_months(n=6):
    today = date.today()
    keys = []
    y, m = today.year, today.month
    for _ in range(n):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(keys))
