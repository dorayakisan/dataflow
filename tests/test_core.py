from io import BytesIO
from decimal import Decimal
import zipfile
import pandas as pd
import pytest
from openpyxl import Workbook, load_workbook
from dataflow.ingest import read_file, sheets, suggest, map_frame, combine, InputError, FIELDS, MAX_BYTES
from dataflow.engine import validate, yen, parse_date
from dataflow.analysis import kpis, aggregates, filter_data
from dataflow.export import create_report, safe_filename
from dataflow.sample import sample_data, sample_raw

def row(**changes):
    result = dict(zip(FIELDS, ['2026-01-01', '商品A', '3', '100', '文具', '1', '通常販売', 'S1', '']))
    result.update(changes)
    return result

def data(*rows, source='test.csv'):
    frame = pd.DataFrame(rows)
    return map_frame(frame, suggest(frame.columns), source)

def ret(qty='1', **changes):
    return row(**{'日付': '2026-02-01', '数量': qty, '取引区分': '返品', '取引ID': 'R1', '元取引ID': 'S1', **changes})

@pytest.mark.parametrize('encoding', ['utf-8', 'utf-8-sig', 'cp932', 'shift_jis'])
def test_csv(encoding):
    frame = read_file('test.csv', pd.DataFrame([row()]).to_csv(index=False).encode(encoding))
    assert frame.iloc[0]['商品名'] == '商品A'

def test_excel_sheet_and_mapping():
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pd.DataFrame({'販売日': ['2026/01/01'], 'product': ['A'], 'qty': [1], 'price': [10]}).to_excel(writer, sheet_name='売上', index=False)
    assert sheets('test.xlsx', output.getvalue()) == ['売上']
    frame = read_file('test.xlsx', output.getvalue(), '売上')
    mapped = map_frame(frame, suggest(frame.columns), 'test.xlsx')
    assert mapped.iloc[0]['カテゴリ'] == '未分類'
    assert kpis(validate(mapped)[0])['純売上合計'] == 10

def test_merge_and_limit():
    a, b = data(row()), data(row(**{'商品名': 'B'}), source='other.csv')
    b['行ID'] = '1:2'
    assert len(combine([a, b])) == 2
    with pytest.raises(InputError): combine([a]*11)
    with pytest.raises(InputError): combine([pd.concat([a]*50001)])

def test_mapping_rejects_missing_and_reuse():
    frame = pd.DataFrame([row()])
    mapping = suggest(frame.columns)
    mapping['数量'] = None
    with pytest.raises(InputError): map_frame(frame, mapping, 'a')
    mapping['数量'] = '単価'
    with pytest.raises(InputError): map_frame(frame, mapping, 'a')

def test_round_per_line():
    frame = data(row(**{'数量': '1', '単価': '0.5', '割引金額': '0'}), row(**{'数量': '1', '単価': '0.5', '割引金額': '0', '取引ID': 'S2'}))
    ledger, issues = validate(frame)
    assert issues.empty
    assert kpis(ledger)['純売上合計'] == 2
    assert yen(Decimal('2.5')) == 3

@pytest.mark.parametrize('field,value', [('商品名',''), ('日付','2026-02-30'), ('日付','01/02/2026'), ('数量','abc'), ('数量','-1'), ('数量','0'), ('単価','NaN'), ('割引金額','301'), ('取引区分','不明'), ('単価','Infinity')])
def test_validation_errors(field, value):
    ledger, issues = validate(data(row(**{field: value})))
    assert not issues.empty
    assert not ledger.iloc[0]['集計対象']

def test_date_confirmation():
    frame = data(row(**{'日付': '01/02/2026'}))
    frame['日付解釈'] = '日/月'
    ledger, issues = validate(frame)
    assert issues.empty
    assert ledger.iloc[0]['日付'] == pd.Timestamp('2026-02-01')
    assert parse_date('31/01/2026') == pd.Timestamp('2026-01-31')

def test_duplicates_and_distinct_items():
    frame = data(row(), row(), row(**{'商品名':'B'}))
    ledger, issues = validate(frame)
    assert len(issues) == 2
    assert kpis(ledger)['純売上合計'] == 299
    frame.loc[0, '警告確認'] = True
    frame.loc[1, '除外'] = True
    ledger, _ = validate(frame)
    assert kpis(ledger)['純売上合計'] == 598
    assert kpis(ledger)['販売取引件数'] == 1

def test_duplicates_across_files():
    a, b = data(row()), data(row(), source='b.csv')
    b['行ID'] = '1:2'
    _, issues = validate(combine([a,b]))
    assert len(issues) == 2

def test_partial_and_final_refund():
    frame = data(row(), ret('1'), ret('1', **{'取引ID': 'R2', '日付':'2026-02-02'}), ret('1', **{'取引ID':'R3','日付':'2026-02-03'}))
    ledger, issues = validate(frame)
    assert issues.empty
    assert list(ledger['返金額']) == [0,100,100,99]
    assert kpis(ledger)['純売上合計'] == 0
    assert kpis(ledger)['平均単価'] is None
    assert kpis(ledger)['返品金額合計'] == 299

def test_quantity_excess():
    ledger, issues = validate(data(row(), ret('4')))
    assert not ledger.iloc[1]['集計対象']
    assert issues['内容・修正方法'].str.contains('累計返品数量').any()

def test_refund_excess():
    # Three quarter-unit refunds round 0.5 up to one yen each on a two-yen sale.
    frame = data(row(**{'数量':'3','単価':'0.6667','割引金額':'0'}), ret('0.75'), ret('0.75', **{'取引ID':'R2','日付':'2026-02-02'}), ret('0.75', **{'取引ID':'R3','日付':'2026-02-03'}))
    ledger, issues = validate(frame)
    assert issues['内容・修正方法'].str.contains('累計返金額').any()
    assert not ledger.iloc[-1]['集計対象']

@pytest.mark.parametrize('original', ['', 'missing'])
def test_unmatched_requires_confirmation(original):
    frame = data(ret(**{'元取引ID':original}))
    ledger, issues = validate(frame)
    assert not ledger.iloc[0]['集計対象']
    frame['警告確認'] = True
    ledger, _ = validate(frame)
    assert ledger.iloc[0]['計上金額'] == -99

def test_ambiguous_original_resolution():
    frame = data(row(), row(**{'単価':'200'}), ret())
    ledger, issues = validate(frame)
    assert not ledger.iloc[2]['集計対象']
    frame.loc[2, '元明細ID'] = '0:3'
    ledger, issues = validate(frame)
    assert issues.empty
    assert ledger.iloc[2]['返金額'] == 200

def test_return_date_and_original_category():
    frame = data(row(), ret(**{'カテゴリ':'違う'}))
    ledger, _ = validate(frame)
    assert ledger.iloc[1]['カテゴリ'] == '文具'
    selected = filter_data(ledger, '2026-02-01', '2026-02-28', ['商品A'], ['文具'])
    assert kpis(selected)['純売上合計'] == -100
    assert kpis(selected)['販売取引件数'] == 0
    frame.loc[1,'日付'] = '2025-01-01'
    assert not validate(frame)[0].iloc[1]['集計対象']

def test_cancel_and_counts():
    ledger, _ = validate(data(row(), row(**{'商品名':'B'}), row(**{'取引ID':'','商品名':'C'}), row(**{'取引区分':'キャンセル','取引ID':'C1'}), row(**{'取引区分':'キャンセル','取引ID':'C1','商品名':'B'})))
    metrics = kpis(ledger)
    assert metrics['キャンセル件数'] == 1
    assert metrics['販売取引件数'] == 2
    assert metrics['純販売数量'] == 9

def test_aggregates():
    ledger, _ = validate(data(row(), ret()))
    tables = aggregates(ledger)
    assert len(tables['日別']) == 2
    assert len(tables['月別']) == 2
    assert tables['商品名'].iloc[0]['純売上'] == 199
    assert tables['カテゴリ'].iloc[0]['純売上'] == 199
    assert filter_data(ledger, products=[]).empty

@pytest.mark.parametrize('name,content', [('bad.exe',b'abc'), ('bad.xlsx',b'abc'), ('bad.csv',b''), ('bad.csv',b'\x00\x00'), ('bad.csv', b'a,b\n1,2,3'), ('bad.csv', b'a,a\n1,2'), ('large.csv',b'x'*(MAX_BYTES+1))], ids=['extension','xlsx','empty','binary','columns','headers','size'])
def test_bad_files(name,content):
    with pytest.raises(InputError): read_file(name,content)

def test_excel_formula_input_and_zip_bomb():
    book = Workbook(); ws = book.active; ws.append(['a']); ws.append(['=1+1'])
    stream = BytesIO(); book.save(stream)
    with pytest.raises(InputError): read_file('a.xlsx', stream.getvalue())
    stream = BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('large', b'x'*(81*1024*1024))
    with pytest.raises(InputError): read_file('a.xlsx', stream.getvalue())

def test_report_and_injection():
    original = data(row(**{'商品名':'=1+1'}), ret(**{'商品名':'=1+1'}))
    ledger, _ = validate(original)
    report = create_report(ledger, original)
    book = load_workbook(BytesIO(report))
    assert book.sheetnames == ['元データ','サマリー','日別・月別集計','商品別・カテゴリ別集計','グラフ']
    assert len(book['グラフ']._charts) == 4
    assert book['元データ']['B2'].value == '=1+1'
    assert book['元データ']['B2'].data_type == 's'
    assert book['元データ'].freeze_panes == 'B2'
    assert book['元データ'].auto_filter.ref
    assert all(c.data_type != 'f' for ws in book for rows in ws for c in rows)
    assert '/' not in safe_filename('../bad:name')
    empty = ledger.iloc[:0]
    assert load_workbook(BytesIO(create_report(empty, original)))

def test_audit_original_unchanged():
    original = data(row())
    edited = original.copy(deep=True); edited.loc[0,'商品名'] = '@other'; edited.loc[0,'除外'] = True
    ledger, _ = validate(edited)
    book = load_workbook(BytesIO(create_report(ledger, original)))
    headers = {c.value:c.column for c in book['元データ'][1]}
    assert book['元データ'].cell(2,headers['修正前_商品名']).value == '商品A'
    assert original.iloc[0]['商品名'] == '商品A'

def test_sample():
    ledger, issues = validate(sample_data())
    assert len(ledger) == 500
    assert issues.empty
    assert ledger['集計対象'].all()
    assert kpis(ledger)['キャンセル件数'] == 9
    assert sample_raw().equals(sample_raw())

def test_date_bounds_and_extreme_exponents():
    for change in ({'日付':'9999-01-01'}, {'数量':'1e-999999999'}, {'単価':'1e999999'}):
        ledger, issues = validate(data(row(**change)))
        assert not ledger.iloc[0]['集計対象']
        assert not issues.empty

def test_long_decimal_product():
    # Exact product is just below half a yen. Default precision=28 would round it up.
    ledger, issues = validate(data(row(**{'数量':'0.4999999999999999999999999999','単価':'1','割引金額':'0'})))
    assert issues.empty
    assert ledger.iloc[0]['計上金額'] == 0
    assert kpis(ledger)['純販売数量'] == Decimal('0.4999999999999999999999999999')
    assert aggregates(ledger)['商品名'].iloc[0]['純数量'] == Decimal('0.4999999999999999999999999999')

@pytest.mark.parametrize('value', ['=SUM(1,2)', '+1', '-1', '@A1', '\t=1+1', 'a\x01b'])
def test_all_formula_prefixes(value):
    original = data(row(**{'商品名':value}))
    ledger, _ = validate(original)
    report = create_report(ledger,original,edited=original)
    book = load_workbook(BytesIO(report))
    assert all(c.data_type != 'f' for ws in book for rows in ws for c in rows)
    headers = {c.value:c.column for c in book['元データ'][1]}
    assert book['元データ'].cell(2,headers['修正あり']).value is False

def test_ten_files_and_excluded_returns_do_not_consume_allowance():
    frames = []
    for i in range(10):
        part = data(row(**{'取引ID':f'S{i}'})); part['行ID'] = f'{i}:2'; frames.append(part)
    assert len(combine(frames)) == 10
    frame = data(row(),ret('3'),ret('3',**{'取引ID':'R2','日付':'2026-02-02'}))
    frame.loc[1,'除外'] = True
    ledger, issues = validate(frame)
    assert issues.empty
    assert ledger.iloc[2]['返金額'] == 299

