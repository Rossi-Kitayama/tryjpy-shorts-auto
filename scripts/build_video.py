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
import shutil
import subprocess
import textwrap
import requests

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
VIDEO_SIZE = (1080, 1920)

# ブランディング素材(リポジトリ内の固定ファイル)
OPENING_PATH = 'assets/opening.mp4'
ENDING_PATH = 'assets/ending.mp4'
FLAG_PATH = 'assets/turkey_flag.png'
FLAG_WIDTH = 300      # 国旗の表示幅(px)
FLAG_MARGIN = 60      # 画面端からの余白(px)


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


def build_scene_image(date_text, headline, out_path, scene_no, total_scenes):
    """1シーン分の背景+見出し画像(1080x1920)を作成する"""
    img = Image.new('RGB', VIDEO_SIZE, color=(15, 23, 42))  # 濃紺背景
    draw = ImageDraw.Draw(img)

    try:
        font_small = ImageFont.truetype('/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc', 44)
        font_headline = ImageFont.truetype('/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc', 96)
    except Exception:
        font_small = ImageFont.load_default()
        font_headline = ImageFont.load_default()

    # 上部: 日付とシーン番号(進捗ドット)
    draw.text((60, 100), f'トルコリラ円 {date_text}', font=font_small, fill=(180, 190, 210))
    dot_gap = 40
    for n in range(total_scenes):
        color = (255, 215, 0) if n == scene_no else (70, 78, 100)
        cx = 60 + n * dot_gap
        draw.ellipse([cx, 170, cx + 20, 190], fill=color)

    # 中央: 見出しを大きく、複数行に折り返して描画
    wrapped = textwrap.wrap(headline, width=8)
    total_h = len(wrapped) * 120
    y = (VIDEO_SIZE[1] - total_h) // 2
    for line in wrapped:
        bbox = draw.textbbox((0, 0), line, font=font_headline)
        w = bbox[2] - bbox[0]
        x = (VIDEO_SIZE[0] - w) // 2
        draw.text((x, y), line, font=font_headline, fill=(255, 255, 255))
        y += 120

    img.save(out_path)


def build_title_image(date_text, out_path):
    """オープニング用の透過タイトル画像(TRY/JPY + 日付)を作成する"""
    img = Image.new('RGBA', VIDEO_SIZE, (0, 0, 0, 0))  # 完全透明
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype('/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc', 88)
    except Exception:
        font_title = ImageFont.load_default()

    title_text = f'TRY/JPY {date_text}'
    bbox = draw.textbbox((0, 0), title_text, font=font_title)
    w = bbox[2] - bbox[0]
    x = (VIDEO_SIZE[0] - w) // 2
    y = 160  # 画面上部寄り

    # 視認性を上げるため、薄い影を先に描いてから本体を描画
    draw.text((x + 4, y + 4), title_text, font=font_title, fill=(0, 0, 0, 160))
    draw.text((x, y), title_text, font=font_title, fill=(255, 215, 0, 255))

    img.save(out_path)


def brand_clip(input_video, flag_path, out_path, title_path=None):
    """固定素材(オープニング/エンディング)に国旗・タイトルを合成し、無音のブランド済みクリップを作る"""
    inputs = ['-i', input_video, '-i', flag_path]
    if title_path:
        inputs += ['-i', title_path]

    # 国旗は左下に固定表示、タイトルがあれば追加で重ねる
    # 動画は縦長(1080x1920)素材を想定。アスペクト比を保ったまま画面いっぱいに拡大し、
    # はみ出た部分は中央基準でクロップする(引き伸ばしによる歪みを防ぐ)
    filter_complex = (
        f'[1:v]scale={FLAG_WIDTH}:-1,format=rgba,colorchannelmixer=aa=0.9[flag];'
        f'[0:v]scale={VIDEO_SIZE[0]}:{VIDEO_SIZE[1]}:force_original_aspect_ratio=increase,'
        f'crop={VIDEO_SIZE[0]}:{VIDEO_SIZE[1]},fps=25[base];'
        f'[base][flag]overlay=x={FLAG_MARGIN}:y=H-h-{FLAG_MARGIN}[withflag]'
    )
    final_label = '[withflag]'
    if title_path:
        filter_complex += ';[withflag][2:v]overlay=0:0[withtitle]'
        final_label = '[withtitle]'

    result = subprocess.run([
        'ffmpeg', '-y', *inputs,
        '-filter_complex', filter_complex,
        '-map', final_label,
        '-an',
        '-pix_fmt', 'yuv420p',
        '-c:v', 'libx264',
        out_path
    ], capture_output=True)

    if result.returncode != 0:
        print('  --- FFmpegエラー詳細 ---')
        print(result.stderr.decode(errors='replace'))
        raise RuntimeError(f'brand_clip失敗(returncode={result.returncode}): {input_video}')


def get_video_duration(video_path):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=noprint_wrappers=1:nokey=1', video_path],
        capture_output=True, text=True
    )
    return float(result.stdout.strip())


def get_audio_duration(audio_path):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=noprint_wrappers=1:nokey=1', audio_path],
        capture_output=True, text=True
    )
    return float(result.stdout.strip())


def build_scene_clip(image_path, duration, out_path):
    """1シーン分の無音動画クリップ(指定秒数)を作成する"""
    subprocess.run([
        'ffmpeg', '-y',
        '-loop', '1', '-i', image_path,
        '-t', str(duration),
        '-vf', f'scale={VIDEO_SIZE[0]}:{VIDEO_SIZE[1]},fps=25',
        '-pix_fmt', 'yuv420p',
        '-c:v', 'libx264',
        out_path
    ], check=True, capture_output=True)


def concat_clips(clip_paths, out_path, workdir):
    """複数の動画クリップを連結する"""
    list_path = os.path.join(workdir, 'concat_list.txt')
    with open(list_path, 'w') as f:
        for p in clip_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")

    subprocess.run([
        'ffmpeg', '-y',
        '-f', 'concat', '-safe', '0', '-i', list_path,
        '-c', 'copy',
        out_path
    ], check=True, capture_output=True)


def mux_audio_with_offset(video_path, audio_path, offset_seconds, out_path, workdir):
    """動画の総尺に合わせ、冒頭にオープニング分の無音を入れてから音声を合成する"""
    total_duration = get_video_duration(video_path)
    offset_ms = int(offset_seconds * 1000)
    padded_audio = os.path.join(workdir, 'padded_audio.wav')

    subprocess.run([
        'ffmpeg', '-y',
        '-i', audio_path,
        '-af', f'adelay={offset_ms}|{offset_ms},apad',
        '-t', str(total_duration),
        padded_audio
    ], check=True, capture_output=True)

    subprocess.run([
        'ffmpeg', '-y',
        '-i', video_path,
        '-i', padded_audio,
        '-c:v', 'copy',
        '-c:a', 'aac', '-b:a', '192k',
        out_path
    ], check=True, capture_output=True)


def parse_scenes(script_text):
    """シーンJSONを解析する。旧形式(プレーンテキスト)の場合は1シーンとして扱う"""
    try:
        parsed = json.loads(script_text)
        if parsed.get('scenes'):
            return parsed['scenes']
    except (json.JSONDecodeError, AttributeError):
        pass
    # フォールバック: 旧形式のプレーンテキストを1シーンとして扱う
    return [{'start': 0, 'end': 45, 'headline': '今日のトルコリラ円', 'narration': script_text}]


def build_video(date_text, script_text, audio_path, out_path, workdir):
    scenes = parse_scenes(script_text)
    audio_duration = get_audio_duration(audio_path)

    # 台本上の秒数の合計を、実際の音声の長さに合わせて比例配分する
    nominal_total = max(s['end'] for s in scenes) or 1
    scale = audio_duration / nominal_total

    clip_paths = []

    # ① オープニング(国旗+日付入りタイトルを合成)
    title_image = os.path.join(workdir, 'title.png')
    build_title_image(date_text, title_image)
    opening_branded = os.path.join(workdir, 'opening_branded.mp4')
    brand_clip(OPENING_PATH, FLAG_PATH, opening_branded, title_path=title_image)
    opening_duration = get_video_duration(opening_branded)
    clip_paths.append(opening_branded)

    # ② 本編シーン
    for idx, scene in enumerate(scenes):
        duration = max((scene['end'] - scene['start']) * scale, 0.5)  # 最低0.5秒は確保
        image_path = os.path.join(workdir, f'scene_{idx}.png')
        clip_path = os.path.join(workdir, f'clip_{idx}.mp4')

        build_scene_image(date_text, scene.get('headline', ''), image_path, idx, len(scenes))
        build_scene_clip(image_path, duration, clip_path)
        clip_paths.append(clip_path)

    # ③ エンディング(国旗のみ合成)
    ending_branded = os.path.join(workdir, 'ending_branded.mp4')
    brand_clip(ENDING_PATH, FLAG_PATH, ending_branded)
    clip_paths.append(ending_branded)

    silent_video = os.path.join(workdir, 'silent.mp4')
    concat_clips(clip_paths, silent_video, workdir)

    # ナレーションはオープニング分だけ遅らせて開始する(オープニング・エンディングは無音)
    mux_audio_with_offset(silent_video, audio_path, opening_duration, out_path, workdir)


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
        local_video = f'/tmp/video_{i}.mp4'
        workdir = f'/tmp/scenes_{i}'
        os.makedirs(workdir, exist_ok=True)
        build_video(date_text, script_text, local_audio, local_video, workdir)

        # 出力用フォルダにコピー(GitHub Actionsのアーティファクトとして保存される)
        output_dir = 'output_videos'
        os.makedirs(output_dir, exist_ok=True)
        final_path = os.path.join(output_dir, f'TRYJPY_{date_text}.mp4')
        shutil.copy(local_video, final_path)

        # GitHub Actionsの実行結果ページへのリンクをシートに記録
        server_url = os.environ.get('GITHUB_SERVER_URL', '')
        repo = os.environ.get('GITHUB_REPOSITORY', '')
        run_id = os.environ.get('GITHUB_RUN_ID', '')
        result_url = f'{server_url}/{repo}/actions/runs/{run_id}' if run_id else '(実行結果を確認してください)'

        sheet.update_cell(i, 4, result_url)
        print(f'行{i}: 完了 → {final_path} (ダウンロードは {result_url} から)')


if __name__ == '__main__':
    main()
