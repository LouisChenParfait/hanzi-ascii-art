#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ascii_cn.py — 中文 / 任意文字 ASCII 艺术字生成器（命令行版）

原理：不枚举汉字字模，而是用系统 TrueType 字体把文字渲染成高分辨率位图，
      再按「字符 cell」降采样成灰阶，映射成字符。因此任意汉字、任意字体都支持。

用法：
  python ascii_cn.py "你好世界"
  python ascii_cn.py "你好世界" -f msyhbd --w 80 -H 14 -c block
  python ascii_cn.py "陸老六" -f simkai -c half --ansi --color cyan
  python ascii_cn.py "HELLO 中文" -f simhei -c custom --custom "六"
  python ascii_cn.py -l                      # 列出可用字体

输出：默认打印到 stdout，可用 -o 写文件（UTF-8）。
"""

import argparse
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------- 字体表
# Windows 系统字体目录；其它平台自动退化到常见路径
FONT_DIRS = [
    os.environ.get("WINDIR", r"C:\Windows") + r"\Fonts",
    "/usr/share/fonts",
    "/System/Library/Fonts",
    os.path.expanduser("~/Library/Fonts"),
]

# 别名 -> 文件名（Windows）。带多个候选，取第一个存在的
FONT_ALIASES = {
    "yahei":     ["msyhbd.ttc", "msyh.ttc"],
    "yahei-l":   ["msyhl.ttc", "msyh.ttc"],
    "hei":       ["simhei.ttf"],
    "song":      ["simsun.ttc", "simsunb.ttf"],  # 注意 simsunb 是扩展B字库，不含常用字
    "kai":       ["simkai.ttf"],
    "deng":      ["Dengb.ttf", "Deng.ttf"],
    "deng-l":    ["Dengl.ttf", "Deng.ttf"],
    "ming":      ["msjhbd.ttc", "msjh.ttc"],
    "mono":      ["consolab.ttf", "consola.ttf"],
}

CHARSETS = {
    "ascii":  " .:-=+*#%@",          # 纯 ASCII 灰阶，兼容性最好
    "block":  " ░▒▓█",               # 方块灰阶，视觉最像艺术字
    "solid":  " █",                   # 纯黑白，轮廓最硬
    "dots":   " ·:●█",               # 点阵感
    "stars":  " .·✦*★",              # 装饰感
    "binary": " 01",                 # 按列位置取 0/1，黑客帝国味
    "half":   " ▀▄█",                # 半块字符，纵向分辨率翻倍
}


def resolve_font(name):
    """把字体别名 / 文件名 / 绝对路径解析成真实路径"""
    if name and os.path.isfile(name):
        return name
    key = (name or "yahei").lower()
    for ext in ("", ".ttf", ".ttc", ".otf"):
        cand = key + ext
        for d in FONT_DIRS:
            p = os.path.join(d, cand)
            if os.path.isfile(p):
                return p
    if key in FONT_ALIASES:
        for fn in FONT_ALIASES[key]:
            for d in FONT_DIRS:
                p = os.path.join(d, fn)
                if os.path.isfile(p):
                    return p
    # 最后兜底：递归找
    for d in FONT_DIRS:
        if not os.path.isdir(d):
            continue
        for root, _, files in os.walk(d):
            if key in [os.path.splitext(f)[0].lower() for f in files]:
                for f in files:
                    if os.path.splitext(f)[0].lower() == key:
                        return os.path.join(root, f)
    raise SystemExit("找不到字体：%s（用 -l 查看可用别名，或直接给 .ttf 路径）" % name)


def list_fonts():
    print("预设别名：")
    for k, v in FONT_ALIASES.items():
        try:
            print("  %-10s -> %s" % (k, os.path.basename(resolve_font(k))))
        except SystemExit:
            print("  %-10s -> (本机无此字体)" % k)
    print("\n也可以直接传字体文件路径或文件名，例如 -f simhei.ttf")
    print("    -f \"C:/Windows/Fonts/msyhbd.ttc\"")


# ---------------------------------------------------------------- 渲染核心
CELL_W, CELL_H = 8, 16   # 等宽字符 cell 视觉宽高比约 1:2
LINE_GAP = 1.12          # 多行文字的行距系数
PROBE = 200              # 测量用基准字号


def measure(lines, font_path):
    """测量文字的像素宽 / 高，供自适应排版用（与最终字号无关）"""
    f = ImageFont.truetype(font_path, PROBE)
    d = ImageDraw.Draw(Image.new("L", (1, 1)))
    max_w, total_h = 1, 0
    for line in lines:
        b = d.textbbox((0, 0), line or "字", font=f)
        max_w = max(max_w, b[2] - b[0])
        total_h += max(1, b[3] - b[1]) * LINE_GAP
    return max_w, total_h


def auto_rows(lines, font_path, cols, aspect=0.5):
    """按字形真实比例自动算输出行数：不变形，也不留上下大块空白。

    aspect = cell 视觉(宽/高)。半角等宽字符 = 0.5；全角汉字 = 1.0。
    """
    mw, mh = measure(lines, font_path)
    return max(2, min(240, int(round(cols * (mh / mw) * aspect))))


def render_bitmap(lines, font_path, cols, rows, bold=0, invert=False,
                  gamma=1.0, threshold=0.5, padding=0.35,
                  cell_w=CELL_W, cell_h=CELL_H,
                  sharp=1, contrast=1.4):
    """把若干行文字渲染成 cols x rows 的灰度矩阵（0..1，1 = 笔画）"""
    W, H = cols * cell_w, rows * cell_h
    pad = int(padding * cell_w)
    inner_w, inner_h = max(1, W - 2 * pad), max(1, H - 2 * pad)

    mw, mh = measure(lines, font_path)
    scale = min(inner_w / mw, inner_h / mh)
    font = ImageFont.truetype(font_path, max(6, int(PROBE * scale)))

    img = Image.new("L", (W, H), 0)
    draw = ImageDraw.Draw(img)
    boxes = [draw.textbbox((0, 0), ln or " ", font=font, stroke_width=bold)
             for ln in lines]
    line_h = max(b[3] - b[1] for b in boxes) * LINE_GAP
    y = (H - line_h * len(lines)) / 2
    for line, b in zip(lines, boxes):
        tw = b[2] - b[0]
        x = (W - tw) / 2 - b[0]
        yy = y + (line_h - (b[3] - b[1])) / 2 - b[1]
        draw.text((x, yy), line, font=font, fill=255,
                  stroke_width=bold, stroke_fill=255)
        y += line_h

    # 降采样：每个字符 cell 求平均亮度（自带抗锯齿）
    small = img.resize((cols, rows), Image.Resampling.BOX)
    px = small.load()
    grid = []
    for r in range(rows):
        row = []
        for c in range(cols):
            v = px[c, r] / 255.0
            if sharp == 2:                       # 纯二值，笔画最硬
                v = 1.0 if v >= threshold else 0.0
            else:
                v = max(0.0, min(1.0, (v - (threshold - 0.5)) * contrast))
                v = v ** gamma
            if invert:
                v = 1.0 - v
            row.append(v)
        grid.append(row)
    return grid


def grid_to_text(grid, charset, custom=None, binary_seed=0):
    """灰度矩阵 -> 字符文本"""
    rows, cols = len(grid), len(grid[0])
    out = []

    if charset == "custom":
        # 字中字玩法：强制二值，用用户指定的字符（可以是汉字）铺满笔画
        ink = (custom or "█").strip() or "█"
        # 全角字符在等宽字体里占 2 列，空白也要补 2 个半角空格才对齐
        blank = "  " if _is_wide(ink[0]) else " "
        for r in range(rows):
            out.append("".join(ink if grid[r][c] > 0.35 else blank
                               for c in range(cols)).rstrip())
        return "\n".join(out)

    ramp = " 01" if charset == "binary" else CHARSETS[charset]

    if charset == "binary":
        for r in range(rows):
            line = []
            for c in range(cols):
                v = grid[r][c]
                if v < 0.45:
                    line.append(" ")
                else:
                    line.append(str((r * 3 + c + binary_seed) % 2))
            out.append("".join(line).rstrip())
    else:
        n = len(ramp)
        for r in range(rows):
            line = []
            for c in range(cols):
                v = grid[r][c]
                if v < 0.06:
                    line.append(ramp[0])
                    continue
                idx = max(1, min(n - 1, int(v * (n - 1) + 0.5)))
                line.append(ramp[idx])
            out.append("".join(line).rstrip())
    return "\n".join(out)


def _is_wide(ch):
    """粗略判断全角（CJK / 全角符号在等宽字体里占 2 列）"""
    o = ord(ch)
    return (0x1100 <= o <= 0x115F or 0x2E80 <= o <= 0xA4CF
            or 0xAC00 <= o <= 0xD7A3 or 0xF900 <= o <= 0xFAFF
            or 0xFE10 <= o <= 0xFE6F or 0xFF00 <= o <= 0xFF60
            or 0xFFE0 <= o <= 0xFFE6 or 0x1F300 <= o <= 0x1FAFF)


def halfblock_text(lines, font_path, cols, rows, **kw):
    """半块字符模式：纵向采样翻倍，用 ▀▄█ 表达，垂直分辨率 x2"""
    grid = render_bitmap(lines, font_path, cols, rows * 2,
                         cell_w=8, cell_h=8, **kw)
    out = []
    for r in range(rows):
        top, bot = grid[r * 2], grid[r * 2 + 1]
        line = []
        for c in range(cols):
            a, b = top[c] > 0.45, bot[c] > 0.45
            line.append("█" if a and b else "▀" if a else "▄" if b else " ")
        out.append("".join(line).rstrip())
    return "\n".join(out)


def colorize(text, color, mode="ansi"):
    """给字符画上色（终端 ANSI 或 HTML）"""
    codes = {
        "red": "31", "green": "32", "yellow": "33", "blue": "34",
        "magenta": "35", "cyan": "36", "white": "37", "orange": "38;5;208",
        "gold": "38;5;220", "neon": "38;5;46",
    }
    if mode == "ansi":
        c = codes.get(color, "36")
        return "\n".join("\033[%sm%s\033[0m" % (c, l) for l in text.split("\n"))
    return "<pre style='color:%s;line-height:1;letter-spacing:0'>%s</pre>" % (color, text)


def main():
    ap = argparse.ArgumentParser(
        description="中文 ASCII 艺术字生成器（任意汉字 / 任意系统字体）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="?", default="你好世界", help="要转换的文字，\\n 换行")
    ap.add_argument("-f", "--font", default="yahei", help="字体别名或字体文件路径")
    ap.add_argument("-w", "--width", type=int, default=76, help="输出列数（默认 76）")
    ap.add_argument("-H", "--height", type=int, default=0,
                    help="输出行数（0 = 按字形比例自动，推荐）")
    ap.add_argument("-c", "--charset", default="block",
                    choices=list(CHARSETS) + ["custom"], help="字符集")
    ap.add_argument("--custom", default="█", help="-c custom 时用作笔画的自定义字符")
    ap.add_argument("--bold", type=int, default=0, help="描边粗细（加粗），0-3")
    ap.add_argument("--invert", action="store_true", help="反色")
    ap.add_argument("--gamma", type=float, default=0.85, help="灰度 gamma，<1 更粗 >1 更细")
    ap.add_argument("--threshold", type=float, default=0.5, help="笔画阈值 0-1")
    ap.add_argument("--sharp", type=int, default=1, choices=[0, 1, 2],
                    help="0=平滑灰阶 1=锐化(默认) 2=纯二值")
    ap.add_argument("--contrast", type=float, default=1.4, help="对比度拉伸")
    ap.add_argument("--ansi", action="store_true", help="输出 ANSI 彩色转义")
    ap.add_argument("--color", default="cyan", help="ANSI 颜色名")
    ap.add_argument("-o", "--out", help="写入文件（UTF-8）")
    ap.add_argument("-l", "--list-fonts", action="store_true", help="列出字体别名")
    a = ap.parse_args()

    if a.list_fonts:
        list_fonts()
        return

    font_path = resolve_font(a.font)
    lines = a.text.replace("\\n", "\n").split("\n") or [""]
    # 全角填充字符占 2 列：网格列数减半、cell 改成正方形，否则字形会被横向拉伸
    custom_ink = (a.custom or "█").strip() or "█"
    wide = a.charset == "custom" and _is_wide(custom_ink[0])
    cols = max(4, a.width // 2) if wide else a.width
    aspect = 1.0 if wide else 0.5
    rows = a.height if a.height > 0 else auto_rows(lines, font_path, cols, aspect)
    kw = dict(bold=a.bold, invert=a.invert, gamma=a.gamma,
              threshold=a.threshold, sharp=a.sharp, contrast=a.contrast)
    if wide:
        kw.update(cell_w=16, cell_h=16)

    if a.charset == "half":
        txt = halfblock_text(lines, font_path, cols, rows, **kw)
    else:
        grid = render_bitmap(lines, font_path, cols, rows, **kw)
        txt = grid_to_text(grid, a.charset, custom_ink)
    if a.ansi:
        txt = colorize(txt, a.color)

    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(txt + "\n")
        print("已写入 %s" % a.out)
    else:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
        print(txt)


if __name__ == "__main__":
    main()
