import os
import json
import logging
import base64
from datetime import datetime, time
import anthropic
import gspread
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

def get_existing_trades(sheet):
    rows = sheet.get_all_values()
    existing = set()
    for row in rows[1:]:
        if len(row) >= 3:
            # Sana + vaqt + instrument + natija = unique key
            key = f"{row[0]}_{row[1]}_{row[2]}"
            existing.add(key)
    return existing

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

async def extract_trades_from_image(image_bytes: bytes) -> list:
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
                            "media_type": "image/jpeg",
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
- instrument: juftlik nomi (XAUUSD, xauusdm -> XAUUSD, EURUSD va h.k.)
- result: musbat son -> "TP", manfiy son -> "SL"
- lot: lot hajmi (0.01, 0.02 va h.k.) — topilmasa ""
- pnl: foyda/zarar summasi (masalan 15.50 yoki -8.20) — topilmasa ""
- setup: ""
- mistake: ""
- lesson: ""

MUHIM: Har bir bitim alohida — bir xil instrument bo'lsa ham barchasi alohida yozilsin!

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

    trades = json.loads(text)
    return trades

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 Trading Journal Bot\n\n"
        "MetaTrader history screenshotini yuboring.\n"
        "Izoh bilan yuborish (caption ga):\n"
        "  setup | xato | saboq\n\n"
        "Buyruqlar:\n"
        "/start — yordam\n"
        "/report — so'nggi 5 ta bitim\n"
        "/hafta — haftalik statistika\n"
        "/oy — oylik statistika\n"
        "/oxirgi — eng oxirgi bitim\n"
        "/instrument XAUUSD — instrument bo'yicha stat"
    )

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        sheet = get_sheet()
        rows = sheet.get_all_values()
        trades = rows[1:]

        if not trades:
            await update.message.reply_text("Hali hech qanday bitim yo'q.")
            return

        last_5 = trades[-5:]
        text = "📋 So'nggi bitimlar:\n\n"
        for row in reversed(last_5):
            if len(row) >= 3:
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
        trades = rows[1:]
        now = datetime.now()
        week_num = now.isocalendar()[1]
        haftalik = []
        for row in trades:
            if len(row) >= 1 and row[0]:
                try:
                    date_part = row[0].split(" ")[0]
                    trade_date = datetime.strptime(date_part, "%d.%m.%Y")
                    if (trade_date.isocalendar()[1] == week_num and
                            trade_date.year == now.year):
                        haftalik.append(row)
                except ValueError:
                    continue
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
        trades = rows[1:]
        now = datetime.now()
        oylik = []
        for row in trades:
            if len(row) >= 1 and row[0]:
                try:
                    date_part = row[0].split(" ")[0]
                    trade_date = datetime.strptime(date_part, "%d.%m.%Y")
                    if (trade_date.month == now.month and
                            trade_date.year == now.year):
                        oylik.append(row)
                except ValueError:
                    continue
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
        trades = rows[1:]
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
        text = (
            f"🔍 Eng oxirgi bitim:\n\n"
            f"{emoji} {date} | {instrument} | {result}\n"
            f"📦 Lot: {lot}\n"
            f"💰 PnL: {pnl_str}\n"
            f"📌 Setup: {setup}\n"
            f"⚠️ Xato: {mistake}\n"
            f"📖 Saboq: {lesson}"
        )
        await update.message.reply_text(text)
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
        trades = rows[1:]
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
        trades = rows[1:]
        now = datetime.now()
        bugungi = []
        for row in trades:
            if len(row) >= 1 and row[0]:
                try:
                    date_part = row[0].split(" ")[0]
                    trade_date = datetime.strptime(date_part, "%d.%m.%Y")
                    if (trade_date.day == now.day and
                            trade_date.month == now.month and
                            trade_date.year == now.year):
                        bugungi.append(row)
                except ValueError:
                    continue
        text = build_stats_text(bugungi, f"🌙 Kunlik hisobot ({now.strftime('%d.%m.%Y')})")
        await context.bot.send_message(chat_id=CHAT_ID, text=text)
    except Exception:
        logger.exception("Kunlik hisobot xatosi")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Ham rasm, ham document (file) sifatida yuborilganda ishlaydi
    await update.message.reply_text("📸 Rasm qabul qilindi, tahlil qilinmoqda...")
    try:
        # Ham oddiy rasm, ham file sifatida yuborilganda ishlaydi
        if update.message.photo:
            photo = update.message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
        elif update.message.document:
            file = await context.bot.get_file(update.message.document.file_id)
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

        trades = await extract_trades_from_image(bytes(image_bytes))

        if not trades:
            await update.message.reply_text(
                "❌ Rasmda hech qanday bitim topilmadi.\n"
                "MetaTrader history screenshotini aniqroq yuboring."
            )
            return

        sheet = get_sheet()
        existing = get_existing_trades(sheet)

        added = 0
        skipped = 0
        total_pnl = 0.0

        for trade in trades:
            # Unique key: sana+vaqt + instrument + natija
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
            pnl_emoji = "✅" if total_pnl >= 0 else "❌"
            pnl_str = f"{total_pnl:+.2f}$" if total_pnl != 0 else "—"
            msg = f"✅ {added} ta yangi bitim qo'shildi!\n"
            msg += f"{pnl_emoji} Jami PnL: {pnl_str}"
            if skipped > 0:
                msg += f"\n⏭️ {skipped} ta bitim allaqachon mavjud edi."
            await update.message.reply_text(msg)
        else:
            await update.message.reply_text(
                f"⏭️ Barcha {skipped} ta bitim allaqachon Sheets da mavjud edi."
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
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo))

    app.job_queue.run_daily(
        kunlik_hisobot,
        time=time(hour=21, minute=0)
    )

    logger.info("Trading Journal Bot ishga tushdi")
    app.run_polling()

if __name__ == "__main__":
    main()
