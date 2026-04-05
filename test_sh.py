import sheets as sh

def check():
    ws = sh._sheet(sh.S.TRANSACTIONS)
    rows = ws.get_all_values()
    print(f"Total rows retrieved: {len(rows)}")
    # Print the last 3 rows
    for i, r in enumerate(rows[-3:]):
        print(f"Row {len(rows) - 2 + i}: {r}")

    ws2 = sh._sheet(sh.S.BUDGET_CONFIG)
    rows2 = ws2.get_all_values()
    print(f"Total budget rows: {len(rows2)}")
    for i, r in enumerate(rows2[-2:]):
        print(f"Budget Row {len(rows2) - 1 + i}: {r}")

if __name__ == "__main__":
    check()
