import requests
from bs4 import BeautifulSoup
from datetime import datetime
from django.db import connection
from celery import shared_task
from clients.models import ExchangeRate # Ensure correct import path

@shared_task
def scrape_exchange_rate_nbc():
    """
    Scrapes the National Bank of Cambodia website for the exchange rate
    and saves it with a new ID (latest ID + 1) only if the date doesn't exist.
    """
    try:
        # Explicitly route the connection to the public schema
        connection.set_schema_to_public()
    except Exception:
        pass

    url = "https://www.nbc.gov.kh/english/economic_research/exchange_rate.php"
    
    # ADDED: Browser-like headers to bypass 403 Forbidden blocks
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
    }

    try:
        # ADDED: headers and a 30-second timeout
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")

        # Find all <tr> elements within the table
        table_rows = soup.find_all("tr")

        date = None  # Initialize date and rate
        rate = None

        # Extract date and rate using a loop and conditions
        for row in table_rows:
            if "Exchange Rate on :" in row.text:
                font_tag = row.find("font", color="#FF3300")
                if font_tag:
                    date_str = font_tag.text.strip()
                    date = datetime.strptime(date_str, "%Y-%m-%d").date()
            if "Official Exchange Rate :" in row.text:
                font_tag = row.find("font", color="#FF3300")
                if font_tag:
                    rate_str = font_tag.text.strip().replace(",", "")
                    rate = int(float(rate_str))

        # Check if an exchange rate with the same date already exists
        if date and rate:
            if not ExchangeRate.objects.filter(date=date).exists():
                ExchangeRate.objects.create(date=date, rate=rate)
                print(f"Exchange rate for {date} saved.") 
            else:
                print(f"Exchange rate for {date} already exists in database.")
        else:
            print("Could not find date or rate in the page content.")

    except requests.exceptions.RequestException as e:
        print(f"Error fetching exchange rate: {e}")
    except (ValueError, AttributeError, IndexError) as e:
        print(f"Error parsing exchange rate: {e}")
    except Exception as e: 
        print(f"An unexpected error occurred: {e}")
