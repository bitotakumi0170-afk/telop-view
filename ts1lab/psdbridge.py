"""TS1 ⇄ Photoshop の橋渡し

  TS1 → PSD : 文字面・縁をピクセルレイヤで書き出し（psd-tools）
  TS1 → JSX : Photoshop で実行すると「編集可能なテキストレイヤ＋境界線」の PSD を作って保存するスクリプト
  PSD → TS1 : PSD のテキストレイヤ（文字・サイズ・トラッキング・位置・色・境界線）を読み、
              テンプレート TS1 の書式で組み直す
"""
from __future__ import annotations

import json

from ts1core import CELL, COLOR_ORDER, TS1, FontGlyphSource, build_text
from render import render_layers

CANVAS = (1920, 1080)


# ----------------------------------------------------------------------------
# TS1 → 画像 / PSD（ピクセルレイヤ）
# ----------------------------------------------------------------------------
def ts1_to_png(ts1: TS1, out_path, color_order=COLOR_ORDER, edge=None):
    """キャンバス全体（1920×1080）の透過 PNG"""
    # VWS と同じ手順（8 倍の整数格子・GDI の塗り・上下 2 ライン・ずらし重ねの縁・整数の色計算）で描く
    import vwsrender
    vwsrender.render_image(ts1, edge=edge, canvas=CANVAS, color_order=color_order).save(out_path)
    return out_path


def ts1_to_psd(ts1: TS1, out_path, color_order=COLOR_ORDER, edge=None):
    from psd_tools import PSDImage
    import vwsrender
    face, edge_img, (l, t) = vwsrender.render_layers(ts1, edge=edge, canvas=CANVAS, color_order=color_order)
    psd = PSDImage.new("RGBA", CANVAS, color=(0, 0, 0, 0))
    e = psd.create_pixel_layer(edge_img, name="edge", top=t, left=l)
    f = psd.create_pixel_layer(face, name="face", top=t, left=l)
    g = psd.create_group([e, f], name="TS1")
    # 日本語名は Unicode レイヤ名ブロックに入る（setter 経由）
    e.name, f.name, g.name = "縁", "文字", f"TS1: {ts1.text}"[:120]
    psd.save(out_path)
    return out_path


# ----------------------------------------------------------------------------
# TS1 → JSX（Photoshop で編集可能なテキストレイヤを生成して PSD 保存）
# ----------------------------------------------------------------------------
JSX_TEMPLATE = r"""// TS1 → Photoshop テキストレイヤ生成スクリプト（ts1lab で自動生成）
// 使い方: Photoshop で [ファイル] > [スクリプト] > [参照...] からこのファイルを選ぶ
//   → 編集可能なテキストレイヤ（＋境界線）の PSD を作り、このファイルと同じフォルダに保存します
#target photoshop
var SPEC = %(spec)s;

function c(s){return charIDToTypeID(s);} function s(x){return stringIDToTypeID(x);}
function toks(t){ var a = String(t).toLowerCase().split(/[\s\u3000\-_]+/), o = []; for (var i = 0; i < a.length; i++) if (a[i]) o.push(a[i]); return o; }
// ヒント語がすべて名前に含まれるか。2 文字以下（B, L7 などウェイト記号）は単語として一致するときだけ
function hintMatch(name, hs) {
  var ts = toks(name), joined = ts.join("");
  for (var k = 0; k < hs.length; k++) {
    var h = String(hs[k]).toLowerCase().replace(/\s/g, ""), ok = false;
    if (h.length <= 2) {
      for (var i = 0; i < ts.length; i++) {
        var t = ts[i];
        if (t == h) { ok = true; break; }
        if (t.length > h.length && t.substr(t.length - h.length) == h) {
          var pc = t.charAt(t.length - h.length - 1);
          if (!(pc >= "a" && pc <= "z")) { ok = true; break; }
        }
      }
    } else { ok = joined.indexOf(h) >= 0; }
    if (!ok) return false;
  }
  return true;
}

// ---- フォントを探す（PostScript 名 → 名前のヒント語）
function findFont() {
  var i, f;
  if (SPEC.ps_font) {
    for (i = 0; i < app.fonts.length; i++) if (app.fonts[i].postScriptName == SPEC.ps_font) return app.fonts[i].postScriptName;
  }
  for (var h = 0; h < SPEC.font_hints.length; h++) {
    for (i = 0; i < app.fonts.length; i++) {
      f = app.fonts[i];
      var names = [f.name, f.family + " " + f.style, f.postScriptName];
      for (var n = 0; n < names.length; n++) if (hintMatch(names[n], SPEC.font_hints[h])) return f.postScriptName;
    }
  }
  return null;
}
var FONT = findFont();
if (!FONT) { alert("フォントが見つかりません: " + SPEC.font_hints.join(" / ") + "\n仮のフォントで作成します。"); FONT = "MS-Gothic"; }

var oldRuler = app.preferences.rulerUnits, oldType = app.preferences.typeUnits;
app.preferences.rulerUnits = Units.PIXELS;
app.preferences.typeUnits = TypeUnits.PIXELS;
var doc = app.documents.add(SPEC.canvas[0], SPEC.canvas[1], 72, SPEC.name,
                            NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
var lay = doc.activeLayer;  // 透明ドキュメントの空レイヤをそのままテキストレイヤにする（空レイヤを残さない）
lay.kind = LayerKind.TEXT;
lay.name = SPEC.text.replace(/\n/g, " ");
var ti = lay.textItem;
ti.contents = SPEC.text.replace(/\n/g, "\r");
ti.font = FONT;
ti.size = new UnitValue(SPEC.ps_size, "px");
ti.justification = Justification.CENTER;
// VWS は全角を常に文字サイズ送りで並べる → Photoshop の既定「メトリクス」カーニングを切る（かなが詰まるため）
ti.autoKerning = AutoKernType.MANUAL;
if (SPEC.antialias) ti.antiAliasMethod = AntiAlias[SPEC.antialias];
var col = new SolidColor(); col.rgb.red = SPEC.fill[0]; col.rgb.green = SPEC.fill[1]; col.rgb.blue = SPEC.fill[2];
ti.color = col;
ti.useAutoLeading = false;
ti.leading = new UnitValue(SPEC.size + SPEC.line_spacing, "px");
ti.position = [new UnitValue(SPEC.anchor[0], "px"), new UnitValue(SPEC.anchor[1], "px")];

// ---- 文字ごとのトラッキング（VWS の字間 → 1/1000 em）
(function(){
  var ref = new ActionReference(); ref.putEnumerated(c("Lyr "), c("Ordn"), c("Trgt"));
  var td = executeActionGet(ref).getObjectValue(s("textKey"));
  var base = td.getList(s("textStyleRange")).getObjectValue(0).getObjectValue(s("textStyle"));
  var list = new ActionList();
  for (var i = 0; i < SPEC.tracking.length; i++) {
    var r = new ActionDescriptor();
    r.putInteger(s("from"), i); r.putInteger(s("to"), i + 1);
    base.putInteger(s("tracking"), SPEC.tracking[i]);
    base.putUnitDouble(s("size"), c("#Pxl"), SPEC.ps_size);
    base.putEnumerated(s("autoKern"), s("autoKern"), s("manual"));
    base.putInteger(s("kerning"), 0);
    r.putObject(s("textStyle"), s("textStyle"), base);
    list.putObject(s("textStyleRange"), r);
  }
  td.putList(s("textStyleRange"), list);
  var d = new ActionDescriptor(); d.putReference(c("null"), ref);
  d.putObject(c("T   "), s("textLayer"), td);
  executeAction(c("setd"), d, DialogModes.NO);
})();
ti.position = [new UnitValue(SPEC.anchor[0], "px"), new UnitValue(SPEC.anchor[1], "px")];

// ---- 縁 = レイヤスタイル「境界線（外側）」
(function(){
  if (SPEC.edge <= 0) return;
  var d = new ActionDescriptor(); var r = new ActionReference();
  r.putProperty(c("Prpr"), c("Lefx")); r.putEnumerated(c("Lyr "), c("Ordn"), c("Trgt"));
  d.putReference(c("null"), r);
  var fx = new ActionDescriptor(); fx.putUnitDouble(c("Scl "), c("#Prc"), 100);
  var st = new ActionDescriptor();
  st.putBoolean(c("enab"), true);
  st.putEnumerated(c("Styl"), c("FStl"), c("OutF"));
  st.putEnumerated(c("PntT"), c("FrFl"), c("SClr"));
  st.putEnumerated(c("Md  "), c("BlnM"), c("Nrml"));
  st.putUnitDouble(c("Opct"), c("#Prc"), SPEC.edge_opacity);
  st.putUnitDouble(c("Sz  "), c("#Pxl"), SPEC.edge);
  var k = new ActionDescriptor();
  k.putDouble(c("Rd  "), SPEC.edge_color[0]); k.putDouble(c("Grn "), SPEC.edge_color[1]); k.putDouble(c("Bl  "), SPEC.edge_color[2]);
  st.putObject(c("Clr "), c("RGBC"), k);
  fx.putObject(c("FrFX"), c("FrFX"), st);
  d.putObject(c("T   "), c("Lefx"), fx);
  executeAction(c("setd"), d, DialogModes.NO);
})();

// ---- VWS の見た目そのものの画像レイヤ（TS1 から VWS と同じ描き方で作った PNG）を一番上に置く
//      VWS の縁は「ずらし重ね」＋上下 2 ライン平均なので、Photoshop の文字＋境界線では同じ絵にならない。
//      見た目はこの画像レイヤ、編集は下のテキストレイヤ、と役割を分ける（PSD → TS1 はテキストレイヤを読む）
var here = File($.fileName).parent;
var EXACT = false;
(function(){
  var png = new File(here + "/" + SPEC.ref_png);
  if (!SPEC.ref_png || !png.exists) return;
  var pd = app.open(png);
  var dup = pd.activeLayer.duplicate(doc, ElementPlacement.PLACEATBEGINNING);
  pd.close(SaveOptions.DONOTSAVECHANGES);
  app.activeDocument = doc;
  dup.name = "VWSの見た目（正確な画像・編集不可）";
  dup.visible = true;
  lay.name = "編集用テキスト：" + lay.name;
  // レイヤーカンプ（ウィンドウ > レイヤーカンプ）で 2 つの見え方をワンクリックで切り替えられるようにする
  dup.visible = false; lay.visible = true;
  doc.layerComps.add("② テキスト編集（Photoshop の文字）", "文字を直すときはこちら。直したら「3_PSDをTS1に変換」へ", false, false, true);
  dup.visible = true; lay.visible = false;
  // ※ add() の戻り値に apply() すると、先に登録したカンプが適用されてしまう（Photoshop 2027 で確認）。
  //    登録時点でこの見え方になっているので apply はしない
  doc.layerComps.add("① VWSの見た目（正確な画像）", "VWS で表示される絵そのもの。編集はできません", false, false, true);
  EXACT = true;
})();

app.preferences.rulerUnits = oldRuler; app.preferences.typeUnits = oldType;
var out = new File(here + "/" + SPEC.name + ".psd");
var opt = new PhotoshopSaveOptions(); opt.layers = true;
doc.saveAs(out, opt, true);
alert("保存しました: " + out.fsName + "\nフォント: " + FONT +
      (EXACT ? "\n\n今の表示 = VWS の見た目そのものの画像です。\n文字を直すときは [ウィンドウ] > [レイヤーカンプ] で「② テキスト編集」に切り替えて編集し、\nその PSD を「3_PSDをTS1に変換」に入れてください。" : ""));
"""


def baseline_shift(size, font_fit):
    """VWS のベースライン（CSML.y）→ フォント本来のベースラインまでの縦の差 px（上が負）。
    フォント表の oy（2048 セル単位）から求める。見本のニューロダン B は oy=-183 → 100px で -8.95px"""
    return (font_fit or {}).get("oy", 0.0) * size / CELL


# VWS の絵は、普通に描いた絵（Photoshop のテキスト）に対して見かけ上 (-0.125, -0.375)px ずれる。
# 正体は VWS の描画位置 (-0.0625, +0.094) と上下 2 ライン平均（見かけ -0.5px）の合計（render.py 参照）。
# Photoshop との直接照合でも縦 -0.42px を実測。Photoshop のアンカーにこの分を足し、PSD → TS1 では引く
VWS_RASTER_OFFSET = (-0.125, -0.375)
# cell_ratio がこれより 1 に近いフォントは、Photoshop のサイズ = VWS のサイズにして全角送りを完全に一致させる
# （トラッキングは整数なので 0.018/1000em のような送り補正は表せず、16 文字で 0.3px ずれていた）
UNIT_RATIO_TOL = 0.005
# VWS の PNG と Photoshop の出力を直接照合して決めた値（2026-11-12、見本 1 件・縁 6px。premultiplied 平均差）
#   境界線 6px:  STRONG 3.45% / SMOOTH 3.61% / CRISP 5.46% / SHARP 6.11%（直す前は既定 CRISP で 6.9%）
#   作った直後なら SMOOTH + 境界線 6.25px が 3.30% で最良だが、Photoshop は境界線の太さを保存時に整数へ丸めるので
#   PSD を開き直すと 6px に戻る（レイヤー効果の比率 Scl は描画に効かない）。→ 保存後も保てる STRONG + 整数を採用
PS_ANTIALIAS = "STRONG"
EDGE_PS_EXTRA = 0  # Photoshop の境界線 = VWS の縁 + この値（整数でないと保存で消える）


def ps_size_of(size, cell_ratio):
    """VWS の文字サイズ（セル高）→ Photoshop の文字サイズ（em）"""
    return size if abs(cell_ratio - 1) < UNIT_RATIO_TOL else size / cell_ratio


def vws_size_of(ps_size, cell_ratio):
    """ps_size_of の逆"""
    return ps_size if abs(cell_ratio - 1) < UNIT_RATIO_TOL else ps_size * cell_ratio


def ts1_spec(ts1: TS1, name="telop", color_order=COLOR_ORDER, edge=None, ps_font=None,
             font_hints=(), cell_ratio=1.0, ref_png="", font_fit=None):
    """Photoshop 再現用のパラメータ

    cell_ratio = (winAscent + winDescent) / unitsPerEm（フォントファイルから。不明なら 1）
      VWS の文字サイズはセル高なので Photoshop のサイズ = VWS サイズ / cell_ratio。
      全角の送りを VWS（= セル幅）に合わせるため、差分をトラッキングに足す。
    font_fit = フォント表の {"scale", "ox", "oy"}。oy でアンカーの縦位置をフォントのベースラインに合わせる。
    """
    face, edgec = ts1.face_color(color_order), ts1.edge_color(color_order)
    size = ts1.char_size[0]
    ps_size = ps_size_of(size, cell_ratio)
    extra = (size - ps_size) / ps_size * 1000  # 送り補正（1/1000 em）
    sp = ts1.spacings()
    edge_px = (ts1.edge_width() if edge is None else edge) or 0
    chars = [ch for ch in ts1.chars if ch.code]
    text = "".join("\n" if ch.code in (0x0A, 0x0D) else ch.char for ch in chars)
    return {
        "name": name,
        "text": text,
        "canvas": list(CANVAS),
        "size": size,
        "ps_size": round(ps_size, 3),
        "line_spacing": ts1.line_spacing,
        # VWS の ACSP は「その文字の前」→ Photoshop では 1 つ前の文字のトラッキング
        "tracking": [int(round(sp[i + 1] / ps_size * 1000 + extra)) if i + 1 < len(chars) else 0
                     for i in range(len(chars))],
        "anchor": [ts1.pos[0] + ts1.box[0] / 2 + VWS_RASTER_OFFSET[0],
                   round(ts1.pos[1] + baseline_shift(size, font_fit) + VWS_RASTER_OFFSET[1], 3)],
        "fill": list(face[:3]),
        "edge_color": list(edgec[:3]),
        "edge_opacity": round(edgec[3] / 255 * 100, 1),
        # Photoshop の境界線は VWS の縁より細く描かれるので EDGE_PS_EXTRA だけ太くする
        "edge": edge_px + EDGE_PS_EXTRA if edge_px else 0,
        "antialias": PS_ANTIALIAS,
        "font_id": ts1.font_id,
        "ps_font": ps_font or "",
        "font_hints": [list(h) for h in font_hints],
        "ref_png": ref_png,
    }


def ts1_to_jsx(ts1: TS1, out_path, **kw):
    spec = ts1_spec(ts1, **kw)
    js = JSX_TEMPLATE % {"spec": json.dumps(spec, ensure_ascii=False, indent=1)}
    with open(out_path, "w", encoding="utf-8-sig") as f:  # ExtendScript は BOM 付き UTF-8 が安全
        f.write(js)
    return spec


# ----------------------------------------------------------------------------
# PSD → TS1
# ----------------------------------------------------------------------------
def _color(v):
    """psd-tools のエンジンデータ色 {'Type':1,'Values':[a,r,g,b]} → (r,g,b,a) 0-255"""
    try:
        vals = v["Values"]
        a, r, g, b = [float(x) for x in vals]
        return (round(r * 255), round(g * 255), round(b * 255), round(a * 255))
    except Exception:
        return None


def _read_stroke(layer):
    """レイヤ効果「境界線」を lfx2 / lmfx ディスクリプタから直接読む（FrFX と frameFXMulti の両対応）"""
    from psd_tools.constants import Tag
    for tag in (Tag.OBJECT_BASED_EFFECTS_LAYER_INFO, getattr(Tag, "OBJECT_BASED_EFFECTS_LAYER_INFO_V1", None)):
        if tag is None:
            continue
        try:
            d = layer.tagged_blocks.get_data(tag)
        except Exception:
            d = None
        if not d:
            continue
        if b"masterFXSwitch" in d and not bool(d[b"masterFXSwitch"]):
            return 0, None
        strokes = []
        if b"FrFX" in d:
            strokes.append(d[b"FrFX"])
        if b"frameFXMulti" in d:
            strokes += list(d[b"frameFXMulti"])
        for st in strokes:
            if b"enab" in st and not bool(st[b"enab"]):
                continue
            size = float(st.get(b"Sz  ", 0))
            c = st.get(b"Clr ")
            col = None
            if c is not None:
                col = (round(float(c.get(b"Rd  ", 0))), round(float(c.get(b"Grn ", 0))),
                       round(float(c.get(b"Bl  ", 0))), 255)
            return size, col
    return 0, None


def read_psd_text(path, layer_name=None):
    """PSD から最初の（または名前指定の）テキストレイヤ情報を取り出す"""
    from psd_tools import PSDImage
    psd = PSDImage.open(path)
    target = None
    for layer in psd.descendants():
        if layer.kind == "type" and (layer_name is None or layer.name == layer_name):
            target = layer
            break
    if target is None:
        raise ValueError("テキストレイヤが見つかりません")
    L = target
    text = L.text.replace("\r", "\n").rstrip("\n")
    eng = L.engine_dict
    xx, xy, yx, yy, tx, ty = L.transform
    fonts = [f["Name"] for f in L.resource_dict["FontSet"]]

    runs = eng["StyleRun"]
    lengths = list(runs["RunLengthArray"])
    per_char = []
    for ln, run in zip(lengths, runs["RunArray"]):
        ssd = run["StyleSheet"]["StyleSheetData"]
        per_char += [ssd] * int(ln)
    # Photoshop のデフォルトスタイル（StyleSheetData に無い値の既定値）
    defaults = {}
    try:
        defaults = L.resource_dict["StyleSheetSet"][0]["StyleSheetData"]
    except Exception:
        pass

    def get(ssd, k, dflt=None):
        if k in ssd:
            return ssd[k]
        return defaults.get(k, dflt)

    first = per_char[0] if per_char else {}
    size = float(get(first, "FontSize", 100)) * float(yy)
    tracking = [float(get(ssd, "Tracking", 0)) for ssd in per_char[:len(text)]]
    fill = _color(get(first, "FillColor", None)) or (255, 255, 255, 255)
    font_name = getattr(fonts[int(get(first, "Font", 0))], "value", None) if fonts else None
    just = 0
    try:
        just = int(eng["ParagraphRun"]["RunArray"][0]["ParagraphSheet"]["Properties"]["Justification"])
    except Exception:
        pass
    leading = get(first, "Leading", None)
    auto_lead = get(first, "AutoLeading", True)

    edge, edge_color = _read_stroke(L)

    # カーニング（メトリクス/オプティカル）・文字ツメは PSD の文字スタイルに区別が残らない
    # （AutoKerning は手動でも True）。効いた結果は組み幅 (TySh の bounds) に出るので、
    # 「全角送り + トラッキング」の計算幅と比べて詰まっていたら警告する（VWS では再現できない）
    warnings = []
    try:
        from psd_tools.constants import Tag
        td = L.tagged_blocks.get_data(Tag.TYPE_TOOL_OBJECT_SETTING)
        b = td.text_data[b"bounds"]
        laid = (float(b[b"Rght"]) - float(b[b"Left"])) * float(xx)
        expect, k = 0.0, 0
        for line in text.split("\n"):
            w = sum(size + tracking[k + j] / 1000 * size for j in range(len(line) - 1)) + (size if line else 0)
            expect = max(expect, w)
            k += len(line) + 1
        if expect - laid > 2:
            warnings.append(f"文字が計算より {expect - laid:.1f}px 詰まっています（組み幅 {laid:.1f}px / 全角送り+トラッキング {expect:.1f}px）。"
                            "カーニング（メトリクス/オプティカル）か文字ツメが効いています。VWS では再現できないので、"
                            "Photoshop の文字パネルでカーニングを「0」、ツメを 0% にしてください")
    except Exception:
        pass
    if any(float(get(ssd, "Tsume", 0) or 0) for ssd in per_char[:len(text)]):
        warnings.append("文字ツメ（Tsume）が設定されています。VWS では再現できません")
    if any(abs(float(get(ssd, k, 1.0)) - 1.0) > 1e-3 for ssd in per_char[:len(text)]
           for k in ("HorizontalScale", "VerticalScale")):
        warnings.append("垂直比率・水平比率（長体/平体）が 100% ではありません。今の変換では反映されません")

    return {
        "layer": L.name,
        "text": str(text),
        "font": font_name,
        "size": size,
        "tracking": tracking,
        "justification": {0: "left", 1: "right", 2: "center"}.get(just, "left"),
        "anchor": (float(tx), float(ty)),
        "fill": fill,
        "edge": edge,
        "edge_color": edge_color,
        "leading": float(leading) * float(yy) if (leading is not None and not auto_lead) else None,
        "canvas": (psd.width, psd.height),
        "warnings": warnings,
    }


def psd_to_ts1(psd_path, template: TS1, font_path=None, font_index=0, layer_name=None, font_id=None,
               color_order=COLOR_ORDER, clear_history=False, font_fit=None, prefer_template=False, glyph_font=None):
    """prefer_template=True: テンプレートと同じフォント番号なら、テンプレートにある文字は VWS の字形をそのまま使う
    glyph_font = 新しい字形を作る元（gdiglyph.GdiGlyphSource など）。無ければ font_path のフォントファイルから作る"""
    info = read_psd_text(psd_path, layer_name)
    text = info["text"]
    font = FontGlyphSource(font_path, font_index, fit=font_fit) if font_path else None
    ratio = font.cell_ratio if font else 1.0
    font = glyph_font or font
    ps_size = info["size"]
    size = round(vws_size_of(ps_size, ratio), 3)  # Photoshop の em → VWS のセル高（ts1_spec の逆）
    n = len(text)
    tr = (info["tracking"] + [0] * n)[:n]
    # Photoshop のトラッキングは「その文字の後ろ」、VWS の ACSP は「その文字の前」に効く
    # → VWS の i 文字目 = Photoshop の i-1 文字目のトラッキング（改行の直後は 0）
    #    さらに全角送りの差 (VWS はセル幅 size、Photoshop は em = ps_size) を差し引く
    def snap(v):  # Photoshop のトラッキングは整数なので、0.1px 未満の端数は丸める
        return float(round(v)) if abs(v - round(v)) < 0.1 else round(v, 3)
    after = [snap(t / 1000 * ps_size - (size - ps_size)) for t in tr]
    spacing = [0.0] + [0.0 if text[i - 1] == "\n" else after[i - 1] for i in range(1, n)] + [0.0]
    chars, glyphs = build_text(template, text, font=font, spacing=spacing,
                               size=(size, size), font_id=font_id, prefer_template=prefer_template)
    # 位置: Photoshop のアンカー（ベースライン）→ VWS の枠（中央揃え）
    lines = text.split("\n")
    widths, k = [], 0
    for line in lines:
        w = len(line) * size + sum(spacing[k + 1:k + len(line)]) if line else 0
        widths.append(w)
        k += len(line) + 1
    w0 = widths[-1]
    ax, ay = info["anchor"]
    ax, ay = ax - VWS_RASTER_OFFSET[0], ay - VWS_RASTER_OFFSET[1]  # ts1_spec で足した VWS の描画の癖を戻す
    left ={"left": ax, "center": ax - w0 / 2, "right": ax - w0}[info["justification"]]
    box_w = template.box[0]
    # Photoshop のアンカーはフォント本来のベースライン → VWS のベースラインへ戻す（ts1_spec の逆）
    ay -= baseline_shift(size, font_fit)
    pos = (left + w0 / 2 - box_w / 2, ay + (len(lines) - 1) * (size + template.line_spacing))

    # 境界線が無ければテンプレートの縁を維持。ts1_spec で足した EDGE_PS_EXTRA を引いて VWS の整数の縁幅に戻す
    edge = max(1, int(round(info["edge"] - EDGE_PS_EXTRA))) if info["edge"] else None
    out = template.rebuild(chars=chars, glyphs=glyphs, pos=pos,
                           face_color=info["fill"], edge_color=info["edge_color"],
                           clear_history=clear_history, edge_width=edge, color_order=color_order)
    return out, info
