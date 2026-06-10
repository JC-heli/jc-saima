"""
saima_live.py
賽馬歷史資料庫：多週次切換，OHLC 表 + 報酬排名 + K 線圖
執行：streamlit run C:\claude\tools\saima_w24_live.py
"""
import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta

WEEK_DATA = {
    25: {
        "label": "第25週（06/14–06/20）",
        "start": "2026-06-14",
        "end":   "2026-06-20",
        "stocks": {
            "3026": "禾伸堂",  "2472": "立隆電",  "2454": "聯發科",
            "3042": "晶技",    "4958": "臻鼎-KY", "8358": "金居",
            "5289": "宜鼎",    "3189": "景碩",    "8064": "東捷",
            "8021": "尖點",
        },
    },
    24: {
        "label": "第24週（06/07–06/13）",
        "start": "2026-06-07",
        "end":   "2026-06-13",
        "stocks": {
            "3026": "禾伸堂",  "6147": "頎邦",    "2472": "立隆電",
            "6274": "台燿",    "2454": "聯發科",   "4958": "臻鼎-KY",
            "6451": "訊芯-KY", "2303": "聯電",    "2464": "盟立",
            "8064": "東捷",
        },
    },
    23: {
        "label": "第23週（05/31–06/06）",
        "start": "2026-05-31",   # 標籤週起始日（週日，yfinance 自動跳到 6/2 週一）
        "end":   "2026-06-06",   # 標籤週結束日（週六），+1 後 yfinance 取到 6/5 週五
        "stocks": {
            "3026": "禾伸堂",  "6147": "頎邦",    "6451": "訊芯-KY",
            "4958": "臻鼎-KY", "2472": "立隆電",  "6727": "亞泰金屬",
            "8064": "東捷",    "2464": "盟立",    "3189": "景碩",
            "8358": "金居",
        },
    },
}

END = (datetime.today() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def _yf_get(ticker: str, **kwargs) -> pd.DataFrame | None:
    df = yf.download(ticker, auto_adjust=True, progress=False, **kwargs)
    if df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df = df.dropna(subset=["Close"])
    return df if not df.empty else None


@st.cache_data(ttl=55)
def fetch_ohlc(code: str, start: str, end: str) -> pd.DataFrame | None:
    for suffix in [".TW", ".TWO"]:
        r = _yf_get(code + suffix, start=start, end=end)
        if r is not None:
            return r
    return None


@st.cache_data(ttl=300)
def fetch_kline_ohlc(code: str) -> pd.DataFrame | None:
    for suffix in [".TW", ".TWO"]:
        r = _yf_get(code + suffix, period="1y", interval="1d")
        if r is not None:
            return r
    return None


def get_rangebreaks(df: pd.DataFrame) -> list:
    """跳過週末 + 台灣國定假日（從資料空缺的平日推算）"""
    all_days    = pd.date_range(df.index.min(), df.index.max(), freq="D")
    trading_set = set(df.index.normalize().date)
    holidays    = [
        d for d in all_days
        if d.weekday() < 5 and d.date() not in trading_set
    ]
    breaks = [dict(bounds=["sat", "mon"])]
    if holidays:
        breaks.append(dict(values=[d.strftime("%Y-%m-%d") for d in holidays]))
    return breaks


def _twse_fmtqik_series(months: int = 13) -> dict:
    """抓 FMTQIK 近 N 個月，回傳 {date: (成交金額億, OHLC_dict)} dict"""
    import requests
    result = {}
    today = pd.Timestamp.today()
    seen = set()
    for m in range(months):
        query_dt = today - pd.DateOffset(months=m)
        key = (query_dt.year, query_dt.month)
        if key in seen:
            continue
        seen.add(key)
        try:
            r = requests.get("https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK",
                             params={"response": "json", "date": query_dt.strftime("%Y%m%d")},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            js = r.json()
            if js.get("stat") != "OK":
                continue
            for item in js.get("data", []):
                parts = item[0].split("/")
                ts = pd.Timestamp(f"{int(parts[0])+1911}-{parts[1]}-{parts[2]}")
                result[ts] = int(item[2].replace(",", "")) // 100_000_000  # 成交金額億
        except Exception:
            continue
    return result


def _twse_fill(df_yf: pd.DataFrame, vol_map: dict) -> pd.DataFrame:
    """用 TWSE 補 ^TWII 近期缺失的交易日 OHLC，成交量用 vol_map（億元）"""
    import requests
    try:
        today = pd.Timestamp.today()
        cutoff = today - pd.Timedelta(days=14)
        all_weekdays = pd.bdate_range(cutoff, today)
        existing = set(df_yf.index.normalize().date)
        missing_dates = {d.date() for d in all_weekdays if d.date() not in existing and d.date() <= today.date()}
        if not missing_dates:
            return df_yf

        ohlc = {}
        for query_dt in [today, today - pd.DateOffset(months=1)]:
            date_str = query_dt.strftime("%Y%m%d")
            r1 = requests.get("https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST",
                               params={"response": "json", "date": date_str},
                               headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            js1 = r1.json()
            if js1.get("stat") == "OK":
                for item in js1.get("data", []):
                    parts = item[0].split("/")
                    ts = pd.Timestamp(f"{int(parts[0])+1911}-{parts[1]}-{parts[2]}")
                    if ts.date() in missing_dates:
                        ohlc[ts] = {
                            "Open": float(item[1].replace(",", "")),
                            "High": float(item[2].replace(",", "")),
                            "Low":  float(item[3].replace(",", "")),
                            "Close": float(item[4].replace(",", "")),
                        }

        if not ohlc:
            return df_yf
        rows = {ts: {**v, "Volume": vol_map.get(ts, 0)} for ts, v in ohlc.items()}
        df_fill = pd.DataFrame(rows).T
        df_fill.index = pd.to_datetime(df_fill.index).tz_localize(None)
        return pd.concat([df_yf, df_fill]).sort_index().drop_duplicates()
    except Exception:
        return df_yf


@st.cache_data(ttl=300)
def fetch_index_kline(ticker: str) -> pd.DataFrame | None:
    df = _yf_get(ticker, period="1y", interval="1d")
    if ticker == "^TWII" and df is not None:
        vol_map = _twse_fmtqik_series(13)          # 成交金額億，覆蓋 yfinance 量
        df = _twse_fill(df, vol_map)
        if vol_map:
            df["Volume"] = df.index.map(lambda t: vol_map.get(t, df.loc[t, "Volume"]))
    return df


def _prev_close_ret(df_full: pd.DataFrame, start: str) -> float | None:
    """從含前期資料的 df 中，取 start 前一交易日收盤為 base 算報酬"""
    start_ts  = pd.Timestamp(start)
    df_pre    = df_full[df_full.index < start_ts]
    df_period = df_full[df_full.index >= start_ts]
    if df_pre.empty or df_period.empty:
        return None
    base  = float(pd.Series(df_pre["Close"]).dropna().iloc[-1])
    last  = float(pd.Series(df_period["Close"]).dropna().iloc[-1])
    if base == 0 or pd.isna(base) or pd.isna(last):
        return None
    return round((last - base) / base * 100, 2)


@st.cache_data(ttl=55)
def fetch_benchmark(ticker: str, start: str, end: str) -> float | None:
    pre_start = (pd.Timestamp(start) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    df = _yf_get(ticker, start=pre_start, end=end)
    return _prev_close_ret(df, start) if df is not None else None


def pct(new, base) -> float:
    if pd.isna(new) or pd.isna(base) or base == 0:
        return float("nan")
    return round((new - base) / base * 100, 2)


def stock_listing_weeks(code: str, up_to_week: int) -> list[int]:
    """回傳該股出現過的所有週次（升序），只含 ≤ up_to_week 的週次"""
    return sorted(w for w, d in WEEK_DATA.items() if code in d["stocks"] and w <= up_to_week)


def build_rows(stocks: dict, start: str, end: str, sel_week: int) -> list[dict]:
    rows = []
    pre_start  = (pd.Timestamp(start) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    start_ts   = pd.Timestamp(start)
    for code, name in stocks.items():
        df_full = fetch_ohlc(code, pre_start, end)
        if df_full is None or df_full.empty:
            continue
        df_pre    = df_full[df_full.index < start_ts]
        df_period = df_full[df_full.index >= start_ts]
        if df_pre.empty or df_period.empty:
            continue
        base  = float(df_pre["Close"].iloc[-1])          # 前一交易日收盤
        high  = float(df_period["High"].max(skipna=True))
        low   = float(df_period["Low"].min(skipna=True))
        close = float(df_period["Close"].dropna().iloc[-1])
        weeks = stock_listing_weeks(code, sel_week)
        date_s = df_period.index[0].strftime("%m/%d")
        date_e = df_period.index[-1].strftime("%m/%d")
        rows.append({
            "股票":       f"{code} {name}",
            "上榜週次":    " · ".join(f"W{w}" for w in weeks),
            "統計區間":    f"{date_s}–{date_e}",
            "_base":    base,
            "開盤":      float(df_period["Open"].iloc[0]),
            "最高":      high,
            "最高幅度(%)": pct(high, base),
            "收盤":      close,
            "報酬率(%)":  pct(close, base),
            "最低":      low,
            "最低幅度(%)": pct(low, base),
        })
    return rows


def main():
    st.set_page_config(page_title="賽馬歷史資料庫", page_icon="🐎", layout="wide")

    # 每 60 秒自動 reload
    components.html(
        "<script>setTimeout(function(){window.location.reload();},60000);</script>",
        height=0,
    )

    # ── 週次選擇器 ────────────────────────────────────────────
    week_nums   = sorted(WEEK_DATA.keys(), reverse=True)
    week_labels = {w: WEEK_DATA[w]["label"] for w in week_nums}

    # 預設選今日所在週次
    today = pd.Timestamp.today().normalize()
    default_week = week_nums[0]
    for w in week_nums:
        w_start = pd.Timestamp(WEEK_DATA[w]["start"])
        w_end_raw = WEEK_DATA[w].get("end")
        w_end = pd.Timestamp(w_end_raw) if w_end_raw else today + pd.Timedelta(days=7)
        if w_start <= today <= w_end:
            default_week = w
            break
    default_idx = week_nums.index(default_week)

    sel_week = st.selectbox(
        "選擇週次", week_nums,
        format_func=lambda w: week_labels[w],
        index=default_idx,
        key="sel_week",
    )
    STOCKS = WEEK_DATA[sel_week]["stocks"]
    START  = WEEK_DATA[sel_week]["start"]

    # 計算本週的績效截止日（yfinance end 是 exclusive，需 +1 天）
    raw_end = WEEK_DATA[sel_week].get("end")
    if raw_end is None:
        week_end = END  # 當前週，用今日+1
    else:
        week_end = (pd.Timestamp(raw_end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    # 切週次時重設 K 線選股
    if st.session_state.get("_last_week") != sel_week:
        st.session_state["_last_week"]  = sel_week
        st.session_state["kline_stock"] = None

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = build_rows(STOCKS, START, week_end, sel_week)

    if not rows:
        st.error("無法取得資料，請確認網路連線")
        return

    df = (
        pd.DataFrame(rows)
        .sort_values("報酬率(%)", ascending=False)
        .reset_index(drop=True)
    )

    avg       = df["報酬率(%)"].mean()
    pos_count = int((df["報酬率(%)"] > 0).sum())
    twii_ret  = fetch_benchmark("^TWII", START, week_end)
    etf_ret   = fetch_benchmark("00675L.TW", START, week_end)

    # ── Header metrics ──────────────────────────────────────
    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    c1.metric("週次",   WEEK_DATA[sel_week]["label"].split("（")[0])
    announce_date = (pd.Timestamp(START) - pd.Timedelta(days=4)).strftime("%Y-%m-%d")
    c2.metric("發布日", announce_date)
    c3.metric("上漲",      f"{pos_count} 檔")
    c4.metric("下跌",      f"{len(rows)-pos_count} 檔")
    with c5:
        avg_color = "#FF3B3B" if avg > 0 else "#2ECC71"
        st.markdown(
            f"""
            <div style="border:2px solid {avg_color};border-radius:8px;padding:8px 14px;background:rgba(255,255,255,0.04);">
                <div style="font-size:13px;color:#aaa;margin-bottom:4px;">投組平均</div>
                <div style="font-size:28px;font-weight:bold;color:{avg_color};">{avg:+.2f}%</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    c6.metric("大盤",      f"{twii_ret:+.2f}%" if twii_ret is not None else "N/A",
              delta=f"{avg - twii_ret:+.2f}% vs 大盤" if twii_ret is not None else None)
    c7.metric("00675L 正二", f"{etf_ret:+.2f}%" if etf_ret is not None else "N/A",
              delta=f"{avg - etf_ret:+.2f}% vs 正二" if etf_ret is not None else None)

    st.divider()

    # ── OHLC 表（可點欄位排序） ──────────────────────────────
    st.subheader("個股之開高低收")

    def pct_color(v: float) -> str:
        return "#FF3B3B" if v >= 0 else "#2ECC71"

    rows_html = ""
    for _, row in df.iterrows():
        code  = row["股票"].split()[0]
        base  = row["_base"]
        op    = row["開盤"]
        hi    = row["最高"];    hi_p = row["最高幅度(%)"]
        cl    = row["收盤"];    cl_p = row["報酬率(%)"]
        lo    = row["最低"];    lo_p = row["最低幅度(%)"]
        rng   = row["統計區間"]
        wks   = row["上榜週次"]
        multi = len(wks.split("·")) > 1
        wks_html = f'<span style="color:#7eb3ff;font-weight:bold">{wks}</span>' if multi else wks
        rows_html += f"""<tr>
          <td data-val="{code}">{row["股票"]}</td>
          <td data-val="0" style="text-align:center">{wks_html}</td>
          <td data-val="0" style="text-align:center;color:#aaa">{rng}</td>
          <td data-val="{op:.4f}" style="color:#ccc">{op:.2f}</td>
          <td data-val="{hi_p:.4f}" style="color:{pct_color(hi_p)};font-weight:bold">{hi:.2f} ({hi_p:+.2f}%)</td>
          <td data-val="{lo_p:.4f}" style="color:{pct_color(lo_p)};font-weight:bold">{lo:.2f} ({lo_p:+.2f}%)</td>
          <td data-val="{cl_p:.4f}" style="color:{pct_color(cl_p)};font-weight:bold">{cl:.2f} ({cl_p:+.2f}%)</td>
        </tr>"""

    table_html = f"""
<style>
body{{margin:0;background:transparent;}}
#ohlc{{width:100%;border-collapse:collapse;font-size:15px;font-family:"Segoe UI","Noto Sans TC","Microsoft JhengHei",sans-serif;color:#fff;font-weight:500;}}
#ohlc th{{background:rgba(255,255,255,0.07);padding:9px 14px;cursor:pointer;user-select:none;text-align:right;white-space:nowrap;border-bottom:1px solid rgba(255,255,255,0.15);color:#fff;font-weight:600;letter-spacing:.3px;}}
#ohlc th:first-child{{text-align:left;}}
#ohlc th:nth-child(2){{text-align:center;}}
#ohlc td{{padding:8px 14px;border-bottom:1px solid rgba(255,255,255,0.06);text-align:right;white-space:nowrap;color:#fff;}}
#ohlc td:first-child{{text-align:left;color:#fff;}}
#ohlc tr:hover td{{background:rgba(255,255,255,0.05);}}
#ohlc th.asc::after{{content:" ▲";color:#7eb3ff;}}
#ohlc th.desc::after{{content:" ▼";color:#7eb3ff;}}
</style>
<table id="ohlc">
<thead><tr>
  <th onclick="srt(0)">股票</th>
  <th>上榜週次</th>
  <th style="text-align:center">統計區間</th>
  <th onclick="srt(3)">開盤</th>
  <th onclick="srt(4)">最高</th>
  <th onclick="srt(5)">最低</th>
  <th onclick="srt(6)">收盤</th>
</tr></thead>
<tbody>{rows_html}</tbody>
</table>
<script>
var _dir={{}};
function srt(col){{
  var tb=document.getElementById('ohlc');
  var ths=tb.querySelectorAll('th');
  var asc=_dir[col]!=='asc';
  _dir[col]=asc?'asc':'desc';
  ths.forEach(function(h,i){{h.className=i===col?(asc?'asc':'desc'):'';}});
  var rows=Array.from(tb.tBodies[0].rows);
  rows.sort(function(a,b){{
    var av=parseFloat(a.cells[col].getAttribute('data-val'));
    var bv=parseFloat(b.cells[col].getAttribute('data-val'));
    if(isNaN(av)){{av=a.cells[col].getAttribute('data-val');bv=b.cells[col].getAttribute('data-val');}}
    return asc?(av>bv?1:av<bv?-1:0):(av<bv?1:av>bv?-1:0);
  }});
  rows.forEach(function(r){{tb.tBodies[0].appendChild(r);}});
}}
</script>
"""
    components.html(table_html, height=len(df) * 38 + 60, scrolling=False)

    st.divider()

    # ── 報酬排名橫條圖 ───────────────────────────────────────
    st.subheader("報酬率排名")
    bar_df = df.sort_values("報酬率(%)", ascending=True)
    colors = ["#2ECC71" if v < 0 else "#FF3B3B" for v in bar_df["報酬率(%)"]]

    # 加入投組平均、大盤、正二作為比較基準
    bench_labels, bench_vals, bench_colors = [], [], []
    bench_labels.append("★ 投組平均")
    bench_vals.append(round(avg, 2))
    bench_colors.append("#2ECC71" if avg < 0 else "#FF3B3B")
    if twii_ret is not None:
        bench_labels.append("▶ 大盤 TWII")
        bench_vals.append(twii_ret)
        bench_colors.append("#A0A0A0")
    if etf_ret is not None:
        bench_labels.append("▶ 00675L 正二")
        bench_vals.append(etf_ret)
        bench_colors.append("#F0A500")

    all_labels = list(bar_df["股票"]) + bench_labels
    all_vals   = list(bar_df["報酬率(%)"]) + bench_vals
    all_colors = colors + bench_colors

    fig = go.Figure(go.Bar(
        x=all_vals,
        y=all_labels,
        orientation="h",
        marker_color=all_colors,
        text=[f"{v:+.2f}%" for v in all_vals],
        textposition="outside",
        hovertemplate="<b>%{y}</b><br>報酬：%{x:.2f}%<extra></extra>",
    ))
    fig.add_vline(x=0, line_dash="dash", line_color="gray", line_width=1)

    # 投組平均整列灰底凸顯
    avg_idx = all_labels.index("★ 投組平均")
    fig.add_hrect(
        y0=avg_idx - 0.5, y1=avg_idx + 0.5,
        fillcolor="rgba(180,180,180,0.15)",
        line_width=0,
        layer="below",
    )
    # 投組平均下方分隔線
    fig.add_shape(
        type="line",
        x0=0, x1=1, xref="paper",
        y0=avg_idx - 0.5, y1=avg_idx - 0.5,
        line=dict(color="rgba(200,200,200,0.5)", width=1.5, dash="dot"),
    )
    fig.update_layout(
        template="plotly_dark",
        height=500,
        margin=dict(l=10, r=80, t=10, b=20),
        xaxis_title="報酬率 (%)",
    )
    st.plotly_chart(fig, use_container_width=True)

    st.caption(f"最後更新：{now}　｜　每 60 秒自動刷新")

    st.divider()

    # ── K 線圖 ───────────────────────────────────────────────
    st.subheader("K 線圖")

    # 依報酬率排序後的按鈕（直列，由高到低）
    sorted_codes = [row["股票"].split()[0] for _, row in df.iterrows()]
    sorted_rets  = {row["股票"].split()[0]: row["報酬率(%)"] for _, row in df.iterrows()}
    if st.session_state.get("kline_stock") is None and sorted_codes:
        st.session_state["kline_stock"] = sorted_codes[0]
    btn_col, chart_col = st.columns([1, 6])
    with btn_col:
        for code in sorted_codes:
            name    = STOCKS.get(code, code)
            ret_val = sorted_rets.get(code, 0)
            ret_sign = "+" if ret_val >= 0 else ""
            label   = f"{code} {name}  ({ret_sign}{ret_val:.2f}%)"
            if st.button(label, key=f"btn_{code}", use_container_width=True):
                st.session_state["kline_stock"] = code

    with chart_col:
        sel = st.session_state.get("kline_stock")
        if sel:
            df_k = fetch_kline_ohlc(sel)
            if df_k is not None and not df_k.empty:
                sname      = STOCKS[sel]
                start_ts   = pd.Timestamp(START)
                df_before  = df_k[df_k.index < start_ts].dropna(subset=["Close"])
                base       = float(df_before["Close"].iloc[-1]) if not df_before.empty else float(df_k["Close"].iloc[0])

                # 標題報酬率：歷史週鎖定到 raw_end，當前週用今日收盤
                if raw_end is None:
                    close = float(df_k["Close"].iloc[-1])
                else:
                    df_in_week = df_k[df_k.index <= pd.Timestamp(raw_end)]
                    close = float(df_in_week["Close"].iloc[-1]) if not df_in_week.empty else float(df_k["Close"].iloc[-1])
                ret   = (close - base) / base * 100

                # MA 在全量資料計算後再切片（保證 MA60 正確）
                df_k = df_k.copy()
                df_k["MA5"]  = df_k["Close"].rolling(5).mean()
                df_k["MA10"] = df_k["Close"].rolling(10).mean()
                df_k["MA20"] = df_k["Close"].rolling(20).mean()
                df_k["MA60"] = df_k["Close"].rolling(60).mean()

                # 範圍選擇器（st.radio 取代 rangeselector，避免相容性問題）
                today = datetime.today()
                range_opt = st.radio(
                    "範圍", ["3M", "6M", "1Y"],
                    horizontal=True, index=0,
                    key="kline_range", label_visibility="collapsed",
                )
                cutoff = {
                    "3M": today - timedelta(days=92),
                    "6M": today - timedelta(days=183),
                    "1Y": today - timedelta(days=365),
                }[range_opt]
                df_plot = df_k[df_k.index >= cutoff]
                vol_colors = [
                    "#ef5350" if c >= o else "#26a69a"
                    for c, o in zip(df_plot["Close"], df_plot["Open"])
                ]

                fig_k = make_subplots(
                    rows=2, cols=1,
                    shared_xaxes=True,
                    row_heights=[0.72, 0.28],
                    vertical_spacing=0.02,
                )
                df_plot = df_plot.copy()
                df_plot["pct_chg"] = df_plot["Close"].pct_change() * 100
                cd = list(zip(
                    df_plot["MA5"].fillna("").tolist(),
                    df_plot["MA10"].fillna("").tolist(),
                    df_plot["MA20"].fillna("").tolist(),
                    df_plot["MA60"].fillna("").tolist(),
                    [f"({v:+.2f}%)" if pd.notna(v) and v != "" else "" for v in df_plot["pct_chg"]],
                ))
                fig_k.add_trace(go.Candlestick(
                    x=df_plot.index,
                    open=df_plot["Open"], high=df_plot["High"],
                    low=df_plot["Low"],   close=df_plot["Close"],
                    customdata=cd,
                    increasing=dict(line=dict(color="#FF3B3B"), fillcolor="#ef5350"),
                    decreasing=dict(line=dict(color="#2ECC71"), fillcolor="#26a69a"),
                    name="K線",
                    hovertemplate=(
                        "<b>%{x|%Y-%m-%d}</b>　%{customdata[4]}<br>"
                        "開: %{open:.2f}　高: %{high:.2f}<br>"
                        "低: %{low:.2f}　收: %{close:.2f}<br>"
                        "MA5: %{customdata[0]:.2f}　MA10: %{customdata[1]:.2f}<br>"
                        "MA20: %{customdata[2]:.2f}　MA60: %{customdata[3]:.2f}"
                        "<extra></extra>"
                    ),
                ), row=1, col=1)
                for ma_col, color, width in [
                    ("MA5",  "#FF4444", 1.2),
                    ("MA10", "#2ECC71", 1.2),
                    ("MA20", "#4488FF", 1.5),
                    ("MA60", "#FF8C00", 1.8),
                ]:
                    fig_k.add_trace(go.Scatter(
                        x=df_plot.index, y=df_plot[ma_col],
                        name=ma_col,
                        line=dict(color=color, width=width),
                        connectgaps=False,
                        hoverinfo="skip",
                    ), row=1, col=1)
                fig_k.add_trace(go.Bar(
                    x=df_plot.index, y=df_plot["Volume"],
                    name="成交量",
                    marker_color=vol_colors,
                    showlegend=False,
                    hovertemplate="成交量: %{y:,.0f}<extra></extra>",
                ), row=2, col=1)

                # 所有上榜週（≤ sel_week）都畫色塊；當週額外加起始垂直線
                listing_weeks = [(w, WEEK_DATA[w]["start"]) for w in stock_listing_weeks(sel, sel_week)]
                week_colors = {
                    sel_week: "rgba(200,200,200,0.18)",   # 當週：灰白
                }
                palette = ["rgba(100,180,255,0.22)", "rgba(255,180,80,0.22)", "rgba(180,100,255,0.22)"]
                other_idx = 0
                for w_num, _ in listing_weeks:
                    if w_num != sel_week:
                        week_colors[w_num] = palette[other_idx % len(palette)]
                        other_idx += 1

                for ann_idx, (w_num, w_start) in enumerate(listing_weeks):
                    w_end_raw = WEEK_DATA[w_num].get("end")
                    w_end_str = (
                        END if w_end_raw is None
                        else (pd.Timestamp(w_end_raw) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                    )
                    plot_min = df_plot.index.min()
                    # 區間與 df_plot 有交集才畫
                    if pd.Timestamp(w_end_str) <= plot_min:
                        continue
                    x0 = max(pd.Timestamp(w_start), plot_min).strftime("%Y-%m-%d")
                    fig_k.add_shape(
                        type="rect",
                        x0=x0, x1=w_end_str,
                        y0=0, y1=1,
                        xref="x", yref="y domain",
                        fillcolor=week_colors[w_num],
                        line_width=0, layer="below",
                        row=1, col=1,
                    )
                    label_color = "#CCCCCC" if w_num == sel_week else "#80C4FF"
                    fig_k.add_annotation(
                        x=x0, y=0.98 - ann_idx * 0.08, yref="paper",
                        text=f"W{w_num}", showarrow=False, xanchor="left",
                        font=dict(color=label_color, size=12, family="Arial Black"),
                    )
                    # 當週額外畫起始垂直線
                    if w_num == sel_week and pd.Timestamp(w_start) >= plot_min:
                        fig_k.add_shape(
                            type="line",
                            x0=w_start, x1=w_start,
                            y0=0, y1=1, yref="paper",
                            line=dict(color="rgba(200,200,200,0.8)", width=2, dash="dash"),
                        )

                # 最早追蹤日起報酬（前一交易日收盤為 base）
                visible_weeks  = stock_listing_weeks(sel, sel_week)
                earliest_start = min(WEEK_DATA[w]["start"] for w in visible_weeks)
                if earliest_start != START:
                    e_start_ts    = pd.Timestamp(earliest_start)
                    df_e_before   = df_k[df_k.index < e_start_ts].dropna(subset=["Close"])
                    if not df_e_before.empty:
                        earliest_base = float(df_e_before["Close"].iloc[-1])
                        earliest_ret  = (close - earliest_base) / earliest_base * 100
                        min_week      = min(visible_weeks)
                        title_extra   = f"　｜　最早(W{min_week})起報酬：{earliest_ret:+.2f}%"
                    else:
                        title_extra = ""
                else:
                    title_extra = ""

                fig_k.update_layout(
                    title=f"{sel} {sname}　W{sel_week}追蹤起報酬：{ret:+.2f}%{title_extra}",
                    template="plotly_dark",
                    height=620,
                    xaxis_rangeslider_visible=False,
                    margin=dict(l=10, r=60, t=50, b=20),
                    legend=dict(orientation="h", y=1.04, x=0),
                    xaxis=dict(type="date", tickangle=-45),
                    xaxis2=dict(rangeslider_visible=False),
                    yaxis=dict(side="right"),
                    yaxis2=dict(side="right", showticklabels=False),
                )
                fig_k.update_xaxes(rangebreaks=get_rangebreaks(df_plot))
                st.plotly_chart(fig_k, use_container_width=True)
        else:
            st.info("← 點選左側按鈕查看 K 線圖")

        # ── 大盤走勢圖（chart_col 下方，與個股 K 線同欄對齊） ──
        st.caption("大盤走勢（^TWII）")
        df_twii = fetch_index_kline("^TWII")
        if df_twii is not None and not df_twii.empty:
            df_twii = df_twii.copy()
            df_twii["MA5"]  = df_twii["Close"].rolling(5).mean()
            df_twii["MA10"] = df_twii["Close"].rolling(10).mean()
            df_twii["MA20"] = df_twii["Close"].rolling(20).mean()
            df_twii["MA60"] = df_twii["Close"].rolling(60).mean()

            twii_range = st.radio(
                "大盤範圍", ["3M", "6M", "1Y"],
                horizontal=True, index=0,
                key="twii_range", label_visibility="collapsed",
            )
            twii_cutoff = {
                "3M": datetime.today() - timedelta(days=92),
                "6M": datetime.today() - timedelta(days=183),
                "1Y": datetime.today() - timedelta(days=365),
            }[twii_range]
            df_twii_plot = df_twii[df_twii.index >= twii_cutoff].copy()
            df_twii_vol = df_twii_plot[df_twii_plot["Volume"] > 0]
            twii_vol_colors = [
                "#ef5350" if c >= o else "#26a69a"
                for c, o in zip(df_twii_vol["Close"], df_twii_vol["Open"])
            ]

            fig_twii = make_subplots(
                rows=2, cols=1,
                shared_xaxes=True,
                row_heights=[0.72, 0.28],
                vertical_spacing=0.02,
            )
            df_twii_plot = df_twii_plot.copy()
            df_twii_plot["pct_chg"] = df_twii_plot["Close"].pct_change() * 100
            cd_twii = list(zip(
                df_twii_plot["MA5"].fillna("").tolist(),
                df_twii_plot["MA10"].fillna("").tolist(),
                df_twii_plot["MA20"].fillna("").tolist(),
                df_twii_plot["MA60"].fillna("").tolist(),
                [f"({v:+.2f}%)" if pd.notna(v) and v != "" else "" for v in df_twii_plot["pct_chg"]],
            ))
            fig_twii.add_trace(go.Candlestick(
                x=df_twii_plot.index,
                open=df_twii_plot["Open"], high=df_twii_plot["High"],
                low=df_twii_plot["Low"],   close=df_twii_plot["Close"],
                customdata=cd_twii,
                increasing=dict(line=dict(color="#FF3B3B"), fillcolor="#ef5350"),
                decreasing=dict(line=dict(color="#2ECC71"), fillcolor="#26a69a"),
                name="大盤",
                hovertemplate=(
                    "<b>%{x|%Y-%m-%d}</b>　%{customdata[4]}<br>"
                    "開: %{open:.0f}　高: %{high:.0f}<br>"
                    "低: %{low:.0f}　收: %{close:.0f}<br>"
                    "MA5: %{customdata[0]:.0f}　MA10: %{customdata[1]:.0f}<br>"
                    "MA20: %{customdata[2]:.0f}　MA60: %{customdata[3]:.0f}"
                    "<extra></extra>"
                ),
            ), row=1, col=1)
            for ma_col, color, width in [
                ("MA5",  "#FF4444", 1.2),
                ("MA10", "#2ECC71", 1.2),
                ("MA20", "#4488FF", 1.5),
                ("MA60", "#FF8C00", 1.8),
            ]:
                fig_twii.add_trace(go.Scatter(
                    x=df_twii_plot.index, y=df_twii_plot[ma_col],
                    name=ma_col, line=dict(color=color, width=width),
                    connectgaps=False,
                    hoverinfo="skip",
                ), row=1, col=1)
            fig_twii.add_trace(go.Bar(
                x=df_twii_vol.index, y=df_twii_vol["Volume"],
                name="成交金額", marker_color=twii_vol_colors,
                showlegend=False,
                hovertemplate="成交金額: %{y:,.0f} 億元<extra></extra>",
            ), row=2, col=1)

            # 畫所有週次色塊（≤ sel_week）
            all_weeks_sorted = sorted(w for w in WEEK_DATA if w <= sel_week)
            twii_week_colors = {sel_week: "rgba(200,200,200,0.18)"}
            twii_palette = ["rgba(100,180,255,0.22)", "rgba(255,180,80,0.22)", "rgba(180,100,255,0.22)"]
            other_idx = 0
            for w in all_weeks_sorted:
                if w != sel_week:
                    twii_week_colors[w] = twii_palette[other_idx % len(twii_palette)]
                    other_idx += 1
            plot_min = df_twii_plot.index.min()
            for ann_idx, w_num in enumerate(all_weeks_sorted):
                w_start = WEEK_DATA[w_num]["start"]
                w_end_raw = WEEK_DATA[w_num].get("end")
                w_end_str = END if w_end_raw is None else (pd.Timestamp(w_end_raw) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                if pd.Timestamp(w_end_str) <= plot_min:
                    continue
                x0 = max(pd.Timestamp(w_start), plot_min).strftime("%Y-%m-%d")
                fig_twii.add_shape(
                    type="rect",
                    x0=x0, x1=w_end_str,
                    y0=0, y1=1,
                    xref="x", yref="y domain",
                    fillcolor=twii_week_colors[w_num],
                    line_width=0, layer="below",
                    row=1, col=1,
                )
                label_color = "#CCCCCC" if w_num == sel_week else "#80C4FF"
                fig_twii.add_annotation(
                    x=x0, y=0.98 - ann_idx * 0.08, yref="paper",
                    text=f"W{w_num}", showarrow=False, xanchor="left",
                    font=dict(color=label_color, size=12, family="Arial Black"),
                )
                if w_num == sel_week and pd.Timestamp(w_start) >= plot_min:
                    fig_twii.add_shape(
                        type="line",
                        x0=w_start, x1=w_start,
                        y0=0, y1=1, yref="paper",
                        line=dict(color="rgba(200,200,200,0.8)", width=2, dash="dash"),
                    )
            fig_twii.update_layout(
                template="plotly_dark",
                height=520,
                xaxis_rangeslider_visible=False,
                margin=dict(l=10, r=60, t=50, b=20),
                legend=dict(orientation="h", y=1.04, x=0),
                xaxis=dict(type="date", tickangle=-45),
                xaxis2=dict(rangeslider_visible=False),
                yaxis=dict(side="right"),
                yaxis2=dict(side="right", showticklabels=False),
            )
            fig_twii.update_xaxes(rangebreaks=get_rangebreaks(df_twii_plot))
            st.plotly_chart(fig_twii, use_container_width=True)
        else:
            st.warning("無法取得大盤資料")

    # 按鈕報酬率著色（紅漲綠跌）
    components.html("""
<script>
(function() {
  function colorBtns() {
    window.parent.document.querySelectorAll('button').forEach(function(btn) {
      var t = btn.innerText || '';
      if (t.includes('(+')) { btn.style.color = '#FF6B6B'; }
      else if (t.includes('(-')) { btn.style.color = '#4ECC71'; }
    });
  }
  colorBtns();
  new MutationObserver(colorBtns).observe(window.parent.document.body,
    {childList: true, subtree: true});
})();
</script>
""", height=1)


if __name__ == "__main__":
    main()
