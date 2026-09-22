export type Summary = {
  companies: number;
  verified_companies: number;
  active_sources: number;
  active_postings: number;
  pending_candidates: number;
  verified_active_candidates: number;
  new_matches: number;
  profiles: number;
};

export type Profile = {
  label: string;
  target_roles: string[];
  next_search_at: string | null;
  last_search_outcome: string | null;
};

export type JobItem = {
  candidate_id: string;
  provider: string;
  company_name: string;
  title: string;
  listing_url: string;
  location: string | null;
  work_mode: string;
  employment_type: string;
  published_at: string | null;
  activity_state: string;
  activity_code: string;
  score: number;
  recommendation: string;
  matched_terms: string[];
  risk_flags: string[];
};

export type JobPage = {
  profile: string;
  activity_filter: "active_only" | "active_and_unverified";
  count: number;
  items: JobItem[];
};

export type EvidenceValue = string | number | boolean | null;

export type JobDetail = {
  candidate_id: string;
  provider: string;
  company_name: string;
  company_identity_required: boolean;
  title: string;
  listing_url: string;
  snippet: string | null;
  location: string | null;
  work_mode: string;
  employment_type: string;
  published_at: string | null;
  activity_state: string;
  activity_code: string;
  activity_checked_at: string | null;
  status: string;
  evidence: Record<string, EvidenceValue>[];
  first_seen_at: string;
  last_seen_at: string;
};

export type SearchRunCandidate = {
  candidate_id: string;
  provider: string;
  title: string;
  company_name: string;
  listing_url: string;
  location: string | null;
  score: number;
  recommendation: string;
  status: string;
  activity_state: string;
  activity_code: string;
  disposition: string;
};

export type SearchRun = {
  run_id: string;
  profile: string;
  status: "queued" | "running" | "succeeded" | "failed";
  result: {
    query_count?: number;
    raw_result_count?: number;
    excluded_result_count?: number;
    matched_candidate_count?: number;
    candidate_count?: number;
    new_candidates?: number;
    refreshed_candidates?: number;
    suppressed_candidates?: number;
    reconciled_candidate_count?: number;
    exclusion_counts?: Record<string, number>;
    activity_checked_count?: number;
    activity_changed_count?: number;
    activity_counts?: {
      active?: number;
      closed?: number;
      unknown?: number;
    };
    matched_candidates?: SearchRunCandidate[];
  };
  error_code: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

type ErrorPayload = {
  detail?: { error_code?: string } | string;
};

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string) {
    super(code);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

async function request<T>(
  token: string,
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: {
      Accept: "application/json",
      "X-Operator-Token": token,
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
    credentials: "omit",
    referrerPolicy: "no-referrer",
  });

  if (!response.ok) {
    let code = "request_failed";
    try {
      const payload = (await response.json()) as ErrorPayload;
      if (
        typeof payload.detail === "object" &&
        payload.detail !== null &&
        typeof payload.detail.error_code === "string"
      ) {
        code = payload.detail.error_code;
      }
    } catch {
      code = "invalid_error_response";
    }
    throw new ApiError(response.status, code);
  }
  return (await response.json()) as T;
}

export function getSummary(token: string): Promise<Summary> {
  return request(token, "/operator/summary");
}

export function getProfiles(token: string): Promise<Profile[]> {
  return request(token, "/operator/profiles");
}

export function getJobs(
  token: string,
  profile: string,
  includeUnverified: boolean,
): Promise<JobPage> {
  const query = new URLSearchParams({
    profile,
    minimum_score: "20",
    limit: "100",
    include_unverified: String(includeUnverified),
  });
  return request(token, `/operator/jobs?${query.toString()}`);
}

export function startProfileSearch(
  token: string,
  profile: string,
): Promise<SearchRun> {
  return request(token, "/operator/search-runs", {
    method: "POST",
    body: JSON.stringify({ profile, confirmed_external_search: true }),
  });
}

export function getLatestProfileSearch(
  token: string,
  profile: string,
): Promise<SearchRun | null> {
  const query = new URLSearchParams({ profile });
  return request(token, `/operator/search-runs/latest?${query.toString()}`);
}

export function getJobDetail(token: string, id: string): Promise<JobDetail> {
  return request(token, `/operator/jobs/${encodeURIComponent(id)}`);
}

export function approveJob(
  token: string,
  id: string,
  companyName: string | null,
): Promise<{ status: string }> {
  return request(token, `/operator/jobs/${encodeURIComponent(id)}/approve`, {
    method: "POST",
    body: JSON.stringify({
      confirmed_active: true,
      company_name: companyName,
    }),
  });
}

export function rejectJob(
  token: string,
  id: string,
): Promise<{ status: string }> {
  return request(token, `/operator/jobs/${encodeURIComponent(id)}/reject`, {
    method: "POST",
    body: JSON.stringify({ confirmed_rejection: true }),
  });
}
