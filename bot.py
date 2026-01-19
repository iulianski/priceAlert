import asyncio
import os
import aiohttp
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import logging
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# File paths for persistence
ALERTS_FILE = 'active_alerts.json'

# Store active alerts: {chat_id: {alert_id: {'symbol': str, 'target': price, 'initial': price, 'last_price': price, 'exchange': str}}}
active_alerts = {}

# Store pending alerts (waiting for exchange selection): {chat_id: {'symbol': str, 'target': float}}
pending_alerts = {}

# Alert ID counter
alert_counter = 0

def load_data():
    """Load alerts from file"""
    global active_alerts, alert_counter
    
    # Load active alerts
    if os.path.exists(ALERTS_FILE):
        try:
            with open(ALERTS_FILE, 'r') as f:
                data = json.load(f)
                # Convert string keys back to integers
                active_alerts = {int(k): v for k, v in data.items()}
            
            # Set alert_counter to max existing ID + 1
            max_id = 0
            for alerts in active_alerts.values():
                for alert_id in alerts.keys():
                    try:
                        max_id = max(max_id, int(alert_id))
                    except:
                        pass
            alert_counter = max_id + 1
            
            logger.info(f"Loaded {len(active_alerts)} active alerts from file")
        except Exception as e:
            logger.error(f"Error loading alerts: {e}")
            active_alerts = {}

def save_alerts():
    """Save active alerts to file"""
    try:
        with open(ALERTS_FILE, 'w') as f:
            json.dump(active_alerts, f, indent=2)
        logger.info("Saved active alerts to file")
    except Exception as e:
        logger.error(f"Error saving alerts: {e}")

async def get_binance_price(symbol: str) -> float:
    """Fetch current price from Binance Futures API"""
    try:
        if not symbol.endswith('USDT'):
            symbol = f"{symbol}USDT"
        
        url = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    if 'price' in data:
                        return float(data['price'])
        return None
    except Exception as e:
        logger.error(f"Error fetching Binance price for {symbol}: {e}")
        return None

async def get_bybit_price(symbol: str) -> float:
    """Fetch current price from Bybit API"""
    try:
        if not symbol.endswith('USDT'):
            symbol = f"{symbol}USDT"
        
        url = f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('retCode') == 0 and data.get('result', {}).get('list'):
                        return float(data['result']['list'][0]['lastPrice'])
        return None
    except Exception as e:
        logger.error(f"Error fetching Bybit price for {symbol}: {e}")
        return None

async def get_bitget_price(symbol: str) -> float:
    """Fetch current price from Bitget API"""
    try:
        if not symbol.endswith('USDT'):
            symbol = f"{symbol}USDT"
        
        url = f"https://api.bitget.com/api/v2/mix/market/ticker?symbol={symbol}&productType=USDT-FUTURES"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('code') == '00000' and data.get('data'):
                        return float(data['data'][0]['lastPr'])
        return None
    except Exception as e:
        logger.error(f"Error fetching Bitget price for {symbol}: {e}")
        return None

async def get_mexc_price(symbol: str) -> float:
    """Fetch current price from MEXC API"""
    try:
        if not symbol.endswith('USDT'):
            symbol = f"{symbol}USDT"
        
        # MEXC uses underscore format for futures
        mexc_symbol = symbol.replace('USDT', '_USDT')
        url = f"https://contract.mexc.com/api/v1/contract/ticker?symbol={mexc_symbol}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('success') and data.get('data'):
                        return float(data['data']['lastPrice'])
        return None
    except Exception as e:
        logger.error(f"Error fetching MEXC price for {symbol}: {e}")
        return None

async def get_price(symbol: str, exchange: str) -> float:
    """Fetch price from specified exchange"""
    exchange = exchange.lower()
    
    if exchange == 'binance':
        return await get_binance_price(symbol)
    elif exchange == 'bybit':
        return await get_bybit_price(symbol)
    elif exchange == 'bitget':
        return await get_bitget_price(symbol)
    elif exchange == 'mexc':
        return await get_mexc_price(symbol)
    else:
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message"""
    await update.message.reply_text(
        "🚀 Welcome to Crypto Alert Bot!\n\n"
        "Usage (Simple):\n"
        "BTC 96000\n\n"
        "Usage (With Exchange):\n"
        "BTC 96000 bybit\n\n"
        "Supported exchanges:\n"
        "• Binance\n"
        "• Bybit\n"
        "• Bitget\n"
        "• MEXC\n\n"
        "Commands:\n"
        "/list - Show active alerts\n"
        "/remove SYMBOL - Remove alert\n"
        "/clear - Clear all alerts"
    )

async def list_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all active alerts for the user"""
    chat_id = update.effective_chat.id
    
    if chat_id not in active_alerts or not active_alerts[chat_id]:
        await update.message.reply_text("📭 No active alerts")
        return
    
    message = "📊 Active Alerts:\n\n"
    for alert_id, alert_data in active_alerts[chat_id].items():
        symbol = alert_data['symbol']
        target = alert_data['target']
        initial = alert_data['initial']
        exchange = alert_data['exchange']
        current = await get_price(symbol, exchange)
        current_str = f"${current:g}" if current else "N/A"
        direction = "↓ below" if target < initial else "↑ above"
        message += f"• {symbol} ({exchange.upper()}): {direction} ${target:g} (Current: {current_str})\n"
    
    await update.message.reply_text(message)

async def remove_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove alert for specific symbol"""
    chat_id = update.effective_chat.id
    
    if not context.args:
        await update.message.reply_text("Usage: /remove BTC or /remove 1 (alert number)")
        return
    
    identifier = context.args[0].upper()
    
    if chat_id not in active_alerts or not active_alerts[chat_id]:
        await update.message.reply_text(f"❌ No alerts found")
        return
    
    # Try to remove by alert ID number first
    try:
        alert_num = int(identifier)
        alert_ids = list(active_alerts[chat_id].keys())
        if 1 <= alert_num <= len(alert_ids):
            alert_id = alert_ids[alert_num - 1]
            symbol = active_alerts[chat_id][alert_id]['symbol']
            del active_alerts[chat_id][alert_id]
            save_alerts()
            await update.message.reply_text(f"✅ Alert #{alert_num} removed for {symbol}")
            return
    except ValueError:
        pass
    
    # Try to remove by symbol name
    removed = []
    for alert_id, alert_data in list(active_alerts[chat_id].items()):
        if alert_data['symbol'] == identifier or alert_data['symbol'] == f"{identifier}USDT":
            removed.append(alert_id)
    
    if removed:
        for alert_id in removed:
            del active_alerts[chat_id][alert_id]
        save_alerts()
        await update.message.reply_text(f"✅ Removed {len(removed)} alert(s) for {identifier}")
    else:
        await update.message.reply_text(f"❌ No alert found for {identifier}")

async def clear_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all alerts for the user"""
    chat_id = update.effective_chat.id
    
    if chat_id in active_alerts:
        active_alerts[chat_id] = {}
        save_alerts()
        await update.message.reply_text("✅ All alerts cleared")
    else:
        await update.message.reply_text("📭 No alerts to clear")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming alert requests"""
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    
    # Parse input: SYMBOL PRICE [EXCHANGE]
    parts = text.split()
    
    if len(parts) < 2 or len(parts) > 3:
        await update.message.reply_text(
            "❌ Invalid format.\n\n"
            "Simple: BTC 96000\n"
            "With exchange: BTC 96000 bybit"
        )
        return
    
    symbol = parts[0].upper()
    
    # Auto-add USDT if not present
    if not symbol.endswith('USDT'):
        symbol = f"{symbol}USDT"
    
    try:
        target_price = float(parts[1].replace(',', ''))
    except ValueError:
        await update.message.reply_text("❌ Invalid price format")
        return
    
    # If exchange specified, use it directly
    if len(parts) == 3:
        exchange = parts[2].lower()
        
        # Validate exchange
        if exchange not in ['binance', 'bybit', 'bitget', 'mexc']:
            await update.message.reply_text(
                "❌ Invalid exchange. Supported: binance, bybit, bitget, mexc"
            )
            return
        
        await set_alert(update, chat_id, symbol, target_price, exchange)
    else:
        # No exchange specified - show buttons
        pending_alerts[chat_id] = {
            'symbol': symbol,
            'target': target_price
        }
        
        keyboard = [
            [
                InlineKeyboardButton("Binance", callback_data=f"exchange_binance"),
                InlineKeyboardButton("Bybit", callback_data=f"exchange_bybit")
            ],
            [
                InlineKeyboardButton("Bitget", callback_data=f"exchange_bitget"),
                InlineKeyboardButton("MEXC", callback_data=f"exchange_mexc")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"📊 Setting alert for {symbol} at ${target_price:,.2f}\n\n"
            f"Select exchange:",
            reply_markup=reply_markup
        )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button presses"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    
    if query.data.startswith("exchange_"):
        exchange = query.data.replace("exchange_", "")
        
        if chat_id not in pending_alerts:
            await query.edit_message_text("❌ Alert expired. Please try again.")
            return
        
        symbol = pending_alerts[chat_id]['symbol']
        target_price = pending_alerts[chat_id]['target']
        
        # Remove from pending
        del pending_alerts[chat_id]
        
        await set_alert_from_callback(query, chat_id, symbol, target_price, exchange)

async def set_alert(update: Update, chat_id: int, symbol: str, target_price: float, exchange: str):
    """Set an alert (called from direct message)"""
    global alert_counter
    
    # Verify symbol exists on the exchange
    current_price = await get_price(symbol, exchange)
    if current_price is None:
        await update.message.reply_text(f"❌ Symbol {symbol} not found on {exchange.upper()}")
        return
    
    # Store alert with unique ID
    if chat_id not in active_alerts:
        active_alerts[chat_id] = {}
    
    alert_id = str(alert_counter)
    alert_counter += 1
    
    active_alerts[chat_id][alert_id] = {
        'symbol': symbol,
        'target': target_price,
        'initial': current_price,
        'last_price': current_price,
        'exchange': exchange
    }
    
    # Save to file
    save_alerts()
    
    # Determine direction symbol
    direction_symbol = "<" if target_price < current_price else ">"
    
    # Exchange emojis
    exchange_emojis = {
        'binance': '🟡',
        'bybit': '🟠',
        'bitget': '🌐',
        'mexc': '🔵'
    }
    exchange_emoji = exchange_emojis.get(exchange, '⚪')
    
    await update.message.reply_text(
        f"✅ Alert: {symbol}/{exchange.upper()} {direction_symbol} ${target_price:g}"
    )

async def set_alert_from_callback(query, chat_id: int, symbol: str, target_price: float, exchange: str):
    """Set an alert (called from button callback)"""
    global alert_counter
    
    # Verify symbol exists on the exchange
    current_price = await get_price(symbol, exchange)
    if current_price is None:
        await query.edit_message_text(f"❌ Symbol {symbol} not found on {exchange.upper()}")
        return
    
    # Store alert with unique ID
    if chat_id not in active_alerts:
        active_alerts[chat_id] = {}
    
    alert_id = str(alert_counter)
    alert_counter += 1
    
    active_alerts[chat_id][alert_id] = {
        'symbol': symbol,
        'target': target_price,
        'initial': current_price,
        'last_price': current_price,
        'exchange': exchange
    }
    
    # Save to file
    save_alerts()
    
    # Determine direction symbol
    direction_symbol = "<" if target_price < current_price else ">"
    
    # Exchange emojis
    exchange_emojis = {
        'binance': '🟡',
        'bybit': '🟠',
        'bitget': '🌐',
        'mexc': '🔵'
    }
    exchange_emoji = exchange_emojis.get(exchange, '⚪')
    
    await query.edit_message_text(
        f"✅ Alert: {symbol}/{exchange.upper()} {direction_symbol} ${target_price:g}"
    )

async def check_alerts(context: ContextTypes.DEFAULT_TYPE):
    """Background task to check all alerts"""
    for chat_id, alerts in list(active_alerts.items()):
        for alert_id, alert_data in list(alerts.items()):
            symbol = alert_data['symbol']
            target_price = alert_data['target']
            initial_price = alert_data['initial']
            last_price = alert_data.get('last_price', initial_price)
            exchange = alert_data['exchange']
            
            current_price = await get_price(symbol, exchange)
            
            if current_price is None:
                continue
            
            # Check if price CROSSED the target (must actually cross, not equal)
            triggered = False
            
            # For downward alerts (target < initial)
            if target_price < initial_price:
                # Last price was above target, current is below (not equal)
                if last_price > target_price and current_price < target_price:
                    triggered = True
            
            # For upward alerts (target > initial)
            elif target_price > initial_price:
                # Last price was below target, current is above (not equal)
                if last_price < target_price and current_price > target_price:
                    triggered = True
            
            # Update last price
            active_alerts[chat_id][alert_id]['last_price'] = current_price
            
            if triggered:
                from datetime import datetime
                
                direction = "dropped below" if target_price < initial_price else "rose above"
                
                # Exchange circle emojis (matching colors)
                exchange_emojis = {
                    'binance': '🟡',  # Yellow circle
                    'bybit': '🟠',    # Orange circle
                    'bitget': '🌐',   # Globe for Bitget
                    'mexc': '🔵'      # Blue circle
                }
                
                exchange_emoji = exchange_emojis.get(exchange, '🤍')
                
                # Coin emoji - green if up, red if down
                coin_emoji = '🟢' if current_price > target_price else '🔴'
                
                # Get current time
                current_time = datetime.now().strftime('%H:%M:%S')
                
                message = (
                    f"{coin_emoji} `${symbol}`\n"
                    f"{exchange_emoji} {exchange.upper()}\n\n"
                    f"_Target price: ${target_price:g}_\n"
                    f"_Current price: ${current_price:g}_\n\n"
                    f"🕓 {current_time}"
                )
                
                # Create button for the alert
                exchange_urls = {
                    'binance': f"https://www.binance.com/en/futures/{symbol}",
                    'bybit': f"https://www.bybit.com/trade/usdt/{symbol}",
                    'bitget': f"https://www.bitget.com/en/futures/usdt/{symbol}",
                    'mexc': f"https://futures.mexc.com/exchange/{symbol.replace('USDT', '_USDT')}"
                }
                
                keyboard = [
                    [InlineKeyboardButton(f"🔗 {exchange.upper()}", url=exchange_urls.get(exchange, "https://www.binance.com"))]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                try:
                    await context.bot.send_message(
                        chat_id=chat_id, 
                        text=message,
                        reply_markup=reply_markup,
                        parse_mode='Markdown'
                    )
                    
                    # Remove triggered alert
                    del active_alerts[chat_id][alert_id]
                    save_alerts()
                except Exception as e:
                    logger.error(f"Error sending alert: {e}")

def main():
    """Start the bot"""
    TOKEN = os.getenv("TOKEN")
    
    # Load saved data
    load_data()
    
    # Create application
    app = Application.builder().token(TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_alerts))
    app.add_handler(CommandHandler("remove", remove_alert))
    app.add_handler(CommandHandler("clear", clear_alerts))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Add job to check alerts every 10 seconds
    app.job_queue.run_repeating(check_alerts, interval=10, first=10)
    
    # Start bot
    print("Bot started! Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()