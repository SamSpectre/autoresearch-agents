"""
AutoAgent: SEC Filing Fetcher
=============================
Downloads 10-K filing text and extracts XBRL financial data for ground truth.
Uses edgartools (free, no API key, MIT license, direct EDGAR access).

This is run ONCE to build the data/ directory. It is fixed infrastructure.
The optimizer never touches this file.

Usage:
    uv run scripts/fetch_filings.py
    uv run scripts/fetch_filings.py --ticker AAPL   # single company
"""

import json
import re
import argparse
from pathlib import Path

from edgar import Company, set_identity


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# SEC requires a User-Agent with your identity (name + email)
# This is a legal requirement for EDGAR access
SEC_IDENTITY = "SamuelSehgal nawabsingh2512@gmail.com"

# Project paths (relative to project root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
FILINGS_DIR = DATA_DIR / "filings"
GROUND_TRUTH_DIR = DATA_DIR / "ground_truth"

# Companies to fetch (ticker -> reason for inclusion)
COMPANIES = {
    "AAPL": "Clean financials, well-structured 10-K",
    "MSFT": "Complex segments, cloud revenue",
    "TSLA": "Unusual structure, automotive + energy",
    "JPM":  "Bank-specific metrics, complex",
    "JNJ":  "Healthcare, spin-off complexity",
    "AMZN": "Multi-segment, AWS vs retail",
    "XOM":  "Commodity-dependent metrics",
    "WMT":  "Thin margins, massive revenue",
    "NVDA": "Explosive growth patterns",
    "META": "Ad revenue, Reality Labs losses",
    "PFE":  "Pharma, post-COVID revenue cliff",
    "BAC":  "Bank-specific, interest rate exposure",
    "UNH":  "Insurance metrics, Optum",
    "HD":   "Same-store sales, housing cycle",
    "CAT":  "Cyclical, backlog metrics",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe_float(value) -> float | None:
    """Convert a value to float, returning None if not possible."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def extract_filing_text(ticker: str) -> str | None:
    """
    Download the latest 10-K filing text for a company.
    Returns clean text suitable for LLM analysis.
    """
    try:
        company = Company(ticker)
        filings = company.get_filings(form="10-K")

        if not filings or len(filings) == 0:
            print(f"  [WARN] No 10-K filings found for {ticker}")
            return None

        filing = filings[0]  # Most recent 10-K
        print(f"  Filing date: {filing.filing_date}, Period: {filing.period_of_report}")

        # Get clean text (strips HTML, returns readable text)
        text = filing.text()

        if not text or len(text) < 1000:
            print(f"  [WARN] Filing text too short for {ticker} ({len(text) if text else 0} chars)")
            return None

        # Smart truncation: keep beginning (risk factors, business description)
        # AND end (financial statements in Item 8). Financial tables are always
        # in the latter half of a 10-K. Taking only the first N chars misses them.
        max_chars = 150_000
        if len(text) > max_chars:
            original_len = len(text)
            # Keep first 50K (narrative) + last 100K (financial statements)
            first_chunk = 50_000
            last_chunk = max_chars - first_chunk  # 100K
            text = (
                text[:first_chunk]
                + "\n\n[...FILING TEXT TRUNCATED...]\n\n"
                + text[-last_chunk:]
            )
            print(f"  Truncated to ~{max_chars} chars (first {first_chunk//1000}K + last {last_chunk//1000}K, original: {original_len} chars)")

        return text

    except Exception as e:
        print(f"  [ERROR] Failed to extract text for {ticker}: {e}")
        return None


def _find_date_columns(df) -> list[str]:
    """
    Find columns that are date-based (e.g., '2025-09-27', '2024-09-28').
    These contain the actual financial values in edgartools dataframes.
    """
    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    return [c for c in df.columns if date_pattern.match(str(c))]


def _get_value(row, date_cols: list[str]) -> float | None:
    """Get the most recent period value (first date column)."""
    if not date_cols:
        return None
    return safe_float(row.get(date_cols[0]))


def _is_top_level(row) -> bool:
    """Check if a row is a top-level aggregate (not a dimensional breakdown).
    In edgartools: dimension=False means top-level, dimension=True means segment/breakdown.
    """
    if row.get("dimension") is True:
        return False
    return True


def extract_ground_truth(ticker: str) -> dict | None:
    """
    Extract verified financial data from XBRL for ground truth.
    This is what we score the agent's extraction against.

    Uses the 'standard_concept' column for reliable matching (standardized
    XBRL names like 'Revenue', 'NetIncomeLoss') and falls back to label
    matching when standard_concept is not available.
    """
    try:
        company = Company(ticker)
        filings = company.get_filings(form="10-K")

        if not filings or len(filings) == 0:
            return None

        filing = filings[0]
        tenk = filing.obj()

        ground_truth = {
            "ticker": ticker,
            "company_name": str(company.name),
            "filing_date": str(filing.filing_date),
            "period_of_report": str(filing.period_of_report),
            "financials": {},
            "segments": [],
            "metadata": {
                "source": "SEC EDGAR XBRL via edgartools",
                "accession_number": str(filing.accession_no),
            },
        }

        if not (tenk and hasattr(tenk, "financials") and tenk.financials):
            print(f"  [WARN] No financials object for {ticker}")
            return ground_truth

        # --- Income Statement ---
        try:
            income = tenk.financials.income_statement()
            if income:
                df = income.to_dataframe()
                date_cols = _find_date_columns(df)
                print(f"  Income statement: {len(df)} rows, periods: {date_cols}")

                # Mapping: standard_concept -> our field name
                concept_map = {
                    "Revenue": "total_revenue",
                    "Revenues": "total_revenue",
                    "CostOfGoodsAndServicesSold": "cost_of_revenue",
                    "CostOfRevenue": "cost_of_revenue",
                    "GrossProfit": "gross_profit",
                    "OperatingIncomeLoss": "operating_income",
                    "NetIncomeLoss": "net_income",
                    "EarningsPerShareDiluted": "eps_diluted",
                }

                # Label fallback mapping (lowercase)
                label_map = {
                    "net sales": "total_revenue",
                    "total net revenues": "total_revenue",
                    "total revenues": "total_revenue",
                    "revenue": "total_revenue",
                    "cost of sales": "cost_of_revenue",
                    "cost of revenue": "cost_of_revenue",
                    "gross profit": "gross_profit",
                    "gross margin": "gross_profit",
                    "operating income": "operating_income",
                    "income from operations": "operating_income",
                    "net income": "net_income",
                    "net income (loss)": "net_income",
                    "diluted": "eps_diluted",
                    "diluted (in dollars per share)": "eps_diluted",
                }

                for _, row in df.iterrows():
                    if not _is_top_level(row):
                        continue

                    val = _get_value(row, date_cols)
                    if val is None:
                        continue

                    # Try standard_concept first
                    std = str(row.get("standard_concept", ""))
                    field = concept_map.get(std)

                    # Fall back to label matching
                    if not field:
                        label = str(row.get("label", "")).strip().lower()
                        field = label_map.get(label)

                    if field and field not in ground_truth["financials"]:
                        ground_truth["financials"][field] = val

        except Exception as e:
            print(f"  [WARN] Income statement extraction failed for {ticker}: {e}")

        # --- Balance Sheet ---
        try:
            balance = tenk.financials.balance_sheet()
            if balance:
                df = balance.to_dataframe()
                date_cols = _find_date_columns(df)
                print(f"  Balance sheet: {len(df)} rows, periods: {date_cols}")

                concept_map_bs = {
                    "Assets": "total_assets",
                    "Liabilities": "total_liabilities",
                    "LongTermDebt": "long_term_debt",
                    "LongTermDebtNoncurrent": "long_term_debt",
                    "StockholdersEquity": "stockholders_equity",
                    "CashAndMarketableSecurities": "cash_and_equivalents",
                    "CashAndCashEquivalentsAtCarryingValue": "cash_and_equivalents",
                }

                label_map_bs = {
                    "total assets": "total_assets",
                    "total liabilities": "total_liabilities",
                    "total stockholders' equity": "stockholders_equity",
                    "total shareholders' equity": "stockholders_equity",
                    "long-term debt": "long_term_debt",
                    "term debt": "long_term_debt",
                    "cash and cash equivalents": "cash_and_equivalents",
                }

                for _, row in df.iterrows():
                    if not _is_top_level(row):
                        continue

                    val = _get_value(row, date_cols)
                    if val is None:
                        continue

                    std = str(row.get("standard_concept", ""))
                    field = concept_map_bs.get(std)

                    if not field:
                        label = str(row.get("label", "")).strip().lower()
                        field = label_map_bs.get(label)

                    if field and field not in ground_truth["financials"]:
                        ground_truth["financials"][field] = val

        except Exception as e:
            print(f"  [WARN] Balance sheet extraction failed for {ticker}: {e}")

        # --- Compute Margins ---
        rev = ground_truth["financials"].get("total_revenue")
        gp = ground_truth["financials"].get("gross_profit")
        oi = ground_truth["financials"].get("operating_income")
        ni = ground_truth["financials"].get("net_income")

        if rev and rev != 0:
            if gp is not None:
                ground_truth["financials"]["gross_margin"] = round(gp / rev, 4)
            if oi is not None:
                ground_truth["financials"]["operating_margin"] = round(oi / rev, 4)
            if ni is not None:
                ground_truth["financials"]["net_margin"] = round(ni / rev, 4)

        return ground_truth

    except Exception as e:
        print(f"  [ERROR] Ground truth extraction failed for {ticker}: {e}")
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def fetch_company(ticker: str) -> bool:
    """Fetch filing text and ground truth for one company. Returns True on success."""

    print(f"\n{'='*60}")
    print(f"Fetching: {ticker} - {COMPANIES.get(ticker, '')}")
    print(f"{'='*60}")

    # 1. Extract filing text
    print(f"  Downloading 10-K text...")
    text = extract_filing_text(ticker)
    if text:
        filepath = FILINGS_DIR / f"{ticker.lower()}_10k.txt"
        filepath.write_text(text, encoding="utf-8")
        print(f"  Saved: {filepath.name} ({len(text):,} chars)")
    else:
        print(f"  SKIPPED filing text for {ticker}")

    # 2. Extract ground truth from XBRL
    print(f"  Extracting XBRL ground truth...")
    gt = extract_ground_truth(ticker)
    if gt:
        filepath = GROUND_TRUTH_DIR / f"{ticker.lower()}.json"
        filepath.write_text(
            json.dumps(gt, indent=2, default=str),
            encoding="utf-8",
        )
        fields = len(gt.get("financials", {}))
        print(f"  Saved: {filepath.name} ({fields} financial fields)")
    else:
        print(f"  SKIPPED ground truth for {ticker}")

    return text is not None and gt is not None


def main():
    parser = argparse.ArgumentParser(description="Fetch SEC 10-K filings for AutoAgent")
    parser.add_argument("--ticker", type=str, help="Fetch a single ticker (e.g., AAPL)")
    args = parser.parse_args()

    # Set SEC identity (required by EDGAR)
    set_identity(SEC_IDENTITY)

    # Create directories
    FILINGS_DIR.mkdir(parents=True, exist_ok=True)
    GROUND_TRUTH_DIR.mkdir(parents=True, exist_ok=True)

    # Determine which tickers to fetch
    if args.ticker:
        tickers = [args.ticker.upper()]
    else:
        tickers = list(COMPANIES.keys())

    print(f"AutoAgent SEC Filing Fetcher")
    print(f"Fetching {len(tickers)} companies: {', '.join(tickers)}")
    print(f"Data directory: {DATA_DIR}")

    # Fetch each company
    results = {}
    for ticker in tickers:
        success = fetch_company(ticker)
        results[ticker] = success

    # Summary
    print(f"\n{'='*60}")
    print(f"FETCH SUMMARY")
    print(f"{'='*60}")
    succeeded = sum(1 for v in results.values() if v)
    failed = sum(1 for v in results.values() if not v)
    print(f"  Succeeded: {succeeded}/{len(results)}")
    if failed > 0:
        print(f"  Failed: {[t for t, v in results.items() if not v]}")

    # List what we got
    print(f"\nFilings saved:")
    for f in sorted(FILINGS_DIR.glob("*.txt")):
        size = f.stat().st_size
        if size > 0:
            print(f"  {f.name} ({size:,} bytes)")

    print(f"\nGround truth saved:")
    for f in sorted(GROUND_TRUTH_DIR.glob("*.json")):
        if f.stat().st_size == 0:
            continue
        try:
            with open(f) as fh:
                data = json.load(fh)
            fields = len(data.get("financials", {}))
            print(f"  {f.name} ({fields} fields)")
        except json.JSONDecodeError:
            print(f"  {f.name} (CORRUPT - delete and re-fetch)")


if __name__ == "__main__":
    main()