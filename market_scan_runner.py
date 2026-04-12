#!/usr/bin/env python3
"""
Daily Market Scan Report Generator
Schedulable script to scan market for new opportunities and send email report
"""

import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from swing_trading_analyzer import MarketScanner, StockAnalyzer
from config import EMAIL_RECIPIENTS, SMTP_SERVER, SMTP_PORT, SMTP_USER, SMTP_PASSWORD


def send_email_notification(report_content, subject="Daily Market Scan Report"):
    """Send report via email"""
    if not SMTP_USER or not SMTP_PASSWORD:
        print("Email not configured - SMTP_USER/SMTP_PASSWORD not set")
        return False
    
    try:
        msg = MIMEMultipart()
        msg["From"] = SMTP_USER
        msg["To"] = ", ".join(EMAIL_RECIPIENTS)
        msg["Subject"] = subject
        
        msg.attach(MIMEText(report_content, "plain"))
        
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, EMAIL_RECIPIENTS, msg.as_string())
        
        print(f"Email sent to {EMAIL_RECIPIENTS}")
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False


def generate_market_scan_report(output_file="market_scan_report.txt"):
    """Generate daily market scan report for new opportunities"""
    import io
    from io import StringIO
    
    print(f"\n{'='*80}")
    print(f"MARKET SCAN REPORT - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*80}")
    
    analyzer = StockAnalyzer(symbols=[], period="1mo", interval="30m")
    scanner = MarketScanner(analyzer)
    
    results = scanner.full_market_scan(top_n=15)
    
    old_stdout = sys.stdout
    buffer = StringIO()
    sys.stdout = buffer
    scanner.display_scan_results(results)
    sys.stdout = old_stdout
    scan_results_text = buffer.getvalue()
    
    print(scan_results_text)
    
    report_lines = []
    report_lines.append(f"MARKET SCAN REPORT - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    report_lines.append("=" * 80)
    report_lines.append("")
    report_lines.append(scan_results_text)
    
    if results['all_symbols']:
        unique_symbols = list(set(results['all_symbols']))
        print(f"\nAnalyze these stocks with full strategy? (y/n): y")
        analyzer.symbols = unique_symbols
        analyzer.analyze_all()
        
        buffer = StringIO()
        sys.stdout = buffer
        analyzer.display_summary()
        sys.stdout = old_stdout
        summary_text = buffer.getvalue()
        
        print(summary_text)
        report_lines.append(summary_text)
    
    report_content = "\n".join(report_lines)
    
    print(f"\n{'='*80}")
    print("Report complete!")
    print(f"{'='*80}")
    
    with open(output_file, "w") as f:
        f.write(report_content)
    
    return report_content


if __name__ == "__main__":
    output = "market_scan_report.txt"
    send_via_email = "--email" in sys.argv
    
    report_content = generate_market_scan_report(output)
    
    if send_via_email:
        send_email_notification(report_content)
