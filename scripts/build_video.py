"""
トルコリラ円 日次レポート 動画自動生成スクリプト
「台本」シート(日付・台本・音声URL)を読み、
音声の長さに合わせた縦型(1080x1920)動画を作成し、
Google Driveにアップロード、シートのD列にリンクを書き込む。
"""

import os
import io
import re
import json
import subprocess
import textwrap

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from PIL import Image, ImageDraw, ImageFont

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
]

SHEET_NAME = '台本'
VIDEO_FOLDER_NAME = 'TRY_JPY_動画'
VIDEO_SIZE = (1080, 1920)


def get_credentials():
    key_json = os.environ['GOOGLE_SERVICE_ACCOUNT_JSON']
    info = json.loads(key_json)
    return Credentials.from_service_account_info(info, scopes=SCOPES)


def extract_drive_file_id(url):
    match = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
    if match:
        return match.group(1)
    match = re.search(r'id=([a-zA-Z0-9_-]+)', url)
    return match.group(1) if match else None


def download_drive_file(drive_service, file_id, local_path):
    request = drive_service.files().get_media(fileId=file_id)
    with io.FileIO(local_path, 'wb') as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def get_or_create_drive_folder(drive_service, name):
    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    results = drive_service.files().list(q=query, fields='files(id, name)').execute()
    files = results.get('files', [])
    if files:
        return files[0]['id']
    folder = drive_service.files().create(
        body={'name': name, 'mimeType': 'application/vnd.google-apps.folder'},
        fields='id'
    ).execute()
    return folder['id']


def build_caption_image(date_text, script_text, out_path):
    """背景+テロップの静止画(1080x1920)を作成する"""
    img = Image.new('RGB', VIDEO_SIZE, color=(15, 23, 42))  # 濃紺背景
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype('/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc', 70)
        font_body = ImageFont.truetype('/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc', 48)
    except Exception:
        font_title = ImageFont.load_default()
        font_body = ImageFont.load_default()

    # タイトル
    draw.text((60, 120), f'トルコリラ円 {date_text}', font=font_title, fill=(255, 215, 0))

    # 台本本文を折り返して描画(字幕的に中央あたりから表示)
    wrapped = textwrap.wrap(script_text, width=20)
    y = 500
    for line in wrapped:
        draw.text((60, y), line, font=font_body, fill=(255, 255, 255))
        y += 70

    img.save(out_path)


def get_audio_duration(audio_path):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=noprint_wrapping=1:nokey=1', audio_path],
        capture_output=True, text=True
    )
    return float(result.stdout.strip())


def build_video(image_path, audio_path, out_path):
    duration = get_audio_duration(audio_path)
    subprocess.run([
        'ffmpeg', '-y',
        '-loop', '1', '-i', image_path,
        '-i', audio_path,
        '-c:v', 'libx264', '-tune', 'stillimage',
        '-c:a', 'aac', '-b:a', '192k',
        '-pix_fmt', 'yuv420p',
        '-t', str(duration),
        '-vf', f'scale={VIDEO_SIZE[0]}:{VIDEO_SIZE[1]}',
        out_path
    ], check=True)


def main():
    creds = get_credentials()
    gc = gspread.authorize(creds)
    drive_service = build('drive', 'v3', credentials=creds)

    spreadsheet_id = os.environ['SPREADSHEET_ID']
    sheet = gc.open_by_key(spreadsheet_id).worksheet(SHEET_NAME)
    rows = sheet.get_all_values()

    # D列見出しがなければ追加
    header = rows[0]
    if len(header) < 4 or header[3] == '':
        sheet.update_cell(1, 4, '動画URL')

    video_folder_id = get_or_create_drive_folder(drive_service, VIDEO_FOLDER_NAME)

    for i, row in enumerate(rows[1:], start=2):
        date_text = row[0] if len(row) > 0 else ''
        script_text = row[1] if len(row) > 1 else ''
        audio_url = row[2] if len(row) > 2 else ''
        video_url = row[3] if len(row) > 3 else ''

        if not script_text or not audio_url or video_url:
            continue  # 未生成データ無し、または既に動画化済み

        print(f'行{i}: 処理開始 ({date_text})')

        audio_id = extract_drive_file_id(audio_url)
        print(f'  音声URL: {audio_url}')
        print(f'  抽出したファイルID: {audio_id}')

        local_audio = f'/tmp/audio_{i}.wav'
        download_drive_file(drive_service, audio_id, local_audio)

        audio_size = os.path.getsize(local_audio)
        print(f'  ダウンロードした音声ファイルサイズ: {audio_size} bytes')
        if audio_size == 0:
            raise ValueError('ダウンロードした音声ファイルが空です(0 bytes)。共有設定またはファイルIDを確認してください。')

        local_image = f'/tmp/caption_{i}.png'
        build_caption_image(date_text, script_text, local_image)

        local_video = f'/tmp/video_{i}.mp4'
        build_video(local_image, local_audio, local_video)

        file_metadata = {'name': f'TRYJPY_{date_text}.mp4', 'parents': [video_folder_id]}
        media = MediaFileUpload(local_video, mimetype='video/mp4')
        uploaded = drive_service.files().create(
            body=file_metadata, media_body=media, fields='id'
        ).execute()
        drive_service.permissions().create(
            fileId=uploaded['id'], body={'role': 'reader', 'type': 'anyone'}
        ).execute()

        result_url = f"https://drive.google.com/file/d/{uploaded['id']}/view"
        sheet.update_cell(i, 4, result_url)
        print(f'行{i}: 完了 → {result_url}')


if __name__ == '__main__':
    main()
