import requests
from bs4 import BeautifulSoup
from django.utils import timezone
from datetime import timedelta, datetime
from decimal import Decimal
from django.db import transaction
from django.db import connection
from celery import shared_task
from django.http import HttpResponse
from django.shortcuts import render
from .models import ExchangeRate

###
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
    try:
        response = requests.get(url)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")

        # Find all <tr> elements within the table
        table_rows = soup.find_all("tr")

        date = None  # Initialize date and rate
        rate = None

        # Extract date and rate using a loop and conditions
        for row in table_rows:
            if "Exchange Rate on :" in row.text:
                date_str = row.find("font", color="#FF3300").text.strip()
                date = datetime.strptime(date_str, "%Y-%m-%d").date()
            if "Official Exchange Rate :" in row.text:
                rate_str = row.find("font", color="#FF3300").text.strip().replace(",", "")
                rate = int(float(rate_str))

        # Check if an exchange rate with the same date already exists
        if date and rate and not ExchangeRate.objects.filter(date=date).exists():
            ExchangeRate.objects.create(date=date, rate=rate)
            print(f"Exchange rate for {date} saved.") #Optional: print for debugging
        else:
            print("No new exchange rate found or already exists.") #Optional: print for debugging

    except requests.exceptions.RequestException as e:
        print(f"Error fetching exchange rate: {e}")
    except (ValueError, AttributeError, IndexError) as e:
        print(f"Error parsing exchange rate: {e}")
    except Exception as e: # Catch any other unexpected errors
        print(f"An unexpected error occurred: {e}")
