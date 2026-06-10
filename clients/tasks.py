import cloudscraper # Use cloudscraper instead of direct requests
from bs4 import BeautifulSoup
from datetime import datetime
from django.db import connection, transaction, IntegrityError
from celery import shared_task
from clients.models import ExchangeRate 

@shared_task
def scrape_exchange_rate_nbc():
    """
    Scrapes the National Bank of Cambodia website using cloudscraper
    to bypass 403 Forbidden blocks.
    """
    try:
        connection.set_schema_to_public()
    except Exception:
        pass

    url = "https://www.nbc.gov.kh/english/economic_research/exchange_rate.php"
    
    # Initialize the cloudscraper to bypass anti-bot challenges
    scraper = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True
        }
    )

    # Expanded headers to mimic a real user session
    headers = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Referer': 'https://www.nbc.gov.kh/',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }

    try:
        # Using scraper.get instead of requests.get
        response = scraper.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")
        table_rows = soup.find_all("tr")

        date = None
        rate = None

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

        if date and rate:
            try:
                # Wrap in atomic so Postgres doesn't abort the entire connection on an IntegrityError
                with transaction.atomic():
                    obj, created = ExchangeRate.objects.update_or_create(
                        date=date,
                        defaults={'rate': rate}
                    )
                    if created:
                        print(f"Exchange rate for {date} saved.") 
                    else:
                        print(f"Exchange rate for {date} already exists in database.")
            except IntegrityError as e:
                # If the auto-increment sequence is out of sync, catch the duplicate PK IntegrityError
                if 'clients_exchangerate_pkey' in str(e) or 'id' in str(e):
                    print("Primary key sequence out of sync detected. Resetting sequence automatically...")
                    with connection.cursor() as cursor:
                        cursor.execute("""
                            SELECT setval(
                                pg_get_serial_sequence('clients_exchangerate', 'id'), 
                                coalesce(max(id), 1), 
                                max(id) IS NOT null
                            ) 
                            FROM clients_exchangerate;
                        """)
                    # Retry creation after fixing the sequence
                    obj, created = ExchangeRate.objects.update_or_create(
                        date=date,
                        defaults={'rate': rate}
                    )
                    print(f"Exchange rate for {date} saved after sequence reset.")
                else:
                    raise e
        else:
            print("Could not find date or rate in the page content.")

    except Exception as e:
        print(f"Error fetching/parsing exchange rate: {e}")
