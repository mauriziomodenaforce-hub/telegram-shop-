import os
import telebot
from telebot import types

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
WEB_APP_URL = os.environ.get('WEB_APP_URL')

bot = telebot.TeleBot(TELEGRAM_TOKEN)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    welcome_text = (
        "👋 **Benvenuto nello shop di BostonGeorge!**\n\n"
        "Qui troverai tutti i prodotti che ti servono per te o per il tuo business.\n\n"
        "📦 - Tutti i PRODOTTI sono in pronta consegna\n"
        "🇮🇹 - Spedizione Da ITALIA\n"
        "🇪🇸 - Spedizione Da Spagna\n"
        "🇨🇿 - Spedizione Da Repubblica ceca"
    )
    
    markup = types.InlineKeyboardMarkup()
    if WEB_APP_URL:
        web_app_info = types.WebAppInfo(WEB_APP_URL)
        btn = types.InlineKeyboardButton("🛍 Apri la vetrina", web_app=web_app_info)
        markup.add(btn)
    
    bot.send_message(message.chat.id, welcome_text, parse_mode='Markdown', reply_markup=markup)

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    bot.reply_to(message, "⚙️ **Pannello Gestionale Attivo**")

print("🤖 Bot Telegram avviato H24...")
bot.infinity_polling()
