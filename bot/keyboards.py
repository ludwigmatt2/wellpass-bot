from datetime import datetime, timezone, date, timedelta
from zoneinfo import ZoneInfo
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

_BERLIN = ZoneInfo("Europe/Berlin")


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _local(iso: str) -> datetime:
    return _dt(iso).astimezone(_BERLIN)


def schedule_keyboard(sessions: list, gym_id: str, target_date: date, studios: list | None = None) -> InlineKeyboardMarkup:
    rows = []
    now = datetime.now(timezone.utc)
    date_str = target_date.strftime("%Y-%m-%d")

    # Studio switcher row (only when user has multiple studios)
    if studios and len(studios) > 1:
        idx = next((i for i, s in enumerate(studios) if s["gym_id"] == gym_id), 0)
        nav = []
        if idx > 0:
            prev_s = studios[idx - 1]
            nav.append(InlineKeyboardButton(f"← {prev_s['gym_name'][:14]}", callback_data=f"sched_studio:{prev_s['gym_id']}:{date_str}"))
        if idx < len(studios) - 1:
            next_s = studios[idx + 1]
            nav.append(InlineKeyboardButton(f"{next_s['gym_name'][:14]} →", callback_data=f"sched_studio:{next_s['gym_id']}:{date_str}"))
        if nav:
            rows.append(nav)

    # Day navigation row
    prev_date = (target_date - timedelta(days=1)).strftime("%Y-%m-%d")
    next_date = (target_date + timedelta(days=1)).strftime("%Y-%m-%d")
    today = datetime.now(timezone.utc).date()
    nav_row = []
    if target_date > today:
        nav_row.append(InlineKeyboardButton("← Zurück", callback_data=f"sched_nav:{gym_id}:{prev_date}"))
    nav_row.append(InlineKeyboardButton("Vorwärts →", callback_data=f"sched_nav:{gym_id}:{next_date}"))
    rows.append(nav_row)

    for s in sessions:
        booking_end = _dt(s["bookingWindowEnd"])
        free = s["capacity"] - s["booked"]
        start = _local(s["startDateTime"])
        time_str = start.strftime("%H:%M")
        name = s["name"]
        instructor = s.get("instructor") or {}
        trainer = instructor.get("fullName", "")
        label = f"{time_str}  {name}"
        if trainer:
            label += f" · {trainer}"
        if free > 0 and booking_end > now:
            rows.append([InlineKeyboardButton(f"✅ {label}", callback_data=f"book:{s['id']}")])
        else:
            rows.append([InlineKeyboardButton(f"👁 {label}", callback_data=f"watch:{s['id']}")])
    return InlineKeyboardMarkup(rows)


def studios_keyboard(studios: list) -> InlineKeyboardMarkup:
    rows = []
    for studio in studios[:20]:
        name = studio["gym_name"][:35]
        rows.append([InlineKeyboardButton(f"🏋️ {name}", callback_data=f"schedule_studio:{studio['gym_id']}")])
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
        start = _dt(w["start_datetime"]).astimezone(_BERLIN)
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
        start_local = start.astimezone(_BERLIN)
        label = f"{b['class_name']} {start_local.strftime('%a %d.%m %H:%M')}"
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
