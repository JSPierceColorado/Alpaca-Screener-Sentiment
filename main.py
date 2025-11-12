import os
import json
import datetime
from typing import List, Tuple, Optional

import requests
import gspread
from google.oauth2.service_account import Credentials
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer


# ---------- Config via environment ----------

# Name of the sheet and worksheet; defaults to what you asked for
SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Active-Investing")
WORKSHEET_NAME = os.getenv("GOOGLE_WORKSHEET_NAME", "Alpaca-Screener")

# Polygon / Massive API key (they rebranded but key still works)
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY") or os.getenv("MASSIVE_API_KEY")

# Google creds JSON (same pattern as your other bot)
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")


# ---------- Helpers ----------

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


def fetch_news_headlines_for_ticker(ticker: str, limit: int = 20) -> List[str]:
    """
    Fetch recent news headlines for a ticker using Polygon/Massive News v2 API.

    Returns a list of strings (titles). If there's an error or no results,
    returns an empty list.
    """
    if not POLYGON_API_KEY:
        print("POLYGON_API_KEY / MASSIVE_API_KEY not configured; skipping news for", ticker)
        return []

    url = "https://api.polygon.io/v2/reference/news"
    params = {
        "ticker": ticker,
        "limit": limit,
        "apiKey": POLYGON_API_KEY,
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", []) or []
        headlines = []

        for article in results:
            title = article.get("title")
            if title:
                headlines.append(title)

        return headlines
    except Exception as e:
        print(f"Error fetching news for {ticker}: {e}")
        return []


analyzer = SentimentIntensityAnalyzer()


def analyze_sentiment(headlines: List[str]) -> Tuple[Optional[float], int]:
    """
    Given a list of headlines, compute average VADER compound sentiment.
    Returns (average_compound, article_count). If no headlines, returns (None, 0).
    """
    if not headlines:
        return None, 0

    scores = []
    for h in headlines:
        vs = analyzer.polarity_scores(h)
        scores.append(vs["compound"])

    if not scores:
        return None, 0

    avg_compound = sum(scores) / len(scores)
    return avg_compound, len(scores)


def process_sheet_once():
    # Connect to Google Sheet
    client = get_gspread_client()
    sheet = client.open(SHEET_NAME)
    ws = sheet.worksheet(WORKSHEET_NAME)

    # Read all tickers from column A
    # col_values(1) returns a list like ["Ticker", "AAPL", "MSFT", ...]
    col_a_values = ws.col_values(1)
    if not col_a_values or len(col_a_values) <= 1:
        print("No tickers found in column A (beyond header). Nothing to do.")
        return

    # Row 1 = header; start at row 2
    tickers = col_a_values[1:]  # skip header
    start_row = 2
    end_row = start_row + len(tickers) - 1

    print(f"Found {len(tickers)} tickers in column A (rows {start_row}-{end_row}).")

    # Prepare rows for Q–S (columns 17–19)
    # Q: avg sentiment (float)
    # R: number of articles used
    # S: timestamp UTC of calculation
    now_utc = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    rows_q_to_s = []

    for idx, raw_ticker in enumerate(tickers):
        row_number = start_row + idx
        ticker = (raw_ticker or "").strip().upper()

        if not ticker:
            print(f"Row {row_number}: empty ticker, skipping.")
            rows_q_to_s.append(["", "", ""])  # keep alignment
            continue

        print(f"Row {row_number}: processing ticker {ticker}")

        headlines = fetch_news_headlines_for_ticker(ticker)
        avg_sentiment, article_count = analyze_sentiment(headlines)

        if avg_sentiment is None:
            print(f"  No news headlines found for {ticker}.")
            rows_q_to_s.append(["", "", ""])
            continue

        print(f"  Avg sentiment: {avg_sentiment:.4f} based on {article_count} articles.")

        rows_q_to_s.append([avg_sentiment, article_count, now_utc])

    # Batch update Q–S for all rows at once
    # Column Q is 17, so Q2:S{end_row}
    range_q_to_s = f"Q{start_row}:S{end_row}"
    print(f"Updating range {range_q_to_s}...")
    ws.update(range_q_to_s, rows_q_to_s, value_input_option="RAW")

    print("Done. Exiting.")


if __name__ == "__main__":
    process_sheet_once()
