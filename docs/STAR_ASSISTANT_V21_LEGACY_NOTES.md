# A股 AI 基本面池 + 中期持仓系统 V2

## 每日自动流程

服务器会在工作日按下面顺序运行：

```text
17:35  AKShare 生成基本面池，并写入 daily_stock_analysis 的 STOCK_LIST
18:10  daily_stock_analysis 自带定时任务读取最新 STOCK_LIST，生成 AI 分析报告
18:45  生成基本面池 + AI 的初步决策复核
19:00  Evidence Hub 抓官方公告、公告 PDF 正文、互动易、财务摘要，生成最终评分
19:05  生成估值 / 预期差层
19:10  生成最终交易计划层、系统状态表
19:15  生成 30/60/90 天持仓复盘打分
19:20  生成策略验证层：AI 对照基准、阈值校准、关注池换手监控
```

## 最该看的报告

```text
/home/ubuntu/stock-ai/daily_stock_analysis/reports/system_status_YYYYMMDD.md
/home/ubuntu/stock-ai/daily_stock_analysis/reports/fundamental_pool_YYYYMMDD.md
/home/ubuntu/stock-ai/daily_stock_analysis/reports/midterm_holding_plan_YYYYMMDD.md
/home/ubuntu/stock-ai/daily_stock_analysis/reports/final_score_YYYYMMDD.md
/home/ubuntu/stock-ai/daily_stock_analysis/reports/valuation_expectation_YYYYMMDD.md
/home/ubuntu/stock-ai/daily_stock_analysis/reports/final_trade_plan_YYYYMMDD.md
/home/ubuntu/stock-ai/daily_stock_analysis/reports/final_review_YYYYMMDD.md
/home/ubuntu/stock-ai/daily_stock_analysis/reports/holding_review_YYYYMMDD.md
/home/ubuntu/stock-ai/daily_stock_analysis/reports/strategy_validation_YYYYMMDD.md
```

单票证据包：

```text
/home/ubuntu/stock-ai/daily_stock_analysis/data/evidence_hub/evidence_pack/YYYYMMDD/{code}.md
```

公告 PDF 正文解析文件：

```text
/home/ubuntu/stock-ai/daily_stock_analysis/data/evidence_hub/pdf_text/YYYYMMDD/{code}/{announcementId}.txt
/home/ubuntu/stock-ai/daily_stock_analysis/data/evidence_hub/pdf_evidence_YYYYMMDD.csv
```

## 手动运行

只重建最终交易计划和复盘，不消耗大模型 token：

```bash
/home/ubuntu/stock-ai/build_final_layers.sh
```

只重建估值 / 预期差层，不消耗大模型 token：

```bash
/home/ubuntu/stock-ai/build_valuation_layer.sh
```

只重建 30/60/90 天持仓复盘层，不消耗大模型 token：

```bash
/home/ubuntu/stock-ai/build_holding_review.sh
```

只重建策略验证层，不消耗大模型 token：

```bash
/home/ubuntu/stock-ai/build_strategy_validation.sh
```

运行历史代理回测，不消耗大模型 token，但会抓较多历史行情：

```bash
BACKTEST_YEARS=5 BACKTEST_MAX_CODES=50 /home/ubuntu/stock-ai/build_historical_backtest.sh
```

完整跑一遍 V2，会调用 daily_stock_analysis，消耗大模型 token：

```bash
/home/ubuntu/stock-ai/run_stock_ai_v1_pipeline.sh
```

完整跑但跳过 AI 分析：

```bash
RUN_ANALYSIS=false /home/ubuntu/stock-ai/run_stock_ai_v1_pipeline.sh
```

扩大分析股票数量：

```bash
ANALYZE_COUNT=10 POOL_LIMIT=10 /home/ubuntu/stock-ai/run_stock_ai_v1_pipeline.sh
```

## V2 规则

- 基本面池负责找“适合 30-90 天持仓研究的公司”，先看财务质量，再看流动性和不过热程度。
- 旧学习池只作为短期线索池备用，不再作为主策略入口。
- daily_stock_analysis 负责 AI 分析和趋势判断。
- Evidence Hub 负责官方证据、公告 PDF 正文关键词、互动易和财务质量。
- 估值 / 预期差层负责判断“成长是否被估值透支”，高估值会压低最终交易计划层级。
- final_score 负责合成研究优先级。
- final_trade_plan 只基于最终层输出，不再只看短线动量。
- holding_review 负责 30/60/90 天窗口的入选价、当前价、研究分和层级变化复盘。
- strategy_validation 负责验证 AI Top5 是否跑赢随机流动性组合、指数和行业龙头篮子；在样本不足前不允许调低买入阈值。
- 可执行候选需要通过稳定性门槛：final>=80、AI>=50、近 5 次进入观察/买入前观察至少 3 次，且没有降级。
- historical_backtest 负责历史代理规则压力测试：用价格、流动性、趋势、不过热和波动回撤构建代理分，比较代理 Top5、阈值组合、同池随机、行业龙头和沪深300。
- 降级票不会给新开仓计划，只保留风控价、恢复条件和复盘任务。

## 当前边界

- 公告 PDF 全文解析已经接入，但只抽取前若干页关键词和摘要；关键年报仍要人工打开原文核对。
- 估值层优先使用百度股市通近三年估值序列，数据源失败时会降级为“估值源不足”。
- 30/60/90 天复盘需要系统持续运行满对应天数；未满周期时只建立基线。
- 策略验证层需要持续积累到期样本；样本不足时只能说明“正在验证”，不能说明策略已经有效。
- 历史回测 V1 不是历史 AI 回测，也不是全市场无偏回测；它使用当前基本面池候选做压力测试，后续要升级为按历史时点重建全市场池。
- 招投标、专利、环评/能评、客户认证还没有做专项源。
- 基本面池默认只对高流动性候选里的 120 只做财务探测，后续可提高或分批扫描。
- 评分是研究优先级，不是买卖指令。
