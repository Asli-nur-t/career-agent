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
  secondary_roles: string[];
  tertiary_roles: string[];
  skills: string[];
  professional_experience_years: number;
  internship_months: number;
  next_search_at: string | null;
  last_search_outcome: string | null;
};

export type CompanyProfileStatus =
  | "all"
  | "unprofiled"
  | "candidate_found"
  | "verified"
  | "needs_review"
  | "not_found";

export type CompanyItem = {
  company_id: string;
  name: string;
  sector: string | null;
  needs_review: boolean;
  teknoparks: string[];
  profile_status: Exclude<CompanyProfileStatus, "all">;
  brand_name: string | null;
  confidence: "high" | "medium" | "low" | null;
  official_website_url: string | null;
  careers_url: string | null;
  official_linkedin_url: string | null;
  last_verified_at: string | null;
  updated_at: string | null;
};

export type CompanyPage = {
  total: number;
  limit: number;
  offset: number;
  items: CompanyItem[];
};

export type CompanyDetail = CompanyItem & {
  evidence: Record<string, EvidenceValue>[];
  search_provider: string | null;
  evaluator_model: string | null;
  last_searched_at: string | null;
  career_sources_last_checked_at: string | null;
  career_sources_next_check_at: string | null;
  career_sources_last_outcome: string | null;
  career_sources_last_error_code: string | null;
  career_sources_candidate_count: number;
  job_boards_last_checked_at: string | null;
  job_boards_next_check_at: string | null;
  job_boards_last_outcome: string | null;
  job_boards_last_error_code: string | null;
  job_boards_candidate_count: number;
  reviewable: boolean;
};

export type CompanyRejectionReason =
  | "wrong_company"
  | "unsafe_or_invalid_url"
  | "insufficient_evidence";

export type CompanyDiscoveryResult = {
  company_id: string;
  company_name: string;
  status: "succeeded" | "failed";
  profile_status: Exclude<CompanyProfileStatus, "all" | "unprofiled"> | null;
  confidence: "high" | "medium" | "low" | null;
  result_count: number;
  attempt_count: number;
  profile_updated: boolean;
  verification_code: string | null;
  error_code: string | null;
};

export type CompanyDiscoveryRun = {
  run_id: string;
  status:
    | "queued"
    | "running"
    | "pause_requested"
    | "paused"
    | "succeeded"
    | "failed";
  scope: "unprofiled";
  query_budget: number;
  query_count: number;
  total_count: number;
  queued_count: number;
  running_count: number;
  succeeded_count: number;
  failed_count: number;
  profile_counts: Record<string, number>;
  error_counts: Record<string, number>;
  error_code: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
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
  operator_viewed_at: string | null;
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
  operator_viewed_at: string | null;
};

export type ApplicationStatus =
  | "to_apply"
  | "applied"
  | "interview"
  | "rejected"
  | "offer"
  | "withdrawn";

export type JobApplication = {
  application_id: string;
  candidate_id: string;
  profile: string;
  status: ApplicationStatus;
  notes: string | null;
  applied_at: string | null;
  created_at: string;
  updated_at: string;
  provider: string;
  title: string;
  company_name: string;
  listing_url: string;
  location: string | null;
  work_mode: string;
  activity_state: string;
};

export type JobApplicationPage = {
  profile: string;
  total: number;
  limit: number;
  offset: number;
  items: JobApplication[];
};

export type BrowserStaleCleanupResponse = {
  matched_count: number;
  quarantined_count: number;
  applied: boolean;
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
  operator_viewed_at: string | null;
};

export type SearchMode = "quick" | "deep";
export type SearchWorkMode = "remote" | "hybrid" | "onsite";
export type SearchSource =
  | "linkedin"
  | "kariyer"
  | "indeed"
  | "glassdoor"
  | "ats"
  | "turkey_tech"
  | "remote_feeds";
export type NativeSearchSource = SearchSource | "turkey_tech" | "remote_feeds";

export type SearchScope = {
  locations: string[];
  workModes: SearchWorkMode[];
  sources: SearchSource[];
  maxAgeDays: number;
};

export type NativeSearchLink = {
  provider: string;
  label: string;
  url: string;
  query_prefilled: boolean;
  location_prefilled: boolean;
  note: string;
};

export type NativeSearchLinksResponse = {
  role: string;
  location: string | null;
  links: NativeSearchLink[];
  unavailable_sources: string[];
};

export type ManualJobImportRequest = {
  listing_url: string;
  title: string;
  company_name: string;
  location: string | null;
  work_mode: "remote" | "hybrid" | "onsite" | "unknown";
  employment_type: "full_time" | "part_time" | "contract" | "internship" | "temporary" | "unknown";
  description_text?: string | null;
  confirmed_visible: true;
};

export type ManualJobImportResponse = {
  candidate_id: string;
  provider: string;
  listing_url: string;
  status: string;
  created: boolean;
  changed: boolean;
};

export type BrowserAgentProvider =
  | "linkedin"
  | "kariyer"
  | "indeed"
  | "glassdoor"
  | "techcareer"
  | "yenibiris"
  | "secretcv"
  | "toptalent"
  | "weworkremotely"
  | "remoteok"
  | "remotive"
  | "jobicy";

export type BrowserSourceDiagnostic = {
  provider: BrowserAgentProvider;
  label: string;
  outcome:
    | "collected"
    | "no_results"
    | "login_required"
    | "rate_limited"
    | "blocked"
    | "source_cooldown"
    | "source_disabled"
    | "failed";
  collected_count: number;
  error_code: string | null;
  agent_used: boolean;
  agent_action_count: number;
  access_reason: string | null;
  access_trace: string[];
};

export type BrowserCollectResponse = {
  provider: BrowserAgentProvider | "multi";
  providers: BrowserAgentProvider[];
  collected_count: number;
  created_count: number;
  updated_count: number;
  skipped_count: number;
  assessment_queued_count: number;
  candidate_ids: string[];
  diagnostics: BrowserSourceDiagnostic[];
};

export type BrowserCollectedJob = {
  candidate_id: string;
  provider: BrowserAgentProvider;
  title: string;
  company_name: string;
  listing_url: string;
  location: string | null;
  work_mode: SearchWorkMode | "unknown";
  status: string;
  collected_at: string;
  search_roles: string[];
  assessment_state: "ready" | "pending" | "unavailable";
  fit_score: number | null;
  fit_stars: number | null;
  fit_recommendation: string | null;
  fit_confidence: "low" | "medium" | "high" | null;
  required_experience_min: number | null;
  experience_gap: number | null;
  matched_requirements: string[];
  missing_requirements: string[];
  preferred_requirements: string[];
  hard_blockers: string[];
  fit_summary: string | null;
};

export type BrowserCollectedPage = {
  total: number;
  limit: number;
  offset: number;
  items: BrowserCollectedJob[];
};

export type BrowserCleanupResponse = {
  repaired_count: number;
  quarantined_count: number;
};

export type JobRoleCatalogItem = {
  name: string;
  aliases: string[];
};

export type JobAssessmentBackfillResponse = {
  queued_count: number;
};

export type BrowserResultDismissResponse = {
  candidate_id: string;
  dismissed: boolean;
};

export type SearchSourceDiagnostic = {
  source: SearchSource;
  outcome: "matched" | "filtered" | "no_results";
  query_count: number;
  fallback_query_count: number;
  raw_result_count: number;
  normalized_result_count: number;
  accepted_count: number;
  exclusion_counts: Record<string, number>;
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
    requested_roles?: string[];
    requested_locations?: string[];
    requested_work_modes?: SearchWorkMode[];
    requested_sources?: SearchSource[];
    source_diagnostics?: SearchSourceDiagnostic[];
    max_listing_age_days?: number;
    search_mode?: SearchMode;
    query_limit?: number;
    result_limit_per_query?: number;
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

export function getJobRoleCatalog(
  token: string,
): Promise<JobRoleCatalogItem[]> {
  return request(token, "/operator/job-roles");
}

export function updateProfileExperience(
  token: string,
  profile: string,
  professionalExperienceYears: number,
  internshipMonths: number,
): Promise<Profile> {
  return request(
    token,
    `/operator/profiles/${encodeURIComponent(profile)}/experience`,
    {
      method: "PATCH",
      body: JSON.stringify({
        professional_experience_years: professionalExperienceYears,
        internship_months: internshipMonths,
      }),
    },
  );
}

export function getCompanies(
  token: string,
  query: string,
  profileStatus: CompanyProfileStatus,
  offset: number,
): Promise<CompanyPage> {
  const params = new URLSearchParams({
    limit: "25",
    offset: String(offset),
    profile_status: profileStatus,
  });
  const cleanedQuery = query.trim();
  if (cleanedQuery) params.set("q", cleanedQuery);
  return request(token, `/operator/companies?${params.toString()}`);
}

export function getCompanyDetail(
  token: string,
  id: string,
): Promise<CompanyDetail> {
  return request(token, `/operator/companies/${encodeURIComponent(id)}`);
}

export function approveCompany(
  token: string,
  id: string,
): Promise<{ status: string }> {
  return request(token, `/operator/companies/${encodeURIComponent(id)}/approve`, {
    method: "POST",
    body: JSON.stringify({ confirmed_identity: true }),
  });
}

export function rejectCompany(
  token: string,
  id: string,
  reason: CompanyRejectionReason,
): Promise<{ status: string }> {
  return request(token, `/operator/companies/${encodeURIComponent(id)}/reject`, {
    method: "POST",
    body: JSON.stringify({ confirmed_rejection: true, reason }),
  });
}

export function discoverCompany(
  token: string,
  id: string,
): Promise<CompanyDiscoveryResult> {
  return request(token, `/operator/companies/${encodeURIComponent(id)}/discover`, {
    method: "POST",
    body: JSON.stringify({ confirmed_external_search: true }),
  });
}

export function getLatestCompanyDiscoveryRun(
  token: string,
): Promise<CompanyDiscoveryRun | null> {
  return request(token, "/operator/company-discovery-runs/latest");
}

export function startCompanyDiscoveryRun(
  token: string,
): Promise<CompanyDiscoveryRun> {
  return request(token, "/operator/company-discovery-runs", {
    method: "POST",
    body: JSON.stringify({
      confirmed_external_search: true,
      query_budget: 2200,
    }),
  });
}

export function pauseCompanyDiscoveryRun(
  token: string,
  runId: string,
): Promise<CompanyDiscoveryRun> {
  return request(
    token,
    `/operator/company-discovery-runs/${encodeURIComponent(runId)}/pause`,
    { method: "POST", body: JSON.stringify({ confirmed: true }) },
  );
}

export function resumeCompanyDiscoveryRun(
  token: string,
  runId: string,
): Promise<CompanyDiscoveryRun> {
  return request(
    token,
    `/operator/company-discovery-runs/${encodeURIComponent(runId)}/resume`,
    { method: "POST", body: JSON.stringify({ confirmed: true }) },
  );
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
  roles: string[],
  searchMode: SearchMode,
  scope: SearchScope,
): Promise<SearchRun> {
  return request(token, "/operator/search-runs", {
    method: "POST",
    body: JSON.stringify({
      profile,
      roles,
      search_mode: searchMode,
      locations: scope.locations,
      work_modes: scope.workModes,
      sources: scope.sources,
      max_age_days: scope.maxAgeDays,
      confirmed_external_search: true,
      force: true,
    }),
  });
}

export function getNativeSearchLinks(
  token: string,
  role: string,
  location: string | null,
  sources: NativeSearchSource[],
): Promise<NativeSearchLinksResponse> {
  return request(token, "/operator/native-search-links", {
    method: "POST",
    body: JSON.stringify({ role, location, sources }),
  });
}

export function importManualJob(
  token: string,
  payload: ManualJobImportRequest,
): Promise<ManualJobImportResponse> {
  return request(token, "/operator/jobs/manual-import", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function collectWithBrowserAgent(
  token: string,
  profile: string,
  role: string,
  location: string | null,
  providers: BrowserAgentProvider[],
  workModes: SearchWorkMode[],
  maxResultsPerProvider = 10,
): Promise<BrowserCollectResponse> {
  return request(token, "/operator/browser-agent/collect", {
    method: "POST",
    body: JSON.stringify({
      providers,
      profile,
      role,
      location,
      work_modes: workModes,
      max_results_per_provider: maxResultsPerProvider,
      confirmed_browser_launch: true,
    }),
  });
}

export function getBrowserCollectedJobs(
  token: string,
  profile: string,
  limit = 100,
  offset = 0,
): Promise<BrowserCollectedPage> {
  const query = new URLSearchParams({
    profile,
    limit: String(limit),
    offset: String(offset),
  });
  return request(token, `/operator/browser-agent/results?${query.toString()}`);
}

export function cleanupBrowserCollectedJobs(
  token: string,
): Promise<BrowserCleanupResponse> {
  return request(token, "/operator/browser-agent/results/cleanup", {
    method: "POST",
  });
}

export function cleanupStaleBrowserCollectedJobs(
  token: string,
  profile: string,
  apply = false,
): Promise<BrowserStaleCleanupResponse> {
  return request(token, "/operator/browser-agent/results/stale-cleanup", {
    method: "POST",
    body: JSON.stringify({
      profile,
      older_than_days: 0,
      limit: 500,
      apply,
      confirmed_cleanup: apply,
    }),
  });
}

export function cleanupRoleMismatchedBrowserJobs(
  token: string,
  profile: string,
  apply = false,
): Promise<BrowserStaleCleanupResponse> {
  return request(token, "/operator/browser-agent/results/role-cleanup", {
    method: "POST",
    body: JSON.stringify({
      profile,
      limit: 500,
      apply,
      confirmed_cleanup: apply,
    }),
  });
}

export function queueJobAssessmentBackfill(
  token: string,
  profile: string,
  limit = 25,
): Promise<JobAssessmentBackfillResponse> {
  return request(token, "/operator/job-assessments/backfill", {
    method: "POST",
    body: JSON.stringify({
      profile,
      limit,
      confirmed_local_processing: true,
    }),
  });
}

export function dismissBrowserCollectedJob(
  token: string,
  candidateId: string,
  profile: string,
): Promise<BrowserResultDismissResponse> {
  return request(
    token,
    `/operator/browser-agent/results/${encodeURIComponent(candidateId)}/dismiss`,
    { method: "POST", body: JSON.stringify({ profile }) },
  );
}

export function restoreBrowserCollectedJob(
  token: string,
  candidateId: string,
  profile: string,
): Promise<BrowserResultDismissResponse> {
  return request(
    token,
    `/operator/browser-agent/results/${encodeURIComponent(candidateId)}/restore`,
    { method: "POST", body: JSON.stringify({ profile }) },
  );
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

export function markJobViewed(
  token: string,
  id: string,
): Promise<{ candidate_id: string; operator_viewed_at: string }> {
  return request(token, `/operator/jobs/${encodeURIComponent(id)}/viewed`, {
    method: "POST",
  });
}

export function getApplications(
  token: string,
  profile: string,
  status?: ApplicationStatus,
): Promise<JobApplicationPage> {
  const query = new URLSearchParams({
    profile,
    limit: "200",
    offset: "0",
  });
  if (status) query.set("application_status", status);
  return request(token, `/operator/applications?${query.toString()}`);
}

export function updateApplication(
  token: string,
  candidateId: string,
  profile: string,
  status: ApplicationStatus,
): Promise<JobApplication> {
  return request(
    token,
    `/operator/jobs/${encodeURIComponent(candidateId)}/application`,
    {
      method: "POST",
      body: JSON.stringify({ profile, status }),
    },
  );
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
