"""JPX公式サイトから東証上場銘柄一覧(data_j.xlsx)を取得するモジュール。

認証不要。JPXが毎月更新して公開している「東証上場銘柄一覧」を使い、
プライム/スタンダード/グロース(内国株式)の銘柄コード・銘柄名一覧を取得する。
ファイル名のURLはJPX側の都合で変わることがあるため、まず一覧ページ
(01.html)からリンクを動的に取得し、それが失敗した場合のみ既知のURLに
フォールバックする。
"""
from __future__ import annotations

import re
import urllib.request
from pathlib import Path

import pandas as pd

LISTING_PAGE = "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"
FALLBACK_XLSX_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

MARKET_SEGMENT_KEYWORDS = {
    "prime": "プライム",
    "standard": "スタンダード",
    "growth": "グロース",
}


def _resolve_xlsx_url() -> str:
    try:
        req = urllib.request.Request(LISTING_PAGE, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode("utf-8", errors="ignore")
        links = re.findall(r'href="([^"]+data_j[^"]*\.xlsx?)"', html, re.IGNORECASE)
        if links:
            url = links[0]
            if url.startswith("/"):
                url = "https://www.jpx.co.jp" + url
            return url
    except Exception:
        pass
    return FALLBACK_XLSX_URL


def download_xlsx(dest: Path) -> Path:
    url = _resolve_xlsx_url()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def load_universe(cache_path: Path, markets: list[str], refresh: bool = False) -> pd.DataFrame:
    """銘柄コード・銘柄名・市場区分のDataFrameを返す。列: Code, CompanyName, MarketSegment"""
    if refresh or not cache_path.exists():
        download_xlsx(cache_path)

    df = pd.read_excel(cache_path)
    # 実列名: 日付, コード, 銘柄名, 市場・商品区分, 33業種コード, 33業種区分, 17業種コード, 17業種区分, 規模コード, 規模区分
    df = df.rename(columns={
        df.columns[1]: "Code",
        df.columns[2]: "CompanyName",
        df.columns[3]: "MarketSegment",
    })
    df["Code"] = df["Code"].astype(str)

    # 内国株式(プライム/スタンダード/グロース)のみを対象とし、ETF/REIT/PRO Market等は除外する
    keywords = [MARKET_SEGMENT_KEYWORDS[m] for m in markets if m in MARKET_SEGMENT_KEYWORDS]
    mask = df["MarketSegment"].apply(lambda v: any(k in str(v) for k in keywords) and "内国株式" in str(v))
    df = df[mask]
    return df[["Code", "CompanyName", "MarketSegment"]].reset_index(drop=True)
