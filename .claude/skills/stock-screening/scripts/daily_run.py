#!/usr/bin/env python3
"""日次再チェック: 直近の週次フルスキャン候補だけを最新値で再確認し、結果をメール送信する。

Windowsタスクスケジューラーから火〜日曜6:00に実行される想定(月曜は週次フルスキャンが担当)。
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

CANDIDATES_LATEST = RESULTS_DIR / "candidates_latest.csv"
STAGE1_OUT = RESULTS_DIR / "stage1_daily.csv"
STAGE2_OUT = RESULTS_DIR / "stage2_daily.csv"

DISPLAY_COLS = ["コード", "銘柄名", "PER", "PBR", "ROE%", "ROA%", "配当利回り%", "自己資本比率%", "時価総額(億円)"]


def run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"コマンド失敗: {' '.join(cmd)}\n--- stdout ---\n{result.stdout[-3000:]}\n--- stderr ---\n{result.stderr[-3000:]}")


def main() -> None:
    today = date.today().isoformat()
    try:
        if not CANDIDATES_LATEST.exists():
            send_email(
                f"【株スクリーニング】日次再チェック {today}",
                "前回の週次フルスキャン結果が見つかりません。週次フルスキャンがまだ一度も実行されていない可能性があります。",
            )
            print("週次結果なし。終了します。")
            return

        prev = pd.read_csv(CANDIDATES_LATEST, dtype={"コード": str})
        if prev.empty:
            send_email(
                f"【株スクリーニング】日次再チェック {today}",
                "前回の週次フルスキャンで候補銘柄はありませんでした。",
            )
            print("前回候補なし。終了します。")
            return

        codes = ",".join(prev["コード"].tolist())
        prev_names = dict(zip(prev["コード"], prev["銘柄名"]))

        run([sys.executable, str(SCRIPTS / "stage1_screen.py"),
             "--codes", codes, "--no-cache", "--out", str(STAGE1_OUT)])
        run([sys.executable, str(SCRIPTS / "stage2_screen.py"),
             "--in", str(STAGE1_OUT), "--out", str(STAGE2_OUT)])

        stage1_df = pd.read_csv(STAGE1_OUT, dtype={"コード": str})
        stage2_df = pd.read_csv(STAGE2_OUT, dtype={"コード": str}) if STAGE2_OUT.exists() else pd.DataFrame()

        still_pass_codes = set(stage2_df.loc[stage2_df.get("第2段階合格", False), "コード"]) if not stage2_df.empty else set()
        merged = stage2_df.merge(stage1_df, on="コード", suffixes=("", "_s1")) if not stage2_df.empty else stage2_df
        still_pass = merged[merged["コード"].isin(still_pass_codes)] if not merged.empty else merged

        dropped = [c for c in prev["コード"] if c not in still_pass_codes]

        cols = [c for c in DISPLAY_COLS if c in still_pass.columns]
        table = still_pass[cols].to_string(index=False) if not still_pass.empty else "(該当銘柄なし)"

        lines = [f"日次再チェック結果 ({today})\n"]
        lines.append(f"前回候補 {len(prev)}銘柄のうち、本日も全条件を満たす銘柄: {len(still_pass)}銘柄\n")
        lines.append(table)

        if dropped:
            lines.append("\n条件から外れた銘柄:")
            for code in dropped:
                s1row = stage1_df[stage1_df["コード"] == code]
                reason = "データ取得失敗、または条件を満たさなくなった"
                if not s1row.empty:
                    failed = [c.replace("cond:", "") for c in s1row.columns if c.startswith("cond:") and not bool(s1row.iloc[0][c])]
                    if failed:
                        reason = "、".join(failed)
                lines.append(f"  - {code} {prev_names.get(code, '')}: {reason}")
        else:
            lines.append("\n前回候補は全銘柄が引き続き条件を満たしています。")

        send_email(f"【株スクリーニング】日次再チェック結果 {today}", "\n".join(lines))
        print("完了しました。")

    except Exception as exc:  # noqa: BLE001
        try:
            send_email(
                f"【株スクリーニング】日次再チェック エラー {today}",
                f"日次再チェックの実行中にエラーが発生しました。\n\n{exc}",
            )
        except Exception as mail_exc:  # noqa: BLE001
            print(f"エラー通知メールの送信にも失敗しました: {mail_exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
