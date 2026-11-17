from pathlib import Path
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / 'app.py'

def test_sample_dashboard_export_and_filters():
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not app.exception
    app.button[0].click().run()
    assert not app.exception
    assert len(app.tabs) == 4
    assert len(app.metric) == 6
    assert app.metric[0].value.startswith('¥')
    app.multiselect[0].set_value([]).run()
    assert not app.exception
    assert app.metric[0].value == '¥0'
    generate = next(b for b in app.button if b.label == 'Excelを生成')
    generate.click().run()
    assert not app.exception
    assert len(app.session_state['report'][0]) > 10000
    next(b for b in app.button if b.label == 'セッションのデータを消去').click().run()
    assert not app.exception
    assert len(app.metric) == 0

def test_revalidation_retains_original():
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.button[0].click().run()
    original = app.session_state['original'].copy()
    next(b for b in app.button if b.label == '編集を確定して再検証').click().run()
    assert not app.exception
    assert app.session_state['original'].equals(original)
