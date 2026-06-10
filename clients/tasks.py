import time
import os
from datetime import datetime
from django.db import connection, transaction, IntegrityError
from celery import shared_task
from bs4 import BeautifulSoup
import undetected_chromedriver as uc
from clients.models import ExchangeRate 

@shared_task
def scrape_exchange_rate_nbc():
    """
    Scrapes the NBC website using a headless Chrome browser.
    Includes robust waits and debug logging to resolve parsing issues.
    """
    # 1. Ensure DB connection is routed correctly
    try:
        connection.set_schema_to_public()
    except Exception:
        pass

    url = "https://www.nbc.gov.kh/english/economic_research/exchange_rate.php"
    
    # 2. Configure Chrome Options for Cloud Run environment
    options = uc.ChromeOptions()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    # Set window size to ensure desktop layout is triggered
    options.add_argument('--window-size=1920,1080')

    driver = None
    try:
        print("Launching headless Chrome...")
        driver = uc.Chrome(options=options)
        
        print(f"Navigating to {url}...")
        driver.get(url)
        
        # 3. INCREASED WAIT: Give JS and network calls time to render the table
        print("Waiting 15 seconds for page to render fully...")
        time.sleep(15) 
        
        # Capture the rendered HTML
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")

        # 4. Parse logic
        table_rows = soup.find_all("tr")
        date = None
        rate = None

        for row in table_rows:
            row_text = row.get_text()
            if "Exchange Rate on :" in row_text:
                font_tag = row.find("font", color="#FF3300")
                if font_tag:
                    date_str = font_tag.get_text().strip()
                    try:
                        date = datetime.strptime(date_str, "%Y-%m-%d").date()
                    except ValueError:
                        print(f"Date parsing failed for string: {date_str}")
            
            if "Official Exchange Rate :" in row_text:
                font_tag = row.find("font", color="#FF3300")
                if font_tag:
                    rate_str = font_tag.get_text().strip().replace(",", "")
                    try:
                        rate = int(float(rate_str))
                    except (ValueError, TypeError):
                        print(f"Rate parsing failed for string: {rate_str}")

        # 5. Database Save Logic
        if date and rate:
            print(f"SUCCESS: Found Rate {rate} for Date {date}")
            try:
                with transaction.atomic():
                    obj, created = ExchangeRate.objects.update_or_create(
                        date=date,
                        defaults={'rate': rate}
                    )
                    if created:
                        print(f"Exchange rate for {date} saved to database.") 
                    else:
                        print(f"Exchange rate for {date} already exists.")
            except IntegrityError as e:
                # Handle potential sequence mismatch
                if 'id' in str(e).lower():
                    print("Primary key sequence mismatch. Resetting...")
                    with connection.cursor() as cursor:
                        cursor.execute("""
                            SELECT setval(
                                pg_get_serial_sequence('clients_exchangerate', 'id'), 
                                coalesce(max(id), 1), 
                                max(id) IS NOT null
                            ) FROM clients_exchangerate;
                        """)
                    ExchangeRate.objects.update_or_create(date=date, defaults={'rate': rate})
                    print(f"Saved successfully after sequence reset.")
                else:
                    raise e
        else:
            # DEBUG LOGGING: If parsing fails, print a snippet of the page text
            print("ERROR: Could not find date or rate in the rendered page.")
            snippet = soup.get_text()[:1000].replace('\n', ' ')
            print(f"Rendered Page Snippet: {snippet}")

    except Exception as e:
        print(f"Headless Chrome Scraper Exception: {e}")
    
    finally:
        # CRITICAL: Clean up browser to stop billing duration
        if driver:
            print("Closing Chrome browser instance...")
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
