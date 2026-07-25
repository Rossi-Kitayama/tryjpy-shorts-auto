"""
接続診断用スクリプト(一時的なデバッグ用)
Sheets APIへの生のHTTPレスポンスを確認して、原因を特定する。
"""

import os
import json
import requests
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
]

def main():
    key_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '')
    spreadsheet_id = os.environ.get('SPREADSHEET_ID', '')

    print('--- 基本情報 ---')
    print('SPREADSHEET_ID の長さ:', len(spreadsheet_id))
    print('SPREADSHEET_ID の repr:', repr(spreadsheet_id))  # 前後の空白・改行が見える

    print('GOOGLE_SERVICE_ACCOUNT_JSON の長さ:', len(key_json))

    info = json.loads(key_json)
    print('client_email:', info.get('client_email'))
    print('project_id:', info.get('project_id'))

    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    creds.refresh(Request())
    print('--- トークン取得: 成功 ---')

    # Sheets APIに直接リクエストして、生のレスポンスを確認
    url = f'https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id.strip()}'
    headers = {'Authorization': f'Bearer {creds.token}'}
    response = requests.get(url, headers=headers)

    print('--- Sheets API 応答 ---')
    print('ステータスコード:', response.status_code)
    print('本文(先頭500文字):', response.text[:500])


if __name__ == '__main__':
    main()
