#!/usr/bin/env python3
"""第2段階スクリーニング: IR BANK(irbank.net)の決算まとめページからEPS/BPS/1株配当の
10期推移を取得し、以下の条件を判定する。

  - EPS10期推移にマイナス転落(赤字)がないこと
  - 今期EPSが9期前の2倍以上あること
  - BPSが10期すべて増額であること
  - 1株配当10期推移に減配が2度以上ないこと(1回までは許容)
  - 過去10期に無配転落がないこと

第1段階(stage1_screen.py)の合格銘柄のみを対象とする想定。
irbank.net の robots.txt は全面クロールを許可しているが、礼儀として
リクエスト間にスリープを入れる。データは data/cache/irbank/ にキャッシュする。
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[4]
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache" / "irbank"
RESULTS_DIR = ROOT / "results"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

STAGE2_COLUMNS = [
    "コード", "銘柄名", "対象期間", "EPS(今期)", "EPS(9期前)", "EPS成長倍率",
    "BPS(今期)", "BPS(9期前)", "一株配当(今期)", "減配回数", "第2段階合格",
    "cond:EPS10期マイナスなし", "cond:今期EPS>=9期前x2", "cond:BPS10期連続増額",
    "cond:減配2回未満", "cond:無配転落なし",
]


def fetch_results_html(code: str, use_cache: bool = True) -> str:
    cache_path = CACHE_DIR / f"{code}.html"
    if use_cache and cache_path.exists():
        return cache_path.read_text(encoding="utf-8")
    url = f"https://irbank.net/{code}/results"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        html = r.read().decode("utf-8", errors="ignore")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(html, encoding="utf-8")
    return html


def parse_num(text: str):
    text = (text or "").strip()
    if text in ("", "-", "―", "‐", "…"):
        return None
    if "赤字" in text:
        return -1.0
    text = text.lstrip("*").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def extract_series(soup: BeautifulSoup, header_keyword: str) -> dict[str, float | None]:
    """ヘッダー行に header_keyword を含むtableを探し、{年度: 値} の辞書を返す
    (予想行・年度欠損行は除外)。見つからなければ None。"""
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        header_cells = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
        if header_keyword not in header_cells:
            continue
        col_idx = header_cells.index(header_keyword)
        series: dict[str, float | None] = {}
        for r in rows[1:]:
            cells = [c.get_text(strip=True) for c in r.find_all(["th", "td"])]
            if len(cells) <= col_idx:
                continue
            year_label = cells[0]
            if not year_label or year_label == header_cells[0]:
                continue  # 表末尾に繰り返されるヘッダー行をスキップ
            if "予" in year_label:
                continue  # 予想期はスキップ(実績のみ対象)
            series[year_label] = parse_num(cells[col_idx])
        return series
    return None


def evaluate(code: str, name: str, html: str):
    soup = BeautifulSoup(html, "lxml")
    eps_series = extract_series(soup, "EPS")
    bps_series = extract_series(soup, "BPS")
    dps_series = extract_series(soup, "一株配当")

    if eps_series is None or bps_series is None or dps_series is None:
        missing = [n for n, s in [("EPS", eps_series), ("BPS", bps_series), ("一株配当", dps_series)] if s is None]
        return None, f"{code} {name}: ページ構造からテーブルを特定できない({','.join(missing)}) — 対象外"

    common_years = sorted(set(eps_series) & set(bps_series) & set(dps_series), reverse=True)
    if len(common_years) < 10:
        return None, f"{code} {name}: 3指標共通の年次データが10期分ない(取得{len(common_years)}期) — 対象外"

    years = common_years[:10]
    eps_list = [eps_series[y] for y in years]
    bps_list = [bps_series[y] for y in years]
    dps_list = [dps_series[y] for y in years]

    if any(v is None for v in eps_list + bps_list + dps_list):
        return None, f"{code} {name}: EPS/BPS/配当のいずれかが直近10期内で欠損 — 対象外"

    decreases = sum(1 for i in range(len(dps_list) - 1) if dps_list[i] < dps_list[i + 1])

    checks = {
        "EPS10期マイナスなし": all(v >= 0 for v in eps_list),
        "今期EPS>=9期前x2": eps_list[9] > 0 and eps_list[0] >= 2 * eps_list[9],
        "BPS10期連続増額": all(bps_list[i] > bps_list[i + 1] for i in range(len(bps_list) - 1)),
        "減配2回未満": decreases < 2,
        "無配転落なし": all(v > 0 for v in dps_list),
    }
    passed = all(checks.values())

    result = {
        "コード": code,
        "銘柄名": name,
        "対象期間": f"{years[-1]}〜{years[0]}",
        "EPS(今期)": eps_list[0],
        "EPS(9期前)": eps_list[9],
        "EPS成長倍率": round(eps_list[0] / eps_list[9], 2) if eps_list[9] else None,
        "BPS(今期)": bps_list[0],
        "BPS(9期前)": bps_list[9],
        "一株配当(今期)": dps_list[0],
        "減配回数": decreases,
        "第2段階合格": passed,
    }
    result.update({f"cond:{k}": v for k, v in checks.items()})
    return result, None


def main() -> None:
    parser = argparse.ArgumentParser(description="第2段階: IR BANKのEPS/BPS/配当10期推移でのスクリーニング")
    parser.add_argument("--in", dest="in_path", required=True, help="第1段階結果CSV(stage1_screen.pyの出力)")
    parser.add_argument("--no-cache", action="store_true", help="IR BANKキャッシュを使わず再取得する")
    parser.add_argument("--sleep", type=float, default=1.5, help="リクエスト間のスリープ秒数(サイトへの配慮)")
    parser.add_argument("--out", default=None, help="出力CSVパス")
    args = parser.parse_args()

    stage1 = pd.read_csv(args.in_path, dtype={"コード": str})
    if "第1段階合格" in stage1.columns:
        stage1 = stage1[stage1["第1段階合格"]]

    total = len(stage1)
    print(f"第2段階の対象(第1段階合格)銘柄数: {total}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else RESULTS_DIR / f"stage2_{date.today():%Y%m%d}.csv"
    log_path = RESULTS_DIR / f"stage2_excluded_{date.today():%Y%m%d}.log"

    # 途中終了しても結果が失われないよう、1銘柄ごとに追記・flushする。
    passed_count = 0
    result_count = 0
    with open(out_path, "w", newline="", encoding="utf-8-sig") as out_f, \
         open(log_path, "w", encoding="utf-8") as log_f:
        writer = csv.DictWriter(out_f, fieldnames=STAGE2_COLUMNS)
        writer.writeheader()

        for i, row in enumerate(stage1.to_dict("records")):
            code, name = row["コード"], row["銘柄名"]
            try:
                html = fetch_results_html(code, use_cache=not args.no_cache)
                res, reason = evaluate(code, name, html)
            except Exception as exc:  # noqa: BLE001
                res, reason = None, f"{code} {name}: 取得/解析エラー {exc}"

            if res is not None:
                writer.writerow(res)
                out_f.flush()
                result_count += 1
                if res["第2段階合格"]:
                    passed_count += 1
            if reason:
                log_f.write(reason + "\n")
                log_f.flush()
            print(f"  [{i + 1}/{total}] {code} {name}: {'済' if res is None else ('合格' if res['第2段階合格'] else '不合格')}")
            time.sleep(args.sleep)

    print(f"\n最終合格(第1段階+第2段階すべて合致): {passed_count}銘柄 / 第2段階評価対象: {result_count}銘柄")
    print(f"結果を保存しました: {out_path}")
    print(f"除外理由ログ: {log_path}")


if __name__ == "__main__":
    main()
