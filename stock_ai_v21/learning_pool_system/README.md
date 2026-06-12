# A股 AI 学习池系统 V1

这个目录是 `daily_stock_analysis` 的外围自动化层。

每天流程：

1. 用 AKShare 拉取 A 股全市场行情。
2. 过滤 ST、退市、新股、北交所、低价低流动性股票。
3. 按成交额、动量、收盘位置、振幅、趋势字段等做规则化评分。
4. 生成学习池、交易计划和复盘 Markdown。
5. 把学习池排名前 `ANALYZE_COUNT` 只写入 `/app/data/app.env` 的 `STOCK_LIST`。
6. `daily_stock_analysis` 的晚间定时任务读取新的 `STOCK_LIST` 做 AI 分析。

默认输出：

- `/app/data/learning_pool/current_learning_pool.csv`
- `/app/data/learning_pool/current_stock_list.txt`
- `/app/reports/learning_pool_YYYYMMDD.md`
- `/app/reports/trade_plan_YYYYMMDD.md`
- `/app/reports/review_YYYYMMDD.md`

默认宿主机定时任务建议在交易日 17:35 执行，早于 `daily_stock_analysis` 的 18:10 分析任务。

注意：

- V1 是“学习池/观察池”，不是自动交易。
- 新加坡服务器访问东财接口不稳定，脚本优先使用 AKShare 的新浪全市场接口；如 AKShare 后续接口恢复，可继续扩展行业、估值、换手率等字段。
