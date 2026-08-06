import os
import json
import logging
import base64
from datetime import datetime, time as dtime
import anthropic
import gspread
from gspread.utils import rowcol_to_a1
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
CHAT_ID = os.environ.get("CHAT_ID")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN topilmadi!")
if not SPREADSHEET_ID:
    raise RuntimeError("SPREADSHEET_ID topilmadi!")
if not GOOGLE_CREDENTIALS:
    raise RuntimeError("GOOGLE_CREDENTIALS topilmadi!")
if not ANTHROPIC_API_KEY:
    raise RuntimeError("ANTHROPIC_API_KEY topilmadi!")

user_states = {}

def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDENTIALS)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).sheet1
    return sheet

def rebuild_sheet(sheet):
    """
    Sheets dagi barcha bitimlarni o'qib, qayta tartiblab yozadi:
    - Har kun bitimlarini guruhlab
    - Har kun tagiga J/K ga kun totali
    - Eng pastga umumiy balance
    - TP qatori yashil, SL qatori qizil fon, kun total qatori ko'k
    """
    all_rows = sheet.get_all_values()
    if len(all_rows) <= 1:
        return

    # Faqat bitim qatorlarini olish (sarlavha va total qatorlarini o'tkazib yuborish)
    trades = []
    for row in all_rows[1:]:
        if len(row) >= 3 and row[2] in ["TP", "SL"]:
            trades.append(row)

    if not trades:
        return

    # Sanalar bo'yicha guruhlash
    from collections import OrderedDict
    days = OrderedDict()
    for trade in trades:
        date_part = trade[0].split(" ")[0] if trade[0] else ""
        if date_part not in days:
            days[date_part] = []
        days[date_part].append(trade)

    # Sheets ni tozalash (sarlavhadan tashqari)
    last_col = 11  # K ustuni
    total_rows = sheet.row_count
    if total_rows > 1:
        sheet.batch_clear([f"A2:K{total_rows}"])

    # Ranglar
    GREEN_BG = {"red": 0.714, "green": 0.843, "blue": 0.659}   # TP - yashil
    RED_BG = {"red": 0.918, "green": 0.6, "blue": 0.6}         # SL - qizil
    BLUE_BG = {"red": 0.643, "green": 0.761, "blue": 0.957}    # Kun total - ko'k
    GOLD_BG = {"red": 1.0, "green": 0.851, "blue": 0.4}        # Balance - oltin

    current_row = 2
    total_pnl = 0.0
    format_requests = []

    for date_str, day_trades in days.items():
        day_tp = 0
        day_sl = 0
        day_pnl = 0.0

        for trade in day_trades:
            result = trade[2] if len(trade) > 2 else ""
            lot = trade[6] if len(trade) > 6 else ""
            pnl_val = trade[7] if len(trade) > 7 else ""

            # PnL hisoblash
            try:
                pnl_float = float(pnl_val) if pnl_val else 0.0
            except ValueError:
                pnl_float = 0.0

            day_pnl += pnl_float
            total_pnl += pnl_float

            if result == "TP":
                day_tp += 1
            elif result == "SL":
                day_sl += 1

            # Qatorni yozish (A-H)
            row_data = [
                trade[0] if len(trade) > 0 else "",  # Date
                trade[1] if len(trade) > 1 else "",  # Instrument
                trade[2] if len(trade) > 2 else "",  # Result
                trade[3] if len(trade) > 3 else "",  # Setup
                trade[4] if len(trade) > 4 else "",  # Mistake
                trade[5] if len(trade) > 5 else "",  # Lesson
                lot,                                   # Lot
                pnl_val,                               # PnL
                "", ""                                 # I, J bo'sh
            ]
            sheet.update(f"A{current_row}:J{current_row}", [row_data])

            # Rang: TP yashil, SL qizil
            bg_color = GREEN_BG if result == "TP" else RED_BG
            format_requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet.id,
                        "startRowIndex": current_row - 1,
                        "endRowIndex": current_row,
                        "startColumnIndex": 0,
                        "endColumnIndex": 8
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": bg_color
                        }
                    },
                    "fields": "userEnteredFormat.backgroundColor"
                }
            })
            current_row += 1

        # Kun total qatori — J va K ustunlarda
        pnl_sign = "+" if day_pnl >= 0 else ""
        kun_label = f"📅 {date_str}"
        kun_stat = f"TP:{day_tp}  SL:{day_sl}  |  {pnl_sign}{day_pnl:.2f}$"

        sheet.update(f"J{current_row}:K{current_row}", [[kun_label, kun_stat]])

        # Kun total rang — ko'k
        format_requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet.id,
                    "startRowIndex": current_row - 1,
                    "endRowIndex": current_row,
                    "startColumnIndex": 9,
                    "endColumnIndex": 11
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": BLUE_BG,
                        "textFormat": {"bold": True}
                    }
                },
                "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.bold"
            }
        })
        current_row += 1

    # Eng pastga BALANCE
    pnl_sign = "+" if total_pnl >= 0 else ""
    sheet.update(f"J{current_row}:K{current_row}",
                 [["💰 JAMI BALANCE:", f"{pnl_sign}{total_pnl:.2f}$"]])

    format_requests.append({
        "repeatCell": {
            "range": {
                "sheetId": sheet.id,
                "startRowIndex": current_row - 1,
                "endRowIndex": current_row,
                "startColumnIndex": 9,
                "endColumnIndex": 11
            },
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": GOLD_BG,
                    "textFormat": {"bold": True, "fontSize": 11}
                }
            },
            "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.bold,userEnteredFormat.textFormat.fontSize"
        }
    })

    # Barcha ranglarni bir vaqtda yuborish
    if format_requests:
        sheet.spreadsheet.batch_update({"requests": format_requests})


def get_existing_trade_keys(sheet):
    all_rows = sheet.get_all_values()
    existing = set()
    for row in all_rows[1:]:
        if len(row) >= 3 and row[2] in ["TP", "SL"]:
            key = f"{row[0]}_{row[1]}_{row[2]}"
            existing.add(key)
    return existing


async def extract_trades_from_image(image_bytes: bytes, media_type: str = "image/jpeg") -> list:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    image_base64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    today = datetime.now().strftime("%d.%m.%Y")

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_base64
                        }
                    },
                    {
                        "type": "text",
                        "text": f"""Bu MetaTrader trading history screenshoti.
Rasmdan barcha bitimlarni ajratib ol va JSON formatda qaytarib ber.

Bugungi sana: {today}

Har bir bitim uchun:
- date: bitim OCHILGAN sanasi va vaqti (DD.MM.YYYY HH:MM formatda).
  Rasmda "2026.07.29 00:30:35" ko'rinsa "29.07.2026 00:30" deb yoz.
  Agar sana yo'q bo'lsa {today} ishlet.
- instrument: juftlik nomi (xauusdm -> XAUUSD, EURUSD va h.k.)
- result: musbat son -> "TP", manfiy son -> "SL"
- lot: lot hajmi (0.01, 0.02 va h.k.) — topilmasa ""
- pnl: foyda/zarar summasi (masalan 15.50 yoki -8.20) — topilmasa ""
- setup: ""
- mistake: ""
- lesson: ""

MUHIM: Har bir bitim alohida — bir xil instrument bo'lsa ham barchasi alohida!

FAQAT JSON qaytarib ber:
[
  {{"date": "29.07.2026 00:30", "instrument": "XAUUSD", "result": "SL", "lot": "0.01", "pnl": "-4.43", "setup": "", "mistake": "", "lesson": ""}},
  ...
]

Agar rasmda bitim topilmasa: []"""
                    }
                ]
            }
        ]
    )

    text = response.content[0].text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    return json.loads(text)


def build_stats_text(trades, title="📊 Statistika"):
    if not trades:
        return f"{title}\n\nHali hech qanday bitim yo'q."
    all_results = [r[2] for r in trades if len(r) >= 3 and r[2] in ["TP", "SL"]]
    if not all_results:
        return f"{title}\n\nBitimlar topilmadi."
    tp_count = all_results.count("TP")
    sl_count = all_results.count("SL")
    winrate = round(tp_count / len(all_results) * 100)
    total_pnl = 0.0
    for r in trades:
        if len(r) >= 8 and r[7]:
            try:
                total_pnl += float(r[7])
            except ValueError:
                pass
    pnl_emoji = "✅" if total_pnl >= 0 else "❌"
    text = f"{title}\n\n"
    text += f"📈 Jami: {len(all_results)} ta bitim\n"
    text += f"✅ TP: {tp_count} | ❌ SL: {sl_count}\n"
    text += f"🎯 Winrate: {winrate}%\n"
    text += f"{pnl_emoji} Jami PnL: {total_pnl:+.2f}$"
    return text


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 Trading Journal Bot\n\n"
        "MetaTrader history screenshotini yuboring.\n"
        "Caption (izoh) bilan: setup | xato | saboq\n\n"
        "Buyruqlar:\n"
        "/start — yordam\n"
        "/report — so'nggi 5 ta bitim\n"
        "/hafta — haftalik statistika\n"
        "/oy — oylik statistika\n"
        "/oxirgi — eng oxirgi bitim\n"
        "/instrument XAUUSD — instrument statistikasi\n"
        "/update — mavjud bitimlarni yangilash (Lot/PnL)"
    )


async def update_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_states[user_id] = "update"
    await update.message.reply_text(
        "🔄 Update rejimi yoqildi!\n\n"
        "Endi rasm yuboring — bot mavjud bitimlarni topib,\n"
        "faqat bo'sh Lot va PnL larni to'ldiradi."
    )


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        sheet = get_sheet()
        rows = sheet.get_all_values()
        trades = [r for r in rows[1:] if len(r) >= 3 and r[2] in ["TP", "SL"]]
        if not trades:
            await update.message.reply_text("Hali hech qanday bitim yo'q.")
            return
        last_5 = trades[-5:]
        text = "📋 So'nggi bitimlar:\n\n"
        for row in reversed(last_5):
            date = row[0] if row[0] else "—"
            instrument = row[1] if row[1] else "—"
            result = row[2] if row[2] else "—"
            lot = row[6] if len(row) > 6 and row[6] else "—"
            pnl = row[7] if len(row) > 7 and row[7] else "—"
            emoji = "✅" if result == "TP" else "❌"
            try:
                pnl_str = f"{float(pnl):+.2f}$" if pnl != "—" else "—"
            except ValueError:
                pnl_str = pnl
            text += f"{emoji} {date} | {instrument} | {result} | {lot} lot | {pnl_str}\n"
        text += "\n"
        text += build_stats_text(trades, "📊 Umumiy statistika")
        await update.message.reply_text(text)
    except Exception:
        logger.exception("Report xatosi")
        await update.message.reply_text("Xatolik yuz berdi.")


async def hafta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        sheet = get_sheet()
        rows = sheet.get_all_values()
        trades = [r for r in rows[1:] if len(r) >= 3 and r[2] in ["TP", "SL"]]
        now = datetime.now()
        week_num = now.isocalendar()[1]
        haftalik = []
        for row in trades:
            if row[0]:
                try:
                    trade_date = datetime.strptime(row[0].split(" ")[0], "%d.%m.%Y")
                    if trade_date.isocalendar()[1] == week_num and trade_date.year == now.year:
                        haftalik.append(row)
                except ValueError:
                    pass
        await update.message.reply_text(
            build_stats_text(haftalik, f"📅 Haftalik statistika ({now.strftime('%d.%m.%Y')})")
        )
    except Exception:
        logger.exception("Hafta xatosi")
        await update.message.reply_text("Xatolik yuz berdi.")


async def oy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        sheet = get_sheet()
        rows = sheet.get_all_values()
        trades = [r for r in rows[1:] if len(r) >= 3 and r[2] in ["TP", "SL"]]
        now = datetime.now()
        oylik = []
        for row in trades:
            if row[0]:
                try:
                    trade_date = datetime.strptime(row[0].split(" ")[0], "%d.%m.%Y")
                    if trade_date.month == now.month and trade_date.year == now.year:
                        oylik.append(row)
                except ValueError:
                    pass
        await update.message.reply_text(
            build_stats_text(oylik, f"🗓 Oylik statistika ({now.strftime('%m.%Y')})")
        )
    except Exception:
        logger.exception("Oy xatosi")
        await update.message.reply_text("Xatolik yuz berdi.")


async def oxirgi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        sheet = get_sheet()
        rows = sheet.get_all_values()
        trades = [r for r in rows[1:] if len(r) >= 3 and r[2] in ["TP", "SL"]]
        if not trades:
            await update.message.reply_text("Hali hech qanday bitim yo'q.")
            return
        row = trades[-1]
        date = row[0] if len(row) > 0 else "—"
        instrument = row[1] if len(row) > 1 else "—"
        result = row[2] if len(row) > 2 else "—"
        setup = row[3] if len(row) > 3 and row[3] else "—"
        mistake = row[4] if len(row) > 4 and row[4] else "—"
        lesson = row[5] if len(row) > 5 and row[5] else "—"
        lot = row[6] if len(row) > 6 and row[6] else "—"
        pnl = row[7] if len(row) > 7 and row[7] else "—"
        emoji = "✅" if result == "TP" else "❌"
        try:
            pnl_str = f"{float(pnl):+.2f}$" if pnl != "—" else "—"
        except ValueError:
            pnl_str = pnl
        await update.message.reply_text(
            f"🔍 Eng oxirgi bitim:\n\n"
            f"{emoji} {date} | {instrument} | {result}\n"
            f"📦 Lot: {lot}\n"
            f"💰 PnL: {pnl_str}\n"
            f"📌 Setup: {setup}\n"
            f"⚠️ Xato: {mistake}\n"
            f"📖 Saboq: {lesson}"
        )
    except Exception:
        logger.exception("Oxirgi xatosi")
        await update.message.reply_text("Xatolik yuz berdi.")


async def instrument_stat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.args:
            await update.message.reply_text("Misol: /instrument XAUUSD")
            return
        inst = context.args[0].upper()
        sheet = get_sheet()
        rows = sheet.get_all_values()
        trades = [r for r in rows[1:] if len(r) >= 3 and r[2] in ["TP", "SL"]]
        filtered = [r for r in trades if len(r) >= 2 and r[1].upper() == inst]
        await update.message.reply_text(
            build_stats_text(filtered, f"📊 {inst} statistikasi")
        )
    except Exception:
        logger.exception("Instrument xatosi")
        await update.message.reply_text("Xatolik yuz berdi.")


async def kunlik_hisobot(context):
    if not CHAT_ID:
        return
    try:
        sheet = get_sheet()
        rows = sheet.get_all_values()
        trades = [r for r in rows[1:] if len(r) >= 3 and r[2] in ["TP", "SL"]]
        now = datetime.now()
        bugungi = []
        for row in trades:
            if row[0]:
                try:
                    trade_date = datetime.strptime(row[0].split(" ")[0], "%d.%m.%Y")
                    if (trade_date.day == now.day and
                            trade_date.month == now.month and
                            trade_date.year == now.year):
                        bugungi.append(row)
                except ValueError:
                    pass
        text = build_stats_text(bugungi, f"🌙 Kunlik hisobot ({now.strftime('%d.%m.%Y')})")
        await context.bot.send_message(chat_id=CHAT_ID, text=text)
    except Exception:
        logger.exception("Kunlik hisobot xatosi")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    is_update_mode = user_states.get(user_id) == "update"

    if is_update_mode:
        await update.message.reply_text("🔄 Rasm qabul qilindi, yangilanmoqda...")
    else:
        await update.message.reply_text("📸 Rasm qabul qilindi, tahlil qilinmoqda...")

    try:
        if update.message.photo:
            photo = update.message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            media_type = "image/jpeg"
        elif update.message.document:
            doc = update.message.document
            file = await context.bot.get_file(doc.file_id)
            mime = doc.mime_type or "image/jpeg"
            media_type = "image/png" if "png" in mime else "image/jpeg"
        else:
            await update.message.reply_text("❌ Rasm topilmadi.")
            return

        image_bytes = await file.download_as_bytearray()

        caption = update.message.caption or ""
        setup, mistake, lesson, cap_lot, cap_pnl = "", "", "", "", ""
        if caption:
            parts = [p.strip() for p in caption.split("|")]
            if len(parts) >= 1: setup = parts[0]
            if len(parts) >= 2: mistake = parts[1]
            if len(parts) >= 3: lesson = parts[2]
            if len(parts) >= 4: cap_lot = parts[3]
            if len(parts) >= 5: cap_pnl = parts[4]

        trades = await extract_trades_from_image(bytes(image_bytes), media_type)

        if not trades:
            await update.message.reply_text(
                "❌ Rasmda hech qanday bitim topilmadi."
            )
            return

        sheet = get_sheet()

        if is_update_mode:
            # Update rejimi: faqat bo'sh Lot/PnL larni to'ldirish
            all_rows = sheet.get_all_values()
            updated = 0
            for trade in trades:
                lot = trade.get("lot") or cap_lot
                pnl = trade.get("pnl") or cap_pnl
                if not lot and not pnl:
                    continue
                trade_date = trade['date'].split(" ")[0] if trade['date'] else ""
                for i, row in enumerate(all_rows[1:], start=2):
                    if len(row) < 3 or row[2] not in ["TP", "SL"]:
                        continue
                    row_date = row[0].split(" ")[0] if row[0] else ""
                    if (row_date == trade_date and
                            row[1].upper() == trade['instrument'].upper() and
                            row[2] == trade['result']):
                        row_lot = row[6] if len(row) > 6 else ""
                        row_pnl = row[7] if len(row) > 7 else ""
                        if not row_lot and lot:
                            sheet.update_cell(i, 7, lot)
                        if not row_pnl and pnl:
                            sheet.update_cell(i, 8, pnl)
                        updated += 1
                        break

            user_states.pop(user_id, None)

            # Sheets ni qayta tartiblab chiqarish
            await update.message.reply_text(f"🔄 {updated} ta bitim yangilandi! Sheets qayta tartiblanmoqda...")
            rebuild_sheet(sheet)
            await update.message.reply_text("✅ Sheets yangilandi!")
            return

        # Oddiy rejim: yangi bitimlarni qo'shish
        existing = get_existing_trade_keys(sheet)
        added = 0
        skipped = 0
        total_pnl = 0.0

        for trade in trades:
            key = f"{trade['date']}_{trade['instrument']}_{trade['result']}"
            if key in existing:
                skipped += 1
                continue

            lot = trade.get("lot") or cap_lot
            pnl = trade.get("pnl") or cap_pnl

            try:
                total_pnl += float(pnl) if pnl else 0.0
            except ValueError:
                pass

            sheet.append_row([
                trade['date'],
                trade['instrument'],
                trade['result'],
                setup or trade.get('setup', ''),
                mistake or trade.get('mistake', ''),
                lesson or trade.get('lesson', ''),
                lot,
                pnl
            ])
            existing.add(key)
            added += 1

        if added > 0:
            # Sheets ni qayta tartiblab chiqarish
            await update.message.reply_text(f"✅ {added} ta yangi bitim qo'shildi! Sheets tartiblanmoqda...")
            rebuild_sheet(sheet)

            pnl_emoji = "✅" if total_pnl >= 0 else "❌"
            pnl_str = f"{total_pnl:+.2f}$" if total_pnl != 0 else "—"
            msg = f"🎉 Tayyor!\n{pnl_emoji} Bu rasmdan PnL: {pnl_str}"
            if skipped > 0:
                msg += f"\n⏭️ {skipped} ta takror o'tkazildi."
            await update.message.reply_text(msg)
        else:
            await update.message.reply_text(
                f"⏭️ Barcha {skipped} ta bitim allaqachon mavjud edi."
            )

    except Exception:
        logger.exception("Rasm tahlil xatosi")
        await update.message.reply_text("❌ Xatolik yuz berdi. Qaytadan urinib ko'ring.")


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("hafta", hafta))
    app.add_handler(CommandHandler("oy", oy))
    app.add_handler(CommandHandler("oxirgi", oxirgi))
    app.add_handler(CommandHandler("instrument", instrument_stat))
    app.add_handler(CommandHandler("update", update_cmd))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo))

    app.job_queue.run_daily(
        kunlik_hisobot,
        time=dtime(hour=21, minute=0)
    )

    logger.info("Trading Journal Bot ishga tushdi")
    app.run_polling()


if __name__ == "__main__":
    main()
