export interface OnboardingState {
  has_vault: boolean;
  vault_quality: number;
  tags: { tag: string; label: string; level: number }[];
  cohort_size: number;
  complete: boolean;
}

export interface Me {
  id: string;
  email: string;
  display_name: string;
  headline: string;
  venue: string;
  venue_label: string;
  affiliation: string;
  affiliation_domain: string;
  role: string;
  onboarding: OnboardingState;
}

export interface GeneralizationOutcome {
  ok: boolean;
  reason: string;
  cohort_size: number;
  cohort_key: string;
  k_target: number;
  tags: { tag: string; label: string; level: number }[];
  promoted: string[];
  suppressed: string[];
  confidence: number;
  raw_fingerprint: string[];
  pool_size?: number;
}

export interface VaultView {
  quality: number;
  intents: string[] | number;
  intent_labels: string[];
  buckets: string[];
  deal_breakers: string[] | number;
  avoid_domains: string[] | number;
  sandbox_consent: boolean;
  source: string;
  extracted_at: string;
  bottleneck: string;
  contrarian: string;
}

export interface Config {
  app: string;
  version: string;
  engine: string;
  venues: { key: string; label: string; city: string; tz: string; dates: string; primary: boolean }[];
  privacy: {
    k_anonymity: number;
    daily_card_budget: number;
    daily_card_budget_max: number;
    fit_threshold: number;
    transcript_policy: string;
    beacon_window_minutes: number;
  };
  intents: { key: string; label: string; bucket: string }[];
  deal_breakers: { key: string; label: string }[];
  umbrellas: { key: string; label: string }[];
  demo_accounts: { email: string; role: string }[];
  demo_password?: string;
}

export interface MatchPeer {
  id?: string;
  alias?: string;
  display_name?: string;
  headline?: string;
  affiliation?: string;
  venue?: string;
  tags?: string[];
  github?: string;
  papers?: string[];
  icebreaker?: string;
  meeting?: { slot: string; zone: string; confirmed_at: string };
}

export interface Card {
  id: string;
  kind: "match" | "group" | "proximity" | "gatekeeper";
  headline: string;
  state: "pending" | "accepted" | "declined" | "expired";
  cohort_size: number;
  payload: Record<string, any>;
  peer?: MatchPeer;
  unlock?: { state: string; accepted_by_me: boolean; min_score: number; expires_at: string | null };
  group?: GroupDetail;
  proximity?: Record<string, any>;
  gatekeeper?: Record<string, any>;
  created_at: string;
}

export interface GroupDetail {
  id: string;
  topic: string;
  size: number;
  status: string;
  slot: { id: string; label: string; zone: string };
  agenda: {
    topic_label: string;
    roles: string[];
    questions: string[];
    timebox: { label: string; minutes: number }[];
    rules: string[];
  };
  members: { id: string; display_name: string; headline: string; affiliation: string }[];
  member_count: number;
}

export interface Quota {
  cap: number;
  used: number;
  remaining: number;
  day: string;
}

export interface RadarPeer {
  alias: string;
  tags: string[];
  shared_tags: string[];
  session_ref: string;
  ref: string;
}

export interface Radar {
  active: boolean;
  reason?: string;
  session_ref?: string;
  bucket?: number;
  expires_at?: string;
  peers: RadarPeer[];
  peer_count?: number;
}

export interface RunStats {
  candidates: number;
  pairs: number;
  sessions: number;
  gated: number;
  burned: Record<string, number>;
  cards: number;
  card_recipients: number;
  cost_usd: number;
}

export interface SandboxSessionRow {
  id: string;
  status: string;
  burn_reason: string;
  redactions: number;
  risk_flags: string[];
  rounds: number;
  digest: string;
  engine: string;
  cost_usd: number;
  created_at: string;
  transcript?: string;
}

export interface GatekeeperPolicy {
  enabled: boolean;
  rules: Record<string, any>;
  pitch_threshold: number;
  fairness: { stranger_slots_per_day: number; used_today: number; remaining: number; floor_enforced: boolean };
}

export interface GatekeeperRequest {
  id: string;
  tier: number;
  score: number;
  verdict: string;
  reason_code: string;
  note: string;
  credentials: string[];
  pitch: Record<string, any>;
  created_at: string;
  requester_id?: string | null;
  target_id: string;
}
