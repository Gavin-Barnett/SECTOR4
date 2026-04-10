import { startTransition, useEffect, useState } from "react";
import { ThemeProvider, useTheme } from "next-themes";
import {
  ActivityIcon,
  ArrowUpRightIcon,
  BrainCircuitIcon,
  CircleAlertIcon,
  ExternalLinkIcon,
  MoonStarIcon,
  SearchIcon,
  SunMediumIcon,
  UsersIcon,
} from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

type ComponentDetailValue =
  | string
  | number
  | boolean
  | null
  | ComponentDetailValue[]
  | { [key: string]: ComponentDetailValue };

type HealthState = {
  status: string;
  app_env?: string;
  scheduler_enabled?: boolean;
  scheduler_running?: boolean;
};

type SignalComponent = {
  status: string;
  raw_score: number | null;
  max_score: number;
  reweighted_score: number | null;
  details: Record<string, ComponentDetailValue>;
};

type SignalAiSummary = {
  text: string;
  highlights: string[];
  warnings: string[];
  provider: string;
  model: string | null;
  generated_at: string | null;
};

type SignalAlertRecord = {
  id: number;
  channel: string;
  status: string;
  sent_at: string | null;
  event_type: string;
  reason: string;
  score_at_send: string;
  total_purchase_usd_at_send: string;
  unique_buyers_at_send: number;
};

type SignalSummary = {
  id: number;
  issuer_cik: string;
  ticker: string | null;
  issuer_name: string;
  window_start: string;
  window_end: string;
  unique_buyers: number;
  total_purchase_usd: string;
  average_purchase_usd: string;
  signal_score: string;
  latest_transaction_date: string | null;
  transaction_count: number;
  first_time_buyer_count: number;
  includes_indirect: boolean;
  includes_amendment: boolean;
  health_status: string;
  price_context_status: string;
  summary_status: string;
  explanation: string;
  component_breakdown: Record<string, SignalComponent>;
};

type SignalDetail = SignalSummary & {
  ai_summary: SignalAiSummary | null;
  alerts: SignalAlertRecord[];
  trade_setup: Record<string, ComponentDetailValue> | null;
  qualifying_transactions: Array<{
    transaction_id: number;
    accession_number: string;
    filing_url: string;
    xml_url: string;
    insider_id: number;
    insider_name: string;
    insider_role: string | null;
    transaction_date: string;
    security_title: string | null;
    shares: string | null;
    price_per_share: string | null;
    value_usd: string | null;
    ownership_type: string | null;
  }>;
};

type IssuerDetail = {
  id: number;
  cik: string;
  ticker: string | null;
  name: string;
  exchange: string | null;
  sic: string | null;
  state_of_incorp: string | null;
  market_cap: string | null;
  latest_price: string | null;
  filing_count: number;
  transaction_count: number;
  latest_signal_id: number | null;
  latest_signal_score: string | null;
  latest_signal_window_end: string | null;
  latest_signal_health_status: string | null;
  latest_signal_price_context_status: string | null;
};

type Filters = {
  ticker: string;
  cik: string;
  minimumScore: string;
  minimumUniqueBuyers: string;
  marketCapMax: string;
  dateFrom: string;
  dateTo: string;
  includeUnknownHealth: boolean;
  includeIndirect: boolean;
  includeAmendments: boolean;
};

type BadgeVariant = "default" | "secondary" | "outline" | "destructive";

type DetailEntry = {
  label: string;
  value: string;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const DEFAULT_FILTERS: Filters = {
  ticker: "",
  cik: "",
  minimumScore: "",
  minimumUniqueBuyers: "",
  marketCapMax: "",
  dateFrom: "",
  dateTo: "",
  includeUnknownHealth: true,
  includeIndirect: false,
  includeAmendments: true,
};

export default function App() {
  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="dark"
      disableTransitionOnChange
      enableSystem
      storageKey="sector4-theme"
    >
      <SignalDashboard />
    </ThemeProvider>
  );
}
function SignalDashboard() {
  const [health, setHealth] = useState<HealthState | null>(null);
  const [signals, setSignals] = useState<SignalSummary[]>([]);
  const [selectedSignalId, setSelectedSignalId] = useState<number | null>(null);
  const [selectedSignal, setSelectedSignal] = useState<SignalDetail | null>(null);
  const [selectedIssuer, setSelectedIssuer] = useState<IssuerDetail | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [appliedFilters, setAppliedFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [loadingSignals, setLoadingSignals] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [signalsError, setSignalsError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    fetch(`${API_BASE}/health`)
      .then(readJson<HealthState>)
      .then((payload) => {
        if (!active) {
          return;
        }
        setHealth(payload);
        setHealthError(null);
      })
      .catch((fetchError: Error) => {
        if (!active) {
          return;
        }
        setHealth(null);
        setHealthError(fetchError.message);
      });

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    setLoadingSignals(true);
    setSignalsError(null);

    fetch(`${API_BASE}/signals${buildSignalQuery(appliedFilters)}`)
      .then(readJson<SignalSummary[]>)
      .then((payload) => {
        if (!active) {
          return;
        }
        setSignals(payload);
        setSelectedSignalId((current) => {
          if (current && payload.some((signal) => signal.id === current)) {
            return current;
          }
          return payload[0]?.id ?? null;
        });
        if (payload.length === 0) {
          setDetailOpen(false);
        }
      })
      .catch((fetchError: Error) => {
        if (!active) {
          return;
        }
        setSignals([]);
        setSelectedSignalId(null);
        setSelectedSignal(null);
        setSelectedIssuer(null);
        setDetailOpen(false);
        setSignalsError(fetchError.message);
      })
      .finally(() => {
        if (active) {
          setLoadingSignals(false);
        }
      });

    return () => {
      active = false;
    };
  }, [appliedFilters]);

  const selectedSignalSummary =
    selectedSignalId === null
      ? null
      : signals.find((signal) => signal.id === selectedSignalId) ?? null;

  useEffect(() => {
    if (!selectedSignalId || !selectedSignalSummary) {
      setSelectedSignal(null);
      setSelectedIssuer(null);
      setDetailError(null);
      setLoadingDetail(false);
      return;
    }

    let active = true;
    setLoadingDetail(true);
    setDetailError(null);

    Promise.all([
      fetch(`${API_BASE}/signals/${selectedSignalId}`).then(readJson<SignalDetail>),
      fetch(`${API_BASE}/issuers/${selectedSignalSummary.issuer_cik}`).then(
        readJson<IssuerDetail>,
      ),
    ])
      .then(([signalPayload, issuerPayload]) => {
        if (!active) {
          return;
        }
        setSelectedSignal(signalPayload);
        setSelectedIssuer(issuerPayload);
      })
      .catch((fetchError: Error) => {
        if (!active) {
          return;
        }
        setSelectedSignal(null);
        setSelectedIssuer(null);
        setDetailError(fetchError.message);
      })
      .finally(() => {
        if (active) {
          setLoadingDetail(false);
        }
      });

    return () => {
      active = false;
    };
  }, [selectedSignalId, selectedSignalSummary]);

  const activeTicker = appliedFilters.ticker.trim().toUpperCase();
  const sidebarSignal = selectedSignalSummary ?? signals[0] ?? null;
  const detailRecord = selectedSignal;
  const healthStatus = health?.status ?? (healthError ? "error" : "pending");

  function openSignalDetail(signalId: number) {
    setSelectedSignalId(signalId);
    setDetailOpen(true);
  }

  function applyTickerSearch() {
    startTransition(() => {
      setAppliedFilters({ ...DEFAULT_FILTERS, ticker: filters.ticker });
    });
  }

  function clearTickerSearch() {
    const reset = { ...DEFAULT_FILTERS };
    setFilters(reset);
    startTransition(() => {
      setAppliedFilters(reset);
    });
  }
  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="mx-auto flex max-w-[1560px] flex-col gap-5 px-4 py-5 sm:px-6 lg:px-8">
        <header className="flex flex-col gap-3 rounded-3xl border border-border/70 bg-card/80 px-4 py-4 shadow-sm backdrop-blur sm:flex-row sm:items-center sm:justify-between">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-heading text-2xl font-medium tracking-tight sm:text-3xl">
                SECTOR4
              </h1>
              <Badge variant="outline">Recent opportunities</Badge>
            </div>

          </div>
          <div className="flex items-center gap-2 self-start sm:self-auto">
            <Badge variant={statusVariant(healthStatus)}>
              API {formatLabel(healthStatus)}
            </Badge>
            <ThemeToggle />
          </div>
        </header>

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px] xl:items-start">
          <Card className="order-2 border-border/70 bg-card/85 shadow-sm xl:order-1">
            <CardHeader className="gap-4 border-b border-border/60">
              <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
                <div className="space-y-1.5">
                  <CardTitle className="text-xl">Opportunity board</CardTitle>
                  <CardDescription>
                    Recent opportunities ranked by signal score. Scanner defaults stay hidden so the
                    board stays readable.
                  </CardDescription>
                </div>
                <form
                  className="flex w-full flex-col gap-2 sm:flex-row sm:items-end lg:w-auto"
                  onSubmit={(event) => {
                    event.preventDefault();
                    applyTickerSearch();
                  }}
                >
                  <div className="w-full sm:min-w-72">
                    <label
                      className="mb-2 block text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground"
                      htmlFor="ticker-search"
                    >
                      Ticker search
                    </label>
                    <Input
                      id="ticker-search"
                      onChange={(event) =>
                        setFilters((current) => ({ ...current, ticker: event.target.value }))
                      }
                      placeholder="Search ticker"
                      value={filters.ticker}
                    />
                  </div>
                  <div className="flex gap-2">
                    {(filters.ticker || activeTicker) && (
                      <Button onClick={clearTickerSearch} type="button" variant="outline">
                        Clear
                      </Button>
                    )}
                    <Button type="submit">
                      <SearchIcon data-icon="inline-start" />
                      Search
                    </Button>
                  </div>
                </form>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline">
                  {loadingSignals ? "Refreshing..." : `${formatCount(signals.length)} visible`}
                </Badge>
                {activeTicker ? (
                  <Badge variant="secondary">Ticker {activeTicker}</Badge>
                ) : (
                  <Badge variant="outline">Scanner defaults active</Badge>
                )}
              </div>
            </CardHeader>
            <CardContent className="pt-4">
              {signalsError ? (
                <Alert variant="destructive" className="mb-4">
                  <CircleAlertIcon />
                  <AlertTitle>Request error</AlertTitle>
                  <AlertDescription>{signalsError}</AlertDescription>
                </Alert>
              ) : null}

              {loadingSignals ? (
                <SignalBoardSkeleton />
              ) : signals.length === 0 ? (
                <Empty className="border-border/70 bg-background/60 py-14">
                  <EmptyHeader>
                    <EmptyMedia variant="icon">
                      <ActivityIcon />
                    </EmptyMedia>
                    <EmptyTitle>No opportunities in view</EmptyTitle>
                    <EmptyDescription>
                      {activeTicker
                        ? `No current signal matched ${activeTicker}. Clear the search to return to the ranked board.`
                        : "No qualifying public clusters are visible yet. Once filings are ingested, the highest-ranking names appear here."}
                    </EmptyDescription>
                  </EmptyHeader>
                </Empty>
              ) : (
                <>
                  <div className="hidden xl:block">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead className="w-[110px]">Score</TableHead>
                          <TableHead>Opportunity</TableHead>
                          <TableHead className="w-[210px]">Cluster</TableHead>
                          <TableHead className="w-[220px]">Context</TableHead>
                          <TableHead className="w-[160px]">Latest trade</TableHead>
                          <TableHead className="w-[140px] text-right">Action</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {signals.map((signal, index) => {
                          const scoreDescriptor = describeScore(signal.signal_score);
                          const isSelected = signal.id === selectedSignalId;

                          return (
                            <TableRow
                              className={cn(
                                "cursor-pointer align-top transition-colors hover:bg-muted/35",
                                isSelected && "bg-muted/35",
                              )}
                              key={signal.id}
                              onClick={() => openSignalDetail(signal.id)}
                            >
                              <TableCell className="align-top">
                                <div className="flex min-w-0 flex-col gap-1">
                                  <span className="text-[0.72rem] uppercase tracking-[0.18em] text-muted-foreground">
                                    #{index + 1}
                                  </span>
                                  <span className="text-2xl font-semibold tabular-nums">
                                    {formatScore(signal.signal_score)}
                                  </span>
                                  <Badge variant={scoreDescriptor.variant}>{scoreDescriptor.label}</Badge>
                                </div>
                              </TableCell>
                              <TableCell className="align-top whitespace-normal">
                                <div className="space-y-3">
                                  <div className="flex flex-wrap items-center gap-2">
                                    <Badge variant="outline">{signal.ticker ?? signal.issuer_cik}</Badge>
                                    {signal.first_time_buyer_count > 0 ? (
                                      <Badge variant="secondary">
                                        {formatCount(signal.first_time_buyer_count)} first-time buyer(s)
                                      </Badge>
                                    ) : null}
                                  </div>
                                  <div className="space-y-1">
                                    <div className="font-medium text-foreground">{signal.issuer_name}</div>
                                    <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
                                      {signal.explanation}
                                    </p>
                                  </div>
                                </div>
                              </TableCell>
                              <TableCell className="align-top whitespace-normal">
                                <div className="space-y-1.5 text-sm text-muted-foreground">
                                  <div className="font-medium text-foreground">
                                    {formatUsd(signal.total_purchase_usd)}
                                  </div>
                                  <div>
                                    {formatCount(signal.unique_buyers)} insiders across {formatCount(signal.transaction_count)} qualifying buy(s)
                                  </div>
                                  <div>Average ticket {formatUsd(signal.average_purchase_usd)}</div>
                                </div>
                              </TableCell>
                              <TableCell className="align-top whitespace-normal">
                                <div className="flex flex-wrap gap-2">
                                  <StatusBadge label="Health" value={signal.health_status} />
                                  <StatusBadge label="Price" value={signal.price_context_status} />
                                  <StatusBadge
                                    label="Catalyst"
                                    value={signal.component_breakdown.event_context?.status ?? "unknown"}
                                  />
                                </div>
                              </TableCell>
                              <TableCell className="align-top whitespace-normal">
                                <div className="space-y-1 text-sm text-muted-foreground">
                                  <div className="font-medium text-foreground">
                                    {formatShortDate(signal.latest_transaction_date ?? signal.window_end)}
                                  </div>
                                  <div>{formatDateRange(signal.window_start, signal.window_end)}</div>
                                </div>
                              </TableCell>
                              <TableCell className="align-top text-right">
                                <Button
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    openSignalDetail(signal.id);
                                  }}
                                  size="sm"
                                  type="button"
                                  variant={isSelected ? "secondary" : "outline"}
                                >
                                  View
                                  <ArrowUpRightIcon data-icon="inline-end" />
                                </Button>
                              </TableCell>
                            </TableRow>
                          );
                        })}
                      </TableBody>
                    </Table>
                  </div>

                  <div className="grid gap-3 xl:hidden">
                    {signals.map((signal, index) => {
                      const scoreDescriptor = describeScore(signal.signal_score);

                      return (
                        <Card
                          key={signal.id}
                          size="sm"
                          className="border border-border/70 bg-background/70 shadow-sm"
                        >
                          <CardHeader className="gap-3">
                            <div className="flex items-start justify-between gap-3">
                              <div className="space-y-2">
                                <div className="flex flex-wrap items-center gap-2">
                                  <Badge variant="outline">#{index + 1}</Badge>
                                  <Badge variant="outline">{signal.ticker ?? signal.issuer_cik}</Badge>
                                </div>
                                <div>
                                  <CardTitle className="text-base">{signal.issuer_name}</CardTitle>
                                  <CardDescription>{signal.explanation}</CardDescription>
                                </div>
                              </div>
                              <div className="text-right">
                                <div className="text-2xl font-semibold tabular-nums">
                                  {formatScore(signal.signal_score)}
                                </div>
                                <Badge variant={scoreDescriptor.variant}>{scoreDescriptor.label}</Badge>
                              </div>
                            </div>
                          </CardHeader>
                          <CardContent className="space-y-4">
                            <div className="grid grid-cols-2 gap-3">
                              <MetricBlock
                                label="Buy value"
                                value={formatCompactUsd(signal.total_purchase_usd)}
                              />
                              <MetricBlock
                                label="Buyers"
                                value={formatCount(signal.unique_buyers)}
                              />
                              <MetricBlock
                                label="Latest"
                                value={formatShortDate(signal.latest_transaction_date ?? signal.window_end)}
                              />
                              <MetricBlock
                                label="First-time"
                                value={formatCount(signal.first_time_buyer_count)}
                              />
                            </div>
                            <div className="flex flex-wrap gap-2">
                              <StatusBadge label="Health" value={signal.health_status} />
                              <StatusBadge label="Price" value={signal.price_context_status} />
                            </div>
                            <Button className="w-full" onClick={() => openSignalDetail(signal.id)} type="button">
                              View details
                              <ArrowUpRightIcon data-icon="inline-end" />
                            </Button>
                          </CardContent>
                        </Card>
                      );
                    })}
                  </div>
                </>
              )}
            </CardContent>
          </Card>

          <Card className="order-1 border-border/70 bg-card/85 shadow-sm xl:order-2 xl:sticky xl:top-5">
            <CardHeader>
              <CardDescription>Current focus</CardDescription>
              <CardTitle className="text-xl">
                {sidebarSignal?.ticker ?? sidebarSignal?.issuer_cik ?? "Waiting for signals"}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {loadingSignals ? (
                <div className="space-y-3">
                  <Skeleton className="h-10 w-28" />
                  <Skeleton className="h-24 w-full rounded-2xl" />
                  <Skeleton className="h-28 w-full rounded-2xl" />
                </div>
              ) : sidebarSignal ? (
                <>
                  <div className="space-y-2">
                    <div className="flex items-end gap-3">
                      <div className="text-5xl font-semibold tracking-tight tabular-nums">
                        {formatScore(sidebarSignal.signal_score)}
                      </div>
                      <Badge variant={describeScore(sidebarSignal.signal_score).variant}>
                        {describeScore(sidebarSignal.signal_score).label}
                      </Badge>
                    </div>
                    <p className="text-sm leading-6 text-muted-foreground">
                      {sidebarSignal.issuer_name}
                    </p>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <MetricBlock
                      label="Buy value"
                      value={formatCompactUsd(sidebarSignal.total_purchase_usd)}
                    />
                    <MetricBlock
                      label="Unique insiders"
                      value={formatCount(sidebarSignal.unique_buyers)}
                    />
                    <MetricBlock
                      label="Latest trade"
                      value={formatShortDate(sidebarSignal.latest_transaction_date ?? sidebarSignal.window_end)}
                    />
                    <MetricBlock
                      label="First-time buyers"
                      value={formatCount(sidebarSignal.first_time_buyer_count)}
                    />
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <StatusBadge label="Health" value={sidebarSignal.health_status} />
                    <StatusBadge label="Price" value={sidebarSignal.price_context_status} />
                    <StatusBadge
                      label="Catalyst"
                      value={sidebarSignal.component_breakdown.event_context?.status ?? "unknown"}
                    />
                  </div>
                  <p className="text-sm leading-6 text-muted-foreground">{sidebarSignal.explanation}</p>
                  <Button className="w-full" onClick={() => openSignalDetail(sidebarSignal.id)} type="button">
                    Open details
                    <ArrowUpRightIcon data-icon="inline-end" />
                  </Button>
                </>
              ) : (
                <Empty className="border-border/70 bg-background/60 py-12">
                  <EmptyHeader>
                    <EmptyMedia variant="icon">
                      <UsersIcon />
                    </EmptyMedia>
                    <EmptyTitle>No focused opportunity yet</EmptyTitle>
                    <EmptyDescription>
                      Once a qualifying cluster is available, the current focus card will mirror it here.
                    </EmptyDescription>
                  </EmptyHeader>
                </Empty>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      <Sheet onOpenChange={setDetailOpen} open={detailOpen}>
        <SheetContent className="h-[min(90vh,980px)] p-0" side="center">
          <div className="flex h-full min-h-0 flex-col">
            <SheetHeader className="gap-3 border-b border-border/60 px-6 py-5 pr-14">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline">
                  {detailRecord?.ticker ?? detailRecord?.issuer_cik ?? "Signal"}
                </Badge>
                {detailRecord ? (
                  <Badge variant={describeScore(detailRecord.signal_score).variant}>
                    {describeScore(detailRecord.signal_score).label}
                  </Badge>
                ) : null}
                {detailRecord ? (
                  <StatusBadge label="Health" value={detailRecord.health_status} />
                ) : null}
                {detailRecord ? (
                  <StatusBadge label="Price" value={detailRecord.price_context_status} />
                ) : null}
              </div>
              <div className="space-y-1">
                <SheetTitle className="text-2xl">
                  {detailRecord?.issuer_name ?? "Signal details"}
                </SheetTitle>
                <SheetDescription>
                  {detailRecord
                    ? `Window ${formatDateRange(detailRecord.window_start, detailRecord.window_end)}.`
                    : "Open any opportunity to inspect the ranking, evidence, and review setup."}
                </SheetDescription>
              </div>
            </SheetHeader>

            <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
              {detailError ? (
                <Alert variant="destructive">
                  <CircleAlertIcon />
                  <AlertTitle>Detail request error</AlertTitle>
                  <AlertDescription>{detailError}</AlertDescription>
                </Alert>
              ) : loadingDetail && !selectedSignal ? (
                <DetailSkeleton />
              ) : detailRecord ? (
                <Tabs defaultValue="overview" className="min-h-0 gap-4">
                  <TabsList variant="line" className="w-full justify-start gap-3 border-b border-border/60 pb-1">
                    <TabsTrigger value="overview">Overview</TabsTrigger>
                    <TabsTrigger value="breakdown">Breakdown</TabsTrigger>
                    <TabsTrigger value="evidence">Evidence</TabsTrigger>
                  </TabsList>

                  <TabsContent value="overview" className="space-y-4 pt-2">
                    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_380px]">
                      <Card className="border border-border/70 bg-card/70">
                        <CardHeader>
                          <CardDescription>Why this is on the board</CardDescription>
                          <CardTitle>Overview</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-4">
                          <div className="grid gap-3 sm:grid-cols-4">
                            <MetricBlock label="Signal score" value={formatScore(detailRecord.signal_score)} />
                            <MetricBlock label="Buy value" value={formatCompactUsd(detailRecord.total_purchase_usd)} />
                            <MetricBlock label="Unique buyers" value={formatCount(detailRecord.unique_buyers)} />
                            <MetricBlock label="Latest trade" value={formatShortDate(detailRecord.latest_transaction_date ?? detailRecord.window_end)} />
                          </div>
                          <p className="text-sm leading-7 text-muted-foreground">{detailRecord.explanation}</p>
                        </CardContent>
                      </Card>

                      <Card className="border border-border/70 bg-card/70">
                        <CardHeader>
                          <CardDescription>Cluster facts</CardDescription>
                          <CardTitle>Cluster</CardTitle>
                        </CardHeader>
                        <CardContent>
                          <DefinitionGrid
                            columns={2}
                            entries={[
                              { label: "Window", value: formatDateRange(detailRecord.window_start, detailRecord.window_end) },
                              { label: "Average buy", value: formatUsd(detailRecord.average_purchase_usd) },
                              { label: "Qualifying buys", value: formatCount(detailRecord.transaction_count) },
                              { label: "First-time buyers", value: formatCount(detailRecord.first_time_buyer_count) },
                              { label: "Ownership scope", value: detailRecord.includes_indirect ? "Direct + indirect" : "Direct only" },
                              { label: "Amendments", value: detailRecord.includes_amendment ? "Included" : "Excluded" },
                            ]}
                          />
                        </CardContent>
                      </Card>
                    </div>

                    <div className="grid gap-4 xl:grid-cols-2">
                      <Card className="border border-border/70 bg-card/70">
                        <CardHeader>
                          <CardDescription>Issuer snapshot</CardDescription>
                          <CardTitle>
                            {selectedIssuer?.ticker ?? selectedIssuer?.cik ?? detailRecord.issuer_cik}
                          </CardTitle>
                        </CardHeader>
                        <CardContent>
                          <DefinitionGrid
                            columns={2}
                            entries={[
                              { label: "CIK", value: selectedIssuer?.cik ?? detailRecord.issuer_cik },
                              { label: "Exchange", value: selectedIssuer?.exchange ?? "-" },
                              { label: "Market cap", value: formatCompactUsd(selectedIssuer?.market_cap ?? null) },
                              { label: "Latest price", value: formatPrice(selectedIssuer?.latest_price ?? null) },
                              { label: "State", value: selectedIssuer?.state_of_incorp ?? "-" },
                              { label: "SIC", value: selectedIssuer?.sic ?? "-" },
                            ]}
                          />
                        </CardContent>
                      </Card>

                      <Card className="border border-border/70 bg-card/70">
                        <CardHeader>
                          <CardDescription>Review setup</CardDescription>
                          <CardTitle>
                            {detailRecord.trade_setup ? "Setup" : "No stored setup"}
                          </CardTitle>
                        </CardHeader>
                        <CardContent>
                          {detailRecord.trade_setup ? (
                            <DefinitionGrid columns={2} entries={detailEntries(detailRecord.trade_setup)} />
                          ) : (
                            <p className="text-sm leading-6 text-muted-foreground">
                              No deterministic review setup was stored for this opportunity.
                            </p>
                          )}
                        </CardContent>
                      </Card>
                    </div>

                    <Card className="border border-border/70 bg-card/70">
                      <CardHeader>
                        <div className="flex items-center gap-2">
                          <BrainCircuitIcon className="size-4 text-muted-foreground" />
                          <CardTitle>AI summary</CardTitle>
                        </div>
                        <CardDescription>
                          Readable summary layered on top of stored facts.
                        </CardDescription>
                      </CardHeader>
                      <CardContent className="space-y-4">
                        {detailRecord.ai_summary ? (
                          <>
                            <p className="text-sm leading-7 text-muted-foreground">
                              {detailRecord.ai_summary.text}
                            </p>
                            {detailRecord.ai_summary.highlights.length > 0 ? (
                              <DefinitionGrid
                                entries={detailRecord.ai_summary.highlights.map((highlight, index) => ({
                                  label: `Highlight ${index + 1}`,
                                  value: highlight,
                                }))}
                              />
                            ) : null}
                            {detailRecord.ai_summary.warnings.length > 0 ? (
                              <DefinitionGrid
                                entries={detailRecord.ai_summary.warnings.map((warning, index) => ({
                                  label: `Warning ${index + 1}`,
                                  value: warning,
                                }))}
                              />
                            ) : null}
                          </>
                        ) : (
                          <p className="text-sm leading-6 text-muted-foreground">
                            {summaryStatusMessage(detailRecord.summary_status)}
                          </p>
                        )}
                      </CardContent>
                    </Card>
                  </TabsContent>

                  <TabsContent value="breakdown" className="space-y-4 pt-2">
                    <div className="grid gap-4 xl:grid-cols-2">
                      {Object.entries(detailRecord.component_breakdown).map(([componentKey, component]) => (
                        <Card className="border border-border/70 bg-card/70" key={componentKey}>
                          <CardHeader>
                            <div className="flex flex-wrap items-center justify-between gap-3">
                              <div>
                                <CardDescription>Score component</CardDescription>
                                <CardTitle>{formatLabel(componentKey)}</CardTitle>
                              </div>
                              <div className="text-right">
                                <div className="text-3xl font-semibold tabular-nums">
                                  {component.reweighted_score !== null
                                    ? component.reweighted_score.toFixed(1)
                                    : component.raw_score !== null
                                      ? component.raw_score.toFixed(1)
                                      : "-"}
                                </div>
                                <Badge variant={statusVariant(component.status)}>
                                  {formatLabel(component.status)}
                                </Badge>
                              </div>
                            </div>
                          </CardHeader>
                          <CardContent className="space-y-4">
                            <DefinitionGrid
                              entries={[
                                {
                                  label: "Raw score",
                                  value:
                                    component.raw_score === null
                                      ? "-"
                                      : component.raw_score.toFixed(2),
                                },
                                {
                                  label: "Reweighted score",
                                  value:
                                    component.reweighted_score === null
                                      ? "-"
                                      : component.reweighted_score.toFixed(2),
                                },
                                {
                                  label: "Max score",
                                  value: component.max_score.toFixed(0),
                                },
                              ]}
                            />
                            <DefinitionGrid entries={detailEntries(component.details)} />
                          </CardContent>
                        </Card>
                      ))}
                    </div>
                  </TabsContent>

                  <TabsContent value="evidence" className="space-y-4 pt-2">
                    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_360px]">
                      <Card className="border border-border/70 bg-card/70">
                        <CardHeader>
                          <CardDescription>Qualifying transactions</CardDescription>
                          <CardTitle>Raw filing evidence</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-3">
                          {detailRecord.qualifying_transactions.length === 0 ? (
                            <p className="text-sm text-muted-foreground">No transactions stored.</p>
                          ) : (
                            detailRecord.qualifying_transactions.map((transaction) => (
                              <Card
                                key={transaction.transaction_id}
                                size="sm"
                                className="border border-border/70 bg-background/60 shadow-none"
                              >
                                <CardHeader className="gap-3">
                                  <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                                    <div className="space-y-1">
                                      <CardTitle className="text-base">
                                        {transaction.insider_name}
                                      </CardTitle>
                                      <CardDescription>
                                        {transaction.insider_role ?? "Role unavailable"}
                                      </CardDescription>
                                    </div>
                                    <div className="flex flex-wrap gap-2">
                                      <a
                                        className={cn(buttonVariants({ size: "sm", variant: "outline" }))}
                                        href={transaction.filing_url}
                                        rel="noreferrer"
                                        target="_blank"
                                      >
                                        Open filing
                                        <ExternalLinkIcon data-icon="inline-end" />
                                      </a>
                                      <a
                                        className={cn(buttonVariants({ size: "sm", variant: "outline" }))}
                                        href={transaction.xml_url}
                                        rel="noreferrer"
                                        target="_blank"
                                      >
                                        Open XML
                                        <ExternalLinkIcon data-icon="inline-end" />
                                      </a>
                                    </div>
                                  </div>
                                </CardHeader>
                                <CardContent>
                                  <DefinitionGrid
                                    columns={3}
                                    entries={[
                                      {
                                        label: "Transaction date",
                                        value: formatShortDate(transaction.transaction_date),
                                      },
                                      {
                                        label: "Security",
                                        value: transaction.security_title ?? "-",
                                      },
                                      {
                                        label: "Ownership",
                                        value:
                                          transaction.ownership_type === "D"
                                            ? "Direct"
                                            : transaction.ownership_type === "I"
                                              ? "Indirect"
                                              : transaction.ownership_type ?? "-",
                                      },
                                      { label: "Shares", value: formatNumericText(transaction.shares) },
                                      {
                                        label: "Price per share",
                                        value: formatPrice(transaction.price_per_share),
                                      },
                                      { label: "Value", value: formatUsd(transaction.value_usd) },
                                    ]}
                                  />
                                </CardContent>
                              </Card>
                            ))
                          )}
                        </CardContent>
                      </Card>

                      <Card className="border border-border/70 bg-card/70">
                        <CardHeader>
                          <CardDescription>Alert history</CardDescription>
                          <CardTitle>Outbound trace</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-3">
                          {detailRecord.alerts.length === 0 ? (
                            <p className="text-sm text-muted-foreground">No alerts were sent for this signal.</p>
                          ) : (
                            detailRecord.alerts.map((alert) => (
                              <Card
                                key={alert.id}
                                size="sm"
                                className="border border-border/70 bg-background/60 shadow-none"
                              >
                                <CardHeader className="gap-2">
                                  <div className="flex flex-wrap items-center gap-2">
                                    <Badge variant={statusVariant(alert.status)}>
                                      {formatLabel(alert.status)}
                                    </Badge>
                                    <Badge variant="outline">{formatLabel(alert.channel)}</Badge>
                                    <Badge variant="outline">{formatLabel(alert.event_type)}</Badge>
                                  </div>
                                  <CardDescription>{formatDateTime(alert.sent_at)}</CardDescription>
                                </CardHeader>
                                <CardContent className="space-y-3">
                                  <p className="text-sm leading-6 text-muted-foreground">{alert.reason}</p>
                                  <DefinitionGrid
                                    entries={[
                                      {
                                        label: "Score at send",
                                        value: formatScore(alert.score_at_send),
                                      },
                                      {
                                        label: "Total buy at send",
                                        value: formatUsd(alert.total_purchase_usd_at_send),
                                      },
                                      {
                                        label: "Unique buyers",
                                        value: formatCount(alert.unique_buyers_at_send),
                                      },
                                    ]}
                                  />
                                </CardContent>
                              </Card>
                            ))
                          )}
                        </CardContent>
                      </Card>
                    </div>
                  </TabsContent>
                </Tabs>
              ) : (
                <Empty className="border-border/70 bg-background/60 py-14">
                  <EmptyHeader>
                    <EmptyMedia variant="icon">
                      <ActivityIcon />
                    </EmptyMedia>
                    <EmptyTitle>No detail selected</EmptyTitle>
                    <EmptyDescription>
                      Pick an opportunity from the board to inspect the evidence.
                    </EmptyDescription>
                  </EmptyHeader>
                </Empty>
              )}
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}

function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const isDark = resolvedTheme !== "light";

  return (
    <Button
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
      onClick={() => setTheme(isDark ? "light" : "dark")}
      size="icon"
      type="button"
      variant="outline"
    >
      {isDark ? <SunMediumIcon /> : <MoonStarIcon />}
    </Button>
  );
}

function MetricBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-border/70 bg-background/60 p-3">
      <div className="text-[0.72rem] uppercase tracking-[0.18em] text-muted-foreground">
        {label}
      </div>
      <div className="mt-2 text-lg font-semibold tracking-tight">{value}</div>
    </div>
  );
}

function StatusBadge({ label, value }: { label: string; value: string }) {
  return <Badge variant={statusVariant(value)}>{label}: {formatLabel(value)}</Badge>;
}

function DefinitionGrid({
  entries,
  columns = 2,
}: {
  entries: DetailEntry[];
  columns?: 2 | 3;
}) {
  const gridClass = columns === 3 ? "md:grid-cols-3" : "sm:grid-cols-2";

  if (entries.length === 0) {
    return <div className="text-sm text-muted-foreground">No structured details stored.</div>;
  }

  return (
    <dl className={`grid gap-3 ${gridClass}`}>
      {entries.map((entry) => (
        <div
          key={`${entry.label}-${entry.value}`}
          className="rounded-2xl border border-border/70 bg-background/60 p-3"
        >
          <dt className="text-[0.72rem] uppercase tracking-[0.18em] text-muted-foreground">
            {entry.label}
          </dt>
          <dd className="mt-2 text-sm leading-6 text-foreground">{entry.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function SignalBoardSkeleton() {
  return (
    <div className="grid gap-3">
      <div className="hidden xl:grid xl:grid-cols-[110px_minmax(0,1.5fr)_210px_220px_160px_140px] xl:gap-3">
        {Array.from({ length: 12 }).map((_, index) => (
          <Skeleton className="h-24 w-full rounded-2xl" key={`desktop-${index}`} />
        ))}
      </div>
      <div className="grid gap-3 xl:hidden">
        {Array.from({ length: 3 }).map((_, index) => (
          <Skeleton className="h-52 w-full rounded-2xl" key={`mobile-${index}`} />
        ))}
      </div>
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      {Array.from({ length: 4 }).map((_, index) => (
        <Skeleton className="h-56 w-full rounded-2xl" key={index} />
      ))}
      <Skeleton className="h-72 w-full rounded-2xl xl:col-span-2" />
    </div>
  );
}

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(`Request failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

function buildSignalQuery(filters: Filters): string {
  const params = new URLSearchParams();
  if (filters.ticker.trim()) {
    params.set("ticker", filters.ticker.trim().toUpperCase());
  }
  if (filters.cik.trim()) {
    params.set("cik", filters.cik.trim());
  }
  if (filters.minimumScore.trim()) {
    params.set("minimum_score", filters.minimumScore.trim());
  }
  if (filters.minimumUniqueBuyers.trim()) {
    params.set("minimum_unique_buyers", filters.minimumUniqueBuyers.trim());
  }
  if (filters.marketCapMax.trim()) {
    params.set("market_cap_max", filters.marketCapMax.trim());
  }
  if (filters.dateFrom) {
    params.set("date_from", filters.dateFrom);
  }
  if (filters.dateTo) {
    params.set("date_to", filters.dateTo);
  }
  params.set("include_unknown_health", String(filters.includeUnknownHealth));
  params.set("include_indirect", String(filters.includeIndirect));
  params.set("include_amendments", String(filters.includeAmendments));
  const query = params.toString();
  return query ? `?${query}` : "";
}

function formatUsd(value: string | null): string {
  if (!value) {
    return "-";
  }
  return `$${Number(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function formatPrice(value: string | null): string {
  if (!value) {
    return "-";
  }
  return `$${Number(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  })}`;
}

function formatCompactUsd(value: number | string | null): string {
  if (value === null) {
    return "-";
  }
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return "-";
  }
  return `$${new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(numeric)}`;
}

function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "-";
  }
  return value.toLocaleString();
}

function formatScore(value: string): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return value;
  }
  return numeric.toFixed(1);
}

function formatDateValue(value: string): Date {
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return new Date(`${value}T00:00:00`);
  }
  return new Date(value);
}

function formatShortDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
  }).format(formatDateValue(value));
}

function formatDateTime(value: string | null): string {
  if (!value) {
    return "Pending";
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatDateRange(start: string, end: string): string {
  return `${formatShortDate(start)} to ${formatShortDate(end)}`;
}

function formatLabel(value: string): string {
  return value
    .split("_")
    .join(" ")
    .replace(/\b\w/g, (match: string) => match.toUpperCase());
}

function formatNumericText(value: string | null): string {
  if (!value) {
    return "-";
  }
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return value;
  }
  return numeric.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 4,
  });
}

function detailEntries(details: Record<string, ComponentDetailValue>): DetailEntry[] {
  return Object.entries(details)
    .map(([key, value]) => ({
      label: formatLabel(key),
      value: formatDetailValue(key, value),
    }))
    .filter((detail) => detail.value.length > 0);
}

function formatDetailValue(key: string, value: ComponentDetailValue): string {
  if (value === null) {
    return "-";
  }
  if (typeof value === "string") {
    return value.includes("_") ? formatLabel(value) : value;
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  if (typeof value === "number") {
    if (key.includes("usd")) {
      return formatUsd(String(value));
    }
    if (key.includes("price") || key.includes("entry") || key.includes("stop")) {
      return formatPrice(String(value));
    }
    if (key.includes("pct") || key.includes("drawdown") || key.includes("risk")) {
      return `${value.toLocaleString(undefined, {
        minimumFractionDigits: 0,
        maximumFractionDigits: 2,
      })}%`;
    }
    return value.toLocaleString(undefined, {
      minimumFractionDigits: 0,
      maximumFractionDigits: 2,
    });
  }
  if (Array.isArray(value)) {
    if (value.length === 0) {
      return "None";
    }
    if (value.every((item) => typeof item === "string")) {
      return value
        .map((item) => (item.includes("_") ? formatLabel(item) : item))
        .join(", ");
    }
    const objectSummaries = value
      .map((item) => summarizeObjectValue(item))
      .filter((item): item is string => item !== null);
    return objectSummaries.length > 0 ? objectSummaries.join(" | ") : `${value.length} item(s)`;
  }
  return summarizeObjectValue(value) ?? `${Object.keys(value).length} field(s)`;
}

function summarizeObjectValue(value: ComponentDetailValue): string | null {
  if (!value || Array.isArray(value) || typeof value !== "object") {
    return null;
  }
  const labeledValue = value.label;
  const formValue = value.form;
  const filedAtValue = value.filed_at;
  const pointsValue = value.points;
  if (
    typeof labeledValue === "string" &&
    typeof formValue === "string" &&
    typeof filedAtValue === "string"
  ) {
    const pointsSuffix =
      typeof pointsValue === "number"
        ? ` (${pointsValue.toLocaleString(undefined, {
            minimumFractionDigits: 0,
            maximumFractionDigits: 2,
          })} pts)`
        : "";
    return `${formatLabel(labeledValue)}: ${formValue} filed ${filedAtValue}${pointsSuffix}`;
  }
  return null;
}

function summaryStatusMessage(status: string): string {
  if (status === "disabled") {
    return "AI summary unavailable because OPENAI_API_KEY is not configured.";
  }
  if (status === "failed") {
    return "AI summary generation failed. Review the raw SEC evidence directly.";
  }
  return "AI summary has not been generated yet.";
}

function describeScore(score: string): { label: string; variant: BadgeVariant } {
  const numeric = Number(score);
  if (!Number.isFinite(numeric)) {
    return { label: "Unscored", variant: "outline" };
  }
  if (numeric >= 85) {
    return { label: "Prime", variant: "default" };
  }
  if (numeric >= 70) {
    return { label: "High Conviction", variant: "secondary" };
  }
  if (numeric >= 55) {
    return { label: "Watchlist", variant: "outline" };
  }
  return { label: "Developing", variant: "outline" };
}

function statusVariant(value: string): BadgeVariant {
  const normalized = value.toLowerCase();
  if (['ok', 'available', 'generated', 'healthy', 'running', 'sent'].includes(normalized)) {
    return "secondary";
  }
  if (['failed', 'distressed', 'error', 'destructive', 'rejected'].includes(normalized)) {
    return "destructive";
  }
  return "outline";
}


