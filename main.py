import os
import json
import datetime
import time
from typing import List, Tuple, Optional

import requests
import gspread
from google.oauth2.service_account import Credentials
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer


# ---------- Config via environment ----------

SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Active-Investing")
WORKSHEET_NAME = os.getenv("GOOGLE_WORKSHEET_NAME", "Alpaca-Screener")

# Alpaca market data credentials (same keys you use for trading)
APCA_API_KEY_ID = os.getenv("APCA_API_KEY_ID")
APCA_API_SECRET_KEY = os.getenv("APCA_API_SECRET_KEY")

# Google service account JSON (full JSON as one line)
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")

# Optional cap so a single run doesn't go forever if you have tons of tickers.
# 0 means "no limit, process all tickers".
MAX_TICKERS_PER_RUN = int(os.getenv("MAX_TICKERS_PER_RUN", "0"))

# --- Simple rate limiting for Alpaca News ---
# Default ~120 req/min (0.5s spacing). You can lower this via env var if needed.
ALPACA_NEWS_REQS_PER_MINUTE = float(os.getenv("ALPACA_NEWS_REQS_PER_MINUTE", "120"))
ALPACA_NEWS_MIN_DELAY = 60.0 / ALPACA_NEWS_REQS_PER_MINUTE

_last_news_call_time = 0.0


# ---------- Google Sheets helpers ----------

def get_gspread_client() -> gspread.Client:
    if not GOOGLE_CREDS_JSON:
        raise RuntimeError("GOOGLE_CREDS_JSON environment variable is not set.")

    service_account_info = json.loads(GOOGLE_CREDS_JSON)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
    client = gspread.authorize(creds)
    return client


# ---------- Alpaca news + sentiment ----------

analyzer = SentimentIntensityAnalyzer()


def fetch_news_texts_for_ticker(ticker: str, limit: int = 20) -> List[str]:
    """
    Fetch recent news for a ticker from Alpaca's /v1beta1/news endpoint,
    and return a list of text snippets (headline + summary) for sentiment.

    Includes simple client-side rate limiting and one retry on 429.
    """
    global _last_news_call_time

    if not (APCA_API_KEY_ID and APCA_API_SECRET_KEY):
        print("Alpaca API keys not configured; skipping news for", ticker)
        return []

    # --- simple pacing between calls ---
    now = time.time()
    elapsed = now - _last_news_call_time
    if elapsed < ALPACA_NEWS_MIN_DELAY:
        time.sleep(ALPACA_NEWS_MIN_DELAY - elapsed)

    url = "https://data.alpaca.markets/v1beta1/news"
    params = {
        "symbols": ticker,
        "limit": min(limit, 20),
    }
    headers = {
        "APCA-API-KEY-ID": APCA_API_KEY_ID,
        "APCA-API-SECRET-KEY": APCA_API_SECRET_KEY,
        "accept": "application/json",
    }

    def do_request(tag: str):
        """Inner helper to log which attempt we're on."""
        global _last_news_call_time
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            _last_news_call_time = time.time()
            return resp
        except Exception as e:
            print(f"Error during {tag} request for {ticker}: {e}")
            raise

    try:
        # First attempt
        resp = do_request("initial")

        # If rate-limited, wait and retry once
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            if retry_after is not None:
                try:
                    wait_s = int(retry_after)
                except ValueError:
                    wait_s = 10
            else:
                wait_s = 10

            print(f"Hit Alpaca news rate limit for {ticker}. Waiting {wait_s}s then retrying once...")
            time.sleep(wait_s)

            resp = do_request("retry")

            if resp.status_code == 429:
                print(f"Still rate limited for {ticker} after retry. Skipping.")
                return []

        resp.raise_for_status()
        data = resp.json()

        # Response can be list or { "news": [...] } style
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("news") or data.get("data") or []
        else:
            items = []

        texts: List[str] = []
        for article in items:
            if not isinstance(article, dict):
                continue
            headline = article.get("headline") or article.get("title") or ""
            summary = article.get("summary") or ""
            if headline:
                if summary:
                    texts.append(f"{headline}. {summary}")
                else:
                    texts.append(headline)

        return texts

    except requests.HTTPError as e:
        print(f"HTTP error fetching Alpaca news for {ticker}: {e}")
        return []
    except Exception as e:
        print(f"Error fetching Alpaca news for {ticker}: {e}")
        return []


def analyze_sentiment(texts: List[str]) -> Tuple[Optional[float], int]:
    """
    Given a list of text snippets, compute average VADER compound sentiment.
    Returns (average_compound, count). If no texts, returns (None, 0).
    """
    if not texts:
        return None, 0

    scores = []
    for t in texts:
        vs = analyzer.polarity_scores(t)
        scores.append(vs["compound"])

    if not scores:
        return None, 0

    avg_compound = sum(scores) / len(scores)
    return avg_compound, len(scores)


# ---------- Main processing ----------

def process_sheet_once():
    # Connect to Google Sheets
    client = get_gspread_client()
    sheet = client.open(SHEET_NAME)
    ws = sheet.worksheet(WORKSHEET_NAME)

    # Column A values: ["Ticker", "AAPL", "MSFT", ...]
    col_a_values = ws.col_values(1)
    if not col_a_values or len(col_a_values) <= 1:
        print("No tickers found in column A (beyond header). Nothing to do.")
        return

    # Row 1 is header; use rows 2..N
    tickers = col_a_values[1:]  # raw strings including blanks

    # Optional per-run cap
    if MAX_TICKERS_PER_RUN > 0:
        tickers = tickers[:MAX_TICKERS_PER_RUN]

    start_row = 2
    end_row = start_row + len(tickers) - 1

    print(f"Found {len(tickers)} tickers to process in column A (rows {start_row}-{end_row}).")

    # Proper UTC timestamp, timezone-aware
    now_utc = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")

    rows_q_to_s: List[List[Optional[object]]] = []

    for idx, raw_ticker in enumerate(tickers):
        row_number = start_row + idx
        ticker = (raw_ticker or "").strip().upper()

        if not ticker:
            print(f"Row {row_number}: empty ticker, skipping.")
            rows_q_to_s.append(["", "", ""])
            continue

        print(f"Row {row_number}: processing ticker {ticker}")

        texts = fetch_news_texts_for_ticker(ticker)
        avg_sentiment, count = analyze_sentiment(texts)

        if avg_sentiment is None:
            print(f"  No usable news for {ticker}. Leaving cells blank.")
            rows_q_to_s.append(["", "", ""])
            continue

        print(f"  Avg sentiment: {avg_sentiment:.4f} from {count} articles.")
        rows_q_to_s.append([avg_sentiment, count, now_utc])

    # Batch update Q–S for all processed rows
    range_q_to_s = f"Q{start_row}:S{end_row}"
    print(f"Updating range {range_q_to_s} in worksheet '{WORKSHEET_NAME}' of '{SHEET_NAME}'...")
    ws.update(range_q_to_s, rows_q_to_s, value_input_option="RAW")

    print("Done. Exiting.")


if __name__ == "__main__":
    process_sheet_once()
