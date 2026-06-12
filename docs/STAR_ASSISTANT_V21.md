# 星星分析助手 v2.1 使用说明

本文档说明当前仓库在原 daily_stock_analysis 基础上新增的 A 股“基本面优先选股 + AI 研究 + 交易机会 + 现实模拟盘 + 回测验证”系统。

重要定位：本系统只做研究、筛选、观察、模拟盘和复盘，不自动实盘交易，不构成投资建议。真正买卖前仍需要人工复核公告、财报、估值、行业景气和账户风险。

## 1. 系统目标

星星分析助手 v2.1 不是“AI 自动炒股”，而是一个研究流水线：

1. 用 AKShare 拉取全市场 A 股行情，先做流动性、价格、波动、趋势初筛。
2. 用财务摘要筛出基本面相对更好的股票，形成 50 只策略池。
3. 从策略池中挑少量重点股票调用 daily_stock_analysis 做 AI 深度研究，避免 50 只全部调模型造成成本和噪声过高。
4. 补充公告、PDF、财务、问询、互动、估值和预期差证据。
5. 通过基本面闸门、证据闸门、估值闸门、交易闸门和大盘风控，决定股票处于“未通过、待研究、观察、交易候选”哪个层级。
6. 只有交易候选进入现实模拟盘，模拟 10 万初始资金、最多 5 只持仓、单票最高 20% 仓位。
7. 用历史行情回测，验证 AI/规则选出的票是否优于随机、指数和行业龙头。

## 2. 前端页面

当前部署后的主页面是策略池工作台：

- `/`：策略池工作台，展示基本面闸门、交易机会、观察池、模拟盘权益、当前选中股票详情。
- `/analysis`：原单股 AI 分析页面，可手动输入股票做分析。
- `/backtest`：历史回测页面，用于查看回测任务结果。
- `/chat`：问股对话。
- `/holdings`：持仓相关页面。
- `/settings`：配置页面。

浏览器标题和侧边栏品牌已改为：星星分析助手 v2.1版。

## 3. 数据来源

当前 v2.1 主要用这些数据源：

- AKShare：A 股实时行情、股票列表、财务摘要、历史 K 线、部分估值数据。
- daily_stock_analysis 原系统：新闻、公告、行情、技术面、AI 单股分析报告。
- 公开公告/PDF/财务数据：用于证据质量层和最终研究层。
- 本地回测缓存：用于全市场历史回测、交易分、沪深300大盘风控。

注意：AKShare 是免费公开数据接口，稳定性和字段会变化。系统已经有降级逻辑，但不能把缺失数据当成强证据。

## 4. 工作流总览

推荐日常顺序如下：

```bash
bash scripts/star_assistant_v21/run_stock_ai_v1_pipeline.sh
```

完整流水线包含 9 步：

1. `run_fundamental_pool.sh`：生成基本面策略池，并把需要 AI 分析的股票写入 `STOCK_LIST`。
2. daily_stock_analysis：对 `STOCK_LIST` 里的股票做 AI 分析。
3. `run_evidence_hub.sh`：收集公告/PDF/财务/证据质量。
4. `build_valuation_layer.sh`：生成估值和预期差层。
5. `build_final_layers.sh`：生成交易计划、降级条件和最终层。
6. `build_fundamental_first.sh`：合并所有结果，形成基本面优先闸门。
7. `run_paper_portfolio.sh`：运行现实模拟盘。
8. `build_holding_review.sh`：做 30/60/90 天持仓复盘打分。
9. `build_strategy_validation.sh`：对策略有效性做验证。

## 5. 第一层：全市场初筛

脚本：`stock_ai_v21/fundamental_pool_system/run_fundamental_pool.py`

默认参数：

| 参数 | 默认值 | 含义 |
| --- | ---: | --- |
| `POOL_SIZE` | 50 | 最终策略池保留 50 只 |
| `ANALYZE_COUNT` | 10 | 每轮写入 `STOCK_LIST` 供 AI 深度分析的股票数 |
| `FUNDAMENTAL_PROBE_COUNT` | 120 | 从全市场预筛后取前 120 只做财务摘要探测 |
| `MIN_AMOUNT` | 100000000 | 成交额不低于 1 亿元 |
| `MIN_PRICE` | 3 | 股价不低于 3 元 |
| `SOURCE_TIMEOUT` | 25 | 单个数据源超时秒数 |

筛选范围：

- 代码匹配 `[036]\d{5}` 的沪深主流 A 股。
- 排除名称包含 `ST`、`退`、`N`、`C` 的股票。
- 排除价格小于 3 元、成交额小于 1 亿元、涨跌幅缺失的股票。

预筛得分：

```text
preselect_score = liquidity_score * 0.55
                + heat_score      * 0.30
                + trend_seed_score* 0.15
```

含义：

- `liquidity_score`：成交额越高越好，避免小票流动性不足。
- `heat_score`：惩罚单日涨跌过猛、振幅过大，避免追极端情绪。
- `trend_seed_score`：用 60 日、年初至今涨跌幅做趋势种子分。

这一层回答的问题是：全市场哪些票“够活跃、不过分异常、值得进一步看财务”。

## 6. 第二层：财务基本面池

同一脚本会调用 `stock_financial_abstract_new_ths` 拉财务摘要，提取：

- 营收同比
- 利润同比
- 经营现金流
- ROE
- 毛利率
- 资产负债率

财务分从 50 分起步，主要加减分：

- 营收同比大于等于 20%，加 14；为正，加 6；明显下滑扣分。
- 利润同比大于等于 30%，加 20；为正，加 9；大幅下滑扣分。
- 经营现金流为正，加 14；为负，扣 18。
- ROE 大于等于 8，加 8；低于 3，扣 8。
- 毛利率大于等于 25，加 5；低于 10，扣 6。
- 资产负债率低于等于 55，加 4；高于等于 75，扣 10。
- 可用财务指标少于 3 个，扣 12。

基本面策略池综合分：

```text
score = fundamental_score * 0.70
      + liquidity_score   * 0.12
      + heat_score        * 0.10
      + trend_seed_score  * 0.08
```

分层：

- `>=72`：基本面优先
- `>=65`：基本面观察
- `>=58`：资料复核
- `<58`：剔除备选

最终保留 `POOL_SIZE=50` 只，写入：

- `data/fundamental_pool/current_fundamental_pool.csv`
- `data/fundamental_pool/current_stock_list.txt`
- `reports/fundamental_pool_YYYYMMDD.md`

## 7. 哪些股票会被 AI 深度分析

不是 50 只全部调用 AI。默认 `ANALYZE_COUNT=10`，逻辑是：

- 前 5 只高分股票固定进入 AI 深度研究。
- 另外 5 只从池子里轮动，避免每天只看同几只票。

这样做的原因：

- 控制 DeepSeek/OpenAI token 成本。
- 避免每天对 50 只票生成大量低执行价值报告。
- 让高分票持续被跟踪，同时给新票轮动进入研究的机会。

被选中的股票会写入 `STOCK_LIST`，daily_stock_analysis 读取这个列表运行 AI 分析。

## 8. 第三层：证据质量和最终研究层

脚本：`stock_ai_v21/evidence_hub_system/run_evidence_hub.py`

证据质量分关注：

- 官方公告数量，最高 25 分。
- 定期报告，最高 24 分。
- 监管/问询/处罚类材料，最高 12 分。
- 项目/招投标/客户认证/产业线索，最高 12 分。
- 互动易等投资者交流，最高 10 分。
- 已解析 PDF 正文，最高 10 分。
- 财务指标覆盖度，指标不少于 3 个给更高分。
- 最新证据的新鲜度。

最终研究分：

```text
final_research_score = learning_score * 0.25
                     + ai_score       * 0.25
                     + finance_score  * 0.30
                     + evidence_score * 0.20
```

如果 AI 明显看空且证据不足，会被压分。证据质量低于 45 时，不能升级为强研究结论。

输出：

- `data/evidence_hub/evidence_quality_YYYYMMDD.csv`
- `data/evidence_hub/final_score_YYYYMMDD.csv`
- `reports/evidence_quality_YYYYMMDD.md`
- `reports/final_score_YYYYMMDD.md`

## 9. 第四层：估值和预期差

脚本：`stock_ai_v21/valuation_layer_system/build_valuation_layer.py`

估值层用近三年估值分位判断贵便宜，核心指标：

- PE(TTM) 分位
- PB 分位
- PCF 分位

估值分规则：

- 平均分位小于等于 30：`78`，估值有保护。
- 平均分位小于等于 60：`63`，估值中性。
- 平均分位小于等于 80：`48`，估值略贵。
- 平均分位大于 80：`32`，估值偏高。
- 如果数据缺失，默认 `45`，不能当作强证据。

预期差分：

```text
support = final_research_score * 0.70 + ai_score * 0.30

expectation_gap_score = growth_score * 0.42
                      + valuation_score * 0.38
                      + support * 0.20
```

预期差标签：

- `正向预期差候选`：成长、估值、研究支持同时较好。
- `成长强但估值透支`：基本面弹性好，但估值过贵。
- `低质量/高估值风险`：质量和估值都不支持。
- `研究层未确认`：估值或成长有线索，但 AI/最终层未确认。
- `中性观察`：没有足够强的正向预期差。

## 10. 第五层：基本面优先闸门

脚本：`stock_ai_v21/fundamental_first_system/build_fundamental_first.py`

这是前端“策略池工作台”的核心数据来源。

综合得分：

```text
company_score     = pool_score * 0.45 + financial_score * 0.35 + learning_score * 0.20
industry_score    = evidence_score * 0.60 + research_score * 0.40
value_score       = expectation_score * 0.60 + valuation_score * 0.40
opportunity_score = trade_score

fundamental_first_score = company_score     * 0.30
                        + industry_score    * 0.25
                        + value_score       * 0.20
                        + opportunity_score * 0.25
```

闸门条件：

| 闸门 | 默认条件 |
| --- | --- |
| 基本面闸门 | 财务质量分 `>=65`，基本面池分 `>=60`，可用财务指标 `>=3` |
| 证据闸门 | 证据质量分 `>=55`，最终研究分 `>=58` |
| 估值闸门 | 预期差分 `>=55`，不能是估值极高，不能是成长强但估值透支或低质量高估值风险 |
| 交易闸门 | 交易分 `>=76` |
| 大盘风控 | 沪深300在 200 日线上方，且 120 日跌幅不超过 10% |
| 红旗规则 | 出现经营现金流为负、退市、处罚、立案、降级、无法表示、保留意见等硬风险时降级 |

输出状态：

- `REJECT`：未通过，不进入交易机会层。
- `FUNDAMENTAL_POOL`：基本面池保留，尚未进入深研。
- `RESEARCH_QUEUE`：基本面较好，优先补公告/PDF/估值/AI 深研。
- `WATCH`：已深研但估值、证据或交易机会未成熟。
- `TRADE_CANDIDATE`：进入交易机会层样本，但尚未达到严格买入闸门。
- `BUY_READY`：所有严格闸门通过，进入现实模拟盘买入候选。

前端对应：

- 基本面闸门筛选：显示 50 只策略池股票以及各层得分。
- 交易机会层：显示 `BUY_READY` 和 `TRADE_CANDIDATE`；只有 `BUY_READY` 会进入严格模拟盘买入候选。
- 观察/研究层：显示 `WATCH`、`RESEARCH_QUEUE` 和 `FUNDAMENTAL_POOL`。
- 模拟盘权益：显示现实模拟盘资金曲线、持仓和交易记录。

## 11. 交易分和历史回测 v2

脚本：`stock_ai_v21/historical_backtest_system/build_market_portfolio_backtest_v2.py`

v2 回测目标是解决三个问题：

1. AI 选出来的标的是否真的比随机、指数、行业龙头更好。
2. 买入阈值多少更合理。
3. 系统是否会高换手、频繁换池，导致不可执行。

交易代理分 `score_proxy_v21`：

```text
score = 55
      + clamp(ret60, -25, 45) * 0.65
      + clamp(ret120, -30, 90) * 0.25
      + (liquidity_score - 50) * 0.12
      - max(ret20 - 18, 0) * 0.90
      - max(ret60 - 50, 0) * 0.80
      - max(ret120 - 110, 0) * 0.35
      - max(vol60 - 45, 0) * 0.75
      - max(-drawdown120 - 28, 0) * 0.90
```

额外规则：

- 60 日涨幅 8% 到 42%、120 日涨幅 5% 到 90%、回撤在 -22% 到 -5%、波动不高、20 日不过热，加 8。
- 60 日为负或 120 日跌幅超过 8%，扣 12。
- 20 日过热、60 日过热、120 日极端过热、波动大、回撤深都会扣分。

默认回测参数：

| 参数 | 默认值 | 含义 |
| --- | ---: | --- |
| `BACKTEST_YEARS` | 5 | 默认回测 5 年，可改 10 |
| `BACKTEST_INITIAL_CAPITAL` | 100000 | 初始资金 10 万 |
| `BACKTEST_TOP_N` | 5 | 最多持有 5 只 |
| `BACKTEST_PORTFOLIO_THRESHOLD` | 76 | 买入阈值 |
| `BACKTEST_HOLD_THRESHOLD` | 68 | 持有阈值，低于则卖出或降级 |
| `BACKTEST_MAX_NEW_PER_REBALANCE` | 2 | 每次调仓最多新增 2 只 |
| `BACKTEST_REBALANCE` | M | 月度调仓，避免高换手 |
| `BACKTEST_MARKET_GUARD` | 1 | 启用大盘风控 |
| `BACKTEST_STICKY_HOLD` | 1 | 启用粘性持仓，减少频繁换股 |

常用命令：

```bash
BACKTEST_YEARS=10 BACKTEST_V2_WORKERS=4 bash scripts/star_assistant_v21/build_market_v2_backtest.sh
```

## 12. 现实模拟盘规则

脚本：`stock_ai_v21/paper_portfolio_system/run_paper_portfolio.py`

默认参数：

| 参数 | 默认值 |
| --- | ---: |
| 初始资金 | 100000 |
| 最大持仓数 | 5 |
| 单票最大仓位 | 20% |
| 买入阈值 | 76 |
| 持有阈值 | 68 |
| 佣金 | 0.03% |
| 印花税 | 0.05% |
| 最小交易单位 | 100 股 |

成交逻辑：

1. 今日出现 `BUY_READY` 不会立即买入，而是先进入 `pending_orders`。
2. 下一次运行时，如果该股票仍是 `BUY_READY` 且交易分仍大于等于 76，才按当日价格模拟买入。
3. 这样避免“收盘后看到信号，却假设当天已经成交”的未来函数。

卖出逻辑：

- 触发风险止损。
- 基本面闸门降级为 `REJECT`。
- 持仓股票交易分低于持有阈值 68。

输出：

- `data/paper_portfolio/paper_portfolio_state.json`
- `data/paper_portfolio/current_paper_holdings.csv`
- `data/paper_portfolio/paper_equity_curve.csv`
- `reports/paper_portfolio_YYYYMMDD.md`

## 13. 定时任务建议

A 股收盘后，很多公开数据会延迟更新。当前建议：

- 20:05：生成基本面策略池。
- 20:45：daily_stock_analysis 定时分析 `STOCK_LIST`。
- 22:05：证据层。
- 22:10：估值层。
- 22:15：最终层。
- 22:20：基本面优先闸门。
- 22:25：现实模拟盘。
- 22:30：持仓复盘。
- 22:35：策略验证。
- 周六 04:00：全市场 v2 回测，避免交易日晚间抢资源。

安装定时任务：

```bash
bash scripts/star_assistant_v21/install_cron.sh
```

查看定时任务：

```bash
crontab -l
```

## 14. Docker 部署说明

当前服务器使用 Docker + Nginx Proxy Manager，前端域名是：

```text
https://stock.baiweiyuan.xyz
```

主 compose 已把 `server` 接入外部反代网络：

```yaml
networks:
  npm_proxy:
    external: true
    name: odoo_default
```

如果换服务器，外部网络名可能不是 `odoo_default`，需要先用下面命令查看：

```bash
sudo docker network ls
```

然后修改 `docker/docker-compose.yml` 里的网络名。

启动：

```bash
sudo docker compose -f docker/docker-compose.yml up -d server analyzer
```

重建：

```bash
sudo docker compose -f docker/docker-compose.yml build server analyzer
sudo docker compose -f docker/docker-compose.yml up -d server analyzer
```

查看状态：

```bash
sudo docker ps
sudo docker logs --tail=100 stock-server
sudo docker logs --tail=100 stock-analyzer
```

## 15. 常用手动命令

只生成策略池：

```bash
POOL_SIZE=50 ANALYZE_COUNT=10 bash scripts/star_assistant_v21/run_fundamental_pool.sh
```

跑完整流水线：

```bash
bash scripts/star_assistant_v21/run_stock_ai_v1_pipeline.sh
```

只跑基本面优先闸门：

```bash
bash scripts/star_assistant_v21/build_fundamental_first.sh
```

只跑现实模拟盘：

```bash
bash scripts/star_assistant_v21/run_paper_portfolio.sh
```

跑 10 年全市场回测：

```bash
BACKTEST_YEARS=10 BACKTEST_V2_WORKERS=4 bash scripts/star_assistant_v21/build_market_v2_backtest.sh
```

## 16. 成本控制

默认只让 10 只股票进入 AI 深度分析，不对 50 只全部调用模型。原因是：

- 50 只全部分析会大幅增加 token 成本。
- 大量报告会增加阅读负担。
- 真正需要持续 AI 监测的是交易机会层、观察池前排和持仓票。

建议：

- 默认 `ANALYZE_COUNT=10`，证据层默认覆盖前 15 只。
- 如果市场强、机会多，可以临时把 AI 分析提高到 15、证据覆盖提高到 20。
- 如果只想省钱，可以降到 5。
- 持仓票和 `BUY_READY` 股票应持续跟踪；普通 `REJECT` 股票不必每天调用 AI。

## 17. 当前版本的主要优点和短板

优点：

- 先基本面后交易，不是单纯追短线热点。
- 使用闸门制，股票必须同时通过基本面、证据、估值、交易和大盘风控。
- 有现实模拟盘，能长期验证策略，不急着实盘。
- 有历史回测，能和指数、随机、行业龙头对比。
- 用粘性持仓和月度调仓降低高换手。

短板：

- 免费数据源稳定性有限，字段变化会影响分数。
- 估值、公告 PDF、客户认证等证据仍需要人工抽查。
- 回测里的交易分是历史行情代理分，不等于过去真实 AI 当时会给出的分。
- 财务摘要粒度还不够细，后续可以接入更完整的财报三表和一致预期。
- 目前只做模拟盘，不接券商实盘。

## 18. 不建议做的事

- 不建议直接按照系统的 `BUY_READY` 实盘满仓买入。
- 不建议把 AI 单股报告当成买卖指令。
- 不建议每天根据池子变化频繁换股。
- 不建议忽略止损、估值透支、现金流恶化和大盘风控。

更合理的使用方式是：把它当成“自动研究员 + 策略纪律表 + 模拟盘记录员”，先跑 3 到 6 个月模拟盘，再决定是否把某些规则迁移到小资金实盘。


## V2.2 三层增强

- 数据源质量层：每天检查 AKShare 行情接口、核心产物字段、缺失率和股票级数据质量；若出现严重问题，基本面闸门会阻断交易候选。
- 财务三表增强层：对策略池股票补抓利润表、资产负债表、现金流量表，加入营收/利润增长、经营现金流、负债率、应收/存货/商誉压力。
- 前向验证系统：每天保存当时的候选、评分、决策、价格和计划，未来回填 30/60/90 天收益，用真实前向表现检验 AI 和规则是否有效。

新增命令：

```bash
bash scripts/star_assistant_v21/build_financial_statements.sh
bash scripts/star_assistant_v21/build_data_quality.sh
bash scripts/star_assistant_v21/build_forward_validation.sh
```

完整流水线已升级为 12 步：学习池、财务三表、AI 单票分析、证据层、估值层、最终层、数据质量、基本面闸门、模拟盘、持仓复盘、策略验证、前向验证。


## V2.3 分层优化：先积累样本，再校准阈值

为避免“严格闸门过早卡死、1-2 个月没有任何可验证样本”，系统新增两层中间状态：

- `RESEARCH_QUEUE`：基本面、三表和数据质量较好，但尚未完成公告/PDF/估值/AI 深研。它不是失败，而是优先研究名单。
- `TRADE_CANDIDATE`：已经进入交易机会层样本，但估值、证据或最终研究分仍未达到 `BUY_READY` 的严格买入标准。

严格规则没有放松：现实模拟盘买入仍只接受 `BUY_READY`，并且仍然要求下一次运行时信号继续存在，避免未来函数。

运行 1 个月后重点复盘：

- `RESEARCH_QUEUE -> WATCH / TRADE_CANDIDATE` 的转化率。
- `TRADE_CANDIDATE` 后 30 天、60 天收益是否优于普通策略池。
- `BUY_READY` 是否过少；若 1 个月仍为 0，再根据前向验证数据调整证据分、估值分或交易分阈值。
- 哪些闸门最常卡住：证据覆盖不足、估值偏高、交易分不足，还是 AI 报告未生成。
