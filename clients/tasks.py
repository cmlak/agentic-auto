import time
from datetime import datetime, timedelta
from django.db import connection, transaction, IntegrityError
from django.db.models import Avg
from celery import shared_task
from bs4 import BeautifulSoup
import undetected_chromedriver as uc
from clients.models import ExchangeRate 
from tools.agents import EconAgent

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
        print("Starting Scraper [DEBUG_PROBE_202]...")
        driver = uc.Chrome(options=options)
        
        print(f"Navigating to {url}...")
        driver.get(url)
        
        print("Waiting 15 seconds for rendering...")
        time.sleep(15)
        
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")

        # THIS IS THE MOST IMPORTANT PART:
        # We need to see what the website is actually sending back.
        page_text = soup.get_text()[:1500].replace('\n', ' ')
        print(f"PAGE TEXT SNIPPET: {page_text}")

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
            print(f"FOUND: {rate} on {date}. Saving...")
            ExchangeRate.objects.update_or_create(date=date, defaults={'rate': rate})
            
            # --- AGENTIC ORCHESTRATION: Evaluate Currency Risk ---
            thirty_days_ago = date - timedelta(days=30)
            avg_data = ExchangeRate.objects.filter(
                date__gte=thirty_days_ago, 
                date__lt=date
            ).aggregate(avg=Avg('rate'))
            
            if avg_data['avg']:
                EconAgent.evaluate_currency_risk(rate, avg_data['avg'])
        else:
            print("ERROR: Parsing failed. The snippet above explains why.")

    except Exception as e:
        print(f"CRASH: {e}")
    finally:
        if driver:
            print("Closing Chrome [DEBUG_PROBE_202].")
            driver.quit()
