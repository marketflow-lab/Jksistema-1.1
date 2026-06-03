import os
import gspread
from google.oauth2.service_account import Credentials

CREDENTIALS_FILE = os.path.join('info', 'credentials.json')
IDS = [
    ('clientes', '1oyLYMd059baSs2KZJ8jqld68Y03a3pNRs32xvZlowNk'),
    ('sistema_fixo', '1Kj8ioVDpjLDH2kTSKWvBYKX4-R5zryTj2Ka5_G2irO0'),
]

scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
client = gspread.authorize(creds)

for nome, sid in IDS:
    try:
        sh = client.open_by_key(sid)
        print(nome, 'OK', sh.title)
    except Exception as e:
        print(nome, 'FAIL', repr(e))
