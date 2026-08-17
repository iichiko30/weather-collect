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
SPREADSHEET_ID = "1G3e_DoNROJFaz15xeNe9DprOi1m-EGGjPJHp9yRWN3c"

def get_yesterday_weather():
    """昨日の気象データを取得する"""
    target_date = datetime.now() - timedelta(days=1)
    url = f"https://www.data.jma.go.jp/obd/stats/etrn/view/hourly_s1.php?prec_no=14&block_no=47412&year={target_date.year}&month={target_date.month}&day={target_date.day}&view="
    
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers)
    response.encoding = response.apparent_encoding
    
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

    # クレンジング関数（スプレッドシート出力用の文字列化）
    def clean(val):
        if pd.isna(val): return ""
        val_str = str(val).strip().replace(']', '').replace(')', '')
        if val_str in ['--', '///', '×', '', 'NaN', 'nan']: return ""
        return val_str

    # クレンジング関数（推測ロジックの計算用）
    def clean_float(val):
        c_val = clean(val)
        if not c_val: return None
        try:
            return float(c_val)
        except ValueError:
            return None

    # ==========================================
    # 天気データの取得と強力な補完ロジック（日中の累積で判定）
    # ==========================================
    weather_col = col_map.get("weather")
    weather_val = None

    # パターンA: 16時の天気が記録されていれば採用
    if weather_col:
        raw_w = row.get(weather_col)
        if pd.notna(raw_w) and str(raw_w).strip() not in ['--', 'NaN', 'nan', '', '///', '×']:
            weather_val = str(raw_w).strip()

    # パターンB: 空欄の場合、日中（9時〜15時）の天気の最頻値を採用
    if not weather_val and weather_col:
        daytime_df = target_df[target_df[hour_col].astype(str).str.match('^(9|10|11|12|13|14|15)$')]
        valid_w = daytime_df[weather_col].dropna().astype(str).str.strip()
        valid_w = valid_w[~valid_w.isin(['--', 'NaN', 'nan', '', '///', '×'])]
        if not valid_w.empty:
            weather_val = valid_w.mode()[0]

    # パターンC: 天気列が無い場合、日中の「降水量」と「日照時間」の合計値から推測
    if not weather_val:
        daytime_df = target_df[target_df[hour_col].astype(str).str.match('^(9|10|11|12|13|14|15)$')]
        
        total_sun = 0.0
        total_precip = 0.0
        mean_temp = 10.0
        
        if col_map.get("sunshine"):
            total_sun = daytime_df[col_map.get("sunshine")].apply(clean_float).dropna().sum()
            
        if col_map.get("precip"):
            total_precip = daytime_df[col_map.get("precip")].apply(clean_float).dropna().sum()
            
        if col_map.get("temp"):
            temp_series = daytime_df[col_map.get("temp")].apply(clean_float).dropna()
            mean_temp = temp_series.mean() if not temp_series.empty else 10.0

        if total_precip > 0:
            weather_val = "雪" if mean_temp < 3.0 else "雨"
        elif total_sun >= 2.0:
            weather_val = "晴"
        else:
            weather_val = "曇"

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
