import logging
import os
import random
import json
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update, ForceReply
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ConversationHandler
)

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Simple HTTP server to keep Render happy
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Bot is running!')
    def log_message(self, format, *args):
        pass

def start_health_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    server.serve_forever()

# Conversation states
UID, NAME, SN, ITEM, AMOUNT, STATUS = range(6)

# File to store S/P/F counts
COUNTS_FILE = "counts.json"

def load_counts():
    if os.path.exists(COUNTS_FILE):
        with open(COUNTS_FILE, "r") as f:
            return json.load(f)
    return {"SUCCESS": 0, "PENDING": 0, "FAILED": 0}

def save_counts(counts):
    with open(COUNTS_FILE, "w") as f:
        json.dump(counts, f)

# Global variable for counts
transaction_counts = load_counts()

async def start(update: Update, context) -> int:
    """Starts the conversation and asks for the UID."""
    await update.message.reply_text(
        "Hi! I'm your Transaction Report Bot. Let's create a new report.\n"
        "Send /cancel to stop at any point.\n\n"
        "Please enter the UID:"
    )
    return UID

async def order(update: Update, context) -> int:
    """Starts the conversation for a new order."""
    await update.message.reply_text(
        "Let's create a new transaction report.\n"
        "Send /cancel to stop at any point.\n\n"
        "Please enter the UID:"
    )
    return UID

async def get_uid(update: Update, context) -> int:
    """Stores the UID and asks for the Name."""
    context.user_data["uid"] = update.message.text
    await update.message.reply_text("Please enter the Name:")
    return NAME

async def get_name(update: Update, context) -> int:
    """Stores the Name and asks for the SN."""
    context.user_data["name"] = update.message.text
    await update.message.reply_text(
        "Please enter the SN (or type 'skip' to auto-generate):"
    )
    return SN

def generate_sn() -> str:
    """Generates a Serial Number based on date + random code."""
    now = datetime.now()
    date_part = now.strftime("%d%m%y")
    random_part = ''.join(random.choices('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ', k=10))
    return f"S{date_part}{random_part}"

async def get_sn(update: Update, context) -> int:
    """Stores the SN or generates it, then asks for the Item."""
    if update.message.text.lower() == 'skip':
        context.user_data["sn"] = generate_sn()
        await update.message.reply_text(f"Auto-generated SN: {context.user_data['sn']}")
    else:
        context.user_data["sn"] = update.message.text
    await update.message.reply_text("Please enter the Item:")
    return ITEM

async def get_item(update: Update, context) -> int:
    """Stores the Item and asks for the Amount."""
    context.user_data["item"] = update.message.text
    await update.message.reply_text("Please enter the Amount:")
    return AMOUNT

async def get_amount(update: Update, context) -> int:
    """Stores the Amount and asks for the Status."""
    try:
        amount = float(update.message.text)
        context.user_data["amount"] = f"{amount:.2f}"
        await update.message.reply_text(
            "Please enter the Status (SUCCESS, PENDING, or FAILED):"
        )
        return STATUS
    except ValueError:
        await update.message.reply_text("Invalid amount. Please enter a numeric value.")
        return AMOUNT

async def get_status(update: Update, context) -> int:
    """Stores the Status, generates the report, and sends it."""
    global transaction_counts

    status = update.message.text.upper()
    if status not in ["SUCCESS", "PENDING", "FAILED"]:
        await update.message.reply_text(
            "Invalid status. Please choose from SUCCESS, PENDING, or FAILED."
        )
        return STATUS

    context.user_data["status"] = status

    # Update counts
    transaction_counts[status] += 1
    save_counts(transaction_counts)

    # Generate report
    report = generate_transaction_report(context.user_data, transaction_counts)

    # Send report to group
    group_chat_id = os.getenv("TELEGRAM_GROUP_CHAT_ID")
    if group_chat_id:
        try:
            await context.bot.send_message(chat_id=group_chat_id, text=report)
            await update.message.reply_text("Transaction report sent to the group!")
        except Exception as e:
            logger.error(f"Failed to send message to group {group_chat_id}: {e}")
            await update.message.reply_text(
                "Failed to send report to the group. Please check the chat ID and bot permissions."
            )
    else:
        await update.message.reply_text(
            "TELEGRAM_GROUP_CHAT_ID is not set. Report not sent to group."
        )

    await update.message.reply_text("Transaction report generated successfully:\n" + report)

    context.user_data.clear()
    return ConversationHandler.END

def generate_transaction_report(data: dict, counts: dict) -> str:
    """Generates the formatted transaction report."""
    status_emoji = {"SUCCESS": "✅", "PENDING": "⏳", "FAILED": "❌"}
    current_time = datetime.now().strftime("%d/%m/%Y %I:%M:%S %p")

    spent_amount = float(data['amount'])
    initial_balance = 1557.58
    final_balance = initial_balance - spent_amount

    report = f"""=== Transaction Report ===

Status: {data['status']} {status_emoji.get(data['status'], '')}
UID   : {data['uid']}
Name  : {data['name']}
SN    : {data['sn']}
Item  : {data['item']}
Amount: {data['amount']} 🪙
--------------------------
Date  : {current_time}
Spent : {data['amount']} 🪙
Initial: {initial_balance:.2f} 🪙
Final : {final_balance:.2f} 🪙

S:{counts['SUCCESS']} / P:{counts['PENDING']} / F:{counts['FAILED']}
"""
    return report

async def cancel(update: Update, context) -> int:
    """Cancels and ends the conversation."""
    user = update.message.from_user
    logger.info("User %s canceled the conversation.", user.first_name)
    await update.message.reply_text(
        "Operation cancelled. You can start a new transaction with /order."
    )
    context.user_data.clear()
    return ConversationHandler.END

async def help_command(update: Update, context) -> None:
    """Sends a message when the command /help is issued."""
    await update.message.reply_text(
        "Use /order to start creating a new transaction report.\n"
        "Use /cancel to stop the current operation.\n"
        "Admins can input order details interactively."
    )

def main() -> None:
    """Run the bot."""
    # Start health check server in background thread
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    logger.info("Health check server started")

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not set.")
        print("Error: TELEGRAM_BOT_TOKEN environment variable not set. Please set it before running the bot.")
        return

    application = Application.builder().token(bot_token).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("order", order), CommandHandler("start", start)],
        states={
            UID: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_uid)],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            SN: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_sn)],
            ITEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_item)],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_amount)],
            STATUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_status)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
