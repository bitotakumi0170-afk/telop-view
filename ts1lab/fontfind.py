"""フォントファイルを名前で探す（Windows のフォントフォルダ等を走査）"""
from __future__ import annotations

import glob
import json
import os
import unicodedata

FONT_DIRS = [
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
    os.path.expanduser("~/Library/Fonts"), "/Library/Fonts", "/System/Library/Fonts",
    "/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
]
EXTS = (".ttf", ".otf", ".ttc", ".otc")


def norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").replace(" ", "").replace("　", "").replace("-", "").lower()


def font_names(path):
    """[(フォント番号, [名前...])]  ファミリ名・フル名・PostScript 名（日英）"""
    from fontTools.ttLib import TTFont, TTCollection
    out = []
    try:
        if path.lower().endswith((".ttc", ".otc")):
            fonts = list(TTCollection(path, lazy=True).fonts)
        else:
            fonts = [TTFont(path, lazy=True)]
    except Exception:
        return out
    for i, f in enumerate(fonts):
        try:
            names = {str(r.toUnicode()) for r in f["name"].names if r.nameID in (1, 4, 6, 16, 17)}
        except Exception:
            names = set()
        out.append((i, sorted(names)))
    return out


def scan(extra_dirs=(), cache_path=None):
    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    table = []
    for d in list(extra_dirs) + FONT_DIRS:
        if not d or not os.path.isdir(d):
            continue
        for p in glob.glob(os.path.join(d, "**", "*"), recursive=True):
            if p.lower().endswith(EXTS):
                for idx, names in font_names(p):
                    table.append({"path": p, "index": idx, "names": names})
    if cache_path:
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(table, f, ensure_ascii=False)
        except Exception:
            pass
    return table


def find(hint_sets=(), postscript=None, extra_dirs=(), cache_path=None):
    """PostScript 名の完全一致 → ヒント語（すべて含む）の順で探す。(path, index, names) か None

    キャッシュで見つからなければフォントフォルダを走査し直す（フォントを後から入れた場合）"""
    r = _find(scan(extra_dirs, cache_path), hint_sets, postscript)
    if r is None and cache_path and os.path.exists(cache_path):
        os.remove(cache_path)
        r = _find(scan(extra_dirs, cache_path), hint_sets, postscript)
    return r


def _find(table, hint_sets, postscript):
    if postscript:
        for e in table:
            if any(norm(n) == norm(postscript) for n in e["names"]):
                return e["path"], e["index"], e["names"]
    for hints in hint_sets:
        hs = [norm(h) for h in hints]
        for e in table:
            if any(all(h in norm(n) for h in hs) for n in e["names"]):
                return e["path"], e["index"], e["names"]
    return None
