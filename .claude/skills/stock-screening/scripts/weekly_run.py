#!/usr/bin/env python3
"""週次フルスキャン: 東証全銘柄を第1段階→第2段階でスクリーニングし、結果をメール送信する。

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
CANDIDATES_LATEST = RESULTS_DIR / "candidates_latest.csv"

DISPLAY_COLS = ["コード", "銘柄名", "PER", "PBR", "ROE%", "ROA%", "配当利回り%", "自己資本比率%", "時価総額(億円)", "EPS成長倍率", "減配回数"]


def run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"コマンド失敗: {' '.join(cmd)}\n--- stdout ---\n{result.stdout[-3000:]}\n--- stderr ---\n{result.stderr[-3000:]}")


def main() -> None:
    today = date.today().isoformat()
    try:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)

        run([sys.executable, str(SCRIPTS / "stage1_screen.py"),
             "--markets", "prime,standard,growth", "--no-cache", "--out", str(STAGE1_OUT)])

        run([sys.executable, str(SCRIPTS / "stage2_screen.py"),
             "--in", str(STAGE1_OUT), "--out", str(STAGE2_OUT)])

        stage1_df = pd.read_csv(STAGE1_OUT, dtype={"コード": str})
        stage1_passed = int(stage1_df["第1段階合格"].sum()) if "第1段階合格" in stage1_df.columns else 0

        stage2_df = pd.read_csv(STAGE2_OUT, dtype={"コード": str})
        candidates = stage2_df[stage2_df["第2段階合格"]] if "第2段階合格" in stage2_df.columns else stage2_df.iloc[0:0]

        # 第1段階の詳細指標を候補にマージして表示用に使う
        merged = candidates.merge(stage1_df, on="コード", suffixes=("", "_s1"))
        candidates_out = merged if not merged.empty else candidates

        candidates_out.to_csv(CANDIDATES_LATEST, index=False, encoding="utf-8-sig")

        cols = [c for c in DISPLAY_COLS if c in candidates_out.columns]
        table = candidates_out[cols].to_string(index=False) if not candidates_out.empty else "(該当銘柄なし)"

        body = (
            f"週次フルスキャン結果 ({today})\n\n"
            f"第1段階通過: {stage1_passed}銘柄 / 第2段階評価: {len(stage2_df)}銘柄 / 最終候補: {len(candidates_out)}銘柄\n\n"
            f"{table}\n"
        )
        send_email(f"【株スクリーニング】週次フルスキャン結果 {today}", body)
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
