# Alpaca News Sentiment Screener (Google Sheets)

This script pulls recent news from Alpaca for a list of stock tickers in Google Sheets, runs VADER sentiment analysis on the headlines/summaries, and writes the results back into the sheet.

It’s designed to be run as a one-shot task (e.g., from a cron job, server, or GitHub Action) to keep a screener sheet enriched with up-to-date sentiment data.

---

## What It Does

For each ticker in your sheet:

1. Fetches recent news from Alpaca’s `/v1beta1/news` endpoint.
2. Builds short text snippets from headline + summary.
3. Uses **VADER** (from `vaderSentiment`) to compute an average **compound sentiment score**.
4. Writes the following to your sheet (for each ticker row):

   * **Column Q** – Average sentiment (float from -1.0 to +1.0)
   * **Column R** – Number of news articles used
   * **Column S** – UTC timestamp of when sentiment was calculated

If there’s no usable news for a ticker, the script leaves those cells blank.

---

## Google Sheets Layout

The script expects:

* A spreadsheet named by `GOOGLE_SHEET_NAME` (default: `Active-Investing`)
* A worksheet/tab named by `GOOGLE_WORKSHEET_NAME` (default: `Alpaca-Screener`)
* **Column A**:

  * `A1` – header (e.g., `"Ticker"`)
  * `A2` and below – ticker symbols (e.g., `AAPL`, `MSFT`, etc.)

The script loops over all non-header rows in column A (or up to `MAX_TICKERS_PER_RUN`, if set), and writes sentiment output to **columns Q–S** for the same rows.

---

## Environment Variables

The script is configured entirely via environment variables:

| Variable                      | Required | Default            | Description                                                        |
| ----------------------------- | -------- | ------------------ | ------------------------------------------------------------------ |
| `GOOGLE_SHEET_NAME`           | No       | `Active-Investing` | Name of the Google Sheet.                                          |
| `GOOGLE_WORKSHEET_NAME`       | No       | `Alpaca-Screener`  | Name of the worksheet/tab.                                         |
| `APCA_API_KEY_ID`             | Yes      | —                  | Alpaca API key ID (market data access).                            |
| `APCA_API_SECRET_KEY`         | Yes      | —                  | Alpaca API secret key.                                             |
| `GOOGLE_CREDS_JSON`           | Yes      | —                  | Full Google service account JSON, **as a single line string**.     |
| `MAX_TICKERS_PER_RUN`         | No       | `0`                | Max number of tickers to process per run. `0` = no limit.          |
| `ALPACA_NEWS_REQS_PER_MINUTE` | No       | `120`              | Client-side rate limit for news requests. Used to space API calls. |

### About `GOOGLE_CREDS_JSON`

`GOOGLE_CREDS_JSON` should contain the **entire** JSON for your Google service account (the same content you’d normally place in a `credentials.json` file), but with no line breaks.

Example (shortened for illustration):

```bash
GOOGLE_CREDS_JSON='{
  "type": "service_account",
  "project_id": "...",
  "private_key_id": "...",
  "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n",
  "client_email": "...@....iam.gserviceaccount.com",
  ...
}'
```

In `.env` form you typically escape or keep it as a single line depending on your environment/process manager.

---

## Prerequisites

* Python 3.9+ (recommended)
* Alpaca account with access to the **Market Data API**
* A Google Cloud service account with:

  * **Google Sheets API** enabled
  * **Google Drive API** enabled
  * The service account email shared with your target Google Sheet (Editor access)

---

## Installation

1. **Clone this repo** (or copy the script into your project).

2. **Install dependencies**:

```bash
pip install requests gspread google-auth google-auth-oauthlib vaderSentiment
```

> Depending on your environment, the exact Google auth package name might be `google-auth` and `google-auth-oauthlib`. The script uses `google.oauth2.service_account.Credentials`.

3. **Set environment variables** (example using a `.env` file + `dotenv` or environment config in your hosting platform):

```bash
export GOOGLE_SHEET_NAME="Active-Investing"
export GOOGLE_WORKSHEET_NAME="Alpaca-Screener"
export APCA_API_KEY_ID="your_alpaca_key_id"
export APCA_API_SECRET_KEY="your_alpaca_secret_key"
export GOOGLE_CREDS_JSON='{"type":"service_account","project_id":"...","private_key_id":"...","private_key":"-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n","client_email":"...","client_id":"...","auth_uri":"...","token_uri":"...","auth_provider_x509_cert_url":"...","client_x509_cert_url":"..."}'
export MAX_TICKERS_PER_RUN="0"
export ALPACA_NEWS_REQS_PER_MINUTE="120"
```

---

## Usage

Run the script as a simple one-off process:

```bash
python sentiment_screener.py
```

* It will:

  * Connect to your sheet.
  * Read tickers from **column A**.
  * Fetch news & compute sentiment.
  * Batch-update **Q–S** for all processed tickers.
  * Then exit.

### Example Output (Logs)

You’ll see logs similar to:

```
Found 5 tickers to process in column A (rows 2-6).
Row 2: processing ticker AAPL
  Avg sentiment: 0.4213 from 12 articles.
Row 3: processing ticker MSFT
  Avg sentiment: 0.1058 from 9 articles.
...
Updating range Q2:S6 in worksheet 'Alpaca-Screener' of 'Active-Investing'...
Done. Exiting.
```

---

## Rate Limiting & Error Handling

* **Rate limiting**:

  * Uses a simple global timer (`_last_news_call_time`) and `ALPACA_NEWS_REQS_PER_MINUTE` to space out news calls.
  * On HTTP 429 from Alpaca, the script:

    * Reads `Retry-After` header if present.
    * Waits that many seconds (or 10s fallback).
    * Retries **once** for that ticker.

* **Graceful failures**:

  * If Alpaca keys are missing, the script logs that news is skipped for that ticker and returns no sentiment.
  * If Google credentials are missing, it raises a clear runtime error.
  * HTTP or network errors on a ticker’s news fetch are logged, and that ticker is skipped for that run.

---

## How Sentiment Is Calculated

The script uses **VADER** sentiment analysis via:

```python
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
```

For each text snippet (`headline[. summary]`):

* `analyzer.polarity_scores(text)["compound"]` returns a score in `[-1.0, 1.0]`.
* The script averages all available `compound` scores for that ticker to get the final sentiment.

You can interpret the average roughly as:

* **> 0.05** – generally positive news
* **< -0.05** – generally negative news
* Otherwise – neutral / mixed

(Thresholds are subjective; adjust to your strategy.)

---

## Customization Ideas

* Change where data is written (e.g., different columns or additional fields).
* Log raw article headlines in a separate tab for audit / deeper inspection.
* Adjust `limit` in `fetch_news_texts_for_ticker` (currently capped at 20).
* Integrate the sentiment scores into a broader screener or trading bot.

---

## License

Add your preferred license here (e.g., MIT, Apache 2.0, etc.).
