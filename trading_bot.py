import os
import json
import logging
import base64
from datetime import datetime
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN topilmadi!")
if not SPREADSHEET_ID:
    raise RuntimeError("SPREADSHEET_ID topilmadi!")
if not GOOGLE_CREDENTIALS:
    raise RuntimeError("GOOGLE_CREDENTIALS topilmadi!")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY topilmadi!")

# Gemini sozlash
genai.configure(api_key=GEMINI_API_KEY)

# Google Sheets ga ulanish
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

# Mavjud bitimlarni olish (takrorlanmaslik uchun)
def get_existing_trades(sheet):
    rows = sheet.get_all_values()
    existing = set()
    for row in rows[1:]:  # 1-qator sarlavha
        if len(row) >= 3:
            key = f"{row[0]}_{row[1]}_{row[2]}"
            existing.add(key)
    return existing

# Rasmdan bitimlarni ajratib olish (Gemini Vision orqali)
async def extract_trades_from_image(image_bytes: bytes) -> list:
    model = genai.GenerativeModel("gemini-1.5-flash")
    today = datetime.now().strftime("%d.%m.%Y")

    image_part = {
        "mime_type": "image/jpeg",
        "data": base64.b64encode(image_bytes).decode("utf-8")
    }

    prompt = f"""Bu MetaTrader trading history screenshoti.
Rasmdan barcha bitimlarni (trade) ajratib ol va JSON formatda qaytarib ber.

Bugungi sana: {today}

Har bir bitim uchun:
- date: bitim sanasi (DD.MM.YYYY formatda, agar rasmda sana yo'q bo'lsa {today} ni ishlet)
- instrument: savdo qilingan juftlik (masalan XAUUSD, EURUSD)
- result: natija (TP yoki SL)
- setup: bo'sh qoldir ("")
- mistake: bo'sh qoldir ("")
- lesson: bo'sh qoldir ("")

FAQAT JSON qaytarib ber, boshqa hech narsa yozma:
[
  {{"date": "DD.MM.YYYY", "instrument": "XAUUSD", "result": "TP", "setup": "", "mistake": "", "lesson": ""}},
  ...
]

Agar rasmda hech qanday bitim topilmasa, bo'sh massiv qaytarib ber: []"""

    response = model.generate_content([prompt, image_part])
    text = response.text.strip()

    # JSON ni tozalash
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    trades = json.loads(text)
    return trades

# /start buyrug'i
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 Trading Journal Bot\n\n"
        "MetaTrader history screenshotini yuboring — "
        "bot bitimlarni avtomatik Google Sheets ga yozadi.\n\n"
        "Buyruqlar:\n"
        "/start — yordam\n"
        "/report — so'nggi 5 ta bitim va statistika"
    )

# /report buyrug'i
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
                emoji = "✅" if result == "TP" else "❌" if result == "SL" else "—"
                text += f"{emoji} {date} | {instrument} | {result}\n"

        # Statistika
        all_results = [r[2] for r in trades if len(r) >= 3 and r[2] in ["TP", "SL"]]
        if all_results:
            tp_count = all_results.count("TP")
            sl_count = all_results.count("SL")
            winrate = round(tp_count / len(all_results) * 100)
            text += f"\n📈 Jami: {len(all_results)} ta bitim\n"
            text += f"✅ TP: {tp_count} | ❌ SL: {sl_count}\n"
            text += f"🎯 Winrate: {winrate}%"

        await update.message.reply_text(text)
    except Exception as e:
        logger.exception("Report xatosi")
        await update.message.reply_text("Xatolik yuz berdi, qaytadan urinib ko'ring.")

# Rasm qabul qilish
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📸 Rasm qabul qilindi, tahlil qilinmoqda...")

    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        image_bytes = await file.download_as_bytearray()

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

        for trade in trades:
            key = f"{trade['date']}_{trade['instrument']}_{trade['result']}"
            if key in existing:
                skipped += 1
                continue

            sheet.append_row([
                trade['date'],
                trade['instrument'],
                trade['result'],
                trade['setup'],
                trade['mistake'],
                trade['lesson']
            ])
            existing.add(key)
            added += 1

        if added > 0 and skipped == 0:
            await update.message.reply_text(
                f"✅ {added} ta yangi bitim Google Sheets ga qo'shildi!"
            )
        elif added > 0 and skipped > 0:
            await update.message.reply_text(
                f"✅ {added} ta yangi bitim qo'shildi.\n"
                f"⏭️ {skipped} ta bitim allaqachon mavjud edi (o'tkazib yuborildi)."
            )
        else:
            await update.message.reply_text(
                f"⏭️ Barcha {skipped} ta bitim allaqachon Sheets da mavjud edi."
            )

    except Exception as e:
        logger.exception("Rasm tahlil xatosi")
        await update.message.reply_text(
            "❌ Xatolik yuz berdi. Qaytadan urinib ko'ring."
        )

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    logger.info("Trading Journal Bot ishga tushdi")
    app.run_polling()

if __name__ == "__main__":
    main()
