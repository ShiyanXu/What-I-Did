"""时间工具：全项目统一 +08:00，统一"工作日"口径。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))
DAY_FMT = "%Y-%m-%d"


def now() -> datetime:
    return datetime.now(TZ)


def iso(dt: datetime) -> str:
    return dt.astimezone(TZ).isoformat(timespec="seconds")


def parse(s: str) -> datetime:
    if not s:
        return now()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def workday_of(dt: datetime, cutoff_hour: int = 4) -> str:
    """把时间点归属到某个"工作日"。凌晨 4 点前算前一天。"""
    d = dt.astimezone(TZ)
    if d.hour < cutoff_hour:
        d = d - timedelta(days=1)
    return d.strftime(DAY_FMT)


def day_window(day: str, cutoff_hour: int = 4) -> tuple[datetime, datetime]:
    start = datetime.strptime(day, DAY_FMT).replace(tzinfo=TZ) + timedelta(hours=cutoff_hour)
    return start, start + timedelta(days=1)


def trigger_at(day: str, cutoff_hour: int = 4, trigger_hour: int = 21) -> datetime:
    """某一天该出报的时刻（真正的钟点）。

    注意别写成 `day_window()[0] + trigger_hour 小时`：窗口起点是当天 04:00，
    加 21 小时得到的是次日 01:00，21:00 永远不会触发（这个坑真踩过）。
    """
    base = datetime.strptime(day, DAY_FMT).replace(tzinfo=TZ)
    if trigger_hour < cutoff_hour:
        base += timedelta(days=1)      # 凌晨出报（比如 02:00）算次日的
    return base + timedelta(hours=trigger_hour)


def parse_day(day: str) -> datetime:
    return datetime.strptime(day, DAY_FMT).replace(tzinfo=TZ)


def fmt_hms(dt: datetime) -> str:
    return dt.strftime("%H:%M:%S")


def fmt_hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def human_dur(sec: float) -> str:
    sec = int(max(0, sec))
    if sec < 60:
        return f"{sec} 秒"
    total_min = sec // 60
    h, m = divmod(total_min, 60)
    if h and m:
        return f"{h} 小时 {m} 分"
    if h:
        return f"{h} 小时"
    return f"{m} 分钟"


def hm_to_minutes(hm: str) -> int:
    """'14:30' -> 870"""
    try:
        h, m = hm.split(":")[:2]
        return int(h) * 60 + int(m)
    except Exception:
        return 0
