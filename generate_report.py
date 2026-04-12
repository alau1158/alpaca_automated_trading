import csv
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv
import uuid

load_dotenv()

from config import SMTP_SERVER, SMTP_PORT, SMTP_USER, SMTP_PASSWORD


def parse_date(date_str):
    if not date_str or date_str.strip() == "":
        return None
    return datetime.strptime(date_str.strip(), "%Y%m%d")


def calculate_holding_days(bought_date, sold_date):
    if bought_date and sold_date:
        return (sold_date - bought_date).days
    return None


def calculate_profit_loss(quantity, bought_price_per_share, sold_price_per_share):
    try:
        qty = float(quantity)
        bought = float(bought_price_per_share)
        sold = float(sold_price_per_share)
        return (sold * qty) - (bought * qty)
    except (ValueError, TypeError):
        return None


def generate_html_report(sold_stocks):
    html = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body { font-family: Arial, sans-serif; padding: 20px; }
        h2 { color: #333; }
        table { border-collapse: collapse; width: 100%; max-width: 800px; }
        th, td { border: 1px solid #ddd; padding: 12px; text-align: left; }
        th { background-color: #4CAF50; color: white; }
        .profit { background-color: #d4edda; }
        .loss { background-color: #f8d7da; }
        .positive { color: #155724; }
        .negative { color: #721c24; }
    </style>
</head>
<body>
    <h2>Sold Stocks Report</h2>
    <table>
        <tr>
            <th>Stock</th>
            <th>Quantity</th>
            <th>Buy Price</th>
            <th>Sell Price</th>
            <th>Sold Date</th>
            <th>Holding Days</th>
            <th>Profit/Loss</th>
        </tr>
"""
    for stock in sold_stocks:
        profit_loss = stock["profit_loss"]
        row_class = "profit" if profit_loss >= 0 else "loss"
        amount_class = "positive" if profit_loss >= 0 else "negative"
        profit_str = f"${profit_loss:,.2f}" if profit_loss >= 0 else f"-${abs(profit_loss):,.2f}"
        
        html += f"""
        <tr class="{row_class}">
            <td>{stock['ticket'].upper()}</td>
            <td>{stock['quantity']}</td>
            <td>${stock['bought_price']:,.2f}</td>
            <td>${stock['sold_price']:,.2f}</td>
            <td>{stock['sold_date']}</td>
            <td>{stock['holding_days']} days</td>
            <td class="{amount_class}">{profit_str}</td>
        </tr>
"""
    
    total_profit = sum(s["profit_loss"] for s in sold_stocks)
    total_class = "positive" if total_profit >= 0 else "negative"
    total_str = f"${total_profit:,.2f}" if total_profit >= 0 else f"-${abs(total_profit):,.2f}"
    
    html += f"""
        <tr>
            <td colspan="5"><strong>Total</strong></td>
            <td class="{total_class}"><strong>{total_str}</strong></td>
        </tr>
    </table>
</body>
</html>
"""
    return html


def send_email(html_content):
    print("Sending email...")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Sold Stocks Report"
    msg["From"] = SMTP_USER
    msg["To"] = SMTP_USER
    msg["Message-ID"] = f"<{uuid.uuid4()}@swingtrading>"
    
    html_part = MIMEText(html_content, "html")
    msg.attach(html_part)
    
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
    print("Email sent!")


def main():
    csv_path = os.path.join(os.path.dirname(__file__), "journal.csv")
    sold_stocks = []
    
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cleaned_row = {k.strip(): v for k, v in row.items()}
            sold_date = cleaned_row.get("sold_date") or ""
            sold_price = cleaned_row.get("sold_price") or ""
            
            if sold_date and sold_price:
                bought_date = parse_date(cleaned_row["bought_date"])
                sold_date_parsed = parse_date(sold_date)
                quantity = float(cleaned_row["quanity"])
                bought_price = float(cleaned_row["bought_price"])
                sold_price_val = float(cleaned_row["sold_price"])
                
                stock = {
                    "ticket": cleaned_row["ticket"].strip(),
                    "quantity": quantity,
                    "bought_price": bought_price,
                    "sold_price": sold_price_val,
                    "sold_date": sold_date,
                    "holding_days": calculate_holding_days(bought_date, sold_date_parsed),
                    "profit_loss": calculate_profit_loss(quantity, bought_price, sold_price_val)
                }
                sold_stocks.append(stock)
    
    print(f"Found {len(sold_stocks)} sold stocks")
    
    sold_stocks.sort(key=lambda x: parse_date(x["sold_date"]) or datetime.min)
    
    html_report = generate_html_report(sold_stocks)
    
    if SMTP_USER and SMTP_PASSWORD:
        send_email(html_report)
        print("Report sent successfully!")
    else:
        print("SMTP credentials not found in .env")
        print(html_report)


if __name__ == "__main__":
    main()
