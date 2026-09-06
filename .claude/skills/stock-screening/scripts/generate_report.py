#!/usr/bin/env python3
"""週次フルスキャンの結果を、メール添付用の自己完結型HTMLレポートに整形する。

内容:
  - 絞り込みファネル(評価対象→第1段階合格→第2段階評価→最終候補)のサマリー
  - 最終候補一覧(PERの低い順)と12条件(第1段階7+第2段階5)の合否チェック表
  - 銘柄ごとのEPS/BPS/1株配当 10期推移グラフ(matplotlib、base64埋め込みPNG)
  - データ不足等で評価できなかった銘柄数の注記

画像を base64 でHTML内に埋め込むため、生成される .html ファイル1つだけで
完結し、メールに添付してそのまま開ける。
"""
from __future__ import annotations

import argparse
import base64
import io
import json
from datetime import date
from html import escape
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import yfinance as yf

matplotlib.rcParams["font.family"] = ["Yu Gothic", "Meiryo", "MS Gothic", "sans-serif"]
matplotlib.rcParams["axes.unicode_minus"] = False

STAGE1_CONDS = [
    ("cond:PER<=12", "PER12倍以下"),
    ("cond:PBR<=1.3", "PBR1.3倍以下"),
    ("cond:ROE>=7%", "ROE7%以上"),
    ("cond:ROA>=3%", "ROA3%以上"),
    ("cond:配当利回り>=3%", "配当利回り3%以上"),
    ("cond:自己資本比率>=35%", "自己資本比率35%以上"),
    ("cond:時価総額>=100億円", "時価総額100億円以上"),
    ("cond:直近営業日に取引あり", "直近営業日に取引あり"),
]
STAGE2_CONDS = [
    ("cond:EPS10期マイナスなし", "EPS赤字なし"),
    ("cond:今期EPS>=9期前x2", "EPS2倍成長"),
    ("cond:BPS10期連続増額", "BPS10期連続増額"),
    ("cond:減配2回未満", "減配1回まで"),
    ("cond:無配転落なし", "無配転落なし"),
]

CHART_COLORS = {"eps": "#4C72B0", "bps": "#55A868", "dps": "#C44E52"}


def fetch_price_series(code: str, year_labels: list[str]) -> dict[str, float]:
    """各決算期末日に最も近い取引日の終値を10年分の株価履歴から取得する。

    yfinanceの株価履歴は財務諸表と違い長期間さかのぼれるため、
    stage2の10期分の年度ラベル(例: "2017/03")に対応する終値を個別に引き当てる。
    取得できない場合は空辞書を返し、呼び出し側でグラフ・PERレンジ計算を
    スキップできるようにする(ネットワーク障害等でレポート全体を止めない)。
    """
    try:
        hist = yf.Ticker(f"{code}.T").history(period="10y")
    except Exception:  # noqa: BLE001
        return {}
    if hist is None or hist.empty:
        return {}
    hist = hist.sort_index()
    idx = hist.index.tz_localize(None) if hist.index.tz is not None else hist.index

    prices: dict[str, float] = {}
    for label in year_labels:
        try:
            y, m = label.split("/")
            target = pd.Timestamp(year=int(y), month=int(m), day=1) + pd.offsets.MonthEnd(0)
        except ValueError:
            continue
        pos = idx.get_indexer([target], method="nearest")
        if pos[0] == -1:
            continue
        prices[label] = float(hist["Close"].iloc[pos[0]])
    return prices


def per_range(years: list[str], eps_list: list[float], price_map: dict[str, float]):
    """10期分の(期末株価 / 期のEPS)からPERレンジ(最小, 最大)を計算する。EPSが0以下の期は除外。"""
    values = []
    for y, eps in zip(years, eps_list):
        price = price_map.get(y)
        if price is not None and eps and eps > 0:
            values.append(price / eps)
    if not values:
        return None, None
    return min(values), max(values)


def make_chart_b64(series: dict, price_map: dict[str, float]) -> str:
    years = series["years"]
    fig, axes = plt.subplots(1, 3, figsize=(10.5, 2.3))

    axes[0].bar(years, series["eps"], color=CHART_COLORS["eps"])
    axes[0].set_title("EPS(円)", fontsize=9)

    axes[1].bar(years, series["bps"], color=CHART_COLORS["bps"])
    axes[1].set_title("BPS(円)", fontsize=9)

    ax = axes[2]
    ax.bar(years, series["dps"], color=CHART_COLORS["dps"], alpha=0.55)
    ax.set_title("株価(線)・配当(棒)", fontsize=9)
    price_values = [price_map.get(y) for y in years]
    plot_years = [y for y, p in zip(years, price_values) if p is not None]
    plot_prices = [p for p in price_values if p is not None]
    if plot_prices:
        ax_price = ax.twinx()
        ax_price.plot(plot_years, plot_prices, color="#333333", marker="o", markersize=3, linewidth=1.3)
        ax_price.set_ylim(bottom=0)  # 0始まりにしないと株価の相対的な下落と誤認されるため
        ax_price.tick_params(axis="y", labelsize=6)
        ax_price.spines["top"].set_visible(False)

    for a in axes:
        a.tick_params(axis="x", rotation=90, labelsize=6)
        a.tick_params(axis="y", labelsize=6)
        a.spines["top"].set_visible(False)
        a.spines["right"].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def cond_cell(value) -> str:
    ok = bool(value) if not pd.isna(value) else False
    cls = "ok" if ok else "ng"
    mark = "○" if ok else "×"
    return f'<td class="{cls}">{mark}</td>'


def build_html(stage1_df, stage2_df, candidates, series_map, counts, excluded_counts) -> str:
    today = date.today().isoformat()

    funnel_html = f"""
    <div class="funnel">
      <div class="funnel-step"><div class="n">{counts['universe']}</div><div class="l">評価対象銘柄</div></div>
      <div class="arrow">→</div>
      <div class="funnel-step"><div class="n">{counts['stage1_passed']}</div><div class="l">第1段階合格</div></div>
      <div class="arrow">→</div>
      <div class="funnel-step"><div class="n">{counts['stage2_evaluated']}</div><div class="l">第2段階評価</div></div>
      <div class="arrow">→</div>
      <div class="funnel-step highlight"><div class="n">{counts['final']}</div><div class="l">最終候補</div></div>
    </div>
    """

    cond_headers = "".join(f"<th>{escape(label)}</th>" for _, label in STAGE1_CONDS + STAGE2_CONDS)
    table_rows = []
    for _, row in candidates.iterrows():
        cond_cells = "".join(cond_cell(row.get(key)) for key, _ in STAGE1_CONDS + STAGE2_CONDS)
        table_rows.append(f"""
        <tr>
          <td>{escape(str(row['コード']))}</td>
          <td>{escape(str(row['銘柄名']))}</td>
          <td>{row['PER']:.2f}</td>
          <td>{row['PBR']:.2f}</td>
          <td>{row['ROE%']:.2f}</td>
          <td>{row['ROA%']:.2f}</td>
          <td>{row['配当利回り%']:.2f}</td>
          <td>{row['自己資本比率%']:.2f}</td>
          <td>{row['時価総額(億円)']:.1f}</td>
          <td>{row['EPS成長倍率']:.2f}倍</td>
          {cond_cells}
        </tr>""")

    detail_sections = []
    for _, row in candidates.iterrows():
        code = str(row["コード"])
        chart_b64 = None
        per_line = ""
        if code in series_map:
            series = series_map[code]
            price_map = fetch_price_series(code, series["years"])
            chart_b64 = make_chart_b64(series, price_map)
            p_min, p_max = per_range(series["years"], series["eps"], price_map)
            if p_min is not None:
                per_line = f" / 10年PERレンジ: {p_min:.1f}倍〜{p_max:.1f}倍(現在 {row['PER']:.2f}倍)"
            else:
                per_line = " / 10年PERレンジ: 株価データ取得不可"
        chart_html = (
            f'<img class="chart" src="data:image/png;base64,{chart_b64}" alt="{escape(code)} 10期推移">'
            if chart_b64 else "<p class='muted'>グラフ用データなし</p>"
        )
        detail_sections.append(f"""
        <div class="card">
          <h3>{escape(str(row['銘柄名']))} ({escape(code)})</h3>
          <p class="muted">PER {row['PER']:.2f}倍 / PBR {row['PBR']:.2f}倍 / 配当利回り {row['配当利回り%']:.2f}% /
             時価総額 {row['時価総額(億円)']:.0f}億円 / EPS成長倍率 {row['EPS成長倍率']:.2f}倍(9期前比){per_line}</p>
          {chart_html}
        </div>""")

    excluded_note = (
        f"第1段階でデータ不足等により評価できなかった銘柄: {excluded_counts['stage1']}件 / "
        f"第2段階で評価できなかった銘柄: {excluded_counts['stage2']}件"
    )

    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<title>株スクリーニング週次レポート {today}</title>
<style>
  body {{ font-family: "Yu Gothic","Meiryo",sans-serif; margin: 24px; color: #1a1a1a; background: #fff; }}
  h1 {{ font-size: 20px; }}
  h2 {{ font-size: 16px; margin-top: 32px; border-bottom: 2px solid #333; padding-bottom: 4px; }}
  h3 {{ font-size: 14px; margin-bottom: 4px; }}
  .muted {{ color: #666; font-size: 12px; }}
  .funnel {{ display: flex; align-items: center; gap: 12px; margin: 16px 0; flex-wrap: wrap; }}
  .funnel-step {{ text-align: center; background: #f2f2f2; border-radius: 8px; padding: 10px 16px; min-width: 90px; }}
  .funnel-step.highlight {{ background: #2a6f2a; color: #fff; }}
  .funnel-step .n {{ font-size: 20px; font-weight: bold; }}
  .funnel-step .l {{ font-size: 11px; }}
  .arrow {{ font-size: 18px; color: #999; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 12px; margin-top: 8px; }}
  th, td {{ border: 1px solid #ddd; padding: 5px 7px; text-align: right; white-space: nowrap; }}
  th {{ background: #333; color: #fff; text-align: center; }}
  td:nth-child(2) {{ text-align: left; }}
  td.ok {{ color: #2a6f2a; font-weight: bold; text-align: center; }}
  td.ng {{ color: #b33; font-weight: bold; text-align: center; }}
  .card {{ border: 1px solid #ddd; border-radius: 8px; padding: 12px 16px; margin: 12px 0; }}
  .chart {{ max-width: 100%; }}
  footer {{ margin-top: 32px; font-size: 11px; color: #888; border-top: 1px solid #ddd; padding-top: 8px; }}
</style></head>
<body>
  <h1>株スクリーニング 週次フルスキャンレポート ({today})</h1>
  {funnel_html}

  <h2>最終候補一覧(PERの低い順)</h2>
  <table>
    <thead><tr>
      <th>コード</th><th>銘柄名</th><th>PER</th><th>PBR</th><th>ROE%</th><th>ROA%</th>
      <th>配当利回り%</th><th>自己資本比率%</th><th>時価総額(億円)</th><th>EPS成長倍率</th>
      {cond_headers}
    </tr></thead>
    <tbody>{''.join(table_rows)}</tbody>
  </table>

  <h2>銘柄別 10期推移(EPS・BPS・株価/配当・PERレンジ)</h2>
  {''.join(detail_sections)}

  <footer>
    {excluded_note}<br>
    データソース: Yahoo Finance(第1段階) / IR BANK(第2段階)。株式分割・併合の調整は行っていません。
  </footer>
</body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="週次スクリーニング結果のHTMLレポートを生成")
    parser.add_argument("--stage1", required=True)
    parser.add_argument("--stage2", required=True)
    parser.add_argument("--series", required=True, help="stage2_screen.py --series-out の出力JSON")
    parser.add_argument("--stage1-excluded-log", default=None)
    parser.add_argument("--stage2-excluded-log", default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    stage1_df = pd.read_csv(args.stage1, dtype={"コード": str})
    stage2_df = pd.read_csv(args.stage2, dtype={"コード": str})
    series_map = json.loads(Path(args.series).read_text(encoding="utf-8")) if Path(args.series).exists() else {}

    candidates = stage2_df[stage2_df["第2段階合格"]].merge(stage1_df, on="コード", suffixes=("", "_s1")) if not stage2_df.empty else stage2_df.iloc[0:0]
    if "銘柄名_s1" in candidates.columns:
        candidates = candidates.drop(columns=["銘柄名_s1"])
    if not candidates.empty:
        candidates = candidates.sort_values("PER")

    counts = {
        "universe": len(stage1_df),
        "stage1_passed": int(stage1_df["第1段階合格"].sum()) if "第1段階合格" in stage1_df.columns else 0,
        "stage2_evaluated": len(stage2_df),
        "final": len(candidates),
    }

    def count_lines(p):
        if p and Path(p).exists():
            text = Path(p).read_text(encoding="utf-8").strip()
            return len(text.splitlines()) if text else 0
        return 0

    excluded_counts = {
        "stage1": count_lines(args.stage1_excluded_log),
        "stage2": count_lines(args.stage2_excluded_log),
    }

    html = build_html(stage1_df, stage2_df, candidates, series_map, counts, excluded_counts)
    Path(args.out).write_text(html, encoding="utf-8")
    print(f"レポートを生成しました: {args.out}")


if __name__ == "__main__":
    main()
