import os
import json
import time
import requests
import pandas as pd
import gspread
from io import StringIO
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials

# スプレッドシートのIDを指定
SPREADSHEET_ID = "1G3e_DoNROJFaz15xeNe9DprOi1m-EGGjPJHp9yRWN3c"

def get_weather_by_date(target_date: datetime):
    """指定された日付の気象データを取得する"""
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

    def clean(val):
        if pd.isna(val): return ""
        val_str = str(val).strip().replace(']', '').replace(')', '')
        if val_str in ['--', '///', '×', '', 'NaN', 'nan']: return ""
        return val_str

    def clean_float(val):
        c_val = clean(val)
        if not c_val: return None
        try:
            return float(c_val)
        except ValueError:
            return None

    # 天気データの取得と補完ロジック
    weather_col = col_map.get("weather")
    weather_val = None

    if weather_col:
        raw_w = row.get(weather_col)
        if pd.notna(raw_w) and str(raw_w).strip() not in ['--', 'NaN', 'nan', '', '///', '×']:
            weather_val = str(raw_w).strip()

    if not weather_val and weather_col:
        daytime_df = target_df[target_df[hour_col].astype(str).str.match('^(9|10|11|12|13|14|15)$')]
        valid_w = daytime_df[weather_col].dropna().astype(str).str.strip()
        valid_w = valid_w[~valid_w.isin(['--', 'NaN', 'nan', '', '///', '×'])]
        if not valid_w.empty:
            weather_val = valid_w.mode()[0]

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
    # 環境変数から期間を取得（指定がなければ「昨日」のみ）
    start_str = os.environ.get('START_DATE', '').strip()
    end_str = os.environ.get('END_DATE', '').strip()

    if start_str and end_str:
        start_date = datetime.strptime(start_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_str, "%Y-%m-%d")
    elif start_str:
        start_date = datetime.strptime(start_str, "%Y-%m-%d")
        end_date = start_date
    else:
        start_date = datetime.now() - timedelta(days=1)
        end_date = start_date

    print(f"取得対象期間: {start_date.strftime('%Y-%m-%d')} 〜 {end_date.strftime('%Y-%m-%d')}")

    # Google認証とスプレッドシート接続
    creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    if not creds_json:
        raise ValueError("環境変数 GOOGLE_CREDENTIALS が設定されていません。")
    
    creds_dict = json.loads(creds_json)
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(credentials)
    sheet = client.open_by_key(SPREADSHEET_ID).sheet1

    # 指定期間を1日ずつループして取得・追記
    current_date = start_date
    rows_to_append = []

    while current_date <= end_date:
        date_label = current_date.strftime("%Y-%m-%d")
        print(f"{date_label} のデータを取得中...")
        try:
            row = get_weather_by_date(current_date)
            rows_to_append.append(row)
            print(f"  -> 取得成功: {row[2]} (気温: {row[3]}℃)")
        except Exception as e:
            print(f"  -> {date_label} の取得に失敗しました: {e}")
        
        current_date += timedelta(days=1)
        # 気象庁サーバーへの負荷軽減のため1秒待機
        if current_date <= end_date:
            time.sleep(1)

    if rows_to_append:
        # 複数行を一括でスプレッドシートに追記（API制限対策）
        sheet.append_rows(rows_to_append)
        print(f"計 {len(rows_to_append)} 日分のデータをスプレッドシートに書き込みました。")
    else:
        print("書き込むデータがありませんでした。")

if __name__ == "__main__":
    main()
