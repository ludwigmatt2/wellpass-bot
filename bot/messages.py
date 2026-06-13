from datetime import datetime, date
from zoneinfo import ZoneInfo

_BERLIN = ZoneInfo("Europe/Berlin")


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _local(iso: str) -> datetime:
    return _dt(iso).astimezone(_BERLIN)


_DAY_NAMES = {0: "Mo", 1: "Di", 2: "Mi", 3: "Do", 4: "Fr", 5: "Sa", 6: "So"}


def format_schedule(sessions: list, gym_name: str, active_filters: list, target_date: date | None = None) -> str:
    lines = [f"🏋️ *{gym_name}*"]
    if target_date:
        lines.append(f"*{_DAY_NAMES[target_date.weekday()]} {target_date.strftime('%d.%m.%Y')}*")
    if active_filters:
        lines.append(f"_Filter: {', '.join(f['class_name'] for f in active_filters)}_")
    filter_names = {f["class_name"].lower() for f in active_filters}
    visible = [s for s in sessions if s["name"].lower() in filter_names] if filter_names else sessions
    if not visible:
        lines.append("\nKeine Klassen für diesen Tag.")
    return "\n".join(lines)


def format_watches(watches: list) -> str:
    if not watches:
        return "Du beobachtest gerade keine Klassen."
    lines = ["👁 *Aktive Überwachungen:*\n"]
    for w in watches:
        start = _dt(w["start_datetime"]).astimezone(_BERLIN)
        lines.append(f"• {w['class_name']} — {start.strftime('%a %d.%m %H:%M')} ({w['gym_name']})")
    return "\n".join(lines)


def format_bookings(bookings: list) -> str:
    if not bookings:
        return "Keine Buchungen in der Historie."
    lines = ["📋 *Letzte Buchungen:*\n"]
    status_emoji = {"BOOKED": "✅", "CANCELLED": "❌", "CHECKED_IN": "🏃", "NO_SHOW": "💸"}
    for b in bookings:
        start = _dt(b["start_datetime"]).astimezone(_BERLIN) if b.get("start_datetime") else None
        time_str = start.strftime("%a %d.%m %H:%M") if start else "?"
        emoji = status_emoji.get(b.get("status", ""), "•")
        lines.append(f"{emoji} {b['class_name']} — {time_str} ({b.get('gym_name', '?')})")
    return "\n".join(lines)


def format_booking_confirmation(booking: dict, session: dict, gym_name: str) -> str:
    start = _local(session["startDateTime"])
    instructor = session.get("instructor", {})
    trainer = instructor.get("fullName", "—") if instructor else "—"
    checkin_start = _local(session.get("checkinStart", session["startDateTime"]))
    return (
        f"🎉 *Gebucht!*\n\n"
        f"*{session['name']}*\n"
        f"📅 {start.strftime('%a %d.%m.%Y')} um {start.strftime('%H:%M')} Uhr\n"
        f"🏋️ {gym_name}\n"
        f"👤 Trainer: {trainer}\n\n"
        f"_Check-in ab {checkin_start.strftime('%H:%M')} Uhr vor Ort per QR-Code._"
    )


def format_cancel_warning(booking: dict) -> str:
    start = _dt(booking["start_datetime"]).astimezone(_BERLIN)
    return (
        f"⚠️ *Stornierungsfenster läuft ab!*\n\n"
        f"{booking['class_name']} — {start.strftime('%a %d.%m.%Y %H:%M')}\n"
        f"In ca. 1 Stunde greift die Late-Cancel Gebühr von 5€.\n\n"
        f"Möchtest du stornieren?"
    )
