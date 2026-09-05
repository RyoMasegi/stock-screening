---
name: stock-screening
description: Yahoo Finance(yfinance)とIR BANKのデータを使い、東証上場銘柄を2段階でスクリーニングする。第1段階はPER/PBR/ROE/ROA/配当利回り/自己資本比率/時価総額、第2段階はEPS・BPS・1株配当の10期推移条件。「株 スクリーニング」「銘柄抽出」「割安高配当連続増配株」などと言われたら使う。
---

# 株式スクリーニング（Yahoo Finance + IR BANK版）

APIキー・アカウント登録は一切不要。すべて公開データ(JPX公式一覧・Yahoo Finance・IR BANK)のみで完結する。

## 2段階スクリーニングの条件

### 第1段階（Yahoo Finance / yfinance、東証全銘柄が対象）
- PER：12倍以下
- PBR：1.3倍以下
- ROE：7%以上
- ROA：3%以上
- 配当利回り：3%以上
- 自己資本比率：35%以上
- 時価総額：1000億円以上

### 第2段階（IR BANK、第1段階合格銘柄のみが対象）
- EPS10期推移にマイナス転落（赤字）がないこと
- 今期EPSが9期前の2倍以上であること
- BPSが10期すべて増額であること（1期でも前年割れがあれば除外）
- 1株配当10期推移に減配が2度以上ないこと（減配は1回まで許容）
- 過去10期に無配転落がないこと

条件を変更する場合は、`scripts/stage1_screen.py` の `DEFAULT_CRITERIA` / `checks`、または
`scripts/stage2_screen.py` の `checks` を編集する。

## なぜ2段階か（データソース上の制約）

Yahoo Financeは直近5期程度の年次決算データしか提供しておらず、10期分のEPS/BPS推移は取得できない。
そのため、まずYahoo Financeの現在スナップショット指標（第1段階）で東証全銘柄（4000銘柄弱）を
数十〜数百銘柄程度に絞り込み、その絞り込んだ銘柄だけをIR BANK（1株あたり指標を10期以上遡って
掲載）で検証する（第2段階）という設計にしている。全銘柄をIR BANKで検証すると負荷が大きく
サイトへの配慮に欠けるため、この順序は変更しないこと。

## セットアップ（初回のみ）

```
pip install -r .claude/skills/stock-screening/requirements.txt
```

## 実行手順

### 1. 小規模テスト（推奨・毎回の仕様変更後は必須）

```
python .claude/skills/stock-screening/scripts/stage1_screen.py --limit 30
```

`results/stage1_YYYYMMDD.csv` が生成され、PER/PBR/ROE/ROA/配当利回り/自己資本比率/時価総額と
各条件の合否(`cond:*`列)が出力されることを確認する。

### 2. 第1段階 本実行（東証全市場）

```
python .claude/skills/stock-screening/scripts/stage1_screen.py --markets prime,standard,growth
```

- 銘柄一覧はJPX公式サイトから `data/cache/jpx/data_j.xlsx` にダウンロードし、プライム/スタンダード/
  グロース(内国株式)のみを対象にする。
- 1銘柄につきyfinanceへ2リクエスト（`.info` と年次貸借対照表）を行うため、全市場だと数十分〜1時間
  程度かかる。取得結果は `data/cache/yfinance/` にキャッシュされ、再実行時は再取得しない
  (`--no-cache` で強制再取得)。
- レート制限で失敗する銘柄が出た場合は `--sleep` を大きくして再実行する（キャッシュ済み銘柄は
  スキップされるので再実行のコストは小さい）。

### 3. 第2段階（第1段階合格銘柄のみ）

```
python .claude/skills/stock-screening/scripts/stage2_screen.py --in results/stage1_YYYYMMDD.csv
```

- IR BANK (`https://irbank.net/<証券コード>/results`) から決算まとめページを取得し、EPS・BPS・
  一株配当の年度別テーブルをそれぞれ見出し文字列(`EPS`/`BPS`/`一株配当`)で特定してパースする。
  ページ構造が変わっていて条件判定できない銘柄は理由付きで除外ログに記録される。
- サイトへの配慮としてデフォルトで1.5秒/リクエストのスリープを入れている。対象は第1段階合格銘柄
  （通常は東証全体の数%程度）に限定されるため、全市場を回すより大幅に軽い処理になる。
- 結果は `results/stage2_YYYYMMDD.csv` に出力され、`第2段階合格`列がTrueの行が最終候補。

### 4. 結果の取りまとめ

`results/stage2_*.csv` の `第2段階合格 == True` の行を最終候補としてユーザーに報告する。以下を含める：

- 最終候補銘柄一覧（コード・銘柄名・PER・PBR・ROE・ROA・配当利回り・自己資本比率・時価総額・
  EPS成長倍率・減配回数など）
- 第1段階通過数 → 第2段階評価数 → 最終合格数の内訳
- 除外ログ（`results/stage1_excluded_*.log` / `results/stage2_excluded_*.log`）にある、データ不足で
  評価できなかった銘柄数
- 既知の注意点：
  - 株式分割・併合があった銘柄はEPS/BPS/DPSの期間比較が歪む可能性がある（分割調整はしていない）
  - IR BANKの一株配当は実績ベース（予想年度の行は除外済み）
  - yfinanceの指標はリアルタイムではなく遅延データであり、決算発表直後は反映が遅れることがある
  - 上場から10期に満たない銘柄・IR BANKにページがない銘柄は第2段階の対象外

## 自動実行（Windowsタスクスケジューラー、ローカルのみ）

クラウド(Anthropicのクラウド実行環境)は、Yahoo Finance/IR BANK/JPXへのアウトバウンド接続が
組織ポリシーで遮断されており使用不可(検証済み)。そのため自動実行はこのマシン上で
Windowsタスクスケジューラーにより行う。

- `scripts/weekly_run.py` — 週次フルスキャン(第1段階全市場→第2段階→メール送信)。
  `results/candidates_latest.csv` に最終候補を保存し、翌日以降の日次再チェックが参照する。
- `scripts/daily_run.py` — 日次再チェック。`results/candidates_latest.csv` の銘柄だけを
  最新値で再確認してメール送信。週次結果がまだ無い場合はその旨だけ通知して終了する。
- `scripts/send_email.py` — Gmail SMTP(アプリパスワード)でのメール送信ヘルパー。
  プロジェクトルートの `.env` に `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` / `MAIL_TO` が必要
  (`.env.example` 参照、2段階認証が有効なアカウントでのみアプリパスワードを発行可能)。

タスクスケジューラー登録例(PowerShell、管理者権限不要):

```powershell
$py = (Get-Command python).Source
$root = "C:\Users\Ryo\Desktop\kabu"

$actionWeekly = New-ScheduledTaskAction -Execute $py -Argument "`"$root\.claude\skills\stock-screening\scripts\weekly_run.py`"" -WorkingDirectory $root
$triggerWeekly = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 6:00am
Register-ScheduledTask -TaskName "StockScreening-Weekly" -Action $actionWeekly -Trigger $triggerWeekly -Description "株スクリーニング週次フルスキャン"

$actionDaily = New-ScheduledTaskAction -Execute $py -Argument "`"$root\.claude\skills\stock-screening\scripts\daily_run.py`"" -WorkingDirectory $root
$triggerDaily = New-ScheduledTaskTrigger -Daily -At 6:00am
Register-ScheduledTask -TaskName "StockScreening-Daily" -Action $actionDaily -Trigger $triggerDaily -Description "株スクリーニング日次再チェック(月曜は週次が兼ねるため実質火〜日曜分のみ意味を持つ)"
```

PCが6時にスリープ/シャットダウン中だと実行されない点に注意(タスクのプロパティで
「スケジュールされた時刻を過ぎている場合はできるだけ早くタスクを実行する」を有効にすると
起動時に追いつき実行される)。

## ファイル構成

- `scripts/jpx_universe.py` — JPX公式サイトから東証上場銘柄一覧(data_j.xlsx)を取得・市場区分でフィルタ
- `scripts/stage1_screen.py` — Yahoo Finance(yfinance)によるスナップショット指標スクリーニング
- `scripts/stage2_screen.py` — IR BANKによるEPS/BPS/配当10期推移スクリーニング
- `scripts/weekly_run.py` / `scripts/daily_run.py` — 自動実行用オーケストレーション+メール送信
- `scripts/send_email.py` — Gmail SMTP送信ヘルパー
- `requirements.txt` — 依存パッケージ（yfinance, pandas, openpyxl, beautifulsoup4, lxml, python-dotenv）
