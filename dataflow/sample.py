from datetime import date, timedelta
import random
import pandas as pd
from .ingest import FIELDS, map_frame, suggest

def sample_raw():
    rng = random.Random(140)
    products = [('ブレンドコーヒー', '食品', '680'), ('紅茶', '食品', '540'), ('ノート', '文具', '320'), ('ボールペン', '文具', '180'), ('マグカップ', '雑貨', '1200'), ('タオル', '雑貨', '850')]
    rows = []
    for i in range(480):
        p, c, price = products[i % len(products)]
        rows.append([str(date(2026, 1, 1) + timedelta(days=(i//2) % 90)), p, str(rng.randint(1, 5)), price, c, str(rng.choice([0, 0, 20, 50])), '通常販売', f'S{i//2:04}', ''])
    # One whole refund, three partial refunds, and further full refunds.
    for i in range(10):
        origin = rows[i*4]
        quantities = [origin[2]] if i else [str(int(origin[2])/2), str(int(origin[2])/2)]
        for j, qty in enumerate(quantities):
            rows.append(['2026-04-' + f'{i+j+1:02}', origin[1], qty, origin[3], origin[4], '0', '返品', f'R{i}-{j}', origin[7]])
    for i in range(9):
        rows.append(['2026-03-25', 'マグカップ', '1', '1200', '雑貨', '0', 'キャンセル', f'C{i}', ''])
    return pd.DataFrame(rows, columns=FIELDS)

def sample_data():
    raw = sample_raw()
    return map_frame(raw, suggest(raw.columns), '架空サンプル.csv', 'sample')
