export type FundamentalDecision = "BUY_READY" | "TRADE_CANDIDATE" | "WATCH" | "RESEARCH_QUEUE" | "FUNDAMENTAL_POOL" | "REJECT" | "PENDING_RESEARCH" | string;

export interface FundamentalCandidate {
  date?: string;
  code: string;
  name: string;
  decision: FundamentalDecision;
  action?: string;
  failed_gates?: string;
  fundamental_first_score?: number;
  company_quality_score?: number;
  industry_logic_score?: number;
  value_gap_score?: number;
  opportunity_score?: number;
  learning_score?: number;
  pool_fundamental_score?: number;
  financial_quality_score?: number;
  base_financial_quality_score?: number;
  financial_statement_score?: number | string;
  statement_coverage_score?: number | string;
  financial_statement_warnings?: string;
  data_quality_score?: number | string;
  data_quality_status?: string;
  data_quality_warnings?: string;
  available_metric_count?: number;
  evidence_quality_score?: number;
  final_research_score?: number;
  valuation_score?: number;
  valuation_level?: string;
  expectation_gap_score?: number;
  expectation_gap?: string;
  trade_score?: number;
  trade_score_date?: string;
  ret20?: number | string;
  ret60?: number | string;
  ret120?: number | string;
  vol60?: number | string;
  drawdown120?: number | string;
  market_ok?: boolean;
  market_reason?: string;
  current_price?: number | string;
  risk_stop?: number | string;
  plan_level?: string;
  final_action?: string;
  warnings?: string;
  research_next_step?: string;
}

export interface FundamentalSummary {
  total: number;
  buyReady: number;
  watch: number;
  reject: number;
  pendingResearch?: number;
  tradeCandidate?: number;
  researchQueue?: number;
  fundamentalPool?: number;
  opportunityCount: number;
  watchCount: number;
  researchQueueCount?: number;
}

export interface PaperState {
  cash?: number;
  equity?: number;
  initial_capital?: number;
  initialCapital?: number;
  last_update?: string;
  lastUpdate?: string;
  positions?: unknown[];
  trades?: unknown[];
}

export interface PaperHolding {
  code: string;
  name: string;
  entry_date?: string;
  entry_price?: number;
  last_price?: number;
  shares?: number;
  cost?: number;
  market_value?: number;
  unrealized_pnl?: number;
  unrealized_return_pct?: number;
  risk_stop?: number;
  last_trade_score?: number;
  fundamental_first_score?: number;
}

export interface PaperTrade {
  date?: string;
  side?: string;
  code?: string;
  name?: string;
  price?: number;
  shares?: number;
  amount?: number;
  fee?: number;
  pnl?: number;
  reason?: string;
}

export interface EquityPoint {
  date: string;
  cash?: number;
  equity?: number;
  positions?: number;
}

export interface DataQualitySummary {
  status?: string;
  overall_score?: number;
  critical_block?: boolean;
  weak_stock_count?: number;
  warnings?: string[];
  generated_at?: string;
}

export interface DataQualityCheck {
  check?: string;
  status?: string;
  score?: number;
  row_count?: number;
  missing_rate?: number;
  warnings?: string;
}

export interface ForwardValidationGroup {
  count?: number;
  matured_count?: number;
  avg_ret30?: number;
  avg_ret60?: number;
  avg_ret90?: number;
  hit_rate30?: number;
  hit_rate60?: number;
  hit_rate90?: number;
}

export interface ForwardValidationSummary {
  prediction_count?: number;
  today_prediction_count?: number;
  groups?: Record<string, ForwardValidationGroup>;
  errors?: string[];
  generated_at?: string;
}

export interface FundamentalFirstDashboard {
  date: string;
  summary: FundamentalSummary;
  candidates: FundamentalCandidate[];
  opportunities: FundamentalCandidate[];
  watch: FundamentalCandidate[];
  paper: {
    state: PaperState;
    holdings: PaperHolding[];
    equityCurve: EquityPoint[];
    trades: PaperTrade[];
    latestTradeFile?: string;
  };
  quality?: {
    summary?: DataQualitySummary;
    checks?: DataQualityCheck[];
  };
  forwardValidation?: ForwardValidationSummary;
  reports: Record<string, string>;
}
