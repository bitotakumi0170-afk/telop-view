"""ドラッグ＆ドロップ用の bat を作る（UTF-8 BOM なし・CRLF。chcp 65001 より前の行には日本語を書かない）

  python make_bats.py [出力先]   … 出力先は ts1lab の 1 つ上（設定.json がある場所）。省略時はこのフォルダ
bat は「自分と同じ場所にある ts1lab\\run.py」を呼ぶので、ts1lab フォルダの隣に置いて使う。
"""
import os
import sys

RUN = r'python "%~dp0ts1lab\run.py"'
BATS = {
    "1_最初に1回だけ_セットアップ.bat": ("setup", None),
    "2_TS1をPhotoshop用に変換.bat": ("to-ps", ".ts1 をこの bat にドラッグ＆ドロップしてください"),
    "3_PSDをTS1に変換.bat": ("to-ts1", "Photoshop で保存した .psd をこの bat にドラッグ＆ドロップしてください"),
    "4_TS1の文字を打ち替える.bat": ("text", ".ts1 をこの bat にドラッグ＆ドロップしてください（新しい文言を聞かれます）"),
    "5_ストックからまとめて打ち替える.bat": ("batch", "ストックの .ts1 を入れたフォルダ（一覧を作ります）か、書き込んだ 打ち替え一覧.csv をこの bat にドラッグ＆ドロップしてください"),
    "フォントを特定する.bat": ("identify", ".ts1 をこの bat にドラッグ＆ドロップしてください（PC のフォントと字形を照合します）"),
    "中身を見る.bat": ("info", ".ts1 をこの bat にドラッグ＆ドロップしてください"),
    "VWS確認用の一式を作る.bat": ("vws-make", ".ts1 をこの bat にドラッグ＆ドロップしてください（条件を 1 つずつ変えた確認用 .ts1 を作ります）"),
    "VWSの書き出しと照合する.bat": ("vws-check", "VWS の書き出し PNG を入れたフォルダをこの bat にドラッグ＆ドロップしてください"),
}
VWSTEST = r'python "%~dp0ts1lab\vwstest.py"'


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    for name, (cmd, hint) in BATS.items():
        lines = ["@echo off", "chcp 65001 >nul", "setlocal", 'cd /d "%~dp0"', "set PYTHONIOENCODING=utf-8"]
        if cmd == "setup":
            lines += ["echo VWSフォトショブリッジツール: 必要な部品を入れます（Python 3 が必要です）",
                      r'python -m pip install -r "%~dp0ts1lab\requirements.txt"',
                      "if errorlevel 1 (echo. & echo ※ 失敗しました。Python 3 が入っているか確認してください) else (echo. & echo セットアップ完了)"]
        else:
            # text は 1 ファイルだけ（文言は run.py が入力を求める）
            if cmd.startswith("vws-"):
                line = f'{VWSTEST} {cmd[4:]} "%~1"'
            else:
                line = f'{RUN} {cmd} "%~1"' if cmd in ("text", "batch") else f"{RUN} {cmd} %*"
            lines += [f'if "%~1"=="" (echo {hint} & pause & exit /b 1)', line]
        lines += ["echo.", "pause"]
        with open(os.path.join(out, name), "w", encoding="utf-8", newline="\r\n") as f:
            f.write("\n".join(lines) + "\n")
    print(f"{len(BATS)} 個の bat を作りました: {out}")


if __name__ == "__main__":
    main()
