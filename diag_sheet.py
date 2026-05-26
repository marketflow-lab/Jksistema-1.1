import os
import json
import traceback
import gspread
from google.oauth2.service_account import Credentials

CREDENTIALS_FILE = os.path.join('info', 'credentials.json')
SPREADSHEET_ID_CLIENTES = '1oyLYMd059baSs2KZJ8jqld68Y03a3pNRs32xvZlowNk'

print('CREDENTIALS_FILE=', os.path.abspath(CREDENTIALS_FILE))
print('CREDENTIALS_EXISTS=', os.path.exists(CREDENTIALS_FILE))
if os.path.exists(CREDENTIALS_FILE):
    with open(CREDENTIALS_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    print('CLIENT_EMAIL=', data.get('client_email', ''))

try:
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
    client = gspread.authorize(creds)
    sh = client.open_by_key(SPREADSHEET_ID_CLIENTES)
    print('SHEET_TITLE=', sh.title)
    try:
        ws = sh.worksheet('Clientes')
        print('WORKSHEET=Clientes')
    except Exception as e:
        print('WORKSHEET_CLIENTES_FAIL=', repr(e))
        ws = sh.sheet1
        print('WORKSHEET=sheet1')
    rows = ws.get_all_values()
    print('ROWS=', len(rows))
except Exception as e:
    print('EXCEPTION=', repr(e))
    print(traceback.format_exc())
