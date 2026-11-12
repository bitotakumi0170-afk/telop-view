# VWSフォトショブリッジツール 引き継ぎメモ（クラウドセッション → PC のセッション）

VWS のテロップファイル（.ts1）と Photoshop を相互変換する実験の、現時点の状態です。
新しいセッションはまずこのファイルと [FORMAT.md](FORMAT.md)、[README.md](README.md) を読んでください。

## 目的（ユーザーの要望）

1. .ts1 を解析して Photoshop で再現する（編集できるテキストレイヤとして）
2. Photoshop で作ったテロップを .ts1 で書き出して VWS で使う
3. フォントを正しく特定・設定する（最優先）

## できていること

| 項目 | 状態 |
|---|---|
| .ts1 の全バイトを項目に割り当て（`ts1tool.py dump`） | 済。意味未確定の項目は FORMAT.md の「未確認」 |
| 解析 → 再構築でバイト完全一致（`ts1tool.py selftest`） | 済 |
| 見た目の再現（文字面・縁） | VWS 出力 PNG と全 16 文字 ±0.25px、縁 6px で一致 |
| 色・縁の取り出し | 文字 #FFFFFF / 縁 **#122FE7（青, B G R A 順）** / 縁 6px。ADCO から役割つきで取得。2026-11-12 に VWS 実機で青と確認して訂正（以前は赤 #E72F12 と誤読） |
| フォント特定 | **VWS フォント番号 1119 = フォントワークス「ニューロダン B」**（公式見本と字形照合 97.2%）。ニィス「ウインクスL7」は 70% で不一致 |
| VWS での読み込み | ユーザー確認: テスト1（既存字形で文言変更）は開けて打ち替えも可。テスト2（IPA ゴシックの字形・番号 1119 のまま）は「見た目は変わるがフォント名は元のまま」→ VWS は表示に字形、フォント名に番号を使う |

## PC での確認結果（2026-11-12、Windows 11 / Python 3.12 / Photoshop 2027）

| 項目 | 結果 |
|---|---|
| セットアップ（pip install） | 通った（psd-tools 1.26 / fonttools 4.63） |
| `identify 見本.ts1` | `FOT-NewRodinPro-B.otf` が **99.3%** で一致（次点 83.4%）→ `フォント表.json` にファイル・PostScript 名 `NewRodinPro-B`・scale/ox/oy を登録 |
| JSX を Photoshop で実行 | フォント NewRodinPro-B、トラッキング、境界線（外側 6px）、参考レイヤ（非表示）、PSD 自動保存 すべて OK |
| 位置 | **直した**: ①一律 9px 下にずれていた → フォント表の oy（ベースラインのずれ）をアンカーに反映 ②右へ行くほど詰まっていた（計 13px）→ Photoshop の既定カーニング「メトリクス」を 0（手動）に。直した後は全 16 文字 ±1px |
| 空の「レイヤー 1」 | **直した**: 透明ドキュメントの既定レイヤをそのままテキストレイヤにする |
| 本物の PSD → TS1 | 通った。フォント番号 1119 を書く。元の TS1 との差は OFRI 左端 1px と冗長な ACSP 0 が 1 か所無いだけ。見た目の重なり 99.0% |
| 出力名 | **直した**: `to-ts1 見本.psd` が元の `見本.ts1` を上書きしていた → `○○_VWS用.ts1` に |
| カーニング警告 | **追加**: PSD の組み幅が「全角送り＋トラッキング」より 2px 以上狭ければ警告（メトリクス/オプティカル/ツメ）。長体・平体も警告 |
| bat | zip が PC に無かったので `bat/make_bats.py` で作り直した（UTF-8・CRLF・`chcp 65001`・日本語ファイル名）。5 つともドロップ相当で動作確認済み |

Photoshop の実行確認は COM（`Photoshop.Application` の `DoJavaScriptFile`）で自動化できる。生成 JSX の最後の `alert` は、
ラッパ側で `$.global.alert` を差し替えれば止まらない。

| 色の並び | **直した**: VWS 実機で縁が青と確認 → CRGB は B G R A 順。Photoshop の境界線も青 (18,47,231) になり、往復で色のバイトも一致 |
| VWS で `見本_VWS用.ts1` を開く | **ユーザー確認: 開ける・フォント名はニューロダン B と表示**（2026-11-12）。見本の VWS 出力 PNG と生成 .ts1 の描画は全 16 文字ずれ 0px・誤差 2.06%（元の見本は 1.96%） |

## まだ確認できていないこと

1. PSD で位置・色・縁・サイズを変えた .ts1 を VWS で開いた結果（テスト3 相当）
2. **テスト3（PSD 由来: 位置・色・縁・サイズ変更）の VWS 読み込み結果**（ユーザー未回答）
3. ニューロダン B 以外のフォントのベースラインのずれ（oy）。別フォントの .ts1 が来たら `identify` で実測される

## 追加サンプルが必要な解析

VWS で同じ文言のまま設定を 1 つだけ変えて別名保存した .ts1 を `ts1tool.py diff A.ts1 B.ts1` で比べると保存場所が分かる。

- 2 行テロップ（改行コード、行の縦位置）
- 半角英数を含むもの（送り幅）
- 縁の色だけ / 太さだけ変えたもの、影・二重縁
- 左揃え・右揃え（SCLS の 2 が中央揃えかどうか）
- 別フォント（フォント番号の対応表を増やす。ウインクスL7 を使うならその .ts1 が必要）

## ファイルの場所

- コード: このディレクトリ（`ts1lab/`）。ブランチ `claude/blissful-heisenberg-ngtohq`
- 作業用一式 `VWSフォトショブリッジツール.zip`（bat・設定.json・フォント表.json・テンプレート.ts1・作業例）は
  クラウドセッションのチャットに添付したもの。**リポジトリには入れていない**
- 見本の .ts1 は編集履歴（ユーザー名・端末名・素材名）を含むためリポジトリに入れていない（`.gitignore` で .ts1 / .psd を除外）。
  PC では zip の `テンプレート.ts1` / `作業例\見本.ts1` を使う
- zip と同じ構成を作るには: `ts1lab/` の 1 つ上に `設定.json`・`フォント表.json`・`テンプレート.ts1` を置く（`run.py` はそこを読む）。
  bat は `python ts1lab/bat/make_bats.py <その場所>` で作る
- PC の作業用フォルダ: `C:\Users\bitot\tools\telop\VWSフォトショブリッジツール\`（`ts1lab` はリポジトリの `ts1lab/` へのジャンクション。
  `テンプレート.ts1` と `作業例\見本.ts1` はユーザーがチャットに添付した見本）

`フォント表.json` の初期内容:

```json
{"1119": {"name": "ニューロダン B", "hints": [["ニューロダン", "B"], ["NewRodin", "B"]]}}
```

`設定.json` の初期内容:

```json
{"template": "テンプレート.ts1", "font_name_hints": [], "font_file": "", "font_index": 0,
 "photoshop_font": "", "vws_font_id": 1119, "clear_history": false}
```

## 主なコマンド

```
pip install -r requirements.txt
python run.py to-ps  見本.ts1        # → 見本_再現.png / 見本_画像レイヤ.psd / 見本_Photoshop用.jsx
python run.py to-ts1 テロップ.psd     # → テロップ_VWS用.ts1 / テロップ_確認.png
python run.py identify 見本.ts1      # 字形から PC 内のフォントを特定して フォント表.json に登録
python run.py info 見本.ts1          # 概要 + 見本_構造.txt
python run.py diff A.ts1 B.ts1       # 項目単位の差分
python ts1tool.py selftest 見本.ts1  # 健全性チェック
python ts1tool.py compare 見本.ts1 VWS出力.png --diff 差分.png   # VWS 出力画像と照合
```

## 注意

- 実機（VWS）での確認は本番データを避け、テスト用の環境で行う
- 色は B G R A 順（`ts1core.COLOR_ORDER`）。見本の縁は青 #122FE7。VWS の PNG 書き出しでの色の入れ替えは無い
- 字間（ACSP）は「その文字の直前」に効く。Photoshop のトラッキングは「その文字の後ろ」なので 1 文字ずらして変換している
