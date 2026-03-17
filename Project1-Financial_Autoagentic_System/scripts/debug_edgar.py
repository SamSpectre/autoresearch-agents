"""Quick diagnostic to see what edgartools actually returns."""
from edgar import Company, set_identity

set_identity("SamuelSehgal nawabsingh2512@gmail.com")

c = Company("AAPL")
f = c.get_filings(form="10-K")[0]
tenk = f.obj()

print("=== INCOME STATEMENT ===")
income = tenk.financials.income_statement()
df = income.to_dataframe()

print("Columns:", list(df.columns))
print()
print("First 20 rows (label + values):")
meta_cols = ["label", "concept", "level", "parent_concept", "parent_abstract_concept", "units"]
value_cols = [c for c in df.columns if c not in meta_cols]
print(f"Value columns: {value_cols}")
print()
for i, row in df.head(20).iterrows():
    label = row.get("label", "?")
    vals = [row.get(c) for c in value_cols[:2]]  # first 2 value columns
    print(f"  {label:50s} | {vals}")

print()
print("=== BALANCE SHEET ===")
balance = tenk.financials.balance_sheet()
df2 = balance.to_dataframe()
print("Columns:", list(df2.columns))
print()
value_cols2 = [c for c in df2.columns if c not in meta_cols]
print(f"Value columns: {value_cols2}")
print()
for i, row in df2.head(20).iterrows():
    label = row.get("label", "?")
    vals = [row.get(c) for c in value_cols2[:2]]
    print(f"  {label:50s} | {vals}")