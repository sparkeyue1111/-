# Evidence Hub V1

用途：给学习池股票补一个“数据源质量层”，输出官方证据包、财务质量分、证据质量分和最终研究排序。

## V1 数据源

- 巨潮资讯公告元数据：`stock_zh_a_disclosure_report_cninfo`
- 巨潮投资者关系公告：`stock_zh_a_disclosure_relation_cninfo`
- 互动易问答：`stock_irm_cninfo`
- 同花顺财务摘要：`stock_financial_abstract_new_ths`

## 服务器运行

```bash
/home/ubuntu/stock-ai/run_evidence_hub.sh
```

默认只跑当前 `current_stock_list.txt` 的 5 只股票。扩大范围：

```bash
POOL_LIMIT=20 /home/ubuntu/stock-ai/run_evidence_hub.sh
```

## 输出文件

- `/home/ubuntu/stock-ai/daily_stock_analysis/data/evidence_hub/evidence_events_YYYYMMDD.csv`
- `/home/ubuntu/stock-ai/daily_stock_analysis/data/evidence_hub/financial_quality_YYYYMMDD.csv`
- `/home/ubuntu/stock-ai/daily_stock_analysis/data/evidence_hub/evidence_quality_YYYYMMDD.csv`
- `/home/ubuntu/stock-ai/daily_stock_analysis/data/evidence_hub/final_score_YYYYMMDD.csv`
- `/home/ubuntu/stock-ai/daily_stock_analysis/data/evidence_hub/evidence_pack/YYYYMMDD/{code}.md`
- `/home/ubuntu/stock-ai/daily_stock_analysis/reports/evidence_quality_YYYYMMDD.md`
- `/home/ubuntu/stock-ai/daily_stock_analysis/reports/final_score_YYYYMMDD.md`

## V1 边界

- V1 只抓公告和互动问答的元数据与链接，不下载全文 PDF。
- 评分用于研究优先级，不是买卖信号。
- 如果官方接口限流或字段变化，脚本会记录到 `source_errors_YYYYMMDD.json`。
