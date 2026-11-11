"""TS1 ⇄ Photoshop の橋渡し

  TS1 → PSD : 文字面・縁をピクセルレイヤで書き出し（psd-tools）
  TS1 → JSX : Photoshop で実行すると「編集可能なテキストレイヤ＋境界線効果」を作るスクリプト
  PSD → TS1 : PSD のテキストレイヤ（文字・サイズ・トラッキング・位置・色・境界線）を読み、
              テンプレート TS1 の書式で組み直す
"""
from __future__ import annotations

import json

from ts1core import TS1, FontGlyphSource, build_text
from render import render_layers

CANVAS = (1920, 1080)


# ----------------------------------------------------------------------------
# TS1 → PSD（ピクセルレイヤ）
# ----------------------------------------------------------------------------
def ts1_to_psd(ts1: TS1, out_path, color_order="BGRA", edge=None):
    from psd_tools import PSDImage
    face, edge_img, (l, t) = render_layers(ts1, edge=edge, color_order=color_order)
    psd = PSDImage.new("RGBA", CANVAS, color=(0, 0, 0, 0))
    e = psd.create_pixel_layer(edge_img, name="edge", top=t, left=l)
    f = psd.create_pixel_layer(face, name="face", top=t, left=l)
    g = psd.create_group([e, f], name="TS1")
    # 日本語名は Unicode レイヤ名ブロックに入る（setter 経由）
    e.name, f.name, g.name = "縁", "文字", f"TS1: {ts1.text}"[:120]
    psd.save(out_path)
    return out_path


# ----------------------------------------------------------------------------
# TS1 → JSX（Photoshop で編集可能なテキストレイヤを生成）
# ----------------------------------------------------------------------------
JSX_TEMPLATE = r"""// TS1 → Photoshop テキストレイヤ生成スクリプト（ts1lab で自動生成）
// 使い方: Photoshop で [ファイル] > [スクリプト] > [参照...] からこのファイルを実行
// ※ VWS のフォント番号 (%(font_id)s) と Photoshop のフォントの対応は未確定。FONT を手で合わせてください。
#target photoshop
var FONT = "%(ps_font)s";            // PostScript 名（例: KozGoPr6N-Heavy）
var SPEC = %(spec)s;

function c(s){return charIDToTypeID(s);} function s(x){return stringIDToTypeID(x);}
app.preferences.rulerUnits = Units.PIXELS;
app.preferences.typeUnits = TypeUnits.PIXELS;
var doc = app.documents.add(SPEC.canvas[0], SPEC.canvas[1], 72, "TS1_" + SPEC.text.substr(0,10),
                            NewDocumentMode.RGB, DocumentFill.TRANSPARENT);
var lay = doc.artLayers.add();
lay.kind = LayerKind.TEXT;
lay.name = SPEC.text;
var ti = lay.textItem;
ti.contents = SPEC.text.replace(/\n/g, "\r");
ti.font = FONT;
ti.size = new UnitValue(SPEC.size, "px");
ti.justification = Justification.CENTER;
var col = new SolidColor(); col.rgb.red = SPEC.fill[0]; col.rgb.green = SPEC.fill[1]; col.rgb.blue = SPEC.fill[2];
ti.color = col;
ti.leading = new UnitValue(SPEC.size + SPEC.line_spacing, "px");
ti.position = [new UnitValue(SPEC.anchor[0], "px"), new UnitValue(SPEC.anchor[1], "px")];

// 文字ごとのトラッキング（VWS の字間 px → 1/1000 em）
(function(){
  var ref = new ActionReference(); ref.putEnumerated(c("Lyr "), c("Ordn"), c("Trgt"));
  var td = executeActionGet(ref).getObjectValue(s("textKey"));
  var base = td.getList(s("textStyleRange")).getObjectValue(0).getObjectValue(s("textStyle"));
  var list = new ActionList();
  for (var i = 0; i < SPEC.tracking.length; i++) {
    var r = new ActionDescriptor();
    r.putInteger(s("from"), i); r.putInteger(s("to"), i + 1);
    base.putInteger(s("tracking"), SPEC.tracking[i]);
    base.putUnitDouble(s("size"), c("#Pxl"), SPEC.size);
    r.putObject(s("textStyle"), s("textStyle"), base);
    list.putObject(s("textStyleRange"), r);
  }
  td.putList(s("textStyleRange"), list);
  var d = new ActionDescriptor(); d.putReference(c("null"), ref);
  d.putObject(c("T   "), s("textLayer"), td);
  executeAction(c("setd"), d, DialogModes.NO);
})();

// 縁 = レイヤスタイル「境界線（外側）」
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
  st.putUnitDouble(c("Opct"), c("#Prc"), 100);
  st.putUnitDouble(c("Sz  "), c("#Pxl"), SPEC.edge);
  var k = new ActionDescriptor();
  k.putDouble(c("Rd  "), SPEC.edge_color[0]); k.putDouble(c("Grn "), SPEC.edge_color[1]); k.putDouble(c("Bl  "), SPEC.edge_color[2]);
  st.putObject(c("Clr "), c("RGBC"), k);
  fx.putObject(c("FrFX"), c("FrFX"), st);
  d.putObject(c("T   "), c("Lefx"), fx);
  executeAction(c("setd"), d, DialogModes.NO);
})();
"""


def ts1_spec(ts1: TS1, color_order="BGRA", edge=None):
    cols = ts1.colors(color_order)
    size = ts1.char_size[0]
    sp = ts1.spacings()
    chars = [ch for ch in ts1.chars if ch.code]
    text = "".join("\n" if ch.code in (0x0A, 0x0D) else ch.char for ch in chars)
    return {
        "text": text,
        "canvas": list(CANVAS),
        "size": size,
        "line_spacing": ts1.line_spacing,
        # VWS の ACSP は「その文字の前」→ Photoshop では 1 つ前の文字のトラッキング
        "tracking": [int(round(sp[i + 1] / size * 1000)) if i + 1 < len(chars) else 0
                     for i in range(len(chars))],
        "anchor": [ts1.pos[0] + ts1.box[0] / 2, ts1.pos[1]],
        "fill": list(cols[0][:3]),
        "edge_color": list(cols[1][:3]) if len(cols) > 1 else [0, 0, 0],
        "edge": (ts1.edge_width() if edge is None else edge) or 0,
        "font_id": ts1.font_id,
    }


def ts1_to_jsx(ts1: TS1, out_path, ps_font="KozGoPr6N-Heavy", color_order="BGRA", edge=None):
    spec = ts1_spec(ts1, color_order, edge)
    js = JSX_TEMPLATE % {"spec": json.dumps(spec, ensure_ascii=False), "ps_font": ps_font,
                         "font_id": spec["font_id"]}
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
    }


def psd_to_ts1(psd_path, template: TS1, font_path=None, layer_name=None, font_id=None,
               color_order="BGRA", clear_history=False):
    info = read_psd_text(psd_path, layer_name)
    text = info["text"]
    size = round(info["size"], 2)
    n = len(text)
    tr = (info["tracking"] + [0] * n)[:n]
    # Photoshop のトラッキングは「その文字の後ろ」、VWS の ACSP は「その文字の前」に効く
    # → VWS の i 文字目 = Photoshop の i-1 文字目のトラッキング（改行の直後は 0）
    after = [round(t / 1000 * size, 3) for t in tr]
    spacing = [0.0] + [0.0 if text[i - 1] == "\n" else after[i - 1] for i in range(1, n)] + [0.0]
    font = FontGlyphSource(font_path) if font_path else None
    chars, glyphs = build_text(template, text, font=font, spacing=spacing,
                               size=(size, size), font_id=font_id)
    # 位置: Photoshop のアンカー（ベースライン）→ VWS の枠（中央揃え）
    lines = text.split("\n")
    widths, k = [], 0
    for line in lines:
        w = len(line) * size + sum(spacing[k + 1:k + len(line)]) if line else 0
        widths.append(w)
        k += len(line) + 1
    w0 = widths[-1]
    ax, ay = info["anchor"]
    left = {"left": ax, "center": ax - w0 / 2, "right": ax - w0}[info["justification"]]
    box_w = template.box[0]
    pos = (left + w0 / 2 - box_w / 2, ay + (len(lines) - 1) * (size + template.line_spacing))

    def to_store(c):
        if c is None:
            return None
        r, g, b, a = c
        return [r, g, b, a] if color_order == "RGBA" else [b, g, r, a]

    colors = [to_store(info["fill"])]
    if info["edge_color"]:
        colors.append(to_store(info["edge_color"]))
    edge = int(round(info["edge"])) if info["edge"] else None
    out = template.rebuild(chars=chars, glyphs=glyphs, pos=pos, colors=colors,
                           clear_history=clear_history, edge_width=edge)
    return out, info
