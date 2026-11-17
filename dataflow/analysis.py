from decimal import Decimal, localcontext
import pandas as pd
from .engine import ZERO

def filter_data(frame, start=None, end=None, products=None, categories=None):
    result = frame.copy()
    if start is not None:
        result = result[result['日付'] >= pd.Timestamp(start)]
    if end is not None:
        result = result[result['日付'] <= pd.Timestamp(end)]
    if products is not None:
        result = result[result['商品名'].isin(products)]
    if categories is not None:
        result = result[result['カテゴリ'].isin(categories)]
    return result

def count_transactions(frame):
    ids = frame['取引ID'].fillna('')
    return int(ids[ids != ''].nunique() + (ids == '').sum())

def kpis(frame):
    with localcontext() as context:
        context.prec = 160
        return _kpis(frame)

def _kpis(frame):
    valid = frame[frame['集計対象']]
    sales = valid[valid['取引区分'] == '通常販売']
    cancelled = valid[valid['取引区分'] == 'キャンセル']
    amount = sum(valid['計上金額'], 0)
    quantity = sum(valid['純数量'], ZERO)
    return {'純売上合計': amount, '純販売数量': quantity, '販売取引件数': count_transactions(sales),
            '平均単価': Decimal(amount) / quantity if quantity else None,
            '返品金額合計': sum(valid['返金額'], 0), 'キャンセル件数': count_transactions(cancelled)}

def aggregates(frame):
    with localcontext() as context:
        context.prec = 160
        return _aggregates(frame)

def _aggregates(frame):
    data = frame[frame['集計対象'] & (frame['取引区分'] != 'キャンセル')].copy()
    data['日別'] = pd.to_datetime(data['日付']).dt.strftime('%Y-%m-%d')
    data['月別'] = pd.to_datetime(data['日付']).dt.strftime('%Y-%m')
    results = {}
    for key in ('日別', '月別', '商品名', 'カテゴリ'):
        if data.empty:
            results[key] = pd.DataFrame(columns=[key, '純売上', '純数量', '返品金額'])
        else:
            results[key] = data.groupby(key, dropna=False).agg(純売上=('計上金額', 'sum'), 純数量=('純数量', 'sum'), 返品金額=('返金額', 'sum')).reset_index()
    return results
