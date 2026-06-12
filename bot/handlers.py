import logging
from datetime import datetime, timezone, timedelta, date

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import db.supabase as db
from wellpass import auth, api
from core.crypto import encrypt, decrypt
from bot.keyboards import (
    schedule_keyboard,
    studios_keyboard,
    studio_results_keyboard,
    watches_keyboard,
    bookings_keyboard,
    filter_keyboard,
    studio_picker_keyboard,
    cancel_keyboard,
)
from bot.messages import (
    format_schedule,
    format_watches,
    format_bookings,
    format_booking_confirmation,
    format_cancel_warning,
)

logger = logging.getLogger(__name__)

# ── Conversation states ────────────────────────────────────────────────────────
SETUP_EMAIL, SETUP_PASSWORD = 1, 2
STUDIO_SEARCH = 10
FILTER_ADD_INPUT = 20


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _get_user_or_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> dict | None:
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        await update.effective_message.reply_text(
            "Du bist noch nicht eingerichtet. Tippe /start um loszulegen."
        )
    return user


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


# ── /start ─────────────────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if user:
        await update.message.reply_text(
            f"Willkommen zurück! 🏋️\n\n"
            f"Befehle:\n"
            f"/schedule — Wochenplan\n"
            f"/studios — Studios verwalten\n"
            f"/watching — Aktive Überwachungen\n"
            f"/bookings — Buchungshistorie\n"
            f"/filter — Klassen-Filter\n"
            f"/stop — Alle Watches stoppen\n"
            f"/help — Hilfe"
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "Willkommen beim Wellpass Gym Bot! 🏋️\n\n"
        "Ich buche automatisch Kursplätze sobald sie frei werden.\n\n"
        "Bitte gib deine Wellpass E-Mail-Adresse ein:"
    )
    return SETUP_EMAIL


async def received_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    email = update.message.text.strip()
    context.user_data["setup_email"] = email
    await update.message.reply_text("Und dein Wellpass-Passwort:")
    return SETUP_PASSWORD


async def received_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    password = update.message.text.strip()
    email = context.user_data.get("setup_email", "")

    # Delete password message for security
    try:
        await context.bot.delete_message(update.effective_chat.id, update.message.message_id)
    except Exception:
        pass

    msg = await update.effective_chat.send_message("⏳ Verbinde mit Wellpass...")

    try:
        exerciser_id, jsessionid, display_name = await auth.login(email, password)
        token_data = await auth.fetch_fls_token(exerciser_id, jsessionid)
        now = datetime.now(timezone.utc)
        session_exp = (now + timedelta(hours=3)).isoformat()

        encrypted_pass = encrypt(password)
        existing = await db.get_user_by_telegram_id(update.effective_user.id)
        if existing:
            await db.update_user_session(
                existing["id"], exerciser_id, jsessionid, session_exp,
                token_data.accessToken, token_data.expires_at_utc().isoformat(),
            )
        else:
            await db.create_user(
                telegram_id=update.effective_user.id,
                telegram_name=update.effective_user.full_name,
                wellpass_email=email,
                wellpass_pass_encrypted=encrypted_pass,
                exerciser_id=exerciser_id,
                jsessionid=jsessionid,
                session_expires=session_exp,
                access_token=token_data.accessToken,
                token_expires=token_data.expires_at_utc().isoformat(),
            )

        # Auto-import Wellpass favourites as studios
        user_record = await db.get_user_by_telegram_id(update.effective_user.id)
        fav_text = ""
        if user_record:
            try:
                favs = await api.get_user_favourites(token_data.accessToken)
                for fav in favs:
                    await db.add_user_studio(
                        user_record["id"],
                        fav["serverGymsId"],
                        fav["name"],
                        fav["slug"],
                    )
                if favs:
                    fav_text = f"\n\n📍 {len(favs)} Wellpass-Favorit(en) automatisch importiert."
            except Exception:
                pass

        await msg.edit_text(
            f"✅ Verbunden als *{display_name}*!{fav_text}\n\n"
            f"Deine Studios verwalten: /studios",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"Login failed for {email}: {e}")
        await msg.edit_text(
            "❌ Login fehlgeschlagen. Bitte prüfe E-Mail und Passwort.\n\n"
            "Versuche es nochmal mit /start"
        )

    context.user_data.clear()
    return ConversationHandler.END


async def cancel_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Setup abgebrochen.")
    return ConversationHandler.END


# ── /studios ───────────────────────────────────────────────────────────────────

async def studios_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _get_user_or_prompt(update, context)
    if not user:
        return
    studios = await db.get_user_studios(user["id"])
    if studios:
        text = f"🏋️ *Deine Studios ({len(studios)}):*"
    else:
        text = "Du hast noch keine Studios hinzugefügt."
    await update.message.reply_text(
        text,
        reply_markup=studios_keyboard(studios),
        parse_mode=ParseMode.MARKDOWN,
    )


async def studio_remove_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = await db.get_user_by_telegram_id(query.from_user.id)
    if not user:
        return
    gym_id = query.data.split(":", 1)[1]
    await db.remove_user_studio(user["id"], gym_id)
    studios = await db.get_user_studios(user["id"])
    await query.edit_message_text(
        "Studio entfernt." if not studios else f"🏋️ *Deine Studios ({len(studios)}):*",
        reply_markup=studios_keyboard(studios),
        parse_mode=ParseMode.MARKDOWN,
    )


async def studio_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("Gib den Studio-Namen oder die Stadt ein:")
    return STUDIO_SEARCH


async def studio_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return ConversationHandler.END

    query_text = update.message.text.strip()
    msg = await update.message.reply_text("🔍 Suche...")

    try:
        token = await auth.get_valid_token(user, db)
        gyms = await api.search_gyms(query_text, token)
        if not gyms:
            await msg.edit_text("Keine Studios gefunden. Versuche einen anderen Suchbegriff.")
            return ConversationHandler.END

        context.user_data["gym_search"] = {g["serverGymsId"]: g for g in gyms[:10]}
        await msg.edit_text(
            f"Gefunden ({min(len(gyms), 10)} Studios):",
            reply_markup=studio_results_keyboard(gyms),
        )
    except Exception as e:
        logger.error(f"Studio search error: {e}")
        await msg.edit_text("Fehler bei der Suche. Versuche es erneut.")

    return ConversationHandler.END


async def studio_add_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = await db.get_user_by_telegram_id(query.from_user.id)
    if not user:
        return

    gym_id = query.data.split(":", 1)[1]
    gym_cache = context.user_data.get("gym_search", {})
    gym = gym_cache.get(gym_id)

    if not gym:
        await query.answer("Studio nicht mehr verfügbar. Bitte erneut suchen.", show_alert=True)
        return

    gym_name = gym.get("name", gym_id)
    gym_slug = gym.get("slug", "")
    await db.add_user_studio(user["id"], gym_id, gym_name, gym_slug)
    await query.edit_message_text(f"✅ *{gym_name}* hinzugefügt!", parse_mode=ParseMode.MARKDOWN)


async def studio_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Abgebrochen.")


# ── /schedule ──────────────────────────────────────────────────────────────────

async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _get_user_or_prompt(update, context)
    if not user:
        return

    studios = await db.get_user_studios(user["id"])
    if not studios:
        await update.message.reply_text("Du hast noch keine Studios. Füge eines hinzu mit /studios")
        return

    if len(studios) > 1:
        await update.message.reply_text(
            "Für welches Studio soll ich den Stundenplan anzeigen?",
            reply_markup=studio_picker_keyboard(studios, "schedule_studio"),
        )
        return

    await _send_schedule(update.effective_chat, user, studios[0], context.bot)


async def schedule_studio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = await db.get_user_by_telegram_id(query.from_user.id)
    if not user:
        return

    gym_id = query.data.split(":", 1)[1]
    studios = await db.get_user_studios(user["id"])
    studio = next((s for s in studios if s["gym_id"] == gym_id), None)
    if not studio:
        await query.edit_message_text("Studio nicht gefunden.")
        return

    await query.message.delete()
    await _send_schedule(query.message.chat, user, studio, context.bot)


async def _send_schedule(chat, user: dict, studio: dict, bot, target_date: date | None = None) -> None:
    if target_date is None:
        target_date = datetime.now(timezone.utc).date()
    msg = await bot.send_message(chat.id, "⏳ Lade Stundenplan...")
    try:
        text, keyboard = await _fetch_schedule(user, studio, target_date)
        await msg.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Schedule fetch error: {e}")
        await msg.edit_text("Fehler beim Laden des Stundenplans. Versuche es erneut.")


async def _fetch_schedule(user: dict, studio: dict, target_date: date):
    token = await auth.get_valid_token(user, db)
    from_dt = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=timezone.utc)
    to_dt = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=timezone.utc)
    sessions = await api.get_schedule(studio["gym_id"], token, from_dt, to_dt)
    filters_list = await db.get_class_filters(user["id"], studio["gym_id"])
    if filters_list:
        filter_names = {f["class_name"].lower() for f in filters_list}
        sessions = [s for s in sessions if s["name"].lower() in filter_names]
    text = format_schedule(sessions, studio["gym_name"], filters_list, target_date)
    keyboard = schedule_keyboard(sessions, studio["gym_id"], target_date)
    return text, keyboard


async def schedule_nav_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = await db.get_user_by_telegram_id(query.from_user.id)
    if not user:
        return
    parts = query.data.split(":")
    gym_id, date_str = parts[1], parts[2]
    studios = await db.get_user_studios(user["id"])
    studio = next((s for s in studios if s["gym_id"] == gym_id), None)
    if not studio:
        await query.answer("Studio nicht gefunden.", show_alert=True)
        return
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        text, keyboard = await _fetch_schedule(user, studio, target_date)
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Schedule nav error: {e}")
        await query.answer("Fehler beim Laden.", show_alert=True)


# ── Book & Watch callbacks ─────────────────────────────────────────────────────

async def book_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Buche...")
    user = await db.get_user_by_telegram_id(query.from_user.id)
    if not user:
        return

    session_id = query.data.split(":", 1)[1]
    try:
        token = await auth.get_valid_token(user, db)
        session = await api.get_session(session_id, token)

        if not api.is_available(session):
            await query.answer("Leider kein freier Platz mehr.", show_alert=True)
            return

        gym_id = session.get("gym", {}).get("serverGymsId", "")
        date_str = session["startDateTime"][:10]
        if await db.has_booking_today(user["id"], gym_id, date_str):
            await query.answer("Du hast heute bei diesem Studio bereits gebucht (Limit: 1/Tag).", show_alert=True)
            return

        booking = await api.book_class(session_id, token)
        gym_name = session.get("gym", {}).get("name", "Studio")

        await db.add_booking(
            user_id=user["id"],
            watch_id=None,
            booking_id=booking["id"],
            gym_id=gym_id,
            gym_name=gym_name,
            class_name=session["name"],
            start_datetime=session["startDateTime"],
        )

        text = format_booking_confirmation(booking, session, gym_name)
        kb = cancel_keyboard(booking["id"], booking["id"])
        await query.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        await query.answer("✅ Gebucht!")
    except Exception as e:
        logger.error(f"Book error: {e}")
        await query.answer(f"Buchung fehlgeschlagen: {str(e)[:100]}", show_alert=True)


async def watch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = await db.get_user_by_telegram_id(query.from_user.id)
    if not user:
        return

    session_id = query.data.split(":", 1)[1]
    if await db.watch_exists(user["id"], session_id):
        await query.answer("Du beobachtest diese Klasse bereits.", show_alert=True)
        return

    try:
        token = await auth.get_valid_token(user, db)
        session = await api.get_session(session_id, token)
        booking_end = _dt(session["bookingWindowEnd"])
        if booking_end <= datetime.now(timezone.utc):
            await query.answer("Das Buchungsfenster für diese Klasse ist abgelaufen.", show_alert=True)
            return

        gym = session.get("gym", {})
        gym_id = gym.get("serverGymsId", "")
        gym_name = gym.get("name", "Studio")

        studios = await db.get_user_studios(user["id"])
        if not any(s["gym_id"] == gym_id for s in studios):
            await db.add_user_studio(user["id"], gym_id, gym_name, gym.get("slug", ""))

        await db.add_watch(
            user_id=user["id"],
            gym_id=gym_id,
            gym_name=gym_name,
            session_id=session_id,
            class_name=session["name"],
            start_datetime=session["startDateTime"],
        )

        start = _dt(session["startDateTime"])
        await query.answer(
            f"👁 Beobachte {session['name']} am {start.strftime('%a %d.%m %H:%M')}",
            show_alert=True,
        )
    except Exception as e:
        logger.error(f"Watch error: {e}")
        await query.answer(f"Fehler: {str(e)[:100]}", show_alert=True)


# ── /watching ──────────────────────────────────────────────────────────────────

async def watching_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _get_user_or_prompt(update, context)
    if not user:
        return
    watches = await db.get_watches_for_user(user["id"])
    text = format_watches(watches)
    kb = watches_keyboard(watches) if watches else None
    await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


async def watch_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    watch_id = query.data.split(":", 1)[1]
    await db.cancel_watch(watch_id)
    user = await db.get_user_by_telegram_id(query.from_user.id)
    watches = await db.get_watches_for_user(user["id"]) if user else []
    text = format_watches(watches)
    kb = watches_keyboard(watches) if watches else None
    await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


# ── /bookings ─────────────────────────────────────────────────────────────────

async def bookings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _get_user_or_prompt(update, context)
    if not user:
        return
    bookings = await db.get_user_bookings_history(user["id"], limit=10)
    text = format_bookings(bookings)
    kb = bookings_keyboard(bookings) if bookings else None
    await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


async def booking_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Storniere...")
    user = await db.get_user_by_telegram_id(query.from_user.id)
    if not user:
        return

    parts = query.data.split(":")
    booking_id_wellpass = parts[1]

    try:
        token = await auth.get_valid_token(user, db)
        success = await api.cancel_booking(booking_id_wellpass, token)
        if success:
            await db.cancel_booking_record(booking_id_wellpass)
            await query.edit_message_text("✅ Buchung storniert.")
        else:
            await query.answer("Stornierung fehlgeschlagen.", show_alert=True)
    except Exception as e:
        logger.error(f"Cancel booking error: {e}")
        await query.answer(f"Fehler: {str(e)[:100]}", show_alert=True)


# ── /filter ────────────────────────────────────────────────────────────────────

async def filter_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _get_user_or_prompt(update, context)
    if not user:
        return
    studios = await db.get_user_studios(user["id"])
    if not studios:
        await update.message.reply_text("Füge zuerst ein Studio hinzu mit /studios")
        return

    if len(studios) > 1:
        await update.message.reply_text(
            "Filter für welches Studio?",
            reply_markup=studio_picker_keyboard(studios, "filter_studio"),
        )
        return

    await _show_filters(update.effective_chat, user, studios[0], context.bot)


async def filter_studio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = await db.get_user_by_telegram_id(query.from_user.id)
    if not user:
        return
    gym_id = query.data.split(":", 1)[1]
    studios = await db.get_user_studios(user["id"])
    studio = next((s for s in studios if s["gym_id"] == gym_id), None)
    if not studio:
        return
    await query.message.delete()
    await _show_filters(query.message.chat, user, studio, context.bot)


async def _show_filters(chat, user: dict, studio: dict, bot) -> None:
    filters_list = await db.get_class_filters(user["id"], studio["gym_id"])
    text = (
        f"📋 *Filter für {studio['gym_name']}:*\n\n"
        f"Ohne Filter werden alle Klassen angezeigt."
    )
    await bot.send_message(
        chat.id,
        text,
        reply_markup=filter_keyboard(filters_list, studio["gym_id"]),
        parse_mode=ParseMode.MARKDOWN,
    )


async def filter_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    gym_id = query.data.split(":", 1)[1]
    context.user_data["filter_gym_id"] = gym_id
    await query.message.reply_text(
        "Wie heißt die Klasse? (z.B. Strength, Mobility, HYROX)"
    )
    return FILTER_ADD_INPUT


async def filter_add_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return ConversationHandler.END

    class_name = update.message.text.strip()
    gym_id = context.user_data.get("filter_gym_id", "")
    if not gym_id:
        await update.message.reply_text("Fehler: Kein Studio ausgewählt.")
        return ConversationHandler.END

    await db.add_class_filter(user["id"], gym_id, class_name)

    studios = await db.get_user_studios(user["id"])
    studio = next((s for s in studios if s["gym_id"] == gym_id), None)
    studio_name = studio["gym_name"] if studio else gym_id

    filters_list = await db.get_class_filters(user["id"], gym_id)
    await update.message.reply_text(
        f"✅ Filter *{class_name}* hinzugefügt!\n\n"
        f"📋 *Filter für {studio_name}:*",
        reply_markup=filter_keyboard(filters_list, gym_id),
        parse_mode=ParseMode.MARKDOWN,
    )
    context.user_data.pop("filter_gym_id", None)
    return ConversationHandler.END


async def filter_remove_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    filter_id = query.data.split(":", 1)[1]
    await db.remove_class_filter(filter_id)
    await query.answer("Filter entfernt.", show_alert=True)
    await query.edit_message_reply_markup(reply_markup=None)


async def filter_clear_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = await db.get_user_by_telegram_id(query.from_user.id)
    if not user:
        return
    gym_id = query.data.split(":", 1)[1]
    await db.clear_class_filters(user["id"], gym_id)
    await query.edit_message_text("🗑 Alle Filter entfernt.")


# ── /stop ──────────────────────────────────────────────────────────────────────

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _get_user_or_prompt(update, context)
    if not user:
        return
    watches = await db.get_watches_for_user(user["id"])
    if not watches:
        await update.message.reply_text("Keine aktiven Überwachungen.")
        return
    for w in watches:
        await db.cancel_watch(w["id"])
    await update.message.reply_text(f"✅ {len(watches)} Überwachung(en) gestoppt.")


# ── /help ──────────────────────────────────────────────────────────────────────

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*Wellpass Gym Bot* — Befehle:\n\n"
        "/start — Setup / Account verknüpfen\n"
        "/schedule — Wochenplan mit Buchungs-Buttons\n"
        "/studios — Studios hinzufügen & verwalten\n"
        "/watching — Aktive Überwachungen\n"
        "/bookings — Buchungshistorie\n"
        "/filter — Klassen-Filter verwalten\n"
        "/stop — Alle Überwachungen stoppen\n"
        "/help — Diese Hilfe",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Noop callback ─────────────────────────────────────────────────────────────

async def noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()


# ── Handler registration ───────────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    setup_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start_command)],
        states={
            SETUP_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_email)],
            SETUP_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_password)],
        },
        fallbacks=[CommandHandler("cancel", cancel_setup)],
        name="setup",
        persistent=False,
    )

    studio_search_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(studio_search_start, pattern="^studio_search$")],
        states={
            STUDIO_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, studio_search_handler)],
        },
        fallbacks=[CommandHandler("cancel", cancel_setup)],
        name="studio_search",
        persistent=False,
        per_message=False,
    )

    filter_add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(filter_add_start, pattern="^filter_add:")],
        states={
            FILTER_ADD_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, filter_add_handler)],
        },
        fallbacks=[CommandHandler("cancel", cancel_setup)],
        name="filter_add",
        persistent=False,
        per_message=False,
    )

    app.add_handler(setup_conv)
    app.add_handler(studio_search_conv)
    app.add_handler(filter_add_conv)

    app.add_handler(CommandHandler("studios", studios_command))
    app.add_handler(CommandHandler("schedule", schedule_command))
    app.add_handler(CommandHandler("watching", watching_command))
    app.add_handler(CommandHandler("bookings", bookings_command))
    app.add_handler(CommandHandler("filter", filter_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("help", help_command))

    app.add_handler(CallbackQueryHandler(schedule_nav_callback, pattern="^sched_nav:"))
    app.add_handler(CallbackQueryHandler(book_callback, pattern="^book:"))
    app.add_handler(CallbackQueryHandler(watch_callback, pattern="^watch:"))
    app.add_handler(CallbackQueryHandler(watch_cancel_callback, pattern="^wcancel:"))
    app.add_handler(CallbackQueryHandler(booking_cancel_callback, pattern="^bcancel:"))
    app.add_handler(CallbackQueryHandler(studio_remove_callback, pattern="^studio_rm:"))
    app.add_handler(CallbackQueryHandler(studio_add_callback, pattern="^studio_add:"))
    app.add_handler(CallbackQueryHandler(studio_cancel_callback, pattern="^studio_cancel$"))
    app.add_handler(CallbackQueryHandler(schedule_studio_callback, pattern="^schedule_studio:"))
    app.add_handler(CallbackQueryHandler(filter_studio_callback, pattern="^filter_studio:"))
    app.add_handler(CallbackQueryHandler(filter_remove_callback, pattern="^filter_rm:"))
    app.add_handler(CallbackQueryHandler(filter_clear_callback, pattern="^filter_clear:"))
    app.add_handler(CallbackQueryHandler(noop_callback, pattern="^noop:"))
