import os
import json
import time
import requests
import pandas as pd
import gspread
from io import StringIO
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials

# スプレッドシートのIDを指定（ステップ1でコピーしたもの）
SPREADSHEET_ID = "ここにスプレッドシートのIDを貼り付けます"

def get_yesterday_weather():
    """昨日の気象データを取得する（先の修正版抽出ロジックを流用）"""
    target_date = datetime.now() - timedelta(days=1)
    url = f"https://www.data.jma.go.jp/obd/stats/etrn/view/hourly_s1.php?prec_no=14&block_no=47412&year={target_date.year}&month={target_date.month}&day={target_date.day}&view="
    
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers)
    response.encoding = response.apparent_encoding
    
    # 以前修正した extract_hourly_data のロジックをここに組み込みます
    dfs = pd.read_html(StringIO(response.text))
    
    target_df = None
    for df in dfs:
        flat_cols = ["".join(str(c) for c in col if pd.notna(c)) for col in df.columns.values]
        if any("時" in c and "間" not in c for c in flat_cols):
            target_df = df
            target_df.columns = flat_cols
            break

    col_map = {}
    for c in target_df.columns:
        if "時" in c and "間" not in c and "hour" not in col_map: col_map["hour"] = c
        elif "天気" in c: col_map["weather"] = c
        elif "気温" in c: col_map["temp"] = c
        elif "降水量" in c: col_map["precip"] = c
        elif "相対湿度" in c or ("湿度" in c and "露点" not in c): col_map["humidity"] = c
        elif "風速" in c and not c.endswith("風向"): col_map["wind_speed"] = c
        elif "風向" in c and not c.endswith("風速"): col_map["wind_dir"] = c
        elif "日照" in c: col_map["sunshine"] = c
        elif "積雪" in c: col_map["snow"] = c
        elif "現地" in c and "気圧" in c: col_map["pressure"] = c

    # 16時の行を取得
    hour_col = col_map.get("hour")
    row_16_series = target_df[target_df[hour_col].astype(str).str.contains("^16:00$|^16$")]
    row = row_16_series.iloc[0]

    # 天気の取得・推測ロジック（先の修正と同じ）
    weather_col = col_map.get("weather")
    weather_val = None
    if weather_col:
        raw_w = row.get(weather_col)
        if pd.notna(raw_w) and str(raw_w).strip() not in ['--', 'NaN', 'nan', '', '///', '×']:
            weather_val = str(raw_w).strip()
    # （中略：晴れ・曇りの推測ロジックなど必要なものを配置）
    if not weather_val:
        weather_val = "調査中"

    # クレンジング関数（必要に応じて簡易化）
    def clean(val):
        if pd.isna(val) or str(val).strip() in ['--', '///', '×']: return ""
        return str(val).replace(']', '').replace(')', '').strip()

    # スプレッドシートに追記する1行分のリスト（ヘッダーの順序に合わせる）
    return [
        target_date.strftime("%Y-%m-%d"),
        "16:00",
        weather_val,
        clean(row.get(col_map.get("temp"))),
        clean(row.get(col_map.get("precip"))),
        clean(row.get(col_map.get("humidity"))),
        clean(row.get(col_map.get("wind_dir"))),
        clean(row.get(col_map.get("wind_speed"))),
        clean(row.get(col_map.get("sunshine"))),
        clean(row.get(col_map.get("snow"))),
        clean(row.get(col_map.get("pressure")))
    ]

def main():
    print("気象データの取得を開始します...")
    data_row = get_yesterday_weather()
    print(f"取得データ: {data_row}")

    # GitHub Actionsの環境変数からJSON認証情報を読み込む
    creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    if not creds_json:
        raise ValueError("環境変数 GOOGLE_CREDENTIALS が設定されていません。")
    
    creds_dict = json.loads(creds_json)
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(credentials)

    # スプレッドシートを開いて末尾に追記
    sheet = client.open_by_key(SPREADSHEET_ID).sheet1
    sheet.append_row(data_row)
    print("スプレッドシートへの書き込みが完了しました。")

if __name__ == "__main__":
    main()
