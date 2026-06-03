import os
import gspread
from google.oauth2.service_account import Credentials

# Basic configuration from login.py
PASTA_INFO = "info"
CREDENTIALS_FILE = os.path.join(PASTA_INFO, 'credentials.json')
SPREADSHEET_ID_CLIENTES = '1oyLYMd059baSs2KZJ8jqld68Y03a3pNRs32xvZlowNk'

def autenticar_google_sheets():
    """Authenticates with Google Sheets and returns a client."""
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    if not os.path.exists(CREDENTIALS_FILE):
        print(f"Error: Credentials file not found at {CREDENTIALS_FILE}")
        return None
    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"Error authenticating with Google: {e}")
        return None

def main():
    """Connects to the sheet and prints the header row."""
    print("Attempting to read spreadsheet headers...")
    client = autenticar_google_sheets()
    if not client:
        print("Could not authenticate with Google Sheets.")
        return

    try:
        spreadsheet = client.open_by_key(SPREADSHEET_ID_CLIENTES)
        worksheet = spreadsheet.worksheet("Clientes")
        headers = worksheet.row_values(1)
        
        print("\n=== Spreadsheet Headers ===")
        for i, header in enumerate(headers):
            print(f"Column {chr(ord('A') + i)} (Index {i}): '{header}'")
        print("=========================\n")

        print("Relevant module headers (K onwards):")
        if len(headers) > 10:
            for i in range(10, len(headers)):
                 print(f"Column {chr(ord('A') + i)} (Index {i}): '{headers[i]}'" )
        else:
            print("No columns found from K onwards.")

    except gspread.exceptions.SpreadsheetNotFound:
        print(f"Error: Spreadsheet with ID '{SPREADSHEET_ID_CLIENTES}' not found.")
    except gspread.exceptions.WorksheetNotFound:
        print("Error: Worksheet 'Clientes' not found in the spreadsheet.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    main()
