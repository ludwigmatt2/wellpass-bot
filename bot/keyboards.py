from datetime import datetime, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def schedule_keyboard(sessions: list) -> InlineKeyboardMarkup:
    rows = []
    now = datetime.now(timezone.utc)
    for s in sessions:
        booking_end = _dt(s["bookingWindowEnd"])
        free = s["capacity"] - s["booked"]
        name = s["name"]
        start = _dt(s["startDateTime"])
        time_str = start.strftime("%a %H:%M")
        emoji = "🔴" if free == 0 else ("🟡" if free <= 3 else "🟢")
        label = f"{emoji} {name} {time_str} ({s['booked']}/{s['capacity']})"
        if free > 0 and booking_end > now:
            rows.append([
                InlineKeyboardButton(f"✅ {name} {time_str}", callback_data=f"book:{s['id']}"),
                InlineKeyboardButton("👁", callback_data=f"watch:{s['id']}"),
            ])
        else:
            rows.append([
                InlineKeyboardButton(f"👁 {name} {time_str}", callback_data=f"watch:{s['id']}"),
            ])
    return InlineKeyboardMarkup(rows)


def studios_keyboard(studios: list) -> InlineKeyboardMarkup:
    rows = []
    for studio in studios:
        rows.append([
            InlineKeyboardButton(
                f"🏋️ {studio['gym_name']}",
                callback_data=f"noop:{studio['gym_id']}",
            ),
            InlineKeyboardButton("❌", callback_data=f"studio_rm:{studio['gym_id']}"),
        ])
    rows.append([InlineKeyboardButton("➕ Studio hinzufügen", callback_data="studio_search")])
    return InlineKeyboardMarkup(rows)


def studio_results_keyboard(gyms: list) -> InlineKeyboardMarkup:
    rows = []
    for gym in gyms[:10]:
        gym_id = gym.get("serverGymsId", "")
        name = gym.get("name", "")
        rows.append([
            InlineKeyboardButton(f"✅ {name[:35]}", callback_data=f"studio_add:{gym_id}"),
        ])
    rows.append([InlineKeyboardButton("❌ Abbrechen", callback_data="studio_cancel")])
    return InlineKeyboardMarkup(rows)


def watches_keyboard(watches: list) -> InlineKeyboardMarkup:
    rows = []
    for w in watches:
        start = _dt(w["start_datetime"])
        label = f"{w['class_name']} {start.strftime('%a %d.%m %H:%M')}"
        rows.append([
            InlineKeyboardButton(label[:40], callback_data=f"noop:{w['id']}"),
            InlineKeyboardButton("❌", callback_data=f"wcancel:{w['id']}"),
        ])
    return InlineKeyboardMarkup(rows)


def bookings_keyboard(bookings: list) -> InlineKeyboardMarkup:
    rows = []
    now = datetime.now(timezone.utc)
    for b in bookings:
        if b["status"] != "BOOKED" or not b.get("start_datetime"):
            continue
        start = _dt(b["start_datetime"])
        diff_hours = (start - now).total_seconds() / 3600
        label = f"{b['class_name']} {start.strftime('%a %d.%m %H:%M')}"
        row = [InlineKeyboardButton(label[:40], callback_data=f"noop:{b['id']}")]
        if diff_hours > 0:
            row.append(InlineKeyboardButton("❌ Stornieren", callback_data=f"bcancel:{b['booking_id']}:{b['id']}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows) if rows else InlineKeyboardMarkup([])


def filter_keyboard(filters: list, gym_id: str) -> InlineKeyboardMarkup:
    rows = []
    for f in filters:
        rows.append([
            InlineKeyboardButton(f"📋 {f['class_name']}", callback_data=f"noop:{f['id']}"),
            InlineKeyboardButton("❌", callback_data=f"filter_rm:{f['id']}"),
        ])
    rows.append([InlineKeyboardButton("➕ Filter hinzufügen", callback_data=f"filter_add:{gym_id}")])
    if filters:
        rows.append([InlineKeyboardButton("🗑 Alle entfernen", callback_data=f"filter_clear:{gym_id}")])
    return InlineKeyboardMarkup(rows)


def studio_picker_keyboard(studios: list, prefix: str) -> InlineKeyboardMarkup:
    rows = []
    for studio in studios:
        rows.append([
            InlineKeyboardButton(
                studio["gym_name"],
                callback_data=f"{prefix}:{studio['gym_id']}",
            )
        ])
    return InlineKeyboardMarkup(rows)


def cancel_keyboard(booking_id_wellpass: str, booking_db_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Stornieren", callback_data=f"bcancel:{booking_id_wellpass}:{booking_db_id}"),
    ]])
