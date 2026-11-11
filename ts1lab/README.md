# ts1lab — VWS テロップ (.ts1) ⇄ Photoshop 互換実験

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
```

色が実機と違う場合は `--color-order BGRA` を試してください（CRGB のバイト順が未確定のため）。

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
