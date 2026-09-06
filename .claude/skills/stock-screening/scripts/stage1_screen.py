#!/usr/bin/env python3
"""第1段階スクリーニング: Yahoo Finance(yfinance)のスナップショット指標で東証全銘柄をふるいにかける。

条件:
  PER<=12, PBR<=1.3, ROE>=7%, ROA>=3%, 配当利回り>=3%,
  自己資本比率>=35%, 時価総額>=100億円

銘柄一覧はJPX公式の東証上場銘柄一覧(data_j.xlsx)から取得する(認証不要)。
1銘柄につき yfinance の .info と 年次貸借対照表 の2リクエストを行うため、
全市場(4000銘柄弱)を回すと数十分〜1時間程度かかる。取得結果は
data/cache/yfinance/ にキャッシュし、再実行時は既存キャッシュを使う
(--no-cache で強制再取得)。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jpx_universe import load_universe  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]
DATA_DIR = ROOT / "data"
UNIVERSE_CACHE = DATA_DIR / "cache" / "jpx" / "data_j.xlsx"
INFO_CACHE_DIR = DATA_DIR / "cache" / "yfinance" / "info"
BS_CACHE_DIR = DATA_DIR / "cache" / "yfinance" / "balance_sheet"
RESULTS_DIR = ROOT / "results"

STAGE1_COLUMNS = [
    "コード", "銘柄名", "市場区分", "株価", "PER", "PBR", "ROE%", "ROA%",
    "配当利回り%", "自己資本比率%", "時価総額(億円)", "最終取引日", "第1段階合格",
    "cond:PER<=12", "cond:PBR<=1.3", "cond:ROE>=7%", "cond:ROA>=3%",
    "cond:配当利回り>=3%", "cond:自己資本比率>=35%", "cond:時価総額>=100億円",
    "cond:直近営業日に取引あり",
]

DEFAULT_CRITERIA = {
    "per_max": 12,
    "pbr_max": 1.3,
    "roe_min": 7,
    "roa_min": 3,
    "div_yield_min": 3,
    "equity_ratio_min": 35,
    "market_cap_min": 10_000_000_000,  # 100億円
}


def to_float(v):
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def fetch_info(code: str, use_cache: bool) -> dict | None:
    cache_path = INFO_CACHE_DIR / f"{code}.json"
    if use_cache and cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    ticker = yf.Ticker(f"{code}.T")
    info = ticker.info
    INFO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(info, ensure_ascii=False, default=str), encoding="utf-8")
    return info


def fetch_equity_ratio(code: str, use_cache: bool) -> float | None:
    """直近本決算の 自己資本(株主資本)/総資産 を年次貸借対照表から計算する。"""
    cache_path = BS_CACHE_DIR / f"{code}.json"
    if use_cache and cache_path.exists():
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        ticker = yf.Ticker(f"{code}.T")
        bs = ticker.get_balance_sheet(freq="yearly")
        data = {}
        if bs is not None and not bs.empty:
            latest_col = bs.columns[0]
            for key in ("StockholdersEquity", "TotalAssets", "CommonStockEquity"):
                if key in bs.index:
                    data[key] = to_float(bs.loc[key, latest_col])
        BS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    equity = data.get("StockholdersEquity") or data.get("CommonStockEquity")
    total_assets = data.get("TotalAssets")
    if equity is None or not total_assets:
        return None
    return equity / total_assets * 100


def evaluate(code: str, name: str, market: str, criteria: dict, use_cache: bool):
    try:
        info = fetch_info(code, use_cache)
    except Exception as exc:  # noqa: BLE001
        return None, f"{code} {name}: info取得エラー {exc}"

    if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
        return None, f"{code} {name}: 株価情報が取得できない(上場廃止/取得失敗の可能性) — 対象外"

    if not name:
        name = info.get("longName") or info.get("shortName") or code

    per = to_float(info.get("trailingPE"))
    pbr = to_float(info.get("priceToBook"))
    roe = to_float(info.get("returnOnEquity"))
    roa = to_float(info.get("returnOnAssets"))
    # yfinanceのdividendYieldは常に%表記の数値(例:3.2 = 3.2%)で返る(実測1600銘柄超で確認済み、
    # 比率表記との混在はない)。かつて0〜1なら100倍する変換を入れていたが、これは低利回り銘柄
    # (例:実際0.85%)を誤って85%に変換してしまう明確なバグだったため撤廃した。
    div_yield = to_float(info.get("dividendYield")) or 0.0
    market_cap = to_float(info.get("marketCap"))

    if roe is not None:
        roe *= 100
    if roa is not None:
        roa *= 100

    try:
        equity_ratio = fetch_equity_ratio(code, use_cache)
    except Exception as exc:  # noqa: BLE001
        return None, f"{code} {name}: 貸借対照表取得エラー {exc}"

    if per is None or pbr is None or roe is None or roa is None or equity_ratio is None or market_cap is None:
        missing = [
            n for n, v in [
                ("PER", per), ("PBR", pbr), ("ROE", roe), ("ROA", roa),
                ("自己資本比率", equity_ratio), ("時価総額", market_cap),
            ] if v is None
        ]
        return None, f"{code} {name}: 指標欠損({','.join(missing)}) — 対象外"

    checks = {
        "PER<=12": per > 0 and per <= criteria["per_max"],
        "PBR<=1.3": pbr > 0 and pbr <= criteria["pbr_max"],
        "ROE>=7%": roe >= criteria["roe_min"],
        "ROA>=3%": roa >= criteria["roa_min"],
        "配当利回り>=3%": div_yield >= criteria["div_yield_min"],
        "自己資本比率>=35%": equity_ratio >= criteria["equity_ratio_min"],
        "時価総額>=100億円": market_cap >= criteria["market_cap_min"],
    }
    passed = all(checks.values())

    market_time = info.get("regularMarketTime")
    last_quote_date = (
        datetime.fromtimestamp(market_time, tz=timezone.utc).date().isoformat()
        if market_time else None
    )

    result = {
        "コード": code,
        "銘柄名": name,
        "市場区分": market,
        "株価": to_float(info.get("currentPrice") or info.get("regularMarketPrice")),
        "PER": round(per, 2),
        "PBR": round(pbr, 2),
        "ROE%": round(roe, 2),
        "ROA%": round(roa, 2),
        "配当利回り%": round(div_yield, 2),
        "自己資本比率%": round(equity_ratio, 2),
        "時価総額(億円)": round(market_cap / 1e8, 1),
        "最終取引日": last_quote_date,
        "第1段階合格": passed,
    }
    result.update({f"cond:{k}": v for k, v in checks.items()})
    return result, None


def main() -> None:
    parser = argparse.ArgumentParser(description="第1段階: Yahoo Financeスナップショット指標でのスクリーニング")
    parser.add_argument("--markets", default="prime,standard,growth", help="対象市場区分(カンマ区切り): prime,standard,growth")
    parser.add_argument("--codes", default=None, help="JPX全銘柄一覧の代わりに、指定した証券コードのみを対象にする(カンマ区切り)。前回候補の再チェック用。")
    parser.add_argument("--limit", type=int, default=None, help="テスト用: 対象銘柄数を制限")
    parser.add_argument("--no-cache", action="store_true", help="yfinanceキャッシュを使わず再取得する")
    parser.add_argument("--refresh-universe", action="store_true", help="JPX銘柄一覧ファイルを再ダウンロードする")
    parser.add_argument("--sleep", type=float, default=0.4, help="APIリクエスト間のスリープ秒数")
    parser.add_argument("--out", default=None, help="出力CSVパス")
    args = parser.parse_args()

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        universe = pd.DataFrame({"Code": codes, "CompanyName": [""] * len(codes), "MarketSegment": [""] * len(codes)})
    else:
        markets = [m.strip() for m in args.markets.split(",") if m.strip()]
        universe = load_universe(UNIVERSE_CACHE, markets, refresh=args.refresh_universe)
    if args.limit:
        universe = universe.head(args.limit)

    total = len(universe)
    print(f"対象銘柄数: {total}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else RESULTS_DIR / f"stage1_{date.today():%Y%m%d}.csv"
    log_path = RESULTS_DIR / f"stage1_excluded_{date.today():%Y%m%d}.log"

    # 全銘柄評価には長時間かかり得るため、タスクスケジューラーのタイムアウト等で
    # 途中終了しても結果が失われないよう、1銘柄ごとに追記・flushする。
    # (直近営業日チェックは全銘柄評価後でないと基準日が定まらないため、all_rowsに
    #  貯めておき完走後に再判定・再書き込みする。途中終了時はこの再判定なしの
    #  結果がそのまま残る。)
    all_rows: list[dict] = []
    result_count = 0
    with open(out_path, "w", newline="", encoding="utf-8-sig") as out_f, \
         open(log_path, "w", encoding="utf-8") as log_f:
        writer = csv.DictWriter(out_f, fieldnames=STAGE1_COLUMNS)
        writer.writeheader()

        for i, row in enumerate(universe.to_dict("records")):
            code, name, market = row["Code"], row["CompanyName"], row["MarketSegment"]
            res, reason = evaluate(code, name, market, DEFAULT_CRITERIA, use_cache=not args.no_cache)
            if res is not None:
                writer.writerow(res)
                out_f.flush()
                result_count += 1
                all_rows.append(res)
            if reason:
                log_f.write(reason + "\n")
                log_f.flush()
            if (i + 1) % 50 == 0:
                print(f"  {i + 1}/{total} 処理済み...")
            time.sleep(args.sleep)

    # 直近営業日に取引が反映されていない銘柄(売買停止・上場廃止手続き中の疑い)を検出する。
    # JPX公式銘柄一覧は削除の反映が遅れることがあるため、評価した銘柄群の中で最も多い
    # 「最終取引日」(=通常の最終営業日)を基準日とし、そこから大きく遅れている銘柄を
    # 「第1段階合格」から除外する。あくまでヒューリスティックであり、閾値内の1〜数日の
    # 未約定は流動性の低い銘柄でも起こり得るため、それ自体は除外しない。
    STALE_TOLERANCE_DAYS = 3
    dated = [r for r in all_rows if r.get("最終取引日")]
    stale_excluded = []
    if dated:
        mode_date_str = Counter(r["最終取引日"] for r in dated).most_common(1)[0][0]
        mode_date = date.fromisoformat(mode_date_str)
        for r in all_rows:
            last = r.get("最終取引日")
            fresh = True
            if last:
                gap = (mode_date - date.fromisoformat(last)).days
                fresh = gap <= STALE_TOLERANCE_DAYS
            r["cond:直近営業日に取引あり"] = fresh
            if not fresh:
                r["第1段階合格"] = False
                stale_excluded.append(f"{r['コード']} {r['銘柄名']}: 最終取引日{last}が基準日{mode_date_str}よりも古い(取引停止/上場廃止の疑い) — 対象外")

        with open(out_path, "w", newline="", encoding="utf-8-sig") as out_f:
            writer = csv.DictWriter(out_f, fieldnames=STAGE1_COLUMNS)
            writer.writeheader()
            for r in all_rows:
                writer.writerow(r)

        if stale_excluded:
            with open(log_path, "a", encoding="utf-8") as log_f:
                log_f.write("\n".join(stale_excluded) + "\n")

    passed_count = sum(1 for r in all_rows if r["第1段階合格"])

    print(f"\n第1段階合格: {passed_count}銘柄 / 評価対象: {result_count}銘柄 / 取引停止疑いで除外: {len(stale_excluded)}銘柄")
    print(f"結果を保存しました: {out_path}")
    print(f"除外理由ログ: {log_path}")


if __name__ == "__main__":
    main()
