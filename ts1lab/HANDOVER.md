# 引き継ぎメモ（クラウドセッション → PC のセッション）

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
| 色・縁の取り出し | 文字 #FFFFFF / 縁 #E72F12（赤, R G B A 順）/ 縁 6px。ADCO から役割つきで取得 |
| フォント特定 | **VWS フォント番号 1119 = フォントワークス「ニューロダン B」**（公式見本と字形照合 97.2%）。ニィス「ウインクスL7」は 70% で不一致 |
| VWS での読み込み | ユーザー確認: テスト1（既存字形で文言変更）は開けて打ち替えも可。テスト2（IPA ゴシックの字形・番号 1119 のまま）は「見た目は変わるがフォント名は元のまま」→ VWS は表示に字形、フォント名に番号を使う |

## まだ確認できていないこと（PC でやること）

1. **Windows の bat が動くか**（`1_最初に1回だけ_セットアップ.bat` ほか。Python 3 が必要）
2. **Photoshop 用 JSX が動くか**（`run.py to-ps` が出す `○○_Photoshop用.jsx`。Photoshop 未実行）
   - フォント自動選択（ニューロダン B）、文字ごとのトラッキング、境界線、位置、参考レイヤ、PSD 自動保存
3. **本物の Photoshop の PSD → TS1**（これまでのテスト PSD は ag-psd で作った代用品）
4. **ニューロダン B が入った PC でのフォント解決**（`フォント表.json` の 1119 はヒント語だけ登録。初回に PC 上のファイルを探し、
   VWS 座標への倍率・オフセットを実測して書き戻す仕組み。`run.py identify 見本.ts1` でも登録できる）
5. **テスト3（PSD 由来: 位置・色・縁・サイズ変更）の VWS 読み込み結果**（ユーザー未回答）
6. 生成した .ts1 を VWS で開いたときのフォント名表示（1119 を書けばニューロダン B になるはず）

## 追加サンプルが必要な解析

VWS で同じ文言のまま設定を 1 つだけ変えて別名保存した .ts1 を `ts1tool.py diff A.ts1 B.ts1` で比べると保存場所が分かる。

- 2 行テロップ（改行コード、行の縦位置）
- 半角英数を含むもの（送り幅）
- 縁の色だけ / 太さだけ変えたもの、影・二重縁
- 左揃え・右揃え（SCLS の 2 が中央揃えかどうか）
- 別フォント（フォント番号の対応表を増やす。ウインクスL7 を使うならその .ts1 が必要）

## ファイルの場所

- コード: このディレクトリ（`ts1lab/`）。ブランチ `claude/blissful-heisenberg-ngtohq`
- 作業用一式 `TS1テロップ変換.zip`（bat・設定.json・フォント表.json・テンプレート.ts1・作業例）は
  クラウドセッションのチャットに添付したもの。**リポジトリには入れていない**
- 見本の .ts1 は編集履歴（ユーザー名・端末名・素材名）を含むためリポジトリに入れていない（`.gitignore` で .ts1 / .psd を除外）。
  PC では zip の `テンプレート.ts1` / `作業例\見本.ts1` を使う
- zip と同じ構成を作るには: `ts1lab/` の 1 つ上に `設定.json`・`フォント表.json`・`テンプレート.ts1` を置く（`run.py` はそこを読む）

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
python run.py to-ts1 テロップ.psd     # → テロップ.ts1 / テロップ_確認.png
python run.py identify 見本.ts1      # 字形から PC 内のフォントを特定して フォント表.json に登録
python run.py info 見本.ts1          # 概要 + 見本_構造.txt
python run.py diff A.ts1 B.ts1       # 項目単位の差分
python ts1tool.py selftest 見本.ts1  # 健全性チェック
python ts1tool.py compare 見本.ts1 VWS出力.png --diff 差分.png   # VWS 出力画像と照合
```

## 注意

- 実機（VWS）での確認は本番データを避け、テスト用の環境で行う
- VWS が書き出した見本 PNG は縁が青（R と B が入れ替わっている）。実際の色は赤 #E72F12
- 字間（ACSP）は「その文字の直前」に効く。Photoshop のトラッキングは「その文字の後ろ」なので 1 文字ずらして変換している
