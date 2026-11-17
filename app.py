from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import pandas as pd
import plotly.express as px
import streamlit as st
from dataflow.ingest import FIELDS, REQUIRED, InputError, read_file, sheets, suggest, map_frame, combine
from dataflow.engine import validate, yen
from dataflow.analysis import filter_data, kpis, aggregates
from dataflow.export import create_report, safe_filename
from dataflow.sample import sample_data

st.set_page_config(page_title='DataFlow | 売上分析', page_icon='📊', layout='wide')
st.markdown('''<style>
.stApp {color:#14243d} .block-container{padding-top:2rem;max-width:1500px}
[data-testid="stMetric"]{background:#f1f5f9;border-radius:12px;padding:20px;border-top:3px solid #2563eb}
h1,h2,h3{color:#14243d} [data-testid="stSidebar"]{border-right:1px solid #e2e8f0}
</style>''', unsafe_allow_html=True)
st.title('DataFlow')
st.caption('売上データを、確かな意思決定へ。　Ver. 1.4')
st.info('公開デモに個人情報・機密情報をアップロードしないでください。データはサーバー上で処理し、セッション内のみで保持します。')

def set_dataset(data):
    st.session_state.original = data.copy(deep=True)
    st.session_state.working = data.copy(deep=True)
    st.session_state.ledger, st.session_state.issues = validate(data)
    st.session_state.revision = st.session_state.get('revision', 0) + 1

with st.sidebar:
    st.header('データを準備')
    if st.button('サンプルデータを読み込む', type='primary', width='stretch'):
        set_dataset(sample_data())
        st.session_state.is_sample = True
        st.rerun()
    uploaded = st.file_uploader('CSV・Excel（最大10件／各10MB）', type=['csv', 'xlsx'], accept_multiple_files=True)
    st.caption('1ファイル20,000行・80列、統合50,000行まで。Excelの数式セルは値に変換してください。')
    if len(uploaded) > 10:
        st.error('最大10ファイルです。選択を減らしてください。')
    elif uploaded:
        signature = tuple((f.name, hashlib.sha256(f.getvalue()).hexdigest()) for f in uploaded)
        if st.session_state.get('upload_signature') != signature:
            st.session_state.upload_signature = signature
            st.session_state.inputs = {}
        mapped, failures = [], []
        st.subheader('シート・列の対応付け')
        for i, file in enumerate(uploaded):
            digest = signature[i][1][:12]
            with st.expander(f'{i+1}. {file.name}'):
                try:
                    content = file.getvalue()
                    sheet = st.selectbox('対象シート', sheets(file.name, content), key=f'sheet_{i}_{digest}') if file.name.lower().endswith('.xlsx') else None
                    cache_key = (i, digest, sheet)
                    cached = st.session_state.inputs.get(i)
                    if cached is None or cached[0] != cache_key:
                        with st.spinner('ファイルを読み込み中…'):
                            st.session_state.inputs[i] = (cache_key, read_file(file.name, content, sheet))
                    frame = st.session_state.inputs[i][1]
                    choices = [None] + list(frame.columns)
                    guesses = suggest(frame.columns)
                    mapping = {}
                    for field in FIELDS:
                        mapping[field] = st.selectbox(field + (' *' if field in REQUIRED else ''), choices,
                            index=choices.index(guesses[field]), format_func=lambda x: '（未指定・任意は既定値）' if x is None else str(x),
                            key=f'map_{i}_{digest}_{sheet}_{field}')
                    mapped.append((i, frame, mapping, file.name))
                except InputError as e:
                    failures.append((file.name, str(e)))
                    st.error(str(e))
        if st.button('対応付けを確定して読み込む', width='stretch'):
            progress = st.progress(0, text='ファイルを読み込み中')
            frames = []
            for j, (i, frame, mapping, name) in enumerate(mapped):
                progress.progress(int(35*(j+1)/max(1,len(mapped))), text=f'列の対応付けを確認中：{name}')
                try:
                    frames.append(map_frame(frame, mapping, name, str(i)))
                except InputError as e:
                    failures.append((name, str(e)))
            for name, message in failures:
                st.error(f'{name}：{message}')
            try:
                progress.progress(45, text='データを統合中')
                data = combine(frames)
                if data.empty:
                    raise InputError('読み込み可能なデータがありません。')
                progress.progress(65, text='データを検証中')
                set_dataset(data)
                st.session_state.is_sample = False
                progress.progress(100, text='読み込み完了')
                st.success(f'{len(frames)}ファイル／{len(data):,}行を読み込みました。')
            except InputError as e:
                st.error(str(e))
                progress.empty()
    if st.button('セッションのデータを消去'):
        for key in list(st.session_state):
            del st.session_state[key]
        st.rerun()

if 'working' not in st.session_state:
    st.subheader('ファイルをアップロードして、分析を始めましょう')
    st.write('列の名前が異なるファイルも統合できます。サンプルでは500件の架空取引をすぐにお試しいただけます。')
    st.stop()

if st.session_state.get('is_sample'):
    st.caption('表示中のサンプルは架空の小売店データです。実在する取引ではありません。')

tabs = st.tabs(['データ確認', 'ダッシュボード', '集計表', 'エクスポート'])
with tabs[0]:
    st.subheader('データを確認・修正')
    st.caption('返品数量は正数で入力。「警告確認」は当該行の全警告を確認した場合に選択してください。編集後は再検証で集計へ反映します。フィルタ変更時も未確定の編集を保持します。')
    st.caption('元明細が複数候補の返品は、元販売の「行ID」を「元明細ID」に入力してください。日付が曖昧な行は「日付解釈」を指定します。')
    revision = st.session_state.revision
    edited = st.data_editor(st.session_state.working, key=f'editor_{revision}', hide_index=True, width='stretch',
        disabled=['行ID', 'ファイル名', '行番号'], column_config={
            '除外': st.column_config.CheckboxColumn('除外'),
            '警告確認': st.column_config.CheckboxColumn('警告確認'),
            '日付解釈': st.column_config.SelectboxColumn('日付解釈', options=['自動', '月/日', '日/月']),
        })
    if st.button('編集を確定して再検証', type='primary'):
        st.session_state.working = edited.copy(deep=True)
        with st.spinner('データを検証中・返品金額を再計算中…'):
            st.session_state.ledger, st.session_state.issues = validate(edited)
        st.session_state.revision += 1
        st.rerun()
    ledger = st.session_state.ledger
    issues = st.session_state.issues
    st.write(f'全{len(ledger):,}行 ／ 集計対象 {int(ledger["集計対象"].sum()):,}行 ／ 集計対象外 {int((~ledger["集計対象"]).sum()):,}行')
    if len(issues):
        st.dataframe(issues, hide_index=True, width='stretch')
    else:
        st.success('エラー・警告はありません。')
    with st.expander('検証・計算後の統合データ（フィルタ前）'):
        view = ledger.copy()
        for col in ('数量', '単価', '割引金額', '純数量'):
            view[col] = view[col].astype(str)
        st.dataframe(view, hide_index=True, width='stretch')

ledger = st.session_state.ledger
with st.sidebar:
    st.header('分析フィルタ')
    dates = pd.to_datetime(ledger['日付'], errors='coerce').dropna()
    dataset_key = str(st.session_state.revision)
    bounds = None
    if len(dates):
        bounds = st.date_input('計上日', value=(dates.min().date(), dates.max().date()), key='date_'+dataset_key)
    products = sorted(ledger['商品名'].dropna().unique())
    categories = sorted(ledger['カテゴリ'].dropna().unique())
    selected_products = st.multiselect('商品', products, default=products, key='products_'+dataset_key)
    selected_categories = st.multiselect('カテゴリ', categories, default=categories, key='categories_'+dataset_key)
    st.caption('返品は返品日・元商品の情報で絞り込みます。商品・カテゴリの選択を空にすると0件になります。')
start, end = (bounds if bounds and len(bounds) == 2 else (None, None))
filtered = filter_data(ledger, start, end, selected_products, selected_categories)
tables = aggregates(filtered)

with tabs[1]:
    st.subheader('売上ダッシュボード')
    metrics = kpis(filtered)
    cols = st.columns(3)
    for i, (name, value) in enumerate(metrics.items()):
        if value is None:
            display = '算出不可'
        elif name in ('純売上合計', '返品金額合計', '平均単価'):
            display = f'¥{yen(value):,}'
        else:
            display = f'{value:,}'
        cols[i % 3].metric(name, display)
    st.caption('平均単価は表示時のみ整数円に四捨五入。純数量が0の場合は算出不可。返品金額は正数、純売上では控除します。')
    period = st.radio('売上推移の単位', ['日別', '月別'], horizontal=True)
    st.plotly_chart(px.line(tables[period], x=period, y='純売上', markers=True, color_discrete_sequence=['#2563eb']), width='stretch')
    left, right = st.columns(2)
    with left:
        st.subheader('商品別売上ランキング')
        ranked = tables['商品名'].sort_values('純売上', ascending=True).tail(20)
        st.plotly_chart(px.bar(ranked, x='純売上', y='商品名', orientation='h', color_discrete_sequence=['#2563eb']), width='stretch')
    with right:
        st.subheader('カテゴリ構成')
        positive = tables['カテゴリ'].loc[lambda x: x['純売上'] > 0]
        st.caption('正の純売上を持つカテゴリのみの構成比。0以下のカテゴリは集計表で確認できます。')
        if len(positive):
            st.plotly_chart(px.pie(positive, names='カテゴリ', values='純売上', hole=.55), width='stretch')
        else:
            st.info('正の純売上を持つカテゴリがありません。')
with tabs[2]:
    for name, table in tables.items():
        st.subheader(name + '集計')
        display = table.copy()
        display['純数量'] = display['純数量'].astype(str)
        st.dataframe(display, hide_index=True, width='stretch')
with tabs[3]:
    st.subheader('Excelレポートを作成')
    scope = st.radio('出力対象', ['全データ', '現在のフィルタ結果'], horizontal=True)
    name = st.text_input('ファイル名', f'DataFlow_Report_{datetime.now(ZoneInfo("Asia/Tokyo")):%Y%m%d}.xlsx')
    st.caption('5シートと編集可能なExcelグラフを出力します。元データには出力範囲内の除外・検証結果・修正前の値を含みます。日付不正の行は日付範囲を指定した出力から外れます。')
    target = ledger if scope == '全データ' else filtered
    filter_description = 'なし' if scope == '全データ' else f'計上日: {start}〜{end} / 商品: {", ".join(selected_products) or "選択なし"} / カテゴリ: {", ".join(selected_categories) or "選択なし"}'
    if st.button('Excelを生成', type='primary'):
        with st.spinner('レポートを生成中…'):
            try:
                st.session_state.report = (create_report(target, st.session_state.original, scope, st.session_state.working, filter_description), safe_filename(name), scope, st.session_state.revision, tuple(target['行ID']), filter_description)
            except Exception:
                st.error('Excelを生成できませんでした。データ量を減らし、内容を確認してから再実行してください。')
    report = st.session_state.get('report')
    if report and report[2:] == (scope, st.session_state.revision, tuple(target['行ID']), filter_description):
        st.download_button('Excelをダウンロード', report[0], file_name=safe_filename(name), mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

