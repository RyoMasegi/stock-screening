#!/usr/bin/env python3
"""週次フルスキャン: 東証全銘柄を第1段階→第2段階でスクリーニングし、HTMLレポートを
Gmailに添付して送信する。

Windowsタスクスケジューラーから毎週月曜6:00に実行される想定。
失敗時もサイレントにせず、必ずメールで報告する。
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from send_email import send_email  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"

STAGE1_OUT = RESULTS_DIR / "stage1_latest.csv"
STAGE2_OUT = RESULTS_DIR / "stage2_latest.csv"
SERIES_OUT = RESULTS_DIR / "stage2_series_latest.json"
CANDIDATES_LATEST = RESULTS_DIR / "candidates_latest.csv"
REPORT_OUT = RESULTS_DIR / "screening_report_latest.html"


def run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"コマンド失敗: {' '.join(cmd)}\n--- stdout ---\n{result.stdout[-3000:]}\n--- stderr ---\n{result.stderr[-3000:]}")


def main() -> None:
    today = date.today().isoformat()
    today_compact = date.today().strftime("%Y%m%d")
    try:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)

        run([sys.executable, str(SCRIPTS / "stage1_screen.py"),
             "--markets", "prime,standard,growth", "--no-cache", "--out", str(STAGE1_OUT)])

        run([sys.executable, str(SCRIPTS / "stage2_screen.py"),
             "--in", str(STAGE1_OUT), "--out", str(STAGE2_OUT), "--series-out", str(SERIES_OUT)])

        stage1_df = pd.read_csv(STAGE1_OUT, dtype={"コード": str})
        stage1_passed = int(stage1_df["第1段階合格"].sum()) if "第1段階合格" in stage1_df.columns else 0

        stage2_df = pd.read_csv(STAGE2_OUT, dtype={"コード": str})
        candidates = stage2_df[stage2_df["第2段階合格"]] if "第2段階合格" in stage2_df.columns else stage2_df.iloc[0:0]

        # 第1段階の詳細指標を候補にマージし、PERの低い順(割安順)に並べる
        merged = candidates.merge(stage1_df, on="コード", suffixes=("", "_s1"))
        candidates_out = merged if not merged.empty else candidates
        if not candidates_out.empty and "PER" in candidates_out.columns:
            candidates_out = candidates_out.sort_values("PER")

        candidates_out.to_csv(CANDIDATES_LATEST, index=False, encoding="utf-8-sig")

        run([sys.executable, str(SCRIPTS / "generate_report.py"),
             "--stage1", str(STAGE1_OUT), "--stage2", str(STAGE2_OUT), "--series", str(SERIES_OUT),
             "--stage1-excluded-log", str(RESULTS_DIR / f"stage1_excluded_{today_compact}.log"),
             "--stage2-excluded-log", str(RESULTS_DIR / f"stage2_excluded_{today_compact}.log"),
             "--out", str(REPORT_OUT)])

        body = (
            f"週次フルスキャン結果 ({today})\n\n"
            f"第1段階通過: {stage1_passed}銘柄 / 第2段階評価: {len(stage2_df)}銘柄 / 最終候補: {len(candidates_out)}銘柄\n\n"
            f"詳細は添付のHTMLレポートを参照してください(銘柄ごとの条件チェック表・10期推移グラフ付き)。"
        )
        send_email(f"【株スクリーニング】週次フルスキャン結果 {today}", body, attachment_path=REPORT_OUT)
        print("完了しました。")

    except Exception as exc:  # noqa: BLE001
        try:
            send_email(
                f"【株スクリーニング】週次フルスキャン エラー {today}",
                f"週次フルスキャンの実行中にエラーが発生しました。\n\n{exc}",
            )
        except Exception as mail_exc:  # noqa: BLE001
            print(f"エラー通知メールの送信にも失敗しました: {mail_exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
