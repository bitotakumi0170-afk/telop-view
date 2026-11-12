# VWSフォトショブリッジツール（ts1lab）— VWS テロップ (.ts1) ⇄ Photoshop

VWS のテロップファイル (.ts1) を解析して Photoshop で再現し、逆に Photoshop のテキストレイヤから
.ts1 を作る実験用ツールです。フォーマットの解析結果は [FORMAT.md](FORMAT.md)。

## できること

| 方向 | コマンド | 内容 |
|---|---|---|
| TS1 → 画像 | `render` | ファイル内のグリフ・アウトラインから正確に描画（PNG / SVG） |
| TS1 → PSD | `to-psd` | 「文字」「縁」のピクセルレイヤを持つ PSD |
| TS1 → Photoshop テキスト | `to-psd --jsx` | Photoshop で実行すると **編集可能なテキストレイヤ＋境界線** を作る JSX |
| PSD → TS1 | `from-psd` | テキストレイヤの文字・サイズ・トラッキング・揃え・位置・塗り色・境界線を読み、テンプレート TS1 の書式で組み直す |
| 文言差し替え | `text` | テンプレート TS1 の書式のまま文言だけ変える |

## セットアップ

```
pip install -r requirements.txt
```

## 使い方

```
# 中身を見る（--history で編集履歴）
python ts1tool.py info 素材.ts1

# プレビュー
python ts1tool.py render 素材.ts1 -o preview.png --svg preview.svg

# Photoshop へ
python ts1tool.py to-psd 素材.ts1 -o 素材.psd --jsx 素材.jsx --ps-font KozGoPr6N-Heavy

# Photoshop から（グリフはフォントファイルから生成）
python ts1tool.py from-psd テロップ.psd -t テンプレート.ts1 --font "C:\Windows\Fonts\xxx.ttf" -o 出力.ts1

# 文言だけ差し替え
python ts1tool.py text テンプレート.ts1 "新しい文言" --font xxx.ttf -o 出力.ts1

# 解析の健全性チェック（解析→再構築でバイト一致するか等）
python ts1tool.py selftest 素材.ts1

# 全バイトの構造ダンプ / 2 つの .ts1 の項目単位の差分（設定を 1 つ変えて保存 → どこに入るか調べる）
python ts1tool.py dump 素材.ts1 -o 構造.txt
python ts1tool.py diff A.ts1 B.ts1

# VWS の出力画像（背景透過 PNG）と照合（位置・縁幅・文字ごとのずれ・差分画像）
python ts1tool.py compare 素材.ts1 VWS出力.png --diff 差分.png
```

見本画像との照合結果（サンプル 1 件）: 16 文字すべて ±0.25px 以内、縁幅 6.0px、全体誤差 1.96%。

ドラッグ＆ドロップで使う場合は `run.py`（`設定.json` を読む）を使います:

```
python run.py identify 素材.ts1   # 字形から PC 内のフォントを特定し フォント表.json に登録（番号 ⇄ フォント）
python run.py to-ps  素材.ts1     # → 素材_再現.png / 素材_画像レイヤ.psd / 素材_Photoshop用.jsx
python run.py to-ts1 テロップ.psd  # → テロップ_VWS用.ts1 / テロップ_確認.png
```

文字だけ打ち替える（VWS 無しでこの PC 内で完結）:

```
python run.py text 素材.ts1 "新しい文言"   # → 素材_打ち替え.ts1 / 素材_打ち替え_確認.png（文言を省くと入力を求める）
```

位置・色・縁・サイズ・フォント番号はそのまま。元の .ts1 にある字は VWS の字形をそのまま使い、無い字だけ PC のフォント
（`フォント表.json` で番号から引く）から作ります。

`to-ps` の JSX が作る PSD は 2 層です。上が「VWSの見た目（正確な画像）」（VWS と同じ描き方で作った画像。初期表示）、
下が「編集用テキスト」（編集できる文字＋境界線。初期は非表示）。[ウィンドウ] > [レイヤーカンプ] の ①／② で切り替えます。
VWS の縁は「文字を 1px 刻みでずらして重ねた形」＋上下 2 ラインの平均なので、Photoshop の文字＋境界線では同じ絵になりません
（詳しくは FORMAT.md「VWS の描き方」）。`to-ts1` は非表示でもテキストレイヤを読みます。

VWS での確認用に、条件を 1 つずつ変えた .ts1 を作って照合できます:

```
python vwstest.py make  見本.ts1        # → 見本と同じ場所の「VWS確認用」に 6 本（打ち替え・縁3px・縁10px・サイズ・位置・色）
python vwstest.py check VWS確認用        # VWS の書き出し PNG（.ts1 と同じ名前）と照合
```

ドラッグ＆ドロップ用の bat は `python bat/make_bats.py <ts1lab の 1 つ上のフォルダ>` で作れます
（セットアップ / TS1→Photoshop / PSD→TS1 / 文字の打ち替え / フォント特定 / 中身を見る / VWS 確認用の一式を作る / VWS の書き出しと照合する）。

Photoshop で作るテロップは、文字パネルの **カーニングを「0」** にしてください。既定の「メトリクス」はかなを詰めますが、
VWS は全角を常に文字サイズ送りで並べるため再現できません（`to-ts1` は詰まりを検出すると警告します）。
`to-ps` が出す JSX は最初からカーニング 0 で作ります。

## 仕組み

- .ts1 は文字ごとのアウトライン（GDI のグリフ形式・2048 セル）を内蔵しているため、
  VWS のフォントが無くても見た目を再現できます。
- 書き出しは「テンプレート方式」: 解明できていない部分（イン/アウト効果、レイヤ設定など）は
  テンプレート .ts1 の生バイトをそのまま使い、文字列・グリフ・位置・色・サイズ・字間だけを差し替え、
  各チャンクのサイズ・件数・リソース番号・描画範囲 (OFRI) を再計算します。
- テンプレートをそのまま再構築するとバイト単位で元ファイルと一致します（`selftest`）。

## 注意

- 実機（VWS）で読み込めるかは未検証です。本番データには使わず、テスト用の環境で試してください。
- .ts1 には編集履歴（ユーザー名・端末名・素材名）が含まれます。共有する場合は `--clear-history`
  を検討してください（ただし VWS がこれを受け付けるかは未確認）。
- このリポジトリには .ts1 / .psd を置かない設定（.gitignore）にしています。
