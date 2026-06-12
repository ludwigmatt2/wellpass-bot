from datetime import datetime, timezone
from collections import defaultdict


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _week_number(dt: datetime) -> int:
    return dt.isocalendar()[1]


def format_schedule(sessions: list, gym_name: str, active_filters: list) -> str:
    if not sessions:
        return f"📅 Keine Klassen gefunden für {gym_name}."

    filter_names = {f["class_name"].lower() for f in active_filters}
    if filter_names:
        sessions = [s for s in sessions if s["name"].lower() in filter_names]

    if not sessions:
        return f"📅 Keine Klassen für deine Filter gefunden.\nFilter aktiv: {', '.join(f['class_name'] for f in active_filters)}"

    sessions = sorted(sessions, key=lambda s: s["startDateTime"])
    by_day: dict[str, list] = defaultdict(list)
    for s in sessions:
        day_key = s["startDateTime"][:10]
        by_day[day_key].append(s)

    now = datetime.now(timezone.utc)
    week = _week_number(now)
    lines = [f"📅 *Wochenplan KW {week} — {gym_name}*"]
    if filter_names:
        lines.append(f"_Filter: {', '.join(f['class_name'] for f in active_filters)}_")
    lines.append("")

    day_names = {0: "Mo", 1: "Di", 2: "Mi", 3: "Do", 4: "Fr", 5: "Sa", 6: "So"}
    for day_key, day_sessions in sorted(by_day.items()):
        dt = _dt(day_sessions[0]["startDateTime"])
        day_label = f"{day_names[dt.weekday()]} {dt.strftime('%d.%m')}"
        lines.append(f"━━━ *{day_label}* ━━━")
        for s in day_sessions:
            start = _dt(s["startDateTime"])
            free = s["capacity"] - s["booked"]
            emoji = "🔴" if free == 0 else ("🟡" if free <= 3 else "🟢")
            instructor = s.get("instructor", {})
            trainer = instructor.get("fullName", "—") if instructor else "—"
            lines.append(
                f"{emoji} {s['name']:<18} {start.strftime('%H:%M')}  {trainer:<12} {s['booked']}/{s['capacity']}"
            )
        lines.append("")

    return "\n".join(lines).rstrip()


def format_watches(watches: list) -> str:
    if not watches:
        return "Du beobachtest gerade keine Klassen."
    lines = ["👁 *Aktive Überwachungen:*\n"]
    for w in watches:
        start = _dt(w["start_datetime"])
        lines.append(
            f"• {w['class_name']} — {start.strftime('%a %d.%m %H:%M')} ({w['gym_name']})"
        )
    return "\n".join(lines)


def format_bookings(bookings: list) -> str:
    if not bookings:
        return "Keine Buchungen in der Historie."
    lines = ["📋 *Letzte Buchungen:*\n"]
    status_emoji = {
        "BOOKED": "✅",
        "CANCELLED": "❌",
        "CHECKED_IN": "🏃",
        "NO_SHOW": "💸",
    }
    for b in bookings:
        start = _dt(b["start_datetime"]) if b.get("start_datetime") else None
        time_str = start.strftime("%a %d.%m %H:%M") if start else "?"
        emoji = status_emoji.get(b.get("status", ""), "•")
        lines.append(f"{emoji} {b['class_name']} — {time_str} ({b.get('gym_name', '?')})")
    return "\n".join(lines)


def format_booking_confirmation(booking: dict, session: dict, gym_name: str) -> str:
    start = _dt(session["startDateTime"])
    instructor = session.get("instructor", {})
    trainer = instructor.get("fullName", "—") if instructor else "—"
    checkin_start = _dt(session.get("checkinStart", session["startDateTime"]))
    return (
        f"🎉 *Gebucht!*\n\n"
        f"*{session['name']}*\n"
        f"📅 {start.strftime('%a %d.%m.%Y')} um {start.strftime('%H:%M')} Uhr\n"
        f"🏋️ {gym_name}\n"
        f"👤 Trainer: {trainer}\n\n"
        f"_Check-in ab {checkin_start.strftime('%H:%M')} Uhr vor Ort per QR-Code._"
    )


def format_cancel_warning(booking: dict) -> str:
    start = _dt(booking["start_datetime"])
    return (
        f"⚠️ *Stornierungsfenster läuft ab!*\n\n"
        f"{booking['class_name']} — {start.strftime('%a %d.%m.%Y %H:%M')}\n"
        f"In ca. 1 Stunde greift die Late-Cancel Gebühr von 5€.\n\n"
        f"Möchtest du stornieren?"
    )
