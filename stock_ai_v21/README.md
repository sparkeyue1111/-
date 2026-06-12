# stock_ai_v21

`stock_ai_v21` 是星星分析助手 v2.1 的策略流水线代码目录，负责把原 daily_stock_analysis 的单股 AI 分析能力扩展成“全市场筛选、证据验证、估值预期差、交易机会、现实模拟盘、回测验证”的完整闭环。

主文档请看：`docs/STAR_ASSISTANT_V21.md`。

## 目录说明

| 目录 | 作用 |
| --- | --- |
| `fundamental_pool_system` | AKShare 全市场初筛、财务摘要评分、生成 50 只策略池和 AI 分析列表 |
| `evidence_hub_system` | 公告、PDF、财务、互动、证据质量和最终研究分 |
| `valuation_layer_system` | 估值分位、成长质量、预期差判断 |
| `final_layers_system` | 交易计划、降级规则、最终计划层 |
| `fundamental_first_system` | 基本面优先闸门，输出前端策略池核心数据 |
| `paper_portfolio_system` | 现实模拟盘，记录现金、持仓、交易和权益曲线 |
| `holding_review_system` | 30/60/90 天持仓复盘打分 |
| `strategy_validation_system` | 验证策略有效性和换手稳定性 |
| `historical_backtest_system` | 历史行情回测，支持全市场 v2 回测 |

## 日常入口

通常不直接调用这里的 Python 文件，而是用仓库根目录下的脚本：

```bash
bash scripts/star_assistant_v21/run_stock_ai_v1_pipeline.sh
```

单独运行某一层时，也优先用 `scripts/star_assistant_v21/` 下的 shell 包装脚本。

## V2.2 新增系统

- `financial_statements_system/`：用利润表、资产负债表、现金流量表补强基本面质量判断。
- `data_quality_system/`：检查 AKShare 可用性、关键 CSV 字段、缺失率和股票级数据质量。
- `forward_validation_system/`：每日保存系统当时判断，并在 30/60/90 天后回填真实收益验证评分有效性。
