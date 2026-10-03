"""北京时间排程；间隔以计划时间为锚，不依赖上次完成时间。"""
from datetime import datetime, timedelta
import re

import pytz

from bot.services.admin_push import PushError
from bot.services.admin_push.content import parse_schedule

ZONE = pytz.timezone("Asia/Shanghai")


def normalize_schedule(value, now):
    if not isinstance(value, dict):
        raise PushError("排程必须是对象。")
    kind = value.get("kind", "manual")
    if kind == "manual":
        return {"kind": kind}
    if kind == "once":
        at = value.get("at")
        if not at:
            raise PushError("请选择一次性运行时间。")
        parse_schedule(at, now)
        return {"kind": kind, "at": at}
    if kind == "interval":
        minutes = value.get("minutes")
        if type(minutes) is not int or not 5 <= minutes <= 525600:
            raise PushError("间隔须为5至525600分钟的整数。")
        return {"kind": kind, "minutes": minutes}
    if kind not in {"daily", "weekly"}:
        raise PushError("不支持的排程。")
    times = value.get("times")
    if not isinstance(times, list) or not 1 <= len(times) <= 24 or any(
        not isinstance(t, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", t) for t in times
    ):
        raise PushError("请填写1至24个有效的北京时间 HH:MM。")
    result = {"kind": kind, "times": sorted(set(times))}
    if kind == "weekly":
        days = value.get("days")
        if len(times) != 1 or not isinstance(days, list) or not days or any(type(d) is not int or not 0 <= d <= 6 for d in days):
            raise PushError("每周排程请选择星期（0为周一）和一个时刻。")
        result["days"] = sorted(set(days))
    return result


def next_due(schedule, after, anchor=None):
    kind = schedule["kind"]
    if kind == "manual":
        return None
    if kind == "once":
        due = ZONE.localize(datetime.fromisoformat(schedule["at"])).timestamp()
        return due if due > after else None
    if kind == "interval":
        period = schedule["minutes"] * 60
        anchor = after if anchor is None else anchor
        return anchor + (max(0, int((after - anchor) // period)) + 1) * period
    local = datetime.fromtimestamp(after, ZONE)
    for offset in range(8):
        day = local.date() + timedelta(days=offset)
        if kind == "weekly" and day.weekday() not in schedule["days"]:
            continue
        for clock in schedule["times"]:
            hour, minute = map(int, clock.split(":"))
            due = ZONE.localize(datetime(day.year, day.month, day.day, hour, minute)).timestamp()
            if due > after:
                return due
    raise PushError("无法计算下次排程。")
