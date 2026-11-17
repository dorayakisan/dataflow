"""Bounded, in-memory input and per-file column mapping."""
from io import BytesIO, StringIO
from pathlib import Path
import csv
import zipfile
import pandas as pd
from openpyxl import load_workbook

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 20000
MAX_TOTAL_ROWS = 50000
MAX_COLUMNS = 80
FIELDS = ['日付', '商品名', '数量', '単価', 'カテゴリ', '割引金額', '取引区分', '取引ID', '元取引ID']
REQUIRED = FIELDS[:4]
DEFAULTS = {'カテゴリ': '未分類', '割引金額': '0', '取引区分': '通常販売', '取引ID': '', '元取引ID': ''}
ALIASES = {
    '日付': ['販売日', '購入日', 'date', '日付'],
    '商品名': ['商品', '品名', 'product', '商品名'],
    '数量': ['個数', 'quantity', 'qty', '数量'],
    '単価': ['価格', 'price', '単価'],
    'カテゴリ': ['カテゴリー', 'category', 'カテゴリ'],
    '割引金額': ['割引', 'discount', '割引金額'],
    '取引区分': ['type', '区分', '取引区分'],
    '取引ID': ['transaction_id', '伝票番号', '取引ID'],
    '元取引ID': ['original_id', '元取引ID'],
}

class InputError(ValueError):
    pass

def check_file(name, data):
    if not data or len(data) > MAX_BYTES:
        raise InputError('空ファイル、または10MBの上限を超えています。')
    suffix = Path(name).suffix.lower()
    if suffix not in ('.csv', '.xlsx'):
        raise InputError('CSVまたは.xlsxファイルを選択してください。')
    if suffix == '.xlsx':
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > 2000 or sum(e.file_size for e in entries) > 80 * 1024 * 1024:
                    raise InputError('Excelの展開サイズが上限を超えています。')
        except (zipfile.BadZipFile, RuntimeError):
            raise InputError('Excelファイルの内容が不正です。') from None
    return suffix

def sheets(name, data):
    check_file(name, data)
    try:
        book = load_workbook(BytesIO(data), read_only=True, data_only=False)
        result = book.sheetnames
        book.close()
        return result
    except Exception:
        raise InputError('Excelのシート情報を読み込めません。暗号化や破損を確認してください。') from None

def read_file(name, data, sheet=None):
    suffix = check_file(name, data)
    try:
        if suffix == '.csv':
            decoded = None
            for encoding in ('utf-8-sig', 'cp932', 'shift_jis'):
                try:
                    decoded = data.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            if decoded is None or '\x00' in decoded:
                raise InputError('CSVの文字コードまたは内容を確認してください。')
            reader = csv.reader(StringIO(decoded))
            header = next(reader)
            if not header or len(header) > MAX_COLUMNS or len(set(header)) != len(header):
                raise InputError('列数は80以下、列名は重複しない見出しにしてください。')
            rows = []
            for row in reader:
                if len(rows) >= MAX_ROWS:
                    raise InputError('1ファイルの上限は20,000行です。')
                if len(row) != len(header):
                    raise InputError('CSVの列数が行によって異なります。')
                rows.append(row)
            frame = pd.DataFrame(rows, columns=header)
        else:
            book = load_workbook(BytesIO(data), read_only=True, data_only=False)
            try:
                ws = book[sheet] if sheet else book.worksheets[0]
                if ws.max_column and ws.max_column > MAX_COLUMNS:
                    raise InputError('列数の上限は80列です。')
                rows = []
                for i, row in enumerate(ws.iter_rows()):
                    if i > MAX_ROWS:
                        raise InputError('1ファイルの上限は20,000行です。')
                    if len(row) > MAX_COLUMNS:
                        raise InputError('列数の上限は80列です。')
                    if any(c.data_type == 'f' for c in row):
                        raise InputError('数式セルを含むExcelは値に変換してからアップロードしてください。')
                    rows.append([c.value for c in row])
                header = [str(x).strip() if x is not None else '' for x in rows[0]]
                if not all(header) or len(set(header)) != len(header):
                    raise InputError('Excelの先頭行には空欄や重複のない列名が必要です。')
                frame = pd.DataFrame(rows[1:], columns=header).fillna('')
            finally:
                book.close()
        if frame.empty:
            raise InputError('データ行がありません。')
        return frame
    except InputError:
        raise
    except Exception:
        raise InputError('読み込めません。ファイルの形式・見出し・内容を確認してください。') from None

def suggest(columns):
    lookup = {str(c).strip().casefold(): c for c in columns}
    return {field: next((lookup[a.casefold()] for a in ALIASES[field] if a.casefold() in lookup), None) for field in FIELDS}

def map_frame(frame, mapping, source, source_key='0'):
    if any(not mapping.get(f) for f in REQUIRED):
        raise InputError('必須4項目の列を対応付けてください。')
    chosen = [v for v in mapping.values() if v is not None]
    if len(chosen) != len(set(chosen)):
        raise InputError('同じ列を複数の項目に対応付けることはできません。')
    result = pd.DataFrame(index=range(len(frame)))
    for field in FIELDS:
        result[field] = frame[mapping[field]].reset_index(drop=True).map(lambda v: '' if v is None or pd.isna(v) else str(v)) if mapping.get(field) else DEFAULTS.get(field, '')
    result['行ID'] = [f'{source_key}:{i+2}' for i in range(len(frame))]
    result['ファイル名'] = source
    result['行番号'] = range(2, len(frame)+2)
    result['除外'] = False
    result['警告確認'] = False
    result['日付解釈'] = '自動'
    result['元明細ID'] = ''
    return result

def combine(frames):
    if len(frames) > 10:
        raise InputError('同時アップロードは最大10ファイルです。')
    if sum(len(f) for f in frames) > MAX_TOTAL_ROWS:
        raise InputError('統合データの上限は50,000行です。')
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
