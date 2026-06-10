import time
from datetime import datetime
from django.db import connection, transaction, IntegrityError
from celery import shared_task
from bs4 import BeautifulSoup
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from clients.models import ExchangeRate 

@shared_task
def scrape_exchange_rate_nbc():
    """
    Scrapes the NBC website using a headless Chrome browser.
    Uses WebDriverWait to ensure data is loaded before parsing.
    """
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
        print("Launching headless Chrome...")
        driver = uc.Chrome(options=options)
        
        print(f"Navigating to {url}...")
        driver.get(url)
        
        # 1. SMART WAIT: Wait up to 20 seconds for the table containing "Exchange Rate on" to appear
        print("Waiting for exchange rate table to render...")
        wait = WebDriverWait(driver, 20)
        try:
            # We wait for any <td> that contains the specific text
            wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Exchange Rate on :')]")))
            print("Target table detected successfully.")
        except Exception:
            print("Timeout: The exchange rate table did not appear within 20 seconds.")

        # Capture the rendered HTML
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")

        # 2. Parse logic
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
                        print(f"Date parsing error: {date_str}")
            
            if "Official Exchange Rate :" in row_text:
                font_tag = row.find("font", color="#FF3300")
                if font_tag:
                    rate_str = font_tag.get_text().strip().replace(",", "")
                    try:
                        rate = int(float(rate_str))
                    except (ValueError, TypeError):
                        print(f"Rate parsing error: {rate_str}")

        # 3. Database Save
        if date and rate:
            print(f"FOUND DATA -> Date: {date}, Rate: {rate}")
            try:
                with transaction.atomic():
                    obj, created = ExchangeRate.objects.update_or_create(
                        date=date,
                        defaults={'rate': rate}
                    )
                    print(f"Status: {'Saved' if created else 'Already exists'}")
            except IntegrityError:
                print("Sequence mismatch detected. Resetting ID sequence...")
                with connection.cursor() as cursor:
                    cursor.execute(f"SELECT setval(pg_get_serial_sequence('clients_exchangerate', 'id'), coalesce(max(id), 1), max(id) IS NOT null) FROM clients_exchangerate;")
                ExchangeRate.objects.update_or_create(date=date, defaults={'rate': rate})
                print("Saved after sequence reset.")
        else:
            # DEBUG SNIPPET: Very important to see what is actually there
            print("ERROR: Parsing failed. Printing page snippet for debugging:")
            clean_text = soup.get_text()[:800].replace('\n', ' ')
            print(f"PAGE TEXT: {clean_text}")

    except Exception as e:
        print(f"Headless Scraper CRASHED: {e}")
    
    finally:
        if driver:
            print("Terminating Chrome session.")
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
