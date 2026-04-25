import pandas as pd

def count_unique_values_in_column_i(file_path):
    # Load workbook
    xls = pd.ExcelFile(file_path)

    counts = {}

    # Loop through all sheet names
    for sheet_name in xls.sheet_names:
        if sheet_name.upper() == "OVERHEAD":
            continue  # Skip this sheet

        # Read only column I (which is the 9th column, index 8)
        try:
            df = pd.read_excel(file_path, sheet_name=sheet_name, usecols="I")
        except (FileNotFoundError, ValueError) as e:
            print(f"Could not read column I in sheet '{sheet_name}': {e}")
            continue

        # Drop empty rows
        values = df.iloc[:, 0].dropna()

        # Count occurrences
        for v in values:
            counts[v] = counts.get(v, 0) + 1

    return counts


if __name__ == "__main__":
    file_path = input("Enter path to Excel file (.xlsx): ").strip()
    result = count_unique_values_in_column_i(file_path)

    print("\n=== Unique Values in Column I (All Sheets Except 'OVERHEAD') ===")
    for value, count in sorted(result.items(), key=lambda x: x[1], reverse=True):
        print(f"{value}: {count}")
