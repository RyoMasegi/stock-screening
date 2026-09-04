#!/usr/bin/env python3
"""第1段階スクリーニング: Yahoo Finance(yfinance)のスナップショット指標で東証全銘柄をふるいにかける。

条件:
  PER<=12, PBR<=1.3, ROE>=7%, ROA>=3%, 配当利回り>=3%,
  自己資本比率>=35%, 時価総額>=1000億円

銘柄一覧はJPX公式の東証上場銘柄一覧(data_j.xlsx)から取得する(認証不要)。
1銘柄につき yfinance の .info と 年次貸借対照表 の2リクエストを行うため、
全市場(4000銘柄弱)を回すと数十分〜1時間程度かかる。取得結果は
data/cache/yfinance/ にキャッシュし、再実行時は既存キャッシュを使う
(--no-cache で強制再取得)。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
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

DEFAULT_CRITERIA = {
    "per_max": 12,
    "pbr_max": 1.3,
    "roe_min": 7,
    "roa_min": 3,
    "div_yield_min": 3,
    "equity_ratio_min": 35,
    "market_cap_min": 100_000_000_000,  # 1000億円
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
    div_yield = to_float(info.get("dividendYield")) or 0.0
    market_cap = to_float(info.get("marketCap"))

    if roe is not None:
        roe *= 100
    if roa is not None:
        roa *= 100
    # yfinanceのdividendYieldは環境により「%表記の数値(例:3.2)」と「比率(例:0.032)」が
    # 混在することがあるため、0〜1の範囲ならパーセントに変換する。
    if div_yield and div_yield < 1:
        div_yield *= 100

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
        "時価総額>=1000億円": market_cap >= criteria["market_cap_min"],
    }
    passed = all(checks.values())

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

    results = []
    excluded_log = []
    for i, row in enumerate(universe.to_dict("records")):
        code, name, market = row["Code"], row["CompanyName"], row["MarketSegment"]
        res, reason = evaluate(code, name, market, DEFAULT_CRITERIA, use_cache=not args.no_cache)
        if res is not None:
            results.append(res)
        if reason:
            excluded_log.append(reason)
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{total} 処理済み...")
        time.sleep(args.sleep)

    df = pd.DataFrame(results)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else RESULTS_DIR / f"stage1_{date.today():%Y%m%d}.csv"
    df.to_csv(out_path, index=False, encoding="utf-8-sig")

    passed = df[df["第1段階合格"]] if not df.empty and "第1段階合格" in df.columns else df.iloc[0:0]
    print(f"\n第1段階合格: {len(passed)}銘柄 / 評価対象: {len(df)}銘柄 / 除外: {len(excluded_log)}銘柄")
    print(f"結果を保存しました: {out_path}")

    log_path = RESULTS_DIR / f"stage1_excluded_{date.today():%Y%m%d}.log"
    log_path.write_text("\n".join(excluded_log), encoding="utf-8")
    print(f"除外理由ログ: {log_path}")


if __name__ == "__main__":
    main()
