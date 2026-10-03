"""生成小学口算题 PDF：每册 30 天，每天 1 页 60 题，册末附答案。"""
import random
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

pdfmetrics.registerFont(TTFont("CN", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"))
W, H = A4
OUT = Path(__file__).parent / "成品" / "口算题"


# ---- 题型：每个函数返回 (题目, 答案) ----
def add(a_rng, b_rng, max_sum, carry=None):
    while True:
        a, b = random.randint(*a_rng), random.randint(*b_rng)
        s = a + b
        if s > max_sum:
            continue
        has_carry = (a % 10 + b % 10) >= 10
        if carry is not None and has_carry != carry:
            continue
        return f"{a} + {b} =", str(s)


def sub(a_rng, b_rng, borrow=None):
    while True:
        a, b = random.randint(*a_rng), random.randint(*b_rng)
        if b > a:
            continue
        has_borrow = a % 10 < b % 10
        if borrow is not None and has_borrow != borrow:
            continue
        return f"{a} - {b} =", str(a - b)


def tens(limit=100):
    if random.random() < 0.5:
        a = random.randint(1, limit // 10 - 1) * 10
        b = random.randint(1, limit // 10 - a // 10) * 10
        return f"{a} + {b} =", str(a + b)
    a = random.randint(2, limit // 10) * 10
    b = random.randint(1, a // 10) * 10
    return f"{a} - {b} =", str(a - b)


def mul(lo=1, hi=9):
    a, b = random.randint(lo, hi), random.randint(1, 9)
    return f"{a} × {b} =", str(a * b)


def div(lo=1, hi=9):
    b, q = random.randint(max(lo, 1), hi), random.randint(1, 9)
    return f"{b * q} ÷ {b} =", str(q)


def div_rem():
    b = random.randint(2, 9)
    q, r = random.randint(1, 9), random.randint(1, b - 1)
    return f"{b * q + r} ÷ {b} =", f"{q}……{r}"


def chain():
    a, b = random.randint(10, 60), random.randint(1, 30)
    if random.random() < 0.5:
        c = random.randint(1, 100 - a - b)
        return f"{a} + {b} + {c} =", str(a + b + c)
    a = random.randint(50, 99)
    b = random.randint(1, a // 2); c = random.randint(1, a - b)
    return f"{a} - {b} - {c} =", str(a - b - c)


def mix_md():
    a, b = random.randint(2, 9), random.randint(2, 9)
    c = random.randint(1, 30)
    if random.random() < 0.5:
        return f"{a} × {b} + {c} =", str(a * b + c)
    c = random.randint(1, a * b)
    return f"{a} × {b} - {c} =", str(a * b - c)


def two_by_one_mul():
    a, b = random.randint(11, 99), random.randint(2, 9)
    return f"{a} × {b} =", str(a * b)


def whole_div():
    b = random.randint(2, 9); q = random.randint(10, 99)
    return f"{b * q} ÷ {b} =", str(q)


# 每册：标题 + 每天使用的题型（按天渐进）
BOOKS = {
    "一年级上册": [
        ("10以内加减法", lambda: random.choice([add((0, 10), (0, 10), 10), sub((0, 10), (0, 10))])),
        ("20以内不进位加、不退位减", lambda: random.choice([add((10, 19), (0, 9), 20, carry=False), sub((10, 20), (0, 9), borrow=False)])),
        ("20以内进位加法", lambda: add((2, 9), (2, 9), 18, carry=True)),
        ("综合练习", lambda: random.choice([add((0, 10), (0, 10), 10), add((2, 9), (2, 9), 18, carry=True), sub((10, 20), (0, 9), borrow=False), sub((0, 10), (0, 10))])),
    ],
    "一年级下册": [
        ("20以内退位减法", lambda: sub((11, 18), (2, 9), borrow=True)),
        ("整十数加减", lambda: tens()),
        ("两位数加减一位数", lambda: random.choice([add((10, 99), (1, 9), 100), sub((10, 99), (1, 9))])),
        ("综合练习", lambda: random.choice([sub((11, 18), (2, 9), borrow=True), tens(), add((10, 99), (1, 9), 100), sub((10, 99), (1, 9)), add((10, 90), (10, 90), 100, carry=False)])),
    ],
    "二年级上册": [
        ("100以内进位加法", lambda: add((10, 89), (10, 89), 100, carry=True)),
        ("100以内退位减法", lambda: sub((20, 99), (10, 89), borrow=True)),
        ("连加连减", chain),
        ("表内乘法", lambda: mul()),
        ("综合练习", lambda: random.choice([add((10, 89), (10, 89), 100), sub((20, 99), (10, 89)), mul(), chain()])),
    ],
    "二年级下册": [
        ("表内除法", lambda: div()),
        ("乘除混合", lambda: random.choice([mul(), div()])),
        ("有余数的除法", div_rem),
        ("乘加乘减", mix_md),
        ("综合练习", lambda: random.choice([div(), mul(), div_rem(), mix_md(), add((10, 89), (10, 89), 100), sub((20, 99), (10, 89))])),
    ],
    "三年级上册": [
        ("两位数乘一位数", two_by_one_mul),
        ("整十整百数乘一位数", lambda: (lambda a, b: (f"{a} × {b} =", str(a * b)))(random.choice([10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 200, 300, 400, 500]), random.randint(2, 9))),
        ("有余数的除法", div_rem),
        ("几百几十加减", lambda: (lambda a, b, plus: (f"{a} + {b} =", str(a + b)) if plus else (f"{max(a,b)} - {min(a,b)} =", str(abs(a - b))))(random.randint(1, 50) * 10, random.randint(1, 40) * 10, random.random() < 0.5)),
        ("综合练习", lambda: random.choice([two_by_one_mul(), div_rem(), mix_md(), whole_div()])),
    ],
}

DAYS, PER_PAGE, COLS = 30, 60, 3


def day_topic(stages, day):
    """30 天平均分给各阶段，最后阶段为综合。"""
    idx = min(len(stages) - 1, (day - 1) * len(stages) // DAYS)
    return stages[idx]


def make_book(name, stages, seed):
    random.seed(seed)
    path = OUT / f"{name}口算30天.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle(f"{name} 口算天天练 30天")
    # 封面
    c.setFont("CN", 34); c.drawCentredString(W / 2, H - 260, f"{name}")
    c.setFont("CN", 40); c.drawCentredString(W / 2, H - 320, "口算天天练 · 30天")
    c.setFont("CN", 15)
    y = H - 400
    for i, (t, _) in enumerate(stages, 1):
        c.drawCentredString(W / 2, y, f"第{i}阶段：{t}"); y -= 26
    c.drawCentredString(W / 2, 140, "每天 1 页 · 每页 60 题 · 册末附答案")
    c.drawCentredString(W / 2, 110, "姓名：____________    班级：____________")
    c.showPage()

    answers = []
    for day in range(1, DAYS + 1):
        topic, gen = day_topic(stages, day)
        seen, probs, tries = set(), [], 0
        while len(probs) < PER_PAGE:
            q, a = gen()
            tries += 1
            if q in seen and tries < 2000:  # 题库小（如20以内进位加只有几十道）时允许重复
                continue
            seen.add(q); probs.append((q, a))
        answers.append((day, topic, probs))
        c.setFont("CN", 18); c.drawString(50, H - 60, f"第 {day} 天  {topic}")
        c.setFont("CN", 11)
        c.drawString(50, H - 85, "日期：____月____日     用时：______分钟     对了：______题     家长签字：________")
        c.line(50, H - 95, W - 50, H - 95)
        col_w = (W - 100) / COLS
        rows = PER_PAGE // COLS
        row_h = (H - 160) / rows
        c.setFont("CN", 14)
        for i, (q, _) in enumerate(probs):
            col, row = i % COLS, i // COLS  # 按行从左到右编号，和答案顺序一致
            x, y = 55 + col * col_w, H - 125 - row * row_h
            c.setFont("CN", 8); c.setFillGray(0.5); c.drawRightString(x + 14, y, f"{i + 1}")
            c.setFont("CN", 14); c.setFillGray(0); c.drawString(x + 20, y, q)
        c.setFont("CN", 9); c.drawCentredString(W / 2, 30, f"- {day} -")
        c.showPage()

    # 答案：每页 3 天
    for start in range(0, DAYS, 3):
        c.setFont("CN", 16); c.drawString(50, H - 50, "参考答案")
        y = H - 80
        for day, topic, probs in answers[start:start + 3]:
            c.setFont("CN", 12); c.drawString(50, y, f"第 {day} 天  {topic}"); y -= 18
            c.setFont("CN", 9)
            for i, (_, a) in enumerate(probs):
                c.drawString(55 + (i % 10) * 50, y - (i // 10) * 13, f"({i + 1}) {a}")
            y -= 6 * 13 + 30
        c.showPage()
    c.save()
    return path


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for i, (name, stages) in enumerate(BOOKS.items()):
        print(make_book(name, stages, seed=2026 + i))
