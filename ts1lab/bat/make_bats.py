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
    "フォントを特定する.bat": ("identify", ".ts1 をこの bat にドラッグ＆ドロップしてください（PC のフォントと字形を照合します）"),
    "中身を見る.bat": ("info", ".ts1 をこの bat にドラッグ＆ドロップしてください"),
}


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    for name, (cmd, hint) in BATS.items():
        lines = ["@echo off", "chcp 65001 >nul", "setlocal", 'cd /d "%~dp0"', "set PYTHONIOENCODING=utf-8"]
        if cmd == "setup":
            lines += ["echo VWSフォトショブリッジツール: 必要な部品を入れます（Python 3 が必要です）",
                      r'python -m pip install -r "%~dp0ts1lab\requirements.txt"',
                      "if errorlevel 1 (echo. & echo ※ 失敗しました。Python 3 が入っているか確認してください) else (echo. & echo セットアップ完了)"]
        else:
            lines += [f'if "%~1"=="" (echo {hint} & pause & exit /b 1)', f"{RUN} {cmd} %*"]
        lines += ["echo.", "pause"]
        with open(os.path.join(out, name), "w", encoding="utf-8", newline="\r\n") as f:
            f.write("\n".join(lines) + "\n")
    print(f"{len(BATS)} 個の bat を作りました: {out}")


if __name__ == "__main__":
    main()
