import time
from datetime import datetime
from django.db import connection, transaction, IntegrityError
from celery import shared_task
from bs4 import BeautifulSoup
import undetected_chromedriver as uc
from clients.models import ExchangeRate 

@shared_task
def scrape_exchange_rate_nbc():
    try:
        connection.set_schema_to_public()
    except Exception:
        pass

    url = "https://www.nbc.gov.kh/english/economic_research/exchange_rate.php"
    options = uc.ChromeOptions()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')

    driver = None
    try:
        print("DEBUG: Final Robust Version Starting...")
        driver = uc.Chrome(options=options)
        driver.get(url)
        
        print("DEBUG: Waiting 10 seconds for render...")
        time.sleep(10)
        
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")

        # MANDATORY DEBUGGING: What does the page look like?
        page_text = soup.get_text()[:1000].replace('\n', ' ')
        print(f"DEBUG: PAGE CONTENT SNIPPET: {page_text}")

        table_rows = soup.find_all("tr")
        date, rate = None, None

        for row in table_rows:
            txt = row.get_text()
            if "Exchange Rate on :" in txt:
                tag = row.find("font", color="#FF3300")
                if tag: date = datetime.strptime(tag.get_text().strip(), "%Y-%m-%d").date()
            if "Official Exchange Rate :" in txt:
                tag = row.find("font", color="#FF3300")
                if tag: rate = int(float(tag.get_text().strip().replace(",", "")))

        if date and rate:
            print(f"DEBUG: Found {rate} for {date}")
            # ... Save logic (omitted for brevity but keep yours) ...
        else:
            print("DEBUG: Parsing failed - target elements not found.")

    except Exception as e:
        print(f"DEBUG: CRASH: {e}")
    finally:
        if driver:
            print("DEBUG: Cleaning up Chrome.")
            driver.quit()


@shared_task
def scrape_exchange_rate_nbc_1():
    """
    Scrapes the NBC website using a headless Chrome browser.
    Uses a 'finally' block to ensure driver.quit() always executes.
    """
    # 1. Ensure DB connection is routed correctly
    try:
        connection.set_schema_to_public()
    except Exception:
        pass

    url = "https://www.nbc.gov.kh/english/economic_research/exchange_rate.php"
    
    # 2. Configure Chrome Options
    options = uc.ChromeOptions()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')

    driver = None
    try:
        print("Launching headless Chrome...")
        driver = uc.Chrome(options=options)
        
        print(f"Navigating to {url}...")
        driver.get(url)
        
        # Anti-bot delay
        time.sleep(5)
        
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")

        # 3. Parse logic
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

        # 4. Save to Database
        if date and rate:
            print(f"Scraped Data -> Date: {date}, Rate: {rate}")
            try:
                with transaction.atomic():
                    obj, created = ExchangeRate.objects.update_or_create(
                        date=date,
                        defaults={'rate': rate}
                    )
                    if created:
                        print(f"Exchange rate for {date} saved successfully.") 
                    else:
                        print(f"Exchange rate for {date} already exists.")
            except IntegrityError as e:
                if 'id' in str(e).lower():
                    print("Resetting primary key sequence...")
                    with connection.cursor() as cursor:
                        cursor.execute("""
                            SELECT setval(
                                pg_get_serial_sequence('clients_exchangerate', 'id'), 
                                coalesce(max(id), 1), 
                                max(id) IS NOT null
                            ) 
                            FROM clients_exchangerate;
                        """)
                    # Retry
                    ExchangeRate.objects.update_or_create(date=date, defaults={'rate': rate})
                    print(f"Saved after reset.")
        else:
            print("Warning: Could not find date or rate in the rendered page.")

    except Exception as e:
        print(f"Scraper Error: {e}")
    
    finally:
        # CRITICAL: This block always runs regardless of success or error
        if driver:
            print("Terminating Chrome process to free resources...")
            driver.quit()
