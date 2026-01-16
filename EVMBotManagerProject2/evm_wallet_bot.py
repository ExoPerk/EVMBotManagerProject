# -*- coding: utf-8 -*-
"""
EVM Wallet Manager Bot (Telegram)
"""

import os
import json
import asyncio
from io import BytesIO
from functools import lru_cache
from datetime import datetime, time as dtime, timezone, timedelta
import logging

import requests
from telegram.request import HTTPXRequest
from web3 import Web3
import matplotlib.pyplot as plt
import pytz

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)


# Configurations
# Telegram BOT Token
TOKEN = "8551507172:AAEtEliQcTqBp8xzLhid1ZrZ2ucHPP-ymQk"

# Public RPCs
RPC_URLS = {
    "ethereum": "https://eth.llamarpc.com",
    "bsc": "https://bsc-dataseed.bnbchain.org",
    "base": "https://mainnet.base.org",
    "arbitrum": "https://arb1.arbitrum.io/rpc",
}

# ERC20 token contract addresses
ERC20_TOKENS = {
    "ethereum": {
        "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "DAI":  "0x6B175474E89094C44Da98b954EedeAC495271d0F",
    },
    "bsc": {
        "USDT": "0x55d398326f99059fF775485246999027B3197955",
        "USDC": "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
    },

}


ERC20_MIN_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"},
]

# Files
WALLETS_FILE = "wallets.json"
SUMMARY_FILE = "summary.json"
HIDE_FILE = "hide_mode.json"
MONITOR_FILE = "monitor.json"

# conversation states for add wallet
STATE_ALIAS, STATE_ADDRESS, STATE_NETWORK = range(3)

# price id mapping for networks and common tokens -> CoinGecko ids
NETWORK_PRICE_ID = {
    "ethereum": "ethereum",
    "bsc": "binancecoin",
    "base": "ethereum",     # approximation
    "arbitrum": "ethereum"  # approximation
}

# symbol to coingecko id mapping
SYMBOL_TO_CGID = {
    "usdt": "tether",
    "usdc": "usd-coin",
    "dai": "dai",
    "eth": "ethereum",
    "bnb": "binancecoin"
}

# logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# File helpers
def ensure_file(path: str, default):
    if not os.path.exists(path):
        with open(path, "w") as f:
            json.dump(default, f, indent=2)

def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path: str, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

ensure_file(WALLETS_FILE, {})
ensure_file(SUMMARY_FILE, {})
ensure_file(HIDE_FILE, {})
ensure_file(MONITOR_FILE, {})


# Web3 helpers
@lru_cache(maxsize=16)
def web3_for_network(network: str) -> Web3:
    url = RPC_URLS.get(network)
    if not url:
        raise ValueError(f"Unsupported network: {network}")
    return Web3(Web3.HTTPProvider(url))

def sync_get_native_balance(address: str, network: str) -> float:
    w3 = web3_for_network(network)
    checksum = Web3.to_checksum_address(address)
    wei = w3.eth.get_balance(checksum)
    eth = w3.from_wei(wei, "ether")
    return float(eth)

def sync_get_token_balance(address: str, network: str, token_contract: str) -> (float, int):
    try:
        w3 = web3_for_network(network)
        contract = w3.eth.contract(address=Web3.to_checksum_address(token_contract), abi=ERC20_MIN_ABI)
        raw = contract.functions.balanceOf(Web3.to_checksum_address(address)).call()
        try:
            decimals = contract.functions.decimals().call()
        except Exception:
            decimals = 18
        bal = raw / (10 ** decimals)
        return float(bal), decimals
    except Exception as e:
        logger.exception("token balance fetch error")
        return 0.0, 18

def sync_fetch_prices(coingecko_ids: list) -> dict:
    if not coingecko_ids:
        return {}
    ids = ",".join(set(coingecko_ids))
    try:
        r = requests.get("https://api.coingecko.com/api/v3/simple/price",
                         params={"ids": ids, "vs_currencies": "usd"}, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        logger.exception("CoinGecko fetch error")
        return {}

# Async wrappers
async def get_native_balance(address: str, network: str) -> float:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, sync_get_native_balance, address, network)

async def get_token_balance(address: str, network: str, token_contract: str) -> float:
    loop = asyncio.get_running_loop()
    bal, _ = await loop.run_in_executor(None, sync_get_token_balance, address, network, token_contract)
    return bal

async def fetch_prices(cg_ids: list) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, sync_fetch_prices, cg_ids)


# Display helpers
def usd_fmt(x: float) -> str:
    return f"${x:,.2f}"

def small_fmt(x: float) -> str:
    if x >= 1:
        return f"{x:,.4f}"
    return f"{x:.8f}"

def make_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add wallet", callback_data="menu_add")],
        [InlineKeyboardButton("📋 List wallets", callback_data="menu_list"),
         InlineKeyboardButton("💹 Summary", callback_data="menu_summary")],
        [InlineKeyboardButton("🗑 Remove wallet", callback_data="menu_remove"),
         InlineKeyboardButton("🙈 Toggle hide", callback_data="menu_toggle_hide")]
    ])

# Chart generation (Visualization)
def generate_pie_bytes(label_to_value: dict, title: str = "Portfolio"):
    labels = list(label_to_value.keys())
    sizes = [float(label_to_value[k]) for k in labels] if labels else []
    if not sizes or sum(sizes) == 0:
        labels = ["No value"]
        sizes = [1]
    fig, ax = plt.subplots(figsize=(5, 4), dpi=120)
    ax.pie(sizes, labels=labels, autopct="%1.1f%%", startangle=90)
    ax.set_title(title)
    ax.axis("equal")
    buf = BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", transparent=True)
    plt.close(fig)
    buf.seek(0)
    return buf


# Wallet storage helpers
def get_wallets_for_chat(chat_id: str) -> dict:
    allw = load_json(WALLETS_FILE, {})
    return allw.get(str(chat_id), {})

def save_wallets_for_chat(chat_id: str, wallets: dict):
    allw = load_json(WALLETS_FILE, {})
    allw[str(chat_id)] = wallets
    save_json(WALLETS_FILE, allw)

def remove_wallet_for_chat(chat_id: str, alias: str):
    allw = load_json(WALLETS_FILE, {})
    cw = allw.get(str(chat_id), {})
    if alias in cw:
        cw.pop(alias)
        allw[str(chat_id)] = cw
        save_json(WALLETS_FILE, allw)
        return True
    return False

# hide mode per chat
def set_hide(chat_id: str, value: bool):
    h = load_json(HIDE_FILE, {})
    h[str(chat_id)] = {"hide": bool(value)}
    save_json(HIDE_FILE, h)

def get_hide(chat_id: str) -> bool:
    h = load_json(HIDE_FILE, {})
    return bool(h.get(str(chat_id), {}).get("hide", False))

# Wallet summary & price resolution

async def fetch_wallet_summary(alias, address, network):
    # native amount
    try:
        native_amt = await get_native_balance(address, network)
    except Exception as e:
        logger.exception("native balance error")
        native_amt = 0.0

    # tokens tracked for this network
    token_contracts = ERC20_TOKENS.get(network, {})
    token_symbols = list(token_contracts.keys())

    # fetch token balances concurrently
    token_tasks = [get_token_balance(address, network, token_contracts[s]) for s in token_symbols]
    token_balances = await asyncio.gather(*token_tasks, return_exceptions=True)

    token_map = {}
    for i, sym in enumerate(token_symbols):
        val = 0.0
        if isinstance(token_balances[i], Exception):
            val = 0.0
        else:
            val = float(token_balances[i] or 0.0)
        token_map[sym] = val

    # prepare coingecko ids to query: network-native + tokens
    cg_ids = []
    net_cg = NETWORK_PRICE_ID.get(network, "ethereum")
    cg_ids.append(net_cg)
    for sym in token_map.keys():
        cg = SYMBOL_TO_CGID.get(sym.lower())
        if cg:
            cg_ids.append(cg)
        else:
            # fallback: use symbol lower
            cg_ids.append(sym.lower())

    price_data = await fetch_prices(cg_ids)
    native_price = float(price_data.get(net_cg, {}).get("usd", 0.0))
    token_prices = {}
    for sym in token_map.keys():
        cg = SYMBOL_TO_CGID.get(sym.lower(), sym.lower())
        token_prices[sym] = float(price_data.get(cg, {}).get("usd", 0.0))

    native_usd = native_amt * native_price
    token_usd_total = sum(token_map[sym] * token_prices.get(sym, 0.0) for sym in token_map)
    wallet_usd = native_usd + token_usd_total

    # attach prices for convenience
    token_map["_prices"] = token_prices
    return alias, address, network, native_amt, native_usd, token_map, wallet_usd


# Telegram handlers


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "💼 *EVM Wallet Manager Bot*\n\n"
        "Manage EVM wallets (Ethereum, BSC, Base, Arbitrum), view balances and receive alerts.\n\n"
        "Commands:\n"
        "• /addwallet — Add wallet interactively\n"
        "• /listwallets — List wallets & balances\n"
        "• /removewallet — Remove a wallet\n"
        "• /summary — Portfolio summary (total USD + chart)\n"
        "• /hideon /hideoff — Hide/show balances\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=make_menu_keyboard())

# Add wallet conversation

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text("🆕 Enter an alias for this wallet (e.g., *Main Wallet*):", parse_mode="Markdown")
    else:
        await update.message.reply_text("🆕 Enter an alias for this wallet (e.g., *Main Wallet*):", parse_mode="Markdown")
    return STATE_ALIAS

async def add_alias_recv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["alias"] = update.message.text.strip()
    await update.message.reply_text("📮 Now send the wallet address (0x...):")
    return STATE_ADDRESS

async def add_address_recv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    addr = update.message.text.strip()
    if not addr.startswith("0x") or len(addr) < 40:
        await update.message.reply_text("⚠️ That doesn't look like a valid address. Please send a valid address (0x...):")
        return STATE_ADDRESS
    try:
        context.user_data["address"] = Web3.to_checksum_address(addr)
    except Exception:
        context.user_data["address"] = addr
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Ethereum", callback_data="net_ethereum")],
        [InlineKeyboardButton("BSC", callback_data="net_bsc"), InlineKeyboardButton("Arbitrum", callback_data="net_arbitrum")],
        [InlineKeyboardButton("Base", callback_data="net_base")],
    ])
    await update.message.reply_text("🌍 Choose network:", reply_markup=kb)
    return STATE_NETWORK

async def add_network_recv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    network = query.data.replace("net_", "")
    alias = context.user_data.get("alias")
    address = context.user_data.get("address")
    if not alias or not address:
        await query.edit_message_text("❌ Missing alias or address. Please start /addwallet again.")
        return ConversationHandler.END

    chat_id = str(query.message.chat_id)
    wallets = get_wallets_for_chat(chat_id)
    wallets[alias] = {"address": address, "network": network}
    save_wallets_for_chat(chat_id, wallets)

    await query.edit_message_text(f"✅ Added *{alias}* on *{network}*.\nAddress: `{address}`", parse_mode="Markdown")
    context.user_data.clear()
    return ConversationHandler.END

# === List wallets ===
async def cmd_listwallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # support both message and callback queries
    if update.callback_query:
        await update.callback_query.answer()
        send_func = update.callback_query.message.reply_text
        chat_id = str(update.callback_query.message.chat_id)
        effective_chat = update.callback_query.message.chat_id
    else:
        send_func = update.message.reply_text
        chat_id = str(update.message.chat_id)
        effective_chat = update.message.chat_id

    wallets = get_wallets_for_chat(chat_id)
    if not wallets:
        await send_func("📭 No wallets saved. Add one with /addwallet.", parse_mode="Markdown")
        return

    hide_mode = get_hide(chat_id)
    tasks = [fetch_wallet_summary(alias, info["address"], info["network"]) for alias, info in wallets.items()]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    total_usd = 0.0
    per_network = {}
    lines = []
    for r in results:
        if isinstance(r, Exception):
            lines.append(f"⚠️ Error fetching wallet: {r}")
            continue
        alias, addr, net, native_amt, native_usd, token_map, wallet_usd = r
        total_usd += wallet_usd
        per_network[net] = per_network.get(net, 0.0) + wallet_usd

        if hide_mode:
            lines.append(f"🔹 *{alias}* ({net})\n💵 Balance: •••\n")
        else:
            token_lines = []
            for sym, bal in token_map.items():
                if sym == "_prices":
                    continue
                price = token_map["_prices"].get(sym.lower(), 0.0) if "_prices" in token_map else 0.0
                token_lines.append(f"    • {sym}: {small_fmt(bal)} ({usd_fmt(bal * price)})")
            if not token_lines:
                token_lines = ["    — no tracked tokens —"]
            tokens_text = "\n".join(token_lines)
            lines.append(
                f"🔹 *{alias}* ({net})\n"
                f"    • Native: {small_fmt(native_amt)} ≈ {usd_fmt(native_usd)}\n"
                f"{tokens_text}\n"
                f"    • Wallet USD total: *{usd_fmt(wallet_usd)}*\n"
            )

    header = f"💰 *Saved wallets* — total: *{usd_fmt(total_usd)}*\n\n"
    text = header + "\n".join(lines)
    await send_func(text, parse_mode="Markdown")

    # send pie chart
    if per_network:
        chart = generate_pie_bytes(per_network, title="Portfolio by Network (USD)")
        if chart:
            await context.bot.send_photo(chat_id=effective_chat, photo=InputFile(chart, filename="portfolio.png"))

# === Remove wallet ===
async def cmd_remove_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        send = update.callback_query.message.reply_text
        chat_id = str(update.callback_query.message.chat_id)
    else:
        send = update.message.reply_text
        chat_id = str(update.message.chat_id)

    wallets = get_wallets_for_chat(chat_id)
    if not wallets:
        await send("📭 No wallets saved.")
        return

    kb = InlineKeyboardMarkup([[InlineKeyboardButton(alias, callback_data=f"remove_{alias}")] for alias in wallets.keys()])
    await send("🗑 Select a wallet to remove:", reply_markup=kb)

async def cmd_remove_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    alias = query.data.replace("remove_", "")
    chat_id = str(query.message.chat_id)
    ok = remove_wallet_for_chat(chat_id, alias)
    if ok:
        await query.edit_message_text(f"✅ Removed *{alias}*", parse_mode="Markdown")
    else:
        await query.edit_message_text("❌ Wallet not found.")

# Summary command
async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        send = update.callback_query.message.reply_text
        chat_id = str(update.callback_query.message.chat_id)
        effective_chat = update.callback_query.message.chat_id
    else:
        send = update.message.reply_text
        chat_id = str(update.message.chat_id)
        effective_chat = update.message.chat_id

    wallets = get_wallets_for_chat(chat_id)
    if not wallets:
        await send("📭 No wallets found. Add wallets with /addwallet", parse_mode="Markdown")
        return

    tasks = [fetch_wallet_summary(alias, info["address"], info["network"]) for alias, info in wallets.items()]
    res = await asyncio.gather(*tasks, return_exceptions=True)

    total_usd = 0.0
    per_network = {}
    rows = []
    for r in res:
        if isinstance(r, Exception):
            continue
        alias, addr, net, native_amt, native_usd, token_map, wallet_usd = r
        total_usd += wallet_usd
        per_network[net] = per_network.get(net, 0.0) + wallet_usd
        rows.append((alias, net, native_amt, wallet_usd))

    summary_store = load_json(SUMMARY_FILE, {})
    prev = float(summary_store.get(str(chat_id), {}).get("last_total_usd", 0.0))
    diff = total_usd - prev
    icon = "▲" if diff >= 0 else "▼"

    # save today's total
    s = summary_store
    s[str(chat_id)] = {"last_total_usd": total_usd, "timestamp": datetime.utcnow().isoformat()}
    save_json(SUMMARY_FILE, s)

    msg = (
        f"📊 *Daily Summary*\n\n"
        f"💵 *Total Portfolio:* {usd_fmt(total_usd)}\n"
        f"{icon} Change vs last saved: {usd_fmt(abs(diff))}\n\n"
        f"Breakdown by wallet:\n"
    )
    for alias, net, native_amt, wallet_usd in rows:
        msg += f"• *{alias}* ({net}) — {usd_fmt(wallet_usd)}\n"

    await send(msg, parse_mode="Markdown")

    # pie chart
    if per_network:
        chart = generate_pie_bytes(per_network, title="Portfolio by Network (USD)")
        if chart:
            await context.bot.send_photo(chat_id=effective_chat, photo=InputFile(chart, filename="summary_chart.png"))

# Hide toggle
async def cmd_hide_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    set_hide(chat_id, True)
    await update.message.reply_text("🙈 Balances will now be hidden.")

async def cmd_hide_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    set_hide(chat_id, False)
    await update.message.reply_text("👀 Balances will now be visible.")

# Background monitoring for incoming txs

def load_monitor_state():
    return load_json(MONITOR_FILE, {})

def save_monitor_state(state):
    save_json(MONITOR_FILE, state)

async def scan_network_for_incoming(network: str, tracked_addresses_by_chat: dict, app):
    """
    tracked_addresses_by_chat: { chat_id: { alias: {"address":..., "network":...}, ... }, ... }
    We'll scan from last checked block -> latest and look for:
     - native transactions where tx.to == address
     - ERC20 Transfer logs where 'to' == address
    When found, send notification to the chat_id that owns the alias.
    """
    w3 = web3_for_network(network)
    mon = load_monitor_state()
    last_checked = int(mon.get(network, max(w3.eth.block_number - 3, 0)))
    try:
        latest = w3.eth.block_number
    except Exception as e:
        logger.exception("Failed to get block number")
        return

    # limit scan span to avoid huge queries
    if latest - last_checked > 100:
        from_block = latest - 100
    else:
        from_block = last_checked + 1

    # if nothing to scan
    if from_block > latest:
        mon[network] = latest
        save_monitor_state(mon)
        return

    # build a set of addresses we care about for fast checking
    addr_to_chats = {}  # address -> list of (chat_id, alias)
    for chat_id, wallets in tracked_addresses_by_chat.items():
        for alias, w in wallets.items():
            if w.get("network") != network:
                continue
            addr = Web3.to_checksum_address(w["address"])
            addr_to_chats.setdefault(addr.lower(), []).append((chat_id, alias))

    # If no addresses on this network, update pointer and return
    if not addr_to_chats:
        mon[network] = latest
        save_monitor_state(mon)
        return

    # 1) scan logs for ERC20 Transfer events to our addresses
    transfer_sig = Web3.keccak(text="Transfer(address,address,uint256)").hex()
    try:
        logs = w3.eth.get_logs({"fromBlock": from_block, "toBlock": latest, "topics": [transfer_sig]})
    except Exception:
        logs = []
    for log in logs:
        # typically topics: [sig, from, to]; data: value
        topics = log["topics"]
        # to is the third topic (index 2), encoded as 32 bytes hex
        if len(topics) >= 3:
            to_topic = topics[2].hex()
            # last 40 hex chars correspond to address
            to_addr = Web3.to_checksum_address("0x" + to_topic[-40:])
            if to_addr.lower() in addr_to_chats:
                # find token contract
                token_contract = log["address"]
                # decode value from data
                try:
                    value = int(log["data"].hex(), 16)
                except Exception:
                    value = 0
                # Resolve decimals by calling contract (sync in executor)
                try:
                    contract = w3.eth.contract(address=Web3.to_checksum_address(token_contract), abi=ERC20_MIN_ABI)
                    decimals = contract.functions.decimals().call()
                    symbol = contract.functions.symbol().call()
                except Exception:
                    decimals = 18
                    symbol = "TOKEN"
                amount = value / (10 ** decimals)
                # fetch approximate USD price via CoinGecko (best-effort)
                cg_id = SYMBOL_TO_CGID.get(symbol.lower(), symbol.lower())
                price_data = await fetch_prices([cg_id])
                usd_price = float(price_data.get(cg_id, {}).get("usd", 0.0))
                approx_usd = amount * usd_price
                for chat_id, alias in addr_to_chats[to_addr.lower()]:
                    text = (
                        f"📥 *Incoming ERC-20 Transfer*\n"
                        f"Wallet: *{alias}*\n"
                        f"Token: *{symbol}* — {small_fmt(amount)}\n"
                        f"Network: *{network}*\n"
                        f"≈ {usd_fmt(approx_usd)}"
                    )
                    try:
                        await app.bot.send_message(int(chat_id), text, parse_mode="Markdown")
                    except Exception:
                        logger.exception("failed to send ERC20 incoming notification")

    # 2) scan transactions for native transfers to our addresses
    # For each block in range, check transactions
    for blk in range(from_block, latest + 1):
        try:
            block = w3.eth.get_block(blk, full_transactions=True)
        except Exception:
            continue
        for tx in block.transactions:
            if tx.to:
                try:
                    to_addr = Web3.to_checksum_address(tx.to)
                except Exception:
                    continue
                if to_addr.lower() in addr_to_chats:
                    amount = w3.from_wei(tx.value, "ether")
                    # get native price
                    net_cg = NETWORK_PRICE_ID.get(network, "ethereum")
                    p = await fetch_prices([net_cg])
                    native_price = float(p.get(net_cg, {}).get("usd", 0.0))
                    approx_usd = float(amount) * native_price
                    for chat_id, alias in addr_to_chats[to_addr.lower()]:
                        text = (
                            f"📥 *Incoming Native Transfer*\n"
                            f"Wallet: *{alias}*\n"
                            f"Amount: {small_fmt(float(amount))} {network.upper()}\n"
                            f"Network: *{network}*\n"
                            f"≈ {usd_fmt(approx_usd)}"
                        )
                        try:
                            await app.bot.send_message(int(chat_id), text, parse_mode="Markdown")
                        except Exception:
                            logger.exception("failed to send native incoming notification")

    # update monitor pointer
    mon[network] = latest
    save_monitor_state(mon)

async def monitor_task(app, interval=60):
    """
    Runs continuously: every `interval` seconds scans each network for incoming transactions.
    """
    while True:
        try:
            all_wallets = load_json(WALLETS_FILE, {})
            # pass the entire mapping
            tracked_by_chat = {k: v for k, v in all_wallets.items()}
            for net in RPC_URLS.keys():
                try:
                    await scan_network_for_incoming(net, tracked_by_chat, app)
                except Exception:
                    logger.exception("Error scanning network %s", net)
        except Exception:
            logger.exception("Monitor outer loop error")
        await asyncio.sleep(interval)


# App builder & startup
def build_app():
    request = HTTPXRequest(connect_timeout=10, read_timeout=10)
    app = ApplicationBuilder().token(TOKEN).request(request).build()

    # Conversation for add wallet
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("addwallet", add_start),
            CallbackQueryHandler(lambda u, c: add_start(u, c), pattern="^menu_add$")
        ],
        states={
            STATE_ALIAS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_alias_recv)],
            STATE_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_address_recv)],
            STATE_NETWORK: [CallbackQueryHandler(add_network_recv, pattern="^net_")],
        },
        fallbacks=[],  # ✅ REQUIRED FIX
        allow_reentry=True
    )

    # Commands & buttons
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(conv)
    app.add_handler(CommandHandler("listwallets", cmd_listwallets))
    app.add_handler(CommandHandler("removewallet", cmd_remove_start))
    app.add_handler(CallbackQueryHandler(cmd_remove_confirm, pattern="^remove_"))
    app.add_handler(CallbackQueryHandler(lambda u, c: asyncio.create_task(cmd_listwallets(u, c)), pattern="^menu_list$"))
    app.add_handler(CallbackQueryHandler(lambda u, c: asyncio.create_task(cmd_summary(u, c)), pattern="^menu_summary$"))
    app.add_handler(CallbackQueryHandler(lambda u, c: asyncio.create_task(add_start(u, c)), pattern="^menu_add$"))
    app.add_handler(CallbackQueryHandler(lambda u, c: asyncio.create_task(cmd_remove_start(u, c)), pattern="^menu_remove$"))
    app.add_handler(CallbackQueryHandler(lambda u, c: asyncio.create_task(cmd_hide_on(u, c)), pattern="^menu_toggle_hide$"))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("hideon", cmd_hide_on))
    app.add_handler(CommandHandler("hideoff", cmd_hide_off))

    return app


# Helper to toggle hide from menu inline button
async def toggle_menu_hide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat_id)
    current = get_hide(chat_id)
    set_hide(chat_id, not current)
    await query.edit_message_text("🙈 Balances hidden." if not current else "👀 Balances visible.")

# Start background monitor and daily summary job after app starts
async def on_startup(app):
    # start monitor background task
    app.create_task(monitor_task(app, interval=60))

    # schedule daily summary at 09:00 Asia/Karachi for all chats that have wallets
    tz = pytz.timezone("Asia/Karachi")
    target_time = dtime(hour=9, minute=0, tzinfo=tz)

    async def daily_job(context: ContextTypes.DEFAULT_TYPE):
        # iterate all chats with wallets and send summary
        allw = load_json(WALLETS_FILE, {})
        for chat_id, wallets in allw.items():
            if not wallets:
                continue
            # prepare and send summary for each chat
            try:

                class StubMessage:
                    def __init__(self, chat_id):
                        self.chat_id = int(chat_id)
                class StubUpdate:
                    def __init__(self, chat_id):
                        self.callback_query = None
                        self.message = StubMessage(chat_id)
                        self.effective_chat = StubMessage(chat_id)

                await cmd_summary(StubUpdate(chat_id), context)
            except Exception:
                logger.exception("Failed daily summary for chat %s", chat_id)

    # Use job queue to run daily
    app.job_queue.run_daily(daily_job, time=target_time, days=(0,1,2,3,4,5,6))


# Entrypoint

import asyncio

async def on_startup(app):
    print("🚀 Bot initialized successfully!")

def main():
    app = build_app()
    print("✅ EVM Wallet Manager Bot starting...")


    asyncio.run(on_startup(app))


    app.run_polling()

if __name__ == "__main__":
    main()


