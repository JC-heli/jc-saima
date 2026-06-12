"""
saima_friday.py
每週五 15:00 自動發送當周賽馬報酬率到 LINE 個股群
"""
import ast
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

LINE_TOKEN    = "GJ5Upa7XxMQ3tkNiGtObU1Am6oyVACHXbwO2Zgq4FjTjFXBVMUtpvn92Gmu2pbFLTlW7Se8BtkOT66icv3tMQaCr4XGsjm8pKcrfPj9m8mOxFw46pxANQ9YXL0dz2GCwC3DzRWWqSI/IlRCmGp9ozwdB04t89/1O/w1cDnyilFU="
LINE_GROUP_ID = "Cc0dedc3a335b5fe926d2f589d07547d1"


def load_week_data():
    src = (Path(__file__).parent / "saima_w24_live.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "WEEK_DATA":
                    return ast.literal_eval(node.value)
    raise ValueError("WEEK_DATA not found in saima_w24_live.py")


def fetch(code, start, end):
    suffixes = [""] if code.startswith("^") else [".TW", ".TWO"]
    for sfx in suffixes:
        try:
            df = yf.download(code + sfx, start=start, end=end, auto_adjust=True, progress=False)
            if not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df.index = pd.to_datetime(df.index).tz_localize(None)
                return df
        except Exception:
            pass
    return None


def calc_ret(df, start):
    if df is None:
        return None
    start_ts = pd.Timestamp(start)
    pre  = df[df.index < start_ts]
    week = df[df.index >= start_ts]
    if pre.empty or week.empty:
        return None
    base  = float(pre["Close"].iloc[-1])
    close = float(week["Close"].dropna().iloc[-1])
    return round((close - base) / base * 100, 2)


def send_line(msg):
    requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
        json={"to": LINE_GROUP_ID, "messages": [{"type": "text", "text": msg}]},
        timeout=10,
    )


def main():
    week_data = load_week_data()
    sel_week  = max(week_data.keys())
    info      = week_data[sel_week]
    start     = info["start"]
    pre_start = (pd.Timestamp(start) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    end       = (pd.Timestamp(start) + pd.Timedelta(days=7)).strftime("%Y-%m-%d")

    rows = []
    for code, name in info["stocks"].items():
        df  = fetch(code, pre_start, end)
        ret = calc_ret(df, start)
        if ret is None:
            continue
        week_df = df[df.index >= pd.Timestamp(start)]
        d0 = week_df.index[0].strftime("%m/%d")
        d1 = week_df.index[-1].strftime("%m/%d")
        rows.append((ret, code, name, d0, d1))

    if not rows:
        print("No data available.")
        return

    rows.sort(reverse=True)
    avg      = round(sum(r for r, *_ in rows) / len(rows), 2)
    twii_ret = calc_ret(fetch("^TWII", pre_start, end), start)
    p2_ret   = calc_ret(fetch("00631L", pre_start, end), start)
    period   = f"{rows[0][3]}-{rows[0][4]}"

    lines = [
        f"鍾建安🐎第{sel_week}週賽馬報酬率",
        f"【{period} 收盤】",
        "",
        "📊 個股表現：",
    ]
    for i, (ret, code, name, d0, d1) in enumerate(rows, 1):
        arrow = "🔥" if ret > 5 else ("↑" if ret > 0 else "↓")
        lines.append(f"{i:2}. {code} {name}  {ret:+.2f}%  {arrow}")
    lines.append("")
    if twii_ret is not None:
        lines.append(f"📈 加權指數：{twii_ret:+.2f}%")
    if p2_ret is not None:
        lines.append(f"⚡ 正二(00631L)：{p2_ret:+.2f}%")
    lines.append("━━━━━━━━━━━━━━━")
    lines.append(f"🏇 投組績效：{avg:+.2f}%")

    msg = "\n".join(lines)
    print(msg)
    send_line(msg)
    print("\nSent to LINE.")


if __name__ == "__main__":
    main()
