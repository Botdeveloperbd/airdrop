import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    Defaults,
    filters,
    CallbackQueryHandler,
)
from config import BOT_TOKEN, ADMIN_ID
from database import Database
from rate_limiter import RateLimiter
from web3 import Web3
from dotenv import load_dotenv
import os
import csv
import io
from datetime import datetime

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Web3 setup for USDT
BSC_NODE_URL = os.getenv("BSC_NODE_URL")
BOT_PRIVATE_KEY = os.getenv("BOT_PRIVATE_KEY")
BOT_WALLET_ADDRESS = os.getenv("BOT_WALLET_ADDRESS")
USDT_CONTRACT_ADDRESS = "0x337610d27c682E347C9cD60BD4b3b107C9d34dDd"  # USDT on BSC testnet

# BEP20 Token ABI (minimal for USDT)
USDT_ABI = [
    {
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_value", "type": "uint256"}
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    }
]

class AirdropBot:
    def __init__(self):
        self.db = Database()
        self.rate_limiter = RateLimiter()
        self.web3 = Web3(Web3.HTTPProvider(BSC_NODE_URL))
        if not self.web3.is_connected():
            logger.error("Failed to connect to BSC node")
            raise Exception("Cannot connect to BSC node")
        
        self.usdt_contract = self.web3.eth.contract(
            address=Web3.to_checksum_address(USDT_CONTRACT_ADDRESS),
            abi=USDT_ABI
        )
        self.bot_address = Web3.to_checksum_address(BOT_WALLET_ADDRESS)
        
        self.app = ApplicationBuilder()\
            .token(BOT_TOKEN)\
            .defaults(Defaults(parse_mode='Markdown'))\
            .build()

        # Register handlers
        self._register_handlers()

    def _register_handlers(self):
        self.app.add_handler(CommandHandler("start", self.start, filters=filters.ChatType.PRIVATE))
        self.app.add_handler(CommandHandler("menu", self.show_menu, filters=filters.ChatType.PRIVATE))
        self.app.add_handler(CommandHandler("wallet", self.set_wallet, filters=filters.ChatType.PRIVATE))
        self.app.add_handler(CallbackQueryHandler(self.handle_button))

    def _get_main_menu(self, user_id: int) -> InlineKeyboardMarkup:
        buttons = [
            InlineKeyboardButton("💰 Balance", callback_data="balance"),
            InlineKeyboardButton("🪙 Set Wallet", callback_data="set_wallet"),
            InlineKeyboardButton("📤 Withdraw", callback_data="withdraw"),
        ]
        if user_id == ADMIN_ID:
            buttons.append(InlineKeyboardButton("🛠 Admin Dashboard", callback_data="admin_dashboard"))

        keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
        return InlineKeyboardMarkup(keyboard)

    def _get_admin_menu(self) -> InlineKeyboardMarkup:
        buttons = [
            InlineKeyboardButton("👥 View Users", callback_data="admin_view_users"),
            InlineKeyboardButton("📬 Manage Withdrawals", callback_data="admin_manage_withdrawals"),
            InlineKeyboardButton("📊 Export Users", callback_data="admin_export_users"),
            InlineKeyboardButton("🔨 Ban User", callback_data="ban"),
            InlineKeyboardButton("🔙 Back to Main", callback_data="back_to_main"),
        ]
        keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
        return InlineKeyboardMarkup(keyboard)

    def _get_user_list_keyboard(self, page: int, users_per_page: int = 5) -> InlineKeyboardMarkup:
        total_users = self.db.execute_query("SELECT COUNT(*) FROM users").fetchone()[0]
        total_pages = (total_users + users_per_page - 1) // users_per_page

        buttons = []
        if page > 1:
            buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin_view_users_{page-1}"))
        if page < total_pages:
            buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin_view_users_{page+1}"))
        buttons.append(InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_dashboard"))

        keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
        if not keyboard or not keyboard[0]:
            keyboard = [[]]
        return InlineKeyboardMarkup(keyboard)

    def _get_withdrawal_list_keyboard(self, page: int, withdrawals_per_page: int = 5) -> InlineKeyboardMarkup:
        total_withdrawals = self.db.execute_query("SELECT COUNT(*) FROM withdrawals WHERE status='pending'").fetchone()[0]
        total_pages = (total_withdrawals + withdrawals_per_page - 1) // withdrawals_per_page

        buttons = []
        if page > 1:
            buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin_manage_withdrawals_{page-1}"))
        if page < total_pages:
            buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin_manage_withdrawals_{page+1}"))
        buttons.append(InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_dashboard"))

        keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
        if not keyboard or not keyboard[0]:
            keyboard = [[]]
        return InlineKeyboardMarkup(keyboard)

    def _get_withdrawal_action_keyboard(self, withdrawal_id: int) -> InlineKeyboardMarkup:
        buttons = [
            InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve_withdrawal_{withdrawal_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject_withdrawal_{withdrawal_id}"),
            InlineKeyboardButton("🔙 Back to Withdrawals", callback_data="admin_manage_withdrawals_1"),
        ]
        keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
        return InlineKeyboardMarkup(keyboard)

    async def show_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            user_id = update.effective_user.id
            if await self._check_ban(user_id):
                await update.message.reply_text("🚫 You are banned from using this bot.")
                return

            reply_markup = self._get_main_menu(user_id)
            await update.message.reply_text(
                "📋 *Main Menu*\nChoose an option below:",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Error in show_menu: {e}")
            await update.message.reply_text("❌ An error occurred. Please try again later.")

    async def handle_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        user_id = query.from_user.id
        callback_data = query.data

        try:
            if await self._check_ban(user_id) and callback_data not in ["start", "admin_dashboard"]:
                await query.message.reply_text("🚫 You are banned from using this bot.")
                await query.answer()
                return

            if callback_data == "start":
                await self.start(update, context)
            elif callback_data == "balance":
                await self.balance(query, context)
            elif callback_data == "set_wallet":
                await query.message.reply_text("🪙 *Usage*:\n/wallet 0xYourBEP20Address")
                await query.answer()
            elif callback_data == "withdraw":
                await self.withdraw(query, context)
            elif callback_data == "ban" and user_id == ADMIN_ID:
                await query.message.reply_text("🔨 *Usage*: /ban <user_id>")
                await query.answer()
            elif callback_data == "admin_dashboard" and user_id == ADMIN_ID:
                reply_markup = self._get_admin_menu()
                await query.message.reply_text(
                    "🛠 *Admin Dashboard*\nSelect an option:",
                    reply_markup=reply_markup
                )
                await query.answer()
            elif callback_data == "admin_view_users" or callback_data.startswith("admin_view_users_"):
                if user_id != ADMIN_ID:
                    await query.message.reply_text("🚫 Unauthorized access.")
                    await query.answer()
                    return
                page = int(callback_data.split("_")[-1]) if callback_data.startswith("admin_view_users_") else 1
                await self.admin_view_users(query, context, page)
            elif callback_data == "admin_manage_withdrawals" or callback_data.startswith("admin_manage_withdrawals_"):
                if user_id != ADMIN_ID:
                    await query.message.reply_text("🚫 Unauthorized access.")
                    await query.answer()
                    return
                page = int(callback_data.split("_")[-1]) if callback_data.startswith("admin_manage_withdrawals_") else 1
                await self.admin_manage_withdrawals(query, context, page)
            elif callback_data.startswith("admin_approve_withdrawal_"):
                if user_id != ADMIN_ID:
                    await query.message.reply_text("🚫 Unauthorized access.")
                    await query.answer()
                    return
                withdrawal_id = int(callback_data.split("_")[-1])
                await self.admin_approve_withdrawal(query, context, withdrawal_id)
            elif callback_data.startswith("admin_reject_withdrawal_"):
                if user_id != ADMIN_ID:
                    await query.message.reply_text("🚫 Unauthorized access.")
                    await query.answer()
                    return
                withdrawal_id = int(callback_data.split("_")[-1])
                await self.admin_reject_withdrawal(query, context, withdrawal_id)
            elif callback_data == "admin_export_users" and user_id == ADMIN_ID:
                await self.admin_export_users(query, context)
            elif callback_data == "back_to_main":
                reply_markup = self._get_main_menu(user_id)
                await query.message.reply_text("📋 *Main Menu*\nChoose an option:", reply_markup=reply_markup)
                await query.answer()
            else:
                await query.message.reply_text("🚫 Invalid action.")
                await query.answer()
                return

        except Exception as e:
            logger.error(f"Error in handle_button: {e}")
            await query.message.reply_text("❌ An error occurred. Please try again later.")
            await query.answer()

    async def _check_ban(self, user_id: int) -> bool:
        return bool(self.db.execute_query("SELECT 1 FROM banned_users WHERE user_id=?", (user_id,)).fetchone())

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            user_id = update.effective_user.id
            if await self._check_ban(user_id):
                await update.message.reply_text("🚫 You are banned from using this bot.")
                return

            if not self.rate_limiter.check_rate_limit(user_id, "start"):
                await update.message.reply_text("⏳ Please wait before trying again.")
                return

            username = update.effective_user.username or "N/A"
            self.db.execute_query(
                "INSERT OR IGNORE INTO users (user_id, username, balance, referrals) VALUES (?, ?, 0, 0)",
                (user_id, username)
            )

            if context.args and context.args[0].isdigit():
                ref_id = int(context.args[0])
                if ref_id != user_id and not await self._check_ban(ref_id):
                    self.db.execute_query(
                        "UPDATE users SET referrals = referrals + 1, balance = balance + 8 WHERE user_id=?",
                        (ref_id,)
                    )

            self.db.commit()

            bot_username = (await context.bot.get_me()).username
            invite_link = f"https://t.me/{bot_username}?start={user_id}"

            reply_markup = self._get_main_menu(user_id)
            await update.message.reply_text(
                f"👋 *Welcome to the USDT Airdrop Bot!*\n"
                f"💰 Check your earnings or withdraw USDT\n"
                f"🎯 Invite friends: `{invite_link}`\n"
                f"📋 Use the buttons below to interact:",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Error in start command: {e}")
            await update.message.reply_text("❌ An error occurred. Please try again later.")

    async def balance(self, query: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            user_id = query.from_user.id
            if await self._check_ban(user_id):
                await query.message.reply_text("🚫 You are banned from using this bot.")
                return

            data = self.db.execute_query(
                "SELECT referrals, balance FROM users WHERE user_id=?", (user_id,)
            ).fetchone()

            if data:
                referrals, balance = data
                await query.message.reply_text(
                    f"🔁 *Referrals*: {referrals}\n"
                    f"💰 *USDT Balance*: ${balance:.2f}"
                )
            else:
                await query.message.reply_text("❌ You are not registered. Use /start to register.")
        except Exception as e:
            logger.error(f"Error in balance command: {e}")
            await query.message.reply_text("❌ An error occurred. Please try again later.")

    async def set_wallet(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            user_id = update.effective_user.id
            if await self._check_ban(user_id):
                await update.message.reply_text("🚫 You are banned from using this bot.")
                return

            if not context.args:
                await update.message.reply_text("🪙 *Usage*:\n/wallet 0xYourBEP20Address")
                return

            wallet = context.args[0].strip()
            if not self._is_valid_wallet(wallet):
                await update.message.reply_text("❌ Invalid wallet address. Must be a valid BEP20 address (0x... 42 characters).")
                return

            self.db.execute_query(
                "UPDATE users SET wallet=? WHERE user_id=?", (wallet, user_id)
            )
            self.db.commit()
            await update.message.reply_text(f"✅ *Wallet saved for USDT withdrawals*:\n`{wallet}`")
        except Exception as e:
            logger.error(f"Error in set_wallet command: {e}")
            await update.message.reply_text("❌ An error occurred. Please try again later.")

    async def withdraw(self, query: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            user_id = query.from_user.id
            if await self._check_ban(user_id):
                await query.message.reply_text("🚫 You are banned from using this bot.")
                return

            if not self.rate_limiter.check_rate_limit(user_id, "withdraw"):
                await query.message.reply_text("⏳ Please wait before submitting another withdrawal.")
                return

            data = self.db.execute_query(
                "SELECT balance, wallet FROM users WHERE user_id=?", (user_id,)
            ).fetchone()

            if not data:
                await query.message.reply_text("❌ You are not registered. Use /start to register.")
                return

            balance, wallet = data
            if balance < 20:
                await query.message.reply_text("🚫 Minimum withdrawal amount is $20.")
                return
            if not wallet:
                await query.message.reply_text("⚠️ Please set your wallet using the *Set Wallet* button.")
                return

            if self.db.execute_query(
                "SELECT 1 FROM withdrawals WHERE user_id=? AND status='pending'", (user_id,)
            ).fetchone():
                await query.message.reply_text("⏳ You already have a pending withdrawal.")
                return

            self.db.execute_query(
                "INSERT INTO withdrawals (user_id, amount, status, wallet) VALUES (?, ?, 'pending', ?)",
                (user_id, balance, wallet)
            )
            self.db.commit()

            await query.message.reply_text("✅ *Withdrawal request submitted for admin approval.*")
            await context.bot.send_message(
                ADMIN_ID,
                f"📬 *New USDT Withdrawal Request*:\n"
                f"👤 User: {user_id}\n"
                f"💰 Amount: ${balance:.2f}\n"
                f"💼 Wallet: `{wallet}`"
            )

        except Exception as e:
            logger.error(f"Error in withdraw command: {e}")
            await query.message.reply_text("❌ An error occurred. Please try again later.")

    async def admin_view_users(self, query: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
        try:
            users_per_page = 5
            offset = (page - 1) * users_per_page
            users = self.db.execute_query(
                "SELECT user_id, username, balance, referrals, wallet FROM users LIMIT ? OFFSET ?",
                (users_per_page, offset)
            ).fetchall()

            if not users:
                await query.message.reply_text("👥 *No users found.*")
                return

            message = f"👥 *Users (Page {page})*\n\n"
            for user in users:
                user_id, username, balance, referrals, wallet = user
                wallet_display = wallet if wallet else "Not set"
                username_display = username if username else "N/A"
                message += (
                    f"👤 *User ID*: {user_id}\n"
                    f"📛 *Username*: {username_display}\n"
                    f"💰 *Balance*: ${balance:.2f}\n"
                    f"🔁 *Referrals*: {referrals}\n"
                    f"💼 *Wallet*: `{wallet_display}`\n\n"
                )

            reply_markup = self._get_user_list_keyboard(page, users_per_page)
            await query.message.reply_text(message, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Error in admin_view_users: {e}")
            await query.message.reply_text("❌ An error occurred. Please try again later.")

    async def admin_manage_withdrawals(self, query: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
        try:
            withdrawals_per_page = 5
            offset = (page - 1) * withdrawals_per_page
            withdrawals = self.db.execute_query(
                "SELECT id, user_id, amount, wallet FROM withdrawals WHERE status='pending' LIMIT ? OFFSET ?",
                (withdrawals_per_page, offset)
            ).fetchall()

            if not withdrawals:
                await query.message.reply_text("📬 *No pending withdrawal requests.*")
                return

            message = f"📬 *Pending Withdrawals (Page {page})*\n\n"
            for withdrawal in withdrawals:
                withdrawal_id, user_id, amount, wallet = withdrawal
                user_data = self.db.execute_query(
                    "SELECT username FROM users WHERE user_id=?", (user_id,)
                ).fetchone()
                username = user_data[0] if user_data and user_data[0] else "N/A"
                message += (
                    f"🆔 *Withdrawal ID*: {withdrawal_id}\n"
                    f"👤 *User ID*: {user_id}\n"
                    f"📛 *Username*: {username}\n"
                    f"💰 *Amount*: ${amount:.2f}\n"
                    f"💼 *Wallet*: `{wallet}`\n\n"
                )

            reply_markup = self._get_withdrawal_list_keyboard(page, withdrawals_per_page)
            await query.message.reply_text(message, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Error in admin_manage_withdrawals: {e}")
            await query.message.reply_text("❌ An error occurred. Please try again later.")

    async def admin_approve_withdrawal(self, query: Update, context: ContextTypes.DEFAULT_TYPE, withdrawal_id: int) -> None:
        try:
            withdrawal = self.db.execute_query(
                "SELECT user_id, amount, wallet FROM withdrawals WHERE id=? AND status='pending'",
                (withdrawal_id,)
            ).fetchone()

            if not withdrawal:
                await query.message.reply_text("❌ Withdrawal request not found or already processed.")
                return

            user_id, amount, wallet = withdrawal
            usdt_amount = int(amount * 10**6)

            bot_usdt_balance = self.usdt_contract.functions.balanceOf(self.bot_address).call()
            if bot_usdt_balance < usdt_amount:
                logger.error(f"Insufficient USDT balance: {bot_usdt_balance / 10**6} USDT")
                await query.message.reply_text("❌ Insufficient USDT in bot wallet.")
                return

            bnb_balance = self.web3.eth.get_balance(self.bot_address)
            gas_price = self.web3.eth.gas_price
            gas_limit = 100000
            if bnb_balance < gas_price * gas_limit:
                logger.error(f"Insufficient BNB: {bnb_balance / 10**18} BNB")
                await query.message.reply_text("❌ Insufficient BNB for gas.")
                return

            user_wallet = Web3.to_checksum_address(wallet)
            tx = self.usdt_contract.functions.transfer(user_wallet, usdt_amount).build_transaction({
                'from': self.bot_address,
                'gas': gas_limit,
                'gasPrice': gas_price,
                'nonce': self.web3.eth.get_transaction_count(self.bot_address),
                'chainId': 97  # BSC testnet
            })

            signed_tx = self.web3.eth.account.sign_transaction(tx, private_key=BOT_PRIVATE_KEY)
            tx_hash = self.web3.eth.send_raw_transaction(signed_tx.raw_transaction)
            tx_receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)

            if tx_receipt.status == 1:
                self.db.execute_query(
                    "UPDATE withdrawals SET status='completed', tx_hash=? WHERE id=?",
                    (tx_hash.hex(), withdrawal_id)
                )
                self.db.execute_query(
                    "UPDATE users SET balance = balance - ? WHERE user_id=?",
                    (amount, user_id)
                )
                self.db.commit()

                await query.message.reply_text(
                    f"✅ *Withdrawal approved!*\n"
                    f"🆔 Withdrawal ID: {withdrawal_id}\n"
                    f"💰 Amount: ${amount:.2f}\n"
                    f"📤 Tx Hash: `{tx_hash.hex()}`"
                )
                await context.bot.send_message(
                    user_id,
                    f"✅ *Your USDT withdrawal of ${amount:.2f} has been approved!*\n"
                    f"📤 Tx Hash: `{tx_hash.hex()}`\n"
                    f"🔗 Explorer: https://testnet.bscscan.com/tx/{tx_hash.hex()}"
                )
            else:
                logger.error(f"USDT Transaction failed: {tx_receipt}")
                self.db.execute_query(
                    "UPDATE withdrawals SET status='failed', tx_hash=? WHERE id=?",
                    (tx_hash.hex(), withdrawal_id)
                )
                self.db.commit()
                await query.message.reply_text("❌ Withdrawal transaction failed.")
                await context.bot.send_message(
                    user_id,
                    f"❌ Your USDT withdrawal of ${amount:.2f} failed. Please contact admin."
                )

            reply_markup = self._get_withdrawal_list_keyboard(1)
            await query.message.reply_text("📬 *Pending Withdrawals*", reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Error in admin_approve_withdrawal: {e}")
            await query.message.reply_text("❌ An error occurred. Please try again later.")

    async def admin_reject_withdrawal(self, query: Update, context: ContextTypes.DEFAULT_TYPE, withdrawal_id: int) -> None:
        try:
            withdrawal = self.db.execute_query(
                "SELECT user_id, amount FROM withdrawals WHERE id=? AND status='pending'",
                (withdrawal_id,)
            ).fetchone()

            if not withdrawal:
                await query.message.reply_text("❌ Withdrawal request not found or already processed.")
                return

            user_id, amount = withdrawal
            self.db.execute_query(
                "UPDATE withdrawals SET status='rejected' WHERE id=?",
                (withdrawal_id,)
            )
            self.db.commit()

            await query.message.reply_text(
                f"❌ *Withdrawal rejected!*\n"
                f"🆔 Withdrawal ID: {withdrawal_id}\n"
                f"💰 Amount: ${amount:.2f}"
            )
            await context.bot.send_message(
                user_id,
                f"❌ Your USDT withdrawal of ${amount:.2f} was rejected by the admin."
            )

            reply_markup = self._get_withdrawal_list_keyboard(1)
            await query.message.reply_text("📬 *Pending Withdrawals*", reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Error in admin_reject_withdrawal: {e}")
            await query.message.reply_text("❌ An error occurred. Please try again later.")

    async def admin_export_users(self, query: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            users = self.db.execute_query(
                "SELECT user_id, username, balance, referrals, wallet FROM users"
            ).fetchall()

            if not users:
                await query.message.reply_text("👥 *No users to export.*")
                return

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["User ID", "Username", "Balance (USDT)", "Referrals", "Wallet Address"])

            for user in users:
                user_id, username, balance, referrals, wallet = user
                username = username or "N/A"
                wallet = wallet or "Not set"
                writer.writerow([user_id, username, balance, referrals, wallet])

            csv_data = output.getvalue().encode('utf-8')
            output.close()

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            await query.message.reply_document(
                document=io.BytesIO(csv_data),
                filename=f"users_export_{timestamp}.csv",
                caption="📊 *User Data Export*"
            )
        except Exception as e:
            logger.error(f"Error in admin_export_users: {e}")
            await query.message.reply_text("❌ An error occurred. Please try again later.")

    async def ban(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            if update.effective_user.id != ADMIN_ID:
                await update.message.reply_text("🚫 Unauthorized access.")
                return

            if not context.args or not context.args[0].isdigit():
                await update.message.reply_text("🔨 *Usage*: /ban <user_id>")
                return

            user_id = int(context.args[0])
            self.db.execute_query(
                "INSERT OR IGNORE INTO banned_users (user_id) VALUES (?)", (user_id,)
            )
            self.db.commit()
            await update.message.reply_text(f"🔨 User {user_id} has been banned.")
        except Exception as e:
            logger.error(f"Error in ban command: {e}")
            await update.message.reply_text("❌ An error occurred. Please try again later.")

    @staticmethod
    def _is_valid_wallet(wallet: str) -> bool:
        return (
            wallet.startswith("0x")
            and len(wallet) == 42
            and all(c in '0123456789abcdefABCDEF' for c in wallet[2:])
        )

    def run(self):
        logger.info("Starting USDT Airdrop Bot...")
        self.app.run_polling()


if __name__ == "__main__":
    bot = AirdropBot()
    bot.run()