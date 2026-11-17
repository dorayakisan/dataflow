# 動作確認記録

実施日：2026年11月17日

- 環境：Windows、Python 3.10.11
- `python -m pytest -q --tb=short`：**52 passed in 10.40s**
- `python -m pip check`：No broken requirements found.
- Streamlitサーバー：ローカル起動成功、`/_stcore/health` は `ok`
- ブラウザ：初期画面、サンプル500件の読み込み、検証エラー・警告0件、ダッシュボード表示を確認
- 画面テスト：4タブ、6 KPI、商品選択解除時の0円表示、再検証、Excel生成、セッション消去
- サンプルKPI：純売上849,200円、純数量1,396、販売240取引、平均単価608円（表示丸め）、返品29,970円、キャンセル9件
- Excel：5シート、4ネイティブグラフ、固定・フィルタ・文字列の数式対策をopenpyxlで再読み込み確認
- `sample_data/sales.csv`、`sales.xlsx`、`DataFlow_Report_sample.xlsx` を生成

未実施：GitHubへの公開、Streamlit Community Cloudへのデプロイ、公開URLでの確認。これらは添付仕様で実行前確認が指定されているため、公開先の指定・許可後に行う必要があります。実際のMicrosoft Excelでのグラフ編集操作、Linux/Python 3.12のCI実行、大量同時アクセスの負荷試験はこの環境では実施していません。

現時点の自動テストに失敗はありません。対応範囲・運用上の制限と判断ルールはREADMEを参照してください。
