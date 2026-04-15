#!/usr/bin/env python3
"""
Daily Performance Report Generator
Sends email at market close with:
- Today's P&L
- Current positions with values
- Month-to-date totals
Can be scheduled to run at 4 PM ET daily.
"""

import os
import sys
import csv
import smtplib
import signal
from datetime import datetime, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    ALPACA_ENDPOINT, ALPACA_KEY, ALPACA_SECRET,
    SMTP_SERVER, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_RECIPIENTS,
    DRY_RUN_MODE, MARKET_TZ
)


JOURNAL_PATH = "/home/alau/autotrading/journal.csv"


def timeout_handler(signum, frame):
    print("Timeout reached")
    sys.exit(1)


def send_email(subject, body):
    if not SMTP_USER or not SMTP_PASSWORD:
        print("Email not configured")
        return False
    
    try:
        msg = MIMEMultipart()
        msg["From"] = SMTP_USER
        msg["To"] = ", ".join(EMAIL_RECIPIENTS)
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, EMAIL_RECIPIENTS, msg.as_string())
        
        print(f"Email sent: {subject}")
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False


def get_alpaca_positions():
    if DRY_RUN_MODE:
        return []
    
    try:
        headers = {
            "APCA-API-KEY-ID": ALPACA_KEY,
            "APCA-API-SECRET-KEY": ALPACA_SECRET
        }
        resp = requests.get(f"{ALPACA_ENDPOINT}/positions", headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json() or []
    except Exception as e:
        print(f"Failed to get positions: {e}")
        return []


def get_alpaca_account():
    if DRY_RUN_MODE:
        return {"cash": 0, "portfolio_value": 0}
    
    try:
        headers = {
            "APCA-API-KEY-ID": ALPACA_KEY,
            "APCA-API-SECRET-KEY": ALPACA_SECRET
        }
        resp = requests.get(f"{ALPACA_ENDPOINT}/account", headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Failed to get account: {e}")
        return {"cash": 0, "portfolio_value": 0}


def read_journal():
    trades = []
    try:
        with open(JOURNAL_PATH, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                row = {k.strip(): v.strip() for k, v in row.items()}
                trades.append(row)
    except FileNotFoundError:
        pass
    return trades


def calculate_today_pnl(trades):
    today = date.today()
    today_str = today.strftime("%Y%m%d")
    
    today_realized = 0
    for trade in trades:
        sold_date = trade.get("sold_date", "").strip()
        if sold_date == today_str:
            bought_price = float(trade.get("bought_price", 0) or 0)
            sold_price = float(trade.get("sold_price", 0) or 0)
            qty = int(trade.get("quanity", 0) or 0)
            if bought_price and sold_price and qty:
                pnl = (sold_price - bought_price) * qty
                today_realized += pnl
    
    return today_realized


def calculate_month_to_date_pnl(trades):
    today = date.today()
    month_start = today.replace(day=1)
    month_start_str = month_start.strftime("%Y%m%d")
    today_str = today.strftime("%Y%m%d")
    
    mtd_realized = 0
    
    for trade in trades:
        sold_date = trade.get("sold_date", "").strip()
        if sold_date and sold_date >= month_start_str and sold_date <= today_str:
            bought_price = float(trade.get("bought_price", 0) or 0)
            sold_price = float(trade.get("sold_price", 0) or 0)
            qty = int(trade.get("quanity", 0) or 0)
            if bought_price and sold_price and qty:
                pnl = (sold_price - bought_price) * qty
                mtd_realized += pnl
    
    return mtd_realized


def get_current_positions_report():
    positions = get_alpaca_positions()
    account = get_alpaca_account()
    
    lines = []
    total_value = 0
    total_cost = 0
    total_unrealized = 0
    
    if not positions:
        lines.append("  No open positions")
    else:
        for pos in positions:
            symbol = pos.get("symbol", "")
            qty = int(float(pos.get("qty", 0)))
            current_price = float(pos.get("current_price", 0))
            market_value = float(pos.get("market_value", 0))
            cost_basis = float(pos.get("cost_basis", 0))
            unrealized = float(pos.get("unrealized_pl", 0))
            
            if qty and current_price:
                pnl_pct = (unrealized / cost_basis * 100) if cost_basis > 0 else 0
                pnl_dir = "+" if unrealized > 0 else ""
                
                lines.append(
                    f"  {symbol}: {qty} shares @ ${current_price:.2f} "
                    f"(Value: ${market_value:.2f}, P&L: {pnl_dir}{pnl_pct:.2f}%)"
                )
                
                total_value += market_value
                total_cost += cost_basis
                total_unrealized += unrealized
    
    portfolio_value = float(account.get("portfolio_value", 0))
    cash = float(account.get("cash", 0))
    
    return lines, total_value, total_cost, total_unrealized, portfolio_value, cash


def generate_daily_report():
    today = datetime.now().strftime("%Y-%m-%d")
    month = datetime.now().strftime("%B %Y")
    
    trades = read_journal()
    
    today_pnl = calculate_today_pnl(trades)
    mtd_pnl = calculate_month_to_date_pnl(trades)
    
    pos_lines, total_value, total_cost, unrealized, portfolio_value, cash = get_current_positions_report()
    
    lines = []
    lines.append("=" * 60)
    lines.append("DAILY PERFORMANCE REPORT")
    lines.append(f"Generated: {today}")
    lines.append("=" * 60)
    lines.append("")
    
    lines.append("CURRENT POSITIONS")
    lines.append("-" * 30)
    if pos_lines:
        lines.extend(pos_lines)
    else:
        lines.append("  No open positions")
    lines.append("")
    
    lines.append(f"Total Position Value: ${total_value:,.2f}")
    lines.append(f"Available Cash:       ${cash:,.2f}")
    lines.append(f"Total Portfolio:     ${portfolio_value:,.2f}")
    lines.append(f"Unrealized P&L:       ${unrealized:+,.2f}")
    lines.append("")
    
    lines.append("=" * 60)
    lines.append("PERFORMANCE SUMMARY")
    lines.append("=" * 60)
    lines.append("")
    
    today_total = today_pnl + unrealized
    today_dir = "+" if today_total > 0 else ""
    lines.append(f"Today's P&L (Realized + Unrealized): {today_dir}${abs(today_total):,.2f}")
    lines.append(f"  - Realized today:                   ${today_pnl:+,.2f}")
    lines.append(f"  - Unrealized (open positions):      ${unrealized:+,.2f}")
    lines.append("")
    
    total_mtd = mtd_pnl + unrealized
    mtd_dir = "+" if total_mtd > 0 else ""
    lines.append(f"Month-to-Date ({month}):")
    lines.append(f"  - Realized MTD:                     ${mtd_pnl:+,.2f}")
    lines.append(f"  - Unrealized (current):            ${unrealized:+,.2f}")
    lines.append(f"  - Grand Total MTD:                  ${mtd_dir}${abs(total_mtd):,.2f}")
    lines.append("")
    
    lines.append("=" * 60)
    
    report = "\n".join(lines)
    print(report)
    
    return report, today_total, total_mtd, portfolio_value


def send_daily_report():
    report, today_pnl, mtd_pnl, portfolio_value = generate_daily_report()
    
    today = datetime.now().strftime("%Y-%m-%d")
    today_dir = "+" if today_pnl > 0 else ""
    mtd_dir = "+" if mtd_pnl > 0 else ""
    
    subject = f"[DAILY] P&L Report - {today} | Today: {today_dir}${abs(today_pnl):,.2f} | MTD: {mtd_dir}${abs(mtd_pnl):,.2f}"
    
    send_email(subject, report)


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(60)
    
    from config import MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE
    
    print("\n" + "=" * 60)
    print("DAILY PERFORMANCE REPORT")
    print("=" * 60)
    print(f"\nConfiguration:")
    print(f"  Report Time: {MARKET_CLOSE_HOUR:02d}:{MARKET_CLOSE_MINUTE:02d} ET (market close)")
    print(f"  Email To: {EMAIL_RECIPIENTS}")
    print(f"  Dry Run Mode: {DRY_RUN_MODE}")
    print(f"\nUsage: Run via cron at 4 PM ET daily:")
    print(f"  0 16 * * 1-5 /home/alau/autotrading/venv/bin/python3 /home/alau/autotrading/daily_report.py")
    print("\n" + "=" * 60 + "\n")
    
    send_daily_report()