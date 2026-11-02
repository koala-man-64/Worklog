export type QuotaTokenEfficiency = {
  tokens_per_point: number;
  tokens: number;
  percentage_points: number;
  start_local: string;
  end_local: string;
};

export type QuotaChartPoint = {
  x: number;
  y: number;
  t: string;
  v: string;
  k?: string;
};

export type QuotaTokenLegendItem = {
  slot: number;
  slot_class: string;
  model: string;
  tokens: number;
};

export type QuotaCard = {
  id: string;
  group: string;
  group_title: string;
  provider: string;
  window: string;
  window_min: number;
  title: string;
  status: string;
  raw_status: string;
  stale: boolean;
  badge: string;
  used: number;
  remaining_pct: number;
  pace: number;
  delta: number;
  rate_per_hour: number;
  remaining_min: number;
  start: string | null;
  resets_at: string | null;
  resets_local: string | null;
  sample_ts: string | null;
  sample_local: string | null;
  age_min: number | null;
  source: string;
  samples_count: number;
  left_value: string;
  left_label: string;
  proj_value: string;
  proj_label: string;
  issue: string | null;
  reset_credits_count: number;
  reset_credits_known: boolean;
  credit_earliest_expiry: string | null;
  next_reset_local: string | null;
  runway_with_resets: string | null;
  token_efficiency: QuotaTokenEfficiency | null;
  domain_start: string | null;
  domain_end: string | null;
  chart_heading: string | null;
  chart_svg: string | null;
  chart_points: QuotaChartPoint[];
  token_legend: QuotaTokenLegendItem[];
};

export type QuotaCapacityWindow = {
  window: string;
  window_min: number;
  used_pct: number | null;
  remaining_pct: number | null;
  usable_pct: number | null;
  resets_at: string | null;
  observed_at: string | null;
  received_at: string | null;
  valid_until: string | null;
  freshness_ttl_s: number | null;
  source_age_s: number | null;
  allowance_state: string;
  freshness: string;
  source: string;
  reset_provenance: string;
  observation_time_provenance: string;
  whole_window_rate_pph: number | null;
  recent_rate_pph: number | null;
  conservative_rate_pph: number | null;
  sustainable_rate_pph: number | null;
  runway_minutes: number | null;
  exhaust_at: string | null;
};

export type QuotaCapacityLimit = {
  id: string;
  label: string;
  pool_id: string | null;
  account_scope: string | null;
  account_scope_confidence: string;
  models: string[];
  mapping_confidence: string;
  constraining_window: string | null;
  window: QuotaCapacityWindow;
  availability_reason: string | null;
  display_only: boolean;
  reset_credits: { id?: string; expires_at?: string | null }[];
  reset_credits_known: boolean;
};

export type QuotaProviderGroup = {
  provider: string;
  label: string;
  limits: QuotaCapacityLimit[];
};

export type QuotaCollectorHealth = {
  state: string;
  last_error?: string | null;
  updated_at?: string | null;
  last_latency_ms?: number;
  max_latency_ms?: number;
};

export type QuotaCapacity = {
  instance_id?: string;
  revision?: number;
  generated_at?: string;
  policy?: { reserve_pct: number };
  provider_groups?: QuotaProviderGroup[];
  collector_health?: Record<string, QuotaCollectorHealth>;
};

export type QuotaTotals = {
  prompts: number;
  requests: number;
  input: number;
  cache_read: number;
  cache_write: number;
  output: number;
  reasoning: number;
  total: number;
  inferred_requests: number;
  inferred_input: number;
};

export type QuotaRecentRequest = {
  id: string;
  ts: string;
  ts_local: string;
  provider: string;
  tool: string;
  session_id: string;
  thread: string;
  model: string;
  effort: string;
  input_tokens: number | null;
  cache_read_tokens: number | null;
  cache_write_tokens: number | null;
  output_tokens: number | null;
  reasoning_tokens: number | null;
  total_tokens: number | null;
  input_tokens_inferred: number | null;
  inferred: boolean;
};

export type QuotaUsagePayload = {
  generated_at: string;
  days: number;
  summary: Record<string, Record<"today" | "7d" | "30d", QuotaTotals>>;
  by_model_effort: ({ key: string } & QuotaTotals)[];
  recent: QuotaRecentRequest[];
  note: string;
};

export type QuotaEfficiencyRow = {
  key: string;
  anchor: string | null;
  session_id?: string | null;
  provider: string;
  requests: number;
  uncached_input: number | null;
  cached_input: number | null;
  output: number | null;
  reasoning: number | null;
  median_tokens_per_request: number | null;
  reasoning_share_of_output_pct: number | null;
  exact_requests: number;
  inferred_requests: number;
  exact: boolean;
};

export type QuotaDailyModelSeries = {
  model: string;
  label: string;
  color: string;
  slot: number;
  tokens: number[];
  total_tokens: number;
  recorded_requests: number;
  excluded_requests: number;
};

export type QuotaDailyProviderGroup = {
  provider: string;
  label: string;
  models: QuotaDailyModelSeries[];
  daily_tokens: number[];
  total_tokens: number;
  recorded_requests: number;
  excluded_requests: number;
};

export type QuotaEfficiencyPayload = {
  generated_at: string;
  days: number;
  by_session: QuotaEfficiencyRow[];
  by_model_effort: QuotaEfficiencyRow[];
  daily_models: {
    dates: string[];
    calendar: string;
    through: string;
    providers: QuotaDailyProviderGroup[];
  };
  daily_models_html?: string;
  insights: {
    label: string;
    value: number;
    session_anchor: string;
    session_key?: string;
    session_id?: string | null;
  }[];
  note: string;
};

export type QuotaPlanModelOption = {
  id: string;
  label: string;
  speeds: string[];
  rates_version: string;
};

export type QuotaCreditPlanReport = {
  amount: string;
  starts_at: string;
  ends_at: string;
  model: string;
  speed: string;
  unit: string;
  status: string;
  confirmed_spent: string | null;
  remaining: string;
  required_rate_per_hour: string | null;
  original_rate_per_hour: string;
  progress_known: boolean;
  note: string;
};

export type QuotaCreditObservation = {
  provider: string;
  account_scope: string;
  observed_at: string;
  source: string;
  unit: string;
  balance: string | null;
  cumulative_spent: string | null;
  counter_period: string | null;
  enabled: boolean | null;
  account_verified: boolean;
  freshness?: string;
  note: string;
};

export type QuotaPlansSnapshot = {
  revision: number;
  plans: Record<string, QuotaCreditPlanReport | null>;
  observations: Record<string, QuotaCreditObservation>;
  models?: Record<string, QuotaPlanModelOption[]>;
};

export type QuotaRecentText = {
  status: string;
  note?: string;
  prompt?: string | null;
  response?: string | null;
};

export type QuotaDashboard = {
  generated_at: string;
  sample_count: number;
  default_target_local: string;
  target_date: string | null;
  warnings: string[];
  capacity: QuotaCapacity;
  cards: QuotaCard[];
  usage: QuotaUsagePayload | null;
  efficiency: QuotaEfficiencyPayload | null;
  plans: QuotaPlansSnapshot;
};
