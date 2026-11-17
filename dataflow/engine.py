"""Pure validation and ledger calculation. Monetary arithmetic uses Decimal only."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from datetime import datetime, date
import re
import unicodedata
import pandas as pd
from .ingest import FIELDS

ZERO = Decimal('0')
KINDS = ('通常販売', '返品', 'キャンセル')

def clean(value):
    if value is None or pd.isna(value):
        return ''
    return unicodedata.normalize('NFKC', str(value)).strip()

def number(value):
    text = clean(value).replace(',', '')
    if len(text) > 30:
        raise ValueError('数値が長すぎます。')
    try:
        result = Decimal(text)
        if not result.is_finite() or result < 0 or result > Decimal('1000000000000'):
            raise ValueError('0以上1兆以下の数値を指定してください。')
        if abs(result.as_tuple().exponent) > 100:
            raise ValueError('小数点の位置は100桁以内にしてください。')
        return result
    except InvalidOperation:
        raise ValueError('数値に変換できません。') from None

def yen(value):
    with localcontext() as context:
        context.prec = 160
        return int(Decimal(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

def parse_date(value, policy='自動'):
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return pd.Timestamp(value).normalize().tz_localize(None)
    value = clean(value)
    match = re.fullmatch(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', value)
    if match:
        a, b, y = map(int, match.groups())
        if a <= 12 and b <= 12 and a != b and policy == '自動':
            raise ValueError('曖昧な日付です。「日付解釈」で月/日または日/月を指定してください。')
        day_first = policy == '日/月' or (policy == '自動' and a > 12)
        try:
            return pd.Timestamp(date(y, b if day_first else a, a if day_first else b))
        except ValueError:
            raise ValueError('存在しない日付です。') from None
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d', '%Y年%m月%d日', '%Y%m%d', '%Y-%m-%d %H:%M:%S'):
        try:
            return pd.Timestamp(datetime.strptime(value, fmt).date())
        except ValueError:
            pass
    raise ValueError('日付を認識できません。YYYY-MM-DDで入力してください。')

def validate(frame):
    # Bounded input scale + 50,000 rows fit in this precision without product/sum loss.
    with localcontext() as context:
        context.prec = 160
        return _validate(frame)

def _validate(frame):
    """Return normalized audit ledger and issues; never mutate the source frame."""
    issues, records = [], []
    def issue(r, col, message, level='エラー'):
        issues.append({'行ID': r['行ID'], 'ファイル名': r['ファイル名'], '行番号': r['行番号'],
                       '種別': level, '列': col, '内容・修正方法': message})
        r['_errors' if level == 'エラー' else '_warnings'].append(message)
    for _, raw in frame.iterrows():
        r = raw.to_dict()
        r['_errors'], r['_warnings'] = [], []
        r['計上金額'], r['純数量'], r['返金額'] = 0, ZERO, 0
        r['参照元行ID'] = ''
        for f in FIELDS:
            r[f] = clean(r.get(f, ''))
        r['カテゴリ'] = r['カテゴリ'] or '未分類'
        r['取引区分'] = r['取引区分'] or '通常販売'
        for f in ('日付', '商品名', '数量', '単価'):
            if not r[f]:
                issue(r, f, '必須項目です。値を入力してください。')
        try:
            r['日付'] = parse_date(raw['日付'], r.get('日付解釈', '自動'))
            if not 1678 <= r['日付'].year <= 2261:
                raise ValueError('対応する日付範囲は1678年〜2261年です。')
        except (ValueError, OverflowError) as e:
            issue(r, '日付', str(e))
            r['日付'] = pd.NaT
        for f in ('数量', '単価', '割引金額'):
            try:
                r[f] = number(r[f] if r[f] else ('0' if f == '割引金額' else ''))
                if f == '数量' and r[f] == 0:
                    issue(r, f, '数量は0より大きい値を入力してください。返品も正数で入力します。')
            except ValueError as e:
                issue(r, f, str(e))
                r[f] = ZERO
        if r['取引区分'] not in KINDS:
            issue(r, '取引区分', '通常販売・返品・キャンセルのいずれかを指定してください。')
        if r['割引金額'] > r['数量'] * r['単価']:
            issue(r, '割引金額', '割引が数量×単価を超えています。修正してください。')
        records.append(r)
    groups = {}
    for r in records:
        if not r['_errors'] and not r.get('除外', False):
            key = tuple(r[f] for f in FIELDS)
            groups.setdefault(key, []).append(r)
    for group in groups.values():
        if len(group) > 1:
            for r in group:
                issue(r, '全項目', '重複候補です。保持する行の「警告確認」を選択するか「除外」してください。', '警告')
    def eligible(r):
        return not r.get('除外', False) and not r['_errors'] and (not r['_warnings'] or r.get('警告確認', False))
    originals_by_product, originals_by_row = {}, {}
    for r in records:
        if r['取引区分'] == '通常販売' and eligible(r):
            r['計上金額'] = yen(r['数量'] * r['単価'] - r['割引金額'])
            r['純数量'] = r['数量']
            if r['取引ID']:
                originals_by_product.setdefault((r['取引ID'], r['商品名']), []).append(r)
                originals_by_row[r['行ID']] = r
    used = {}
    returns = sorted((r for r in records if r['取引区分'] == '返品'), key=lambda r: (str(r['日付']), r['行ID']))
    for r in returns:
        if r['_errors'] or r.get('除外', False):
            continue
        original = None
        if r['元取引ID']:
            selected = clean(r.get('元明細ID', ''))
            if selected:
                selected_original = originals_by_row.get(selected)
                candidates = [selected_original] if selected_original and selected_original['取引ID'] == r['元取引ID'] else []
                if len(candidates) != 1:
                    issue(r, '元明細ID', '指定した元明細がありません。検証済み通常販売の行IDを指定してください。')
            else:
                candidates = originals_by_product.get((r['元取引ID'], r['商品名']), [])
            if len(candidates) > 1:
                issue(r, '元明細ID', '元明細が複数あります。元データの行IDを「元明細ID」に指定してください。')
            elif len(candidates) == 1:
                original = candidates[0]
            elif not r['_errors']:
                issue(r, '元取引ID', '元取引・商品を照合できません。返品行の金額を使用する場合は警告確認が必要です。', '警告')
        else:
            issue(r, '元取引ID', '元取引IDがなく照合できません。返品行の金額を確認してください。', '警告')
        if original:
            r['参照元行ID'] = original['行ID']
            r['商品名'], r['カテゴリ'] = original['商品名'], original['カテゴリ']
            if r['日付'] < original['日付']:
                issue(r, '日付', '返品日が元販売日より前です。日付を修正してください。')
            prev_qty, prev_amount = used.get(original['行ID'], (ZERO, 0))
            total_qty = prev_qty + r['数量']
            if total_qty > original['数量']:
                issue(r, '数量', '累計返品数量が元販売数量を超えています。数量・元明細を確認してください。')
            refund = (original['計上金額'] - prev_amount if total_qty == original['数量']
                      else yen(Decimal(original['計上金額']) * r['数量'] / original['数量']))
            if refund < 0 or prev_amount + refund > original['計上金額']:
                issue(r, '返金額', '累計返金額が元売上金額を超えています。返品の数量配分を見直してください。')
            if eligible(r):
                used[original['行ID']] = (total_qty, prev_amount + refund)
        else:
            refund = yen(r['数量'] * r['単価'] - r['割引金額'])
            if r['カテゴリ'] == '未分類':
                issue(r, 'カテゴリ', '元商品情報がなくカテゴリは未分類です。確認または修正してください。', '警告')
        if eligible(r):
            r['返金額'], r['計上金額'], r['純数量'] = refund, -refund, -r['数量']
    for r in records:
        r['集計対象'] = eligible(r)
        r['検証結果'] = '除外' if r.get('除外', False) else ('エラー' if r['_errors'] else ('警告確認済' if r['_warnings'] and r.get('警告確認', False) else ('確認待ち' if r['_warnings'] else '正常')))
        r['検証詳細'] = ' / '.join(r['_errors'] + r['_warnings'])
        if not r['集計対象']:
            r['計上金額'], r['純数量'], r['返金額'] = 0, ZERO, 0
        del r['_errors'], r['_warnings']
    return pd.DataFrame(records), pd.DataFrame(issues, columns=['行ID', 'ファイル名', '行番号', '種別', '列', '内容・修正方法'])
