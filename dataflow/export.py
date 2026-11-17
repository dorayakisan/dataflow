"""Styled five-sheet workbook with native editable charts and audit history."""
from io import BytesIO
from decimal import Decimal
from datetime import datetime
import re
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
from openpyxl.chart import LineChart, BarChart, PieChart, Reference
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from .analysis import aggregates, kpis
from .ingest import FIELDS

def safe_filename(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .')[:120]
    name = name or 'DataFlow_Report'
    if not name.lower().endswith('.xlsx'):
        name += '.xlsx'
    if name.split('.')[0].upper() in ['CON', 'PRN', 'AUX', 'NUL'] + [f'{x}{i}' for x in ('COM', 'LPT') for i in range(1, 10)]:
        name = '_' + name
    return name

def write_cell(ws, row, col, value):
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        value = None
    elif isinstance(value, Decimal):
        # Monetary ledger columns are integer yen. Decimal conversion here is only presentation.
        value = int(value) if value == value.to_integral_value() else float(value)
    elif isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    elif hasattr(value, 'item'):
        value = value.item()
    if isinstance(value, str):
        value = ILLEGAL_CHARACTERS_RE.sub('', value)[:32767]
    cell = ws.cell(row, col, value)
    if isinstance(value, str):
        # Force text, including formula-like strings and leading whitespace/control characters.
        cell.data_type = 's'
    if isinstance(value, datetime):
        cell.number_format = 'yyyy/mm/dd'
    return cell

def table(ws, frame, start=1):
    for j, name in enumerate(frame.columns, 1):
        c = write_cell(ws, start, j, str(name))
        c.fill, c.font = PatternFill('solid', fgColor='14243D'), Font(color='FFFFFF', bold=True)
    thin = Side(style='thin', color='E2E8F0')
    for i, row in enumerate(frame.itertuples(index=False, name=None), start+1):
        for j, value in enumerate(row, 1):
            c = write_cell(ws, i, j, value)
            c.border = Border(bottom=thin)
            if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
                c.number_format = '#,##0.########'
            if any(t in str(frame.columns[j-1]) for t in ('売上', '金額', '返金', '単価')):
                c.number_format = '#,##0;[Red]-#,##0'
    return start + len(frame) + 1

def create_report(audit, original, scope='全データ', edited=None, filters='なし'):
    book = Workbook()
    book.remove(book.active)
    for name in ('元データ', 'サマリー', '日別・月別集計', '商品別・カテゴリ別集計', 'グラフ'):
        book.create_sheet(name)
    raw = audit.copy()
    before = original.set_index('行ID')
    for f in FIELDS:
        raw['修正前_' + f] = raw['行ID'].map(before[f])
    after = (edited if edited is not None else audit).set_index('行ID')
    tracked = FIELDS + ['除外', '警告確認', '日付解釈', '元明細ID']
    raw['修正あり'] = [any(str(after.loc[rid, f]) != str(before.loc[rid, f]) for f in tracked) for rid in raw['行ID']]
    table(book['元データ'], raw)
    summary = kpis(audit)
    summary['平均単価'] = summary['平均単価'] if summary['平均単価'] is not None else '算出不可'
    table(book['サマリー'], pd.DataFrame({'指標': list(summary), '値': list(summary.values())}))
    ws = book['サマリー']
    ws.cell(10, 1, '出力対象'); ws.cell(10, 2, scope)
    ws.cell(11, 1, '監査範囲'); ws.cell(11, 2, '元データは対象範囲内の除外・エラー・警告行も含む。KPIは集計対象のみ。')
    ws.cell(12, 1, '返品計算'); ws.cell(12, 2, '全データで元販売と照合して確定後、計上日と元商品の情報でフィルタ。')
    ws.cell(13, 1, 'フィルタ条件'); write_cell(ws, 13, 2, filters)
    tables = aggregates(audit)
    offsets = {}
    for sheet, keys in [('日別・月別集計', ('日別', '月別')), ('商品別・カテゴリ別集計', ('商品名', 'カテゴリ'))]:
        start = 1
        for key in keys:
            offsets[key] = (sheet, start, len(tables[key]))
            start = table(book[sheet], tables[key], start) + 3
    graphs = book['グラフ']
    graphs.cell(1, 1, 'カテゴリ構成は正の純売上のみ。0以下のカテゴリは対象外。')
    for key, chart_type, anchor in [('日別', LineChart, 'A3'), ('月別', LineChart, 'J3'), ('商品名', BarChart, 'A20')]:
        sheet, start, size = offsets[key]
        if size:
            chart = chart_type()
            chart.title = key + ' 純売上（円）'
            chart.add_data(Reference(book[sheet], min_col=2, min_row=start, max_row=start+size), titles_from_data=True)
            chart.set_categories(Reference(book[sheet], min_col=1, min_row=start+1, max_row=start+size))
            chart.width, chart.height = 16, 9
            graphs.add_chart(chart, anchor)
    positive = tables['カテゴリ'].loc[lambda x: x['純売上'] > 0, ['カテゴリ', '純売上']]
    table(graphs, positive, 40)
    if len(positive):
        pie = PieChart()
        pie.title = 'カテゴリ構成（正の純売上のみ）'
        pie.add_data(Reference(graphs, min_col=2, min_row=40, max_row=40+len(positive)), titles_from_data=True)
        pie.set_categories(Reference(graphs, min_col=1, min_row=41, max_row=40+len(positive)))
        graphs.add_chart(pie, 'J20')
    for ws in book:
        ws.freeze_panes = 'B2'
        if ws != graphs:
            # Combined sections use separate headings; filter only the first table.
            first_end = next((r for r in range(2, ws.max_row+1) if ws.cell(r, 1).value is None), ws.max_row+1) - 1
            ws.auto_filter.ref = f'A1:{ws.cell(1, ws.max_column).column_letter}{first_end}'
        for col in ws.columns:
            width = max((len(str(c.value or '')) for c in list(col)[:200]), default=10)
            ws.column_dimensions[col[0].column_letter].width = min(45, max(14, width * 1.5))
        ws.sheet_view.showGridLines = False
    output = BytesIO()
    book.save(output)
    return output.getvalue()
