from datetime import date
from dateutil import parser


def parse_date_safe(value: str | None):
    if not value:
        return None

    value = value.strip()
    if not value:
        return None

    if value.lower() in {"present", "current", "now", "till date"}:
        return date.today()

    try:
        return parser.parse(value).date()
    except Exception:
        return None


def month_diff(start_date, end_date) -> int:
    if not start_date or not end_date or end_date < start_date:
        return 0
    return (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)