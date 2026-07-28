import type React from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  BarChart3,
  ClipboardList,
  Database,
  FileText,
  LineChart,
  MessageSquareText,
  RefreshCw,
  Search,
  ShieldCheck,
  Target,
  TrendingUp,
  WalletCards,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { fundamentalFirstApi } from "../api/fundamentalFirst";
import { historyApi } from "../api/history";
import { Badge, Button, Card, EmptyState } from "../components/common";
import { ReportMarkdownDrawer } from "../components/report/ReportMarkdownDrawer";
import type { AnalysisReport } from "../types/analysis";
import type { FundamentalCandidate, FundamentalFirstDashboard, PaperHolding, PaperTrade } from "../types/fundamentalFirst";
import { cn } from "../utils/cn";

type Tone = "default" | "success" | "warning" | "danger" | "info" | "history";
type CandidateTab = "all" | "BUY_READY" | "TRADE_CANDIDATE" | "WATCH" | "RESEARCH_QUEUE" | "FUNDAMENTAL_POOL" | "REJECT";

function asNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function formatNumber(value: unknown, digits = 1): string {
  const number = asNumber(value);
  if (number === null) return "--";
  return number.toFixed(digits);
}

function formatMoney(value: unknown): string {
  const number = asNumber(value);
  if (number === null) return "--";
  return number.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatPct(value: unknown): string {
  const number = asNumber(value);
  if (number === null) return "--";
  return number.toFixed(2) + "%";
}

function decisionMeta(decision?: string): { label: string; tone: Tone; description: string } {
  if (decision === "BUY_READY") return { label: "严格买入", tone: "success", description: "全部严格闸门通过，下一交易日仍满足才进入严格模拟盘" };
  if (decision === "TRADE_CANDIDATE") return { label: "交易候选", tone: "info", description: "进入交易机会层样本，但尚未达到严格买入闸门" };
  if (decision === "WATCH") return { label: "继续观察", tone: "warning", description: "已深研但估值、买点或证据强度尚未完全满足" };
  if (decision === "RESEARCH_QUEUE" || decision === "PENDING_RESEARCH") return { label: "深研队列", tone: "info", description: "基本面较好但尚未完成本轮公告、证据、估值和AI深研" };
  if (decision === "FUNDAMENTAL_POOL") return { label: "基本面池", tone: "default", description: "保留在基本面池，等待轮动进入深度研究" };
  if (decision === "REJECT") return { label: "未通过", tone: "danger", description: "已研究后不满足当前系统闸门或存在硬条件不足" };
  return { label: decision || "未知", tone: "default", description: "暂无状态说明" };
}

function tabLabel(tab: CandidateTab): string {
  if (tab === "BUY_READY") return "严格买入";
  if (tab === "TRADE_CANDIDATE") return "交易候选";
  if (tab === "WATCH") return "观察";
  if (tab === "RESEARCH_QUEUE") return "深研队列";
  if (tab === "FUNDAMENTAL_POOL") return "基本面池";
  if (tab === "REJECT") return "未通过";
  return "全部";
}

const StatTile: React.FC<{ label: string; value: string | number; hint?: string; icon: React.ComponentType<{ className?: string }> }> = ({ label, value, hint, icon: Icon }) => (
  <Card padding="sm" className="min-h-[92px] sm:min-h-[108px]">
    <div className="flex items-start justify-between gap-3">
      <div>
        <div className="label-uppercase">{label}</div>
        <div className="mt-2 text-xl font-semibold text-foreground sm:mt-3 sm:text-2xl">{value}</div>
      </div>
      <div className="rounded-xl border border-cyan/25 bg-cyan/10 p-2 text-cyan">
        <Icon className="h-4 w-4" />
      </div>
    </div>
    {hint ? <div className="mt-2 text-[11px] leading-4 text-muted-text sm:mt-3 sm:text-xs sm:leading-5">{hint}</div> : null}
  </Card>
);

const ScoreCell: React.FC<{ label: string; value: unknown; tone?: Tone }> = ({ label, value, tone = "default" }) => {
  const toneClass = tone === "success"
    ? "text-success"
    : tone === "warning"
      ? "text-warning"
      : tone === "danger"
        ? "text-danger"
        : tone === "info"
          ? "text-cyan"
          : "text-foreground";
  return (
    <div className="rounded-xl border border-border/50 bg-surface/35 p-3">
      <div className="text-xs text-muted-text">{label}</div>
      <div className={cn("mt-1 font-mono text-lg font-semibold", toneClass)}>{formatNumber(value, 1)}</div>
    </div>
  );
};

function textOrDash(value: unknown): string {
  if (value === null || value === undefined || value === "") return "--";
  return String(value);
}

const SerenityField: React.FC<{ label: string; value: unknown; wide?: boolean }> = ({ label, value, wide }) => (
  <div className={cn("rounded-xl border border-border/50 bg-surface/25 p-4", wide && "md:col-span-2 xl:col-span-3")}>
    <div className="label-uppercase">{label}</div>
    <div className="mt-2 text-sm leading-6 text-secondary-text">{textOrDash(value)}</div>
  </div>
);

const CandidateListItem: React.FC<{
  row: FundamentalCandidate;
  active: boolean;
  onSelect: () => void;
}> = ({ row, active, onSelect }) => {
  const meta = decisionMeta(row.decision);
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "w-full rounded-xl border p-3 text-left transition-all",
        active
          ? "border-cyan/70 bg-cyan/10 shadow-glow-cyan"
          : "border-border/55 bg-card/50 hover:border-cyan/35 hover:bg-hover/60",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-foreground">{row.name}</div>
          <div className="mt-1 font-mono text-xs text-muted-text">{row.code}</div>
        </div>
        <Badge variant={meta.tone} className="shrink-0">{meta.label}</Badge>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
        <div>
          <div className="text-muted-text">总分</div>
          <div className="mt-0.5 font-mono text-foreground">{formatNumber(row.fundamental_first_score, 1)}</div>
        </div>
        <div>
          <div className="text-muted-text">交易</div>
          <div className="mt-0.5 font-mono text-foreground">{formatNumber(row.trade_score ?? row.opportunity_score, 1)}</div>
        </div>
        <div>
          <div className="text-muted-text">估值</div>
          <div className="mt-0.5 font-mono text-foreground">{formatNumber(row.value_gap_score, 1)}</div>
        </div>
      </div>
    </button>
  );
};

const HoldingRow: React.FC<{ row: PaperHolding }> = ({ row }) => (
  <tr className="border-b border-border/40 last:border-0">
    <td className="px-3 py-3"><div className="font-medium text-foreground">{row.name}</div><div className="mt-1 text-xs text-muted-text">{row.code}</div></td>
    <td className="px-3 py-3 text-right font-mono text-xs text-secondary-text">{row.shares || 0}</td>
    <td className="px-3 py-3 text-right font-mono text-xs text-secondary-text">{formatNumber(row.entry_price, 2)}</td>
    <td className="px-3 py-3 text-right font-mono text-xs text-secondary-text">{formatNumber(row.last_price, 2)}</td>
    <td className="px-3 py-3 text-right font-mono text-xs text-secondary-text">{formatMoney(row.market_value)}</td>
    <td className="px-3 py-3 text-right font-mono text-xs text-secondary-text">{formatPct(row.unrealized_return_pct)}</td>
    <td className="px-3 py-3 text-right font-mono text-xs text-secondary-text">{formatNumber(row.risk_stop, 2)}</td>
  </tr>
);

const TradeRow: React.FC<{ row: PaperTrade }> = ({ row }) => (
  <tr className="border-b border-border/40 last:border-0">
    <td className="px-3 py-3 text-xs text-muted-text">{row.date || "--"}</td>
    <td className="px-3 py-3"><Badge variant={row.side === "BUY" ? "success" : "warning"}>{row.side || "--"}</Badge></td>
    <td className="px-3 py-3"><div className="font-medium text-foreground">{row.name || "--"}</div><div className="mt-1 text-xs text-muted-text">{row.code || "--"}</div></td>
    <td className="px-3 py-3 text-right font-mono text-xs text-secondary-text">{formatNumber(row.price, 2)}</td>
    <td className="px-3 py-3 text-right font-mono text-xs text-secondary-text">{row.shares || 0}</td>
    <td className="px-3 py-3 text-right font-mono text-xs text-secondary-text">{formatMoney(row.amount)}</td>
    <td className="px-3 py-3 text-xs text-muted-text">{row.reason || "--"}</td>
  </tr>
);

const FundamentalFirstPage: React.FC = () => {
  const navigate = useNavigate();
  const [dashboard, setDashboard] = useState<FundamentalFirstDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCode, setSelectedCode] = useState<string>("");
  const [filterText, setFilterText] = useState("");
  const [activeTab, setActiveTab] = useState<CandidateTab>("all");
  const [visibleCount, setVisibleCount] = useState(15);
  const [selectedDetail, setSelectedDetail] = useState<FundamentalCandidate | null>(null);
  const [selectedDetailLoading, setSelectedDetailLoading] = useState(false);
  const [selectedDetailError, setSelectedDetailError] = useState<string | null>(null);
  const [latestReport, setLatestReport] = useState<AnalysisReport | null>(null);
  const [latestReportLoading, setLatestReportLoading] = useState(false);
  const [latestReportError, setLatestReportError] = useState<string | null>(null);
  const [markdownDrawerOpen, setMarkdownDrawerOpen] = useState(false);
  const detailRef = useRef<HTMLElement | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fundamentalFirstApi.getDashboard();
      setDashboard(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const candidates = dashboard?.candidates || [];
  const opportunities = dashboard?.opportunities || [];
  const watch = dashboard?.watch || [];
  const strictBuyReady = dashboard?.summary.buyReady ?? 0;
  const tradeCandidate = dashboard?.summary.tradeCandidate ?? 0;
  const researchQueue = dashboard?.summary.researchQueue ?? dashboard?.summary.pendingResearch ?? 0;
  const fundamentalPool = dashboard?.summary.fundamentalPool ?? 0;
  const paper = dashboard?.paper;
  const state = paper?.state || {};
  const equity = asNumber(state.equity) ?? asNumber(state.initial_capital) ?? asNumber(state.initialCapital) ?? 0;
  const initial = asNumber(state.initial_capital) ?? asNumber(state.initialCapital) ?? 1000000;
  const totalReturn = initial > 0 ? ((equity / initial) - 1) * 100 : 0;
  const latestCurve = useMemo(() => (paper?.equityCurve || []).slice(-8), [paper?.equityCurve]);
  const shadowPaper = dashboard?.shadowPaper;
  const shadowState = shadowPaper?.state || {};
  const shadowEquity = asNumber(shadowState.equity) ?? asNumber(shadowState.initial_capital) ?? 0;
  const shadowInitial = asNumber(shadowState.initial_capital) ?? 1000000;
  const shadowReturn = shadowInitial > 0 ? ((shadowEquity / shadowInitial) - 1) * 100 : 0;
  const shadowCurve = useMemo(() => (shadowPaper?.equityCurve || []).slice(-8), [shadowPaper?.equityCurve]);
  const strictPending = state.pending_orders?.length ?? 0;
  const shadowPending = shadowState.pending_orders?.length ?? 0;
  const strictRejectedOrder = state.order_rejections?.[0];
  const qualitySummary = dashboard?.quality?.summary || {};
  const qualityScore = asNumber(qualitySummary.overall_score);
  const qualityStatus = typeof qualitySummary.status === "string" ? qualitySummary.status : "--";
  const weakStockCount = asNumber(qualitySummary.weak_stock_count) ?? 0;
  const forwardSummary = dashboard?.forwardValidation || {};
  const forwardPredictionCount = asNumber(forwardSummary.prediction_count) ?? 0;
  const forwardTodayCount = asNumber(forwardSummary.today_prediction_count) ?? 0;

  useEffect(() => {
    if (!candidates.length) {
      setSelectedCode("");
      return;
    }
    if (selectedCode && candidates.some((row) => row.code === selectedCode)) {
      return;
    }
    const preferred = opportunities[0] || watch[0] || candidates[0];
    setSelectedCode(preferred.code);
  }, [candidates, opportunities, selectedCode, watch]);

  const filteredCandidates = useMemo(() => {
    const keyword = filterText.trim().toLowerCase();
    return candidates.filter((row) => {
      if (activeTab !== "all" && row.decision !== activeTab) return false;
      if (!keyword) return true;
      return row.code.toLowerCase().includes(keyword) || row.name.toLowerCase().includes(keyword);
    });
  }, [activeTab, candidates, filterText]);

  useEffect(() => {
    setVisibleCount(15);
  }, [activeTab, filterText]);

  const visibleCandidates = useMemo(
    () => filteredCandidates.slice(0, visibleCount),
    [filteredCandidates, visibleCount],
  );

  const selectedSummary = useMemo(
    () => candidates.find((row) => row.code === selectedCode) || candidates[0],
    [candidates, selectedCode],
  );
  const selected = selectedDetail?.code === selectedSummary?.code ? selectedDetail : selectedSummary;
  const selectedMeta = decisionMeta(selected?.decision);
  const selectedHolding = useMemo(
    () => (paper?.holdings || []).find((row) => row.code === selected?.code),
    [paper?.holdings, selected?.code],
  );
  const selectedShadowHolding = useMemo(
    () => (shadowPaper?.holdings || []).find((row) => row.code === selected?.code),
    [selected?.code, shadowPaper?.holdings],
  );

  const handleSelectCandidate = useCallback((code: string) => {
    setSelectedCode(code);
    if (typeof window !== "undefined" && window.innerWidth < 1024) {
      window.setTimeout(() => {
        detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      }, 80);
    }
  }, []);

  useEffect(() => {
    let active = true;
    setSelectedDetail(null);
    setSelectedDetailError(null);
    if (!selectedSummary?.code) {
      setSelectedDetailLoading(false);
      return () => { active = false; };
    }
    setSelectedDetailLoading(true);
    fundamentalFirstApi.getCandidate(selectedSummary.code)
      .then((detail) => {
        if (active) setSelectedDetail(detail);
      })
      .catch((err) => {
        if (active) setSelectedDetailError(err instanceof Error ? err.message : "候选详情加载失败");
      })
      .finally(() => {
        if (active) setSelectedDetailLoading(false);
      });
    return () => { active = false; };
  }, [selectedSummary?.code]);

  useEffect(() => {
    setLatestReport(null);
    setLatestReportError(null);
    setLatestReportLoading(false);
    setMarkdownDrawerOpen(false);
  }, [selectedSummary?.code]);

  const loadLatestReport = useCallback(async () => {
    if (!selectedSummary?.code) return;
    setLatestReportLoading(true);
    setLatestReportError(null);
    try {
      const list = await historyApi.getList({ stockCode: selectedSummary.code, limit: 1 });
      const item = list.items[0];
      if (!item?.id) {
        setLatestReport(null);
        setLatestReportError("这只股票尚未生成单票 AI 报告。");
        return;
      }
      setLatestReport(await historyApi.getDetail(item.id));
    } catch (err) {
      setLatestReportError(err instanceof Error ? err.message : "历史报告加载失败");
    } finally {
      setLatestReportLoading(false);
    }
  }, [selectedSummary?.code]);

  const handleOpenAnalysis = useCallback(() => {
    if (!selected) return;
    navigate(`/analysis?stock=${encodeURIComponent(selected.code)}&name=${encodeURIComponent(selected.name)}`);
  }, [navigate, selected]);

  const handleAskAi = useCallback(() => {
    if (!selected) return;
    const params = new URLSearchParams({ stock: selected.code, name: selected.name });
    if (latestReport?.meta.id !== undefined) params.set("recordId", String(latestReport.meta.id));
    navigate(`/chat?${params.toString()}`);
  }, [latestReport?.meta.id, navigate, selected]);

  const tabs: CandidateTab[] = ["all", "BUY_READY", "TRADE_CANDIDATE", "WATCH", "RESEARCH_QUEUE", "FUNDAMENTAL_POOL", "REJECT"];

  return (
    <div className="flex min-h-[calc(100vh-5rem)] flex-col pb-6 lg:h-[calc(100vh-2rem)] lg:min-h-0 lg:overflow-hidden lg:pb-2">
      <header className="flex flex-shrink-0 flex-col gap-3 px-1 pb-4 sm:px-0">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="label-uppercase">基本面优先系统</div>
            <h1 className="mt-2 text-2xl font-semibold text-foreground">策略池工作台</h1>
            <p className="mt-2 text-sm text-muted-text">先看财务和产业证据，再看估值预期差与交易机会，最后进入现实模拟盘。</p>
          </div>
          <Button type="button" variant="secondary" size="md" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            刷新
          </Button>
        </div>

        <div className="flex flex-col gap-2">
          <div className="relative min-w-0">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-text" />
            <input
              value={filterText}
              onChange={(event) => setFilterText(event.target.value)}
              placeholder="输入股票代码或名称筛选策略池"
              className="h-11 w-full rounded-xl border border-border/70 bg-card/70 pl-9 pr-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted-text focus:border-cyan/55"
            />
          </div>
          <div className="-mx-1 overflow-x-auto px-1 pb-1">
            <div className="flex min-w-max items-center gap-2">
              {tabs.map((tab) => (
                <button
                  key={tab}
                  type="button"
                  onClick={() => setActiveTab(tab)}
                  className={cn(
                    "min-h-11 shrink-0 whitespace-nowrap rounded-xl border px-3 text-sm transition-colors",
                    activeTab === tab
                      ? "border-cyan/60 bg-cyan/12 text-cyan"
                      : "border-border/70 bg-card/60 text-secondary-text hover:bg-hover hover:text-foreground",
                  )}
                >
                  {tabLabel(tab)}
                </button>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-3 gap-2 sm:flex sm:flex-wrap sm:items-center">
            <Button type="button" variant="home-action-ai" size="md" className="w-full px-2 sm:w-auto sm:px-4" onClick={handleOpenAnalysis} disabled={!selected}>
              <TrendingUp className="h-4 w-4" />
              打开分析
            </Button>
            <Button type="button" variant="home-action-ai" size="md" className="w-full px-2 sm:w-auto sm:px-4" onClick={handleAskAi} disabled={!selected}>
              <MessageSquareText className="h-4 w-4" />
              追问 AI
            </Button>
            <Button type="button" variant="home-action-ai" size="md" className="w-full px-2 sm:w-auto sm:px-4" onClick={() => setMarkdownDrawerOpen(true)} disabled={!latestReport?.meta.id}>
              <FileText className="h-4 w-4" />
              完整报告
            </Button>
          </div>
        </div>
      </header>

      {error ? <Card padding="sm" className="mb-4 border-danger/30 text-danger">{error}</Card> : null}

      <div className="grid flex-shrink-0 grid-cols-2 gap-3 sm:gap-4 md:grid-cols-3 xl:grid-cols-6">
        <StatTile label="闸门股票" value={dashboard?.summary.total ?? 0} hint={(dashboard?.date || "--") + " 最新生成"} icon={ShieldCheck} />
        <StatTile label="交易机会层" value={strictBuyReady + tradeCandidate} hint={"严格买入 " + strictBuyReady + " / 候选 " + tradeCandidate} icon={Target} />
        <StatTile label="研究队列" value={researchQueue + fundamentalPool} hint={"深研 " + researchQueue + " / 基本面池 " + fundamentalPool} icon={ClipboardList} />
        <StatTile label="数据质量" value={qualityStatus + "/" + (qualityScore === null ? "--" : qualityScore.toFixed(0))} hint={weakStockCount + " 只数据偏弱"} icon={Database} />
        <StatTile label="前向验证" value={forwardPredictionCount} hint={"今日保存 " + forwardTodayCount + " 条判断"} icon={LineChart} />
        <StatTile
          label="双轨模拟盘"
          value={formatMoney(equity)}
          hint={"严格 " + totalReturn.toFixed(2) + "% / 影子 " + shadowReturn.toFixed(2) + "% / 待确认 " + strictPending + "+" + shadowPending}
          icon={WalletCards}
        />
      </div>

      <div className="mt-4 grid gap-4 lg:min-h-0 lg:flex-1 xl:grid-cols-[22rem_minmax(0,1fr)]">
        <Card padding="sm" className="flex flex-col lg:min-h-0">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div>
              <div className="label-uppercase">Pool</div>
              <h2 className="mt-1 text-lg font-semibold text-foreground">策略池股票</h2>
            </div>
            <div className="text-xs text-muted-text">{filteredCandidates.length}/{candidates.length}</div>
          </div>
          <div className="max-h-[45vh] space-y-2 overflow-y-auto overscroll-contain pr-1 lg:max-h-none lg:min-h-0 lg:flex-1">
            {loading ? <div className="p-6 text-center text-sm text-muted-text">加载中...</div> : null}
            {!loading && visibleCandidates.length ? visibleCandidates.map((row) => (
              <CandidateListItem
                key={row.code}
                row={row}
                active={row.code === selected?.code}
                onSelect={() => handleSelectCandidate(row.code)}
              />
            )) : null}
            {!loading && visibleCandidates.length < filteredCandidates.length ? (
              <Button
                type="button"
                variant="secondary"
                size="md"
                className="w-full"
                onClick={() => setVisibleCount((count) => count + 15)}
              >
                加载更多（剩余 {filteredCandidates.length - visibleCandidates.length}）
              </Button>
            ) : null}
            {!loading && !filteredCandidates.length ? (
              <EmptyState title="暂无匹配股票" description="调整筛选条件后再看。" icon={<Search className="h-6 w-6" />} />
            ) : null}
          </div>
        </Card>

        <section ref={detailRef} className="scroll-mt-20 pr-1 lg:min-h-0 lg:overflow-y-auto">
          {selected ? (
            <div className="space-y-4 pb-6">
              {selectedDetailLoading ? (
                <div className="text-xs text-muted-text">正在加载完整研究详情...</div>
              ) : null}
              {selectedDetailError ? (
                <div className="text-xs text-warning">{selectedDetailError}，当前显示精简摘要。</div>
              ) : null}
              <Card padding="lg">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-2xl font-semibold text-foreground sm:text-3xl">{selected.name}</h2>
                      <Badge variant={selectedMeta.tone} size="md">{selectedMeta.label}</Badge>
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-muted-text">
                      <span className="font-mono text-cyan">{selected.code}</span>
                      <span>{selectedMeta.description}</span>
                    </div>
                  </div>
                  <div className="w-full rounded-2xl border border-border/60 bg-surface/30 px-4 py-3 text-left sm:w-auto sm:text-right">
                    <div className="text-xs text-muted-text">基本面优先总分</div>
                    <div className="mt-1 font-mono text-3xl font-semibold text-foreground">{formatNumber(selected.fundamental_first_score, 1)}</div>
                  </div>
                </div>

                <div className="mt-5 grid gap-3 md:grid-cols-3 xl:grid-cols-7">
                  <ScoreCell label="公司质量" value={selected.company_quality_score} tone="success" />
                  <ScoreCell label="财务三表" value={selected.financial_statement_score} tone="success" />
                  <ScoreCell label="产业逻辑" value={selected.industry_logic_score} tone="info" />
                  <ScoreCell label="证据质量" value={selected.evidence_quality_score} />
                  <ScoreCell label="数据质量" value={selected.data_quality_score} tone={selected.data_quality_status === "BAD" ? "danger" : "info"} />
                  <ScoreCell label="估值预期差" value={selected.value_gap_score} tone="warning" />
                  <ScoreCell label="交易机会" value={selected.trade_score ?? selected.opportunity_score} />
                </div>

                <div className="mt-5 grid gap-3 lg:grid-cols-2">
                  <div className="rounded-xl border border-border/50 bg-surface/25 p-4">
                    <div className="label-uppercase">Gate Result</div>
                    <div className="mt-2 text-sm leading-6 text-secondary-text">{selected.action || selected.failed_gates || "暂无闸门说明"}</div>
                    <div className="mt-3 text-xs leading-5 text-muted-text">{selected.failed_gates || "没有记录失败闸门"}</div>
                  </div>
                  <div className="rounded-xl border border-border/50 bg-surface/25 p-4">
                    <div className="label-uppercase">Next Step</div>
                    <div className="mt-2 text-sm leading-6 text-secondary-text">{selected.research_next_step || selected.final_action || "等待下一轮证据、估值或交易结构确认"}</div>
                    <div className="mt-3 text-xs leading-5 text-muted-text">{[selected.financial_statement_warnings, selected.data_quality_warnings, selected.warnings, selected.market_reason].filter(Boolean).join("；") || "暂无额外风险提示"}</div>
                  </div>
                </div>
              </Card>

              <Card title="Serenity 研究层" subtitle="Why" padding="md">
                <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                  <div className="text-sm leading-6 text-muted-text">把产业链卡点、证据强度、估值压力和交易闸门放在同一张研究卡里。</div>
                  <div className="rounded-xl border border-cyan/30 bg-cyan/10 px-3 py-2 text-sm text-cyan">
                    研究分 {formatNumber(selected.serenity_research_score, 1)}
                  </div>
                </div>
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  <SerenityField label="产业链位置" value={selected.serenity_chain_position} />
                  <SerenityField label="核心卡点" value={selected.serenity_bottleneck_layer} />
                  <SerenityField label="公司是否真卡位" value={selected.serenity_positioning_verdict} />
                  <SerenityField label="证据等级" value={selected.serenity_evidence_grade} />
                  <SerenityField label="估值是否透支" value={selected.serenity_valuation_stretch} />
                  <SerenityField label="识别依据" value={selected.serenity_chain_note} />
                  <SerenityField label="客户/认证/订单证据" value={selected.serenity_customer_order_evidence} wide />
                  <SerenityField label="财务质量证据" value={selected.serenity_financial_evidence} wide />
                  <SerenityField label="反证与降级条件" value={selected.serenity_downgrade_conditions} wide />
                  <SerenityField label="为什么进入/不进入交易机会层" value={selected.serenity_opportunity_rationale} wide />
                </div>
              </Card>

              <div className="grid gap-4 xl:grid-cols-[1fr_0.85fr]">
                <Card title="交易计划层" subtitle="Plan" padding="md">
                  <div className="grid gap-3 md:grid-cols-2">
                    <div className="rounded-xl border border-border/50 bg-surface/25 p-4">
                      <div className="text-xs text-muted-text">现价 / 止损</div>
                      <div className="mt-2 font-mono text-xl text-foreground">{formatNumber(selected.current_price, 2)} / {formatNumber(selected.risk_stop, 2)}</div>
                    </div>
                    <div className="rounded-xl border border-border/50 bg-surface/25 p-4">
                      <div className="text-xs text-muted-text">20/60/120日收益</div>
                      <div className="mt-2 font-mono text-sm text-foreground">{formatPct(selected.ret20)} / {formatPct(selected.ret60)} / {formatPct(selected.ret120)}</div>
                    </div>
                    <div className="rounded-xl border border-border/50 bg-surface/25 p-4">
                      <div className="text-xs text-muted-text">120日回撤</div>
                      <div className="mt-2 font-mono text-xl text-foreground">{formatPct(selected.drawdown120)}</div>
                    </div>
                    <div className="rounded-xl border border-border/50 bg-surface/25 p-4">
                      <div className="text-xs text-muted-text">计划层级</div>
                      <div className="mt-2 text-sm text-foreground">{selected.plan_level || "--"}</div>
                    </div>
                  </div>
                </Card>

                <Card title="最新 AI 分析" subtitle="Report" padding="md">
                  {latestReportLoading ? (
                    <div className="py-8 text-center text-sm text-muted-text">加载历史报告中...</div>
                  ) : latestReport ? (
                    <div className="space-y-3">
                      <div>
                        <div className="text-xs text-muted-text">核心洞察</div>
                        <div className="mt-2 text-sm leading-6 text-secondary-text">{latestReport.summary.analysisSummary}</div>
                      </div>
                      <div className="grid gap-3 sm:grid-cols-2">
                        <div className="rounded-xl border border-border/50 bg-surface/25 p-3">
                          <div className="text-xs text-muted-text">操作建议</div>
                          <div className="mt-1 text-sm text-foreground">{latestReport.summary.operationAdvice}</div>
                        </div>
                        <div className="rounded-xl border border-border/50 bg-surface/25 p-3">
                          <div className="text-xs text-muted-text">情绪分</div>
                          <div className="mt-1 font-mono text-lg text-foreground">{latestReport.summary.sentimentScore ?? "--"}</div>
                        </div>
                      </div>
                      <div className="text-xs text-muted-text">报告时间：{latestReport.meta.createdAt || "--"}</div>
                    </div>
                  ) : (
                    <div className="space-y-4">
                      <EmptyState
                        title={latestReportError ? "报告暂不可用" : "AI 报告按需加载"}
                        description={latestReportError || "点击后再读取该股票的最新历史报告，减少首屏等待。"}
                        icon={<FileText className="h-6 w-6" />}
                      />
                      <Button type="button" variant="secondary" size="md" className="w-full" onClick={() => void loadLatestReport()}>
                        加载 AI 报告
                      </Button>
                    </div>
                  )}
                </Card>
              </div>

              <Card title="现实模拟盘关联" subtitle="Paper" padding="md">
                {selectedHolding || selectedShadowHolding ? (
                  <div className="space-y-4">
                    {selectedHolding ? (
                      <div>
                        <div className="mb-2"><Badge variant="success">严格盘持仓</Badge></div>
                        <div className="grid gap-3 md:grid-cols-4">
                          <ScoreCell label="持仓股数" value={selectedHolding.shares} />
                          <ScoreCell label="成本价" value={selectedHolding.entry_price} />
                          <ScoreCell label="现价" value={selectedHolding.last_price} />
                          <ScoreCell label="收益率" value={selectedHolding.unrealized_return_pct} tone={asNumber(selectedHolding.unrealized_return_pct) !== null && Number(selectedHolding.unrealized_return_pct) >= 0 ? "success" : "danger"} />
                        </div>
                      </div>
                    ) : null}
                    {selectedShadowHolding ? (
                      <div>
                        <div className="mb-2"><Badge variant="info">影子盘持仓</Badge></div>
                        <div className="grid gap-3 md:grid-cols-4">
                          <ScoreCell label="持仓股数" value={selectedShadowHolding.shares} />
                          <ScoreCell label="成本价" value={selectedShadowHolding.entry_price} />
                          <ScoreCell label="现价" value={selectedShadowHolding.last_price} />
                          <ScoreCell label="收益率" value={selectedShadowHolding.unrealized_return_pct} tone={asNumber(selectedShadowHolding.unrealized_return_pct) !== null && Number(selectedShadowHolding.unrealized_return_pct) >= 0 ? "success" : "danger"} />
                        </div>
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <EmptyState
                    title="未进入模拟持仓"
                    description={strictRejectedOrder?.reason || "严格盘只接收严格买入；影子盘接收已深研且连续满足条件的交易候选，两者都在下一次运行确认后成交。"}
                    icon={<WalletCards className="h-6 w-6" />}
                  />
                )}
              </Card>
            </div>
          ) : (
            <EmptyState title="暂无策略池数据" description="运行基本面优先流水线后这里会显示详情。" icon={<ShieldCheck className="h-6 w-6" />} />
          )}
        </section>
      </div>

      <div className="mt-4 grid flex-shrink-0 gap-4 pb-4 xl:grid-cols-3">
        <Card title="严格盘持仓" subtitle="Strict" padding="none">
          {paper?.holdings.length ? (
            <div className="overflow-x-auto">
              <table className="min-w-[720px] text-left text-sm">
                <thead className="border-b border-border/60 text-xs text-muted-text"><tr><th className="px-3 py-3">股票</th><th className="px-3 py-3 text-right">股数</th><th className="px-3 py-3 text-right">成本价</th><th className="px-3 py-3 text-right">现价</th><th className="px-3 py-3 text-right">市值</th><th className="px-3 py-3 text-right">收益率</th><th className="px-3 py-3 text-right">止损</th></tr></thead>
                <tbody>{paper.holdings.map((row) => <HoldingRow key={row.code} row={row} />)}</tbody>
              </table>
            </div>
          ) : <EmptyState title="暂无持仓" description="只有严格闸门全部通过并在下一次运行仍有效时才买入。" icon={<WalletCards className="h-6 w-6" />} />}
        </Card>

        <Card title="候选影子盘持仓" subtitle="Shadow" padding="none">
          {shadowPaper?.holdings.length ? (
            <div className="overflow-x-auto">
              <table className="min-w-[720px] text-left text-sm">
                <thead className="border-b border-border/60 text-xs text-muted-text"><tr><th className="px-3 py-3">股票</th><th className="px-3 py-3 text-right">股数</th><th className="px-3 py-3 text-right">成本价</th><th className="px-3 py-3 text-right">现价</th><th className="px-3 py-3 text-right">市值</th><th className="px-3 py-3 text-right">收益率</th><th className="px-3 py-3 text-right">止损</th></tr></thead>
                <tbody>{shadowPaper.holdings.map((row) => <HoldingRow key={row.code} row={row} />)}</tbody>
              </table>
            </div>
          ) : <EmptyState title="暂无影子持仓" description="已深研交易候选需连续两次满足交易分和证据条件后才进入。" icon={<WalletCards className="h-6 w-6" />} />}
        </Card>

        <Card title="双轨净值与交易" subtitle="Equity" padding="md">
          <div className="grid gap-3">
            <div className="rounded-xl border border-border/50 p-3">
              <div className="mb-2 flex items-center gap-2 text-xs text-muted-text"><Activity className="h-4 w-4" />严格 / 影子净值</div>
              {latestCurve.length || shadowCurve.length ? Array.from(new Set([...latestCurve, ...shadowCurve].map((point) => point.date))).slice(-8).map((date) => (
                <div key={date} className="grid grid-cols-[1fr_auto_auto] items-center gap-3 border-b border-border/30 py-2 text-xs last:border-0">
                  <span className="text-muted-text">{date}</span>
                  <span className="font-mono text-foreground">{formatMoney(latestCurve.find((point) => point.date === date)?.equity)}</span>
                  <span className="font-mono text-cyan">{formatMoney(shadowCurve.find((point) => point.date === date)?.equity)}</span>
                </div>
              )) : <div className="py-4 text-center text-xs text-muted-text">暂无净值记录</div>}
            </div>
            <div className="rounded-xl border border-border/50 p-3">
              <div className="mb-2 flex items-center gap-2 text-xs text-muted-text"><BarChart3 className="h-4 w-4" />最近交易</div>
              {paper?.trades.length || shadowPaper?.trades.length ? (
                <div className="max-h-56 overflow-y-auto">
                  <table className="min-w-[760px] text-left text-sm">
                    <tbody>
                      {(paper?.trades || []).slice(-4).map((row, index) => <TradeRow key={"strict-" + (row.code || "") + String(index)} row={{ ...row, reason: "[严格] " + (row.reason || "") }} />)}
                      {(shadowPaper?.trades || []).slice(-4).map((row, index) => <TradeRow key={"shadow-" + (row.code || "") + String(index)} row={{ ...row, reason: "[影子] " + (row.reason || "") }} />)}
                    </tbody>
                  </table>
                </div>
              ) : <div className="py-4 text-center text-xs text-muted-text">暂无交易</div>}
            </div>
          </div>
        </Card>
      </div>

      {markdownDrawerOpen && latestReport?.meta.id ? (
        <ReportMarkdownDrawer
          key={latestReport.meta.id}
          recordId={latestReport.meta.id}
          stockName={latestReport.meta.stockName || selected?.name || ""}
          stockCode={latestReport.meta.stockCode || selected?.code || ""}
          reportLanguage={latestReport.meta.reportLanguage}
          onClose={() => setMarkdownDrawerOpen(false)}
        />
      ) : null}
    </div>
  );
};

export default FundamentalFirstPage;
