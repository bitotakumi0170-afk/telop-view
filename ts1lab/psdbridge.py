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
def _decorated(ts1):
    """文字面 + 縁 1 本（+ 影）より複雑な装飾か（そのときは vwslayers で層を重ねて描く）"""
    try:
        return not ts1.is_plain()
    except Exception:
        return False


def ts1_to_png(ts1: TS1, out_path, color_order=COLOR_ORDER, edge=None):
    """キャンバス全体（1920×1080）の透過 PNG"""
    if _decorated(ts1) and edge is None:
        from PIL import Image
        import vwslayers
        out, _ = vwslayers.render_layers(ts1, CANVAS, color_order)
        Image.fromarray(out, "RGBA").save(out_path)
        return out_path
    # VWS と同じ手順（8 倍の整数格子・GDI の塗り・上下 2 ライン・ずらし重ねの縁・整数の色計算）で描く
    import vwsrender
    vwsrender.render_image(ts1, edge=edge, canvas=CANVAS, color_order=color_order).save(out_path)
    return out_path


TEXTURE_GROUP = "模様（VWS の文字面に貼る画像。編集すると .ts1 に戻ります）"


def texture_layer_name(index, plane):
    return f"模様 {index} 面 {plane}"


def texture_images(ts1: TS1):
    """模様の画像 → [(リソースの添字, 面, レイヤ名, RGB の PIL 画像)]。R = G = B = .ts1 の中の値 0〜255"""
    import wpf
    from PIL import Image
    out = []
    for i, tag, tx in wpf.textures_of(ts1):
        for k, pl in enumerate(tx.planes()):
            out.append((i, k, texture_layer_name(i, k), Image.fromarray(pl, "L").convert("RGB")))
    return out


def read_texture_layers(psd_path):
    """PSD の「模様 i 面 k」レイヤ → {(i, k): uint8 の配列（レイヤの左上から幅 × 高さ。キャンバスの外は 0）}"""
    import re
    import numpy as np
    from psd_tools import PSDImage
    psd = PSDImage.open(psd_path)
    out = {}
    for layer in psd.descendants():
        m = re.fullmatch(r"模様 (\d+) 面 (\d+)", layer.name or "")
        if not m or layer.kind not in ("pixel", "shape", "smartobject"):
            continue
        im = layer.topil()
        if im is None:
            continue
        a = np.asarray(im.convert("RGB"), np.uint8)[..., 0]
        out[(int(m.group(1)), int(m.group(2)))] = (layer.left, layer.top, a)
    return out


def apply_texture_layers(out_bytes, template: TS1, layers):
    """psd_to_ts1 の結果に、PSD の模様レイヤを書き戻す。模様の番号は雛形の番号（何番目の模様か、で対応させる）"""
    import numpy as np
    import wpf
    if not layers:
        return out_bytes, 0
    t = TS1(out_bytes)
    src = wpf.textures_of(template)
    dst = wpf.textures_of(t)
    res = list(t.resources)
    changed = 0
    for (si, _, stx), (di, tag, dtx) in zip(src, dst):
        planes = dtx.planes()
        new = list(planes)
        for k in range(len(planes)):
            if (si, k) not in layers:
                continue
            left, top, a = layers[(si, k)]
            full = np.zeros((dtx.height, dtx.width), np.uint8)
            h, w = a.shape
            x0, y0 = max(left, 0), max(top, 0)
            x1, y1 = min(left + w, dtx.width), min(top + h, dtx.height)
            if x1 > x0 and y1 > y0:
                full[y0:y1, x0:x1] = a[y0 - top:y1 - top, x0 - left:x1 - left]
            if not np.array_equal(full, planes[k]):
                new[k] = full
                changed += 1
        if any(n is not o for n, o in zip(new, planes)):
            res[di] = (tag, dtx.replaced(new))
    if not changed:
        return out_bytes, 0
    return t.rebuild_resources(res), changed


def ts1_to_psd(ts1: TS1, out_path, color_order=COLOR_ORDER, edge=None):
    from psd_tools import PSDImage
    import vwsrender
    face, edge_img, (l, t) = vwsrender.render_layers(ts1, edge=edge, canvas=CANVAS, color_order=color_order)
    psd = PSDImage.new("RGBA", CANVAS, color=(0, 0, 0, 0))
    if _decorated(ts1) and edge is None:
        _add_decoration_layers(psd, ts1, color_order)
        _add_texture_group(psd, ts1)
        psd.save(out_path)
        return out_path
    e = psd.create_pixel_layer(edge_img, name="edge", top=t, left=l)
    f = psd.create_pixel_layer(face, name="face", top=t, left=l)
    g = psd.create_group([e, f], name="TS1")
    # 日本語名は Unicode レイヤ名ブロックに入る（setter 経由）
    e.name, f.name, g.name = "縁", "文字", f"TS1: {ts1.text}"[:120]
    _add_texture_group(psd, ts1)
    psd.save(out_path)
    return out_path


def _add_decoration_layers(psd, ts1, color_order=COLOR_ORDER):
    """装飾の層を 1 枚ずつピクセルレイヤにする（下の層から積む）。グループ名に文言"""
    from PIL import Image
    import vwslayers
    _, layers = vwslayers.render_layers(ts1, CANVAS, color_order)
    made = []
    for name, L in reversed(layers):
        im = Image.fromarray(L, "RGBA")
        bb = im.getbbox()
        if not bb:
            continue
        ly = psd.create_pixel_layer(im.crop(bb), name="layer", top=bb[1], left=bb[0])
        ly.name = f"装飾 {name}"
        made.append(ly)
    if made:
        g = psd.create_group(made, name="TS1")
        g.name = f"TS1: {ts1.text}"[:120]


def _add_texture_group(psd, ts1):
    tex = texture_images(ts1)
    if tex:
        layers = []
        for i, k, name, im in tex:
            ly = psd.create_pixel_layer(im.convert("RGBA"), name="tex", top=0, left=0)
            ly.name = name
            ly.visible = False
            layers.append(ly)
        tg = psd.create_group(layers, name="textures")
        tg.name = TEXTURE_GROUP
        tg.visible = False


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
if (!FONT) { alert("フォントが見つかりません: " + (SPEC.ps_font || SPEC.font_hints.join(" / ")) + "\n仮のフォントで作成します。"); FONT = "MS-Gothic"; }

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
    base.putDouble(s("horizontalScale"), SPEC.hscale[i]);   // 文字ごとの倍率（パーセント。VWS の ACMG）
    base.putDouble(s("verticalScale"), SPEC.vscale[i]);
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

// ---- 模様（VWS の文字面に貼る画像）を隠したレイヤで入れておく。PSD → TS1 でこのレイヤを読み戻す
(function(){
  if (!SPEC.textures || !SPEC.textures.length) return;
  var set = doc.layerSets.add();
  set.name = SPEC.texture_group;
  for (var i = 0; i < SPEC.textures.length; i++) {
    var f = new File(here + "/" + SPEC.textures[i].png);
    if (!f.exists) continue;
    var pd = app.open(f);
    var dup = pd.activeLayer.duplicate(set, ElementPlacement.INSIDE);
    pd.close(SaveOptions.DONOTSAVECHANGES);
    app.activeDocument = doc;
    dup.name = SPEC.textures[i].name;
    dup.visible = false;
  }
  set.visible = false;
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


# VWS の専用空白（輪郭が無く、送りが全角の 1/4 = 512）⇄ Photoshop の 1/4 em 空白
BLANK_TO_PS = {0xE73E: "\u2005"}
BLANK_FROM_PS = {"\u2005": "\ue73e"}
# Photoshop は、水平比率を掛けた字でもトラッキングには比率を掛けない（別サンプルの組み幅で確認: 掛けると 1 行目が 5px 狭くなった）
TRACKING_SCALES = False


def ps_size_of(size, cell_ratio):
    """VWS の文字サイズ（セル高）→ Photoshop の文字サイズ（em）"""
    return size if abs(cell_ratio - 1) < UNIT_RATIO_TOL else size / cell_ratio


def vws_size_of(ps_size, cell_ratio):
    """ps_size_of の逆"""
    return ps_size if abs(cell_ratio - 1) < UNIT_RATIO_TOL else ps_size * cell_ratio


def ts1_spec(ts1: TS1, name="telop", color_order=COLOR_ORDER, edge=None, ps_font=None,
             font_hints=(), cell_ratio=1.0, ref_png="", font_fit=None, textures=()):
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
    # VWS の専用空白 U+E73E（全角の 1/4 送り）は、Photoshop では 1/4 em の空白 U+2005 にする（PSD → TS1 で戻す）
    text = "".join("\n" if ch.code in (0x0A, 0x0D) else BLANK_TO_PS.get(ch.code, ch.char) for ch in chars)
    mg = ts1.magnifications()
    n_lines = text.count("\n") + 1
    return {
        "name": name,
        "text": text,
        "canvas": list(CANVAS),
        "size": size,
        "ps_size": round(ps_size, 3),
        "line_spacing": ts1.line_spacing,
        # VWS の ACSP は「その文字の前」→ Photoshop では 1 つ前の文字のトラッキング
        # 行末の字（改行の前・最後の字）のトラッキングは 0 にする: Photoshop の中央揃えは行末のトラッキングも幅に数えるので、
        # 残すと行がその半分だけ横へずれる（VWS では改行や終端の前の字間は並びに効かない）
        "tracking": [int(round((sp[i + 1] / ps_size * 1000 + extra) / (mg[i][0] / 100 if TRACKING_SCALES else 1)))
                     if (i + 1 < len(chars) and text[i] != "\n" and text[i + 1] != "\n") else 0 for i in range(len(chars))],
        "hscale": [round(mg[i][0], 3) for i in range(len(chars))],
        "vscale": [round(mg[i][1], 3) for i in range(len(chars))],
        # Photoshop のアンカーは 1 行目のベースライン、VWS の y は最終行のベースライン
        "anchor": [ts1.pos[0] + ts1.box[0] / 2 + VWS_RASTER_OFFSET[0],
                   round(ts1.pos[1] - (n_lines - 1) * (size + ts1.line_spacing)
                         + baseline_shift(size, font_fit) + VWS_RASTER_OFFSET[1], 3)],
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
        "textures": [{"png": png, "name": name} for png, name in textures],
        "texture_group": TEXTURE_GROUP,
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
    hscale = [float(get(ssd, "HorizontalScale", 1.0) or 1.0) * 100 for ssd in per_char[:len(text)]]
    vscale = [float(get(ssd, "VerticalScale", 1.0) or 1.0) * 100 for ssd in per_char[:len(text)]]
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
        import unicodedata
        plain = all(ch == "\n" or unicodedata.east_asian_width(ch) in ("W", "F") for ch in text) \
            and all(abs(h - 100) < 0.01 for h in hscale)
        expect, k = 0.0, 0
        for line in text.split("\n"):
            w = sum(size + tracking[k + j] / 1000 * size for j in range(len(line) - 1)) + (size if line else 0)
            expect = max(expect, w)
            k += len(line) + 1
        if plain and expect - laid > 2:      # 半角や倍率つきの字があるときは「全角送り」で比べられないので見ない
            warnings.append(f"文字が計算より {expect - laid:.1f}px 詰まっています（組み幅 {laid:.1f}px / 全角送り+トラッキング {expect:.1f}px）。"
                            "カーニング（メトリクス/オプティカル）か文字ツメが効いています。VWS では再現できないので、"
                            "Photoshop の文字パネルでカーニングを「0」、ツメを 0% にしてください")
    except Exception:
        pass
    if any(float(get(ssd, "Tsume", 0) or 0) for ssd in per_char[:len(text)]):
        warnings.append("文字ツメ（Tsume）が設定されています。VWS では再現できません")
    return {
        "layer": L.name,
        "text": str(text),
        "font": font_name,
        "size": size,
        "tracking": tracking,
        "hscale": hscale,
        "vscale": vscale,
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
    if getattr(template, "kind", "CSML") == "CSSL" and "\n" in text:
        raise ValueError("雛形が枠なしの 1 行ものです。改行のある文言は戻せません（枠つきの .ts1 を雛形にしてください）")
    if any(g.code == 0xE73E for g in template.glyphs):     # 雛形が専用空白を使っているときだけ、1/4 em 空白を専用空白に戻す
        text = "".join(BLANK_FROM_PS.get(ch, ch) for ch in text)
    font = FontGlyphSource(font_path, font_index, fit=font_fit) if font_path else None
    ratio = font.cell_ratio if font else 1.0
    font = glyph_font or font
    ps_size = info["size"]
    size = round(vws_size_of(ps_size, ratio), 3)  # Photoshop の em → VWS のセル高（ts1_spec の逆）
    n = len(text)
    tr = (info["tracking"] + [0] * n)[:n]
    hs = (info.get("hscale", []) + [100.0] * n)[:n]
    vs = (info.get("vscale", []) + [100.0] * n)[:n]
    mags = [(round(h, 3), round(v, 3)) for h, v in zip(hs, vs)]
    # Photoshop のトラッキングは「その文字の後ろ」、VWS の ACSP は「その文字の前」に効く
    # → VWS の i 文字目 = Photoshop の i-1 文字目のトラッキング（改行の直後は 0）
    #    さらに全角送りの差 (VWS はセル幅 size、Photoshop は em = ps_size) を差し引く
    def snap(v):  # Photoshop のトラッキングは整数なので、0.1px 未満の端数は丸める
        return float(round(v)) if abs(v - round(v)) < 0.1 else round(v, 3)
    after = [snap(t / 1000 * ps_size * (hs[i] / 100 if TRACKING_SCALES else 1) - (size - ps_size)) for i, t in enumerate(tr)]
    # 改行と行頭の字間は並びに効かない。VWS のファイルでは「基準の字間」（先頭文字と同じ値）が入っているので、それに合わせる
    base_sp = (template.chars[0].spacing() if template.chars else None) or 0.0
    spacing = [0.0] + [base_sp if (text[i - 1] == "\n" or text[i] == "\n") else after[i - 1] for i in range(1, n)] + [0.0]
    uniform = all(m == mags[0] for m in mags) if mags else True
    tm = template.magnifications()[0] if template.chars else (100.0, 100.0)
    chars, glyphs = build_text(template, text, font=font, spacing=spacing,
                               size=(size, size), font_id=font_id, prefer_template=prefer_template,
                               mags=None if (uniform and (not mags or mags[0] == tm)) else mags)
    # 位置: Photoshop のアンカー（1 行目のベースライン）→ VWS の枠（中央揃え）と最終行のベースライン
    by_code = {g.code: g for g in glyphs}
    lines = text.split("\n")
    widths, k = [], 0
    for line in lines:
        w = 0.0
        for j, ch in enumerate(line):
            g = by_code.get(ord(ch))
            w += (g.advance_fx(size * hs[k + j] / 100) / 256 if g else size * hs[k + j] / 100) + (spacing[k + j] if j else 0.0)
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

    # 境界線が無ければテンプレートの縁を維持。太さが雛形の縁（太さ + 追加分）と同じなら触らない
    edge = max(1, int(round(info["edge"] - EDGE_PS_EXTRA))) if info["edge"] else None
    if edge is not None and edge == (template.edge_width() or 0):
        edge = None
    out = template.rebuild(chars=chars, glyphs=glyphs, pos=pos,
                           face_color=info["fill"], edge_color=info["edge_color"],
                           clear_history=clear_history, edge_width=edge, color_order=color_order)
    # 模様のレイヤ（「模様 i 面 k」）があれば、その画像で .ts1 の模様を書き戻す（変わっていなければ元のバイト列のまま）
    out, n_tex = apply_texture_layers(out, template, read_texture_layers(psd_path))
    if n_tex:
        info.setdefault("warnings", []).append(f"模様の画像 {n_tex} 枚を PSD のレイヤから書き戻しました")
    return out, info
