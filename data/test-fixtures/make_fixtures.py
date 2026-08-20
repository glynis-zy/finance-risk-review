# -*- coding: utf-8 -*-
"""生成财务单据风险审核系统的测试附件（发票/合同/行程单 PNG）。

用途：让用户在浏览器「新建单据」时直接上传这些图，验证 OCR 解析、附件完整性、
风险规则命中。文字均为清晰黑体白底，可被百度 OCR（若已配置 key）或预制回退读取。

运行：
    python make_fixtures.py
输出到同目录：invoice_sample.png / invoice_mismatch_sample.png /
            contract_sample.png / itinerary_sample.png /
            payment_basis_sample.png
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent

# 尽量用系统中文字体；找不到则退回默认（英文/数字仍清晰）
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",        # 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",      # 黑体
    "C:/Windows/Fonts/simsun.ttc",      # 宋体
]


def load_font(size: int):
    for p in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def new_sheet(title: str, width=900):
    img = Image.new("RGB", (width, 1180), "white")
    d = ImageDraw.Draw(img)
    # 顶部标题栏
    d.rectangle([0, 0, width, 70], fill="#1f3a5f")
    d.text((30, 18), title, font=load_font(34), fill="white")
    return img, d


def line(d, y, text, x=40, size=22, fill="black", bold=False):
    d.text((x, y), text, font=load_font(size), fill=fill)
    return y + size + 14


def kv_block(d, start_y, rows, x=40, size=22, gap=40):
    y = start_y
    for k, v in rows:
        d.text((x, y), f"{k}：", font=load_font(size), fill="#333333")
        d.text((x + 240, y), str(v), font=load_font(size), fill="black")
        y += gap
    return y + 10


def table(d, start_y, headers, rows, x=40, col_w=None, size=20):
    col_w = col_w or [200, 200, 200, 200]
    y = start_y
    # 表头
    cx = x
    for h, w in zip(headers, col_w):
        d.rectangle([cx, y, cx + w, y + 34], outline="#999999", fill="#eef2f7")
        d.text((cx + 8, y + 7), h, font=load_font(size), fill="#1f3a5f")
        cx += w
    y += 34
    for r in rows:
        cx = x
        for cell, w in zip(r, col_w):
            d.rectangle([cx, y, cx + w, y + 34], outline="#cccccc")
            d.text((cx + 8, y + 7), str(cell), font=load_font(size), fill="black")
            cx += w
        y += 34
    return y + 10


# ---------------------------------------------------------------------------
# 1) 发票（正常，价税合计 5000，销售方=恒通科技）—— 用于干净用例
# ---------------------------------------------------------------------------
def make_invoice_normal():
    img, d = new_sheet("增值税普通发票")
    y = 100
    y = kv_block(d, y, [
        ("发票号码", "24417000000123456789"),
        ("开票日期", "2026-08-18"),
        ("购买方名称", "测试集团有限责任公司"),
        ("购买方税号", "91110000TEST000001"),
        ("销售方名称", "恒通科技有限公司"),
        ("销售方税号", "91310000MA1FL0001X"),
    ])
    y = table(d, y, ["货物或应税劳务", "规格", "数量", "金额"],
              [["技术服务费", "次", "1", "4716.98"]], col_w=[340, 160, 120, 200])
    d.text((40, y), "税率 6%    税额 283.02", font=load_font(20), fill="#333")
    y += 50
    d.rectangle([40, y, 860, y + 56], outline="#1f3a5f", width=2)
    d.text((60, y + 12), "价税合计（小写）：¥5000.00", font=load_font(26), fill="#b00020")
    d.text((520, y + 12), "伍仟元整", font=load_font(24), fill="black")
    img.save(OUT / "invoice_sample.png")
    print("written invoice_sample.png")


# ---------------------------------------------------------------------------
# 2) 发票（金额不符，价税合计 8000）—— 用于「发票-单据金额差异」规则命中
# ---------------------------------------------------------------------------
def make_invoice_mismatch():
    img, d = new_sheet("增值税普通发票")
    y = 100
    y = kv_block(d, y, [
        ("发票号码", "24417000000987654321"),
        ("开票日期", "2026-08-18"),
        ("购买方名称", "测试集团有限责任公司"),
        ("销售方名称", "恒通科技有限公司"),
        ("销售方税号", "91310000MA1FL0001X"),
    ])
    y = table(d, y, ["货物或应税劳务", "规格", "数量", "金额"],
              [["咨询服务费", "次", "1", "7547.17"]], col_w=[340, 160, 120, 200])
    d.text((40, y), "税率 6%    税额 452.83", font=load_font(20), fill="#333")
    y += 50
    d.rectangle([40, y, 860, y + 56], outline="#1f3a5f", width=2)
    d.text((60, y + 12), "价税合计（小写）：¥8000.00", font=load_font(26), fill="#b00020")
    d.text((520, y + 12), "捌仟元整", font=load_font(24), fill="black")
    img.save(OUT / "invoice_mismatch_sample.png")
    print("written invoice_mismatch_sample.png")


# ---------------------------------------------------------------------------
# 3) 采购合同（恒通科技，合同总额 50000，付款比例 10%）—— 干净用例对齐
# ---------------------------------------------------------------------------
def make_contract():
    img, d = new_sheet("采购合同")
    y = 100
    y = kv_block(d, y, [
        ("合同编号", "C2026-TEST-001"),
        ("甲方（采购方）", "测试集团有限责任公司"),
        ("乙方（供应商）", "恒通科技有限公司"),
        ("签订日期", "2026-08-01"),
    ])
    y = line(d, y, "第一条  合同标的与金额", size=24, fill="#1f3a5f")
    y = kv_block(d, y, [
        ("合同总金额", "¥50,000.00（伍万元整）"),
        ("付款比例（首期）", "10%"),
        ("付款条件", "收到合规发票后 30 日内支付"),
        ("计划付款日期", "2026-08-25"),
    ])
    y = line(d, y, "第二条  甲乙双方权利义务", size=24, fill="#1f3a5f")
    y = line(d, y, "乙方应按约定交付技术服务，甲方按上述比例与条件支付款项。")
    y = line(d, y, "甲方（盖章）：测试集团有限责任公司")
    y = line(d, y, "乙方（盖章）：恒通科技有限公司")
    img.save(OUT / "contract_sample.png")
    print("written contract_sample.png")


# ---------------------------------------------------------------------------
# 4) 银行转账回单（付款依据）—— 批量付款单必需附件类别 payment_basis
# ---------------------------------------------------------------------------
def make_payment_basis():
    img, d = new_sheet("银行转账回单")
    y = 100
    y = kv_block(d, y, [
        ("付款账户", "测试集团有限责任公司 1100 **** **** 8888"),
        ("收款账户", "恒通科技有限公司 3100 **** **** 1234"),
        ("交易时间", "2026-08-18 14:32:18"),
        ("交易流水号", "TXN202608180000123456"),
    ])
    y = line(d, y, "附言：8月技术服务费付款（批量付款批次：8月供应商付款）")
    y += 20
    d.rectangle([40, y, 860, y + 70], outline="#1f3a5f", width=2)
    d.text((60, y + 18), "转账金额：¥20,000.00", font=load_font(30), fill="#b00020")
    d.text((520, y + 22), "状态：交易成功", font=load_font(22), fill="black")
    img.save(OUT / "payment_basis_sample.png")
    print("written payment_basis_sample.png")


# ---------------------------------------------------------------------------
# 5) 出差行程单（北京，8/15-8/16 含周末）—— 命中差旅「周末消费」规则
# ---------------------------------------------------------------------------
def make_itinerary():
    img, d = new_sheet("出差行程单")
    y = 100
    y = kv_block(d, y, [
        ("出差人", "张三"),
        ("目的地", "北京"),
        ("出差日期", "2026-08-15 至 2026-08-16"),
        ("出差事由", "客户拜访与项目验收"),
    ])
    y = table(d, y, ["日期", "地点", "项目", "金额"],
              [
                  ["2026-08-15", "北京", "机票(京-沪)", "1200.00"],
                  ["2026-08-15", "北京", "住宿费", "1500.00"],
                  ["2026-08-15", "北京", "餐费", "300.00"],
                  ["2026-08-16", "北京", "机票(沪-京)", "1200.00"],
              ], col_w=[160, 140, 260, 200])
    y = line(d, y, "合计：交通 2400.00 / 住宿 1500.00 / 餐费 300.00 / 补贴 0.00")
    img.save(OUT / "itinerary_sample.png")
    print("written itinerary_sample.png")


if __name__ == "__main__":
    make_invoice_normal()
    make_invoice_mismatch()
    make_contract()
    make_payment_basis()
    make_itinerary()
    print("ALL DONE ->", OUT)
