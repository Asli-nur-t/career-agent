import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  ApplicationStatus,
  BrowserAgentProvider,
  BrowserCollectedJob,
  BrowserSourceDiagnostic,
  CompanyDetail,
  CompanyDiscoveryRun,
  CompanyItem,
  CompanyPage,
  CompanyProfileStatus,
  CompanyRejectionReason,
  JobDetail,
  JobApplication,
  JobItem,
  NativeSearchLink,
  NativeSearchSource,
  Profile,
  SearchMode,
  SearchRun,
  SearchRunCandidate,
  SearchSource,
  SearchWorkMode,
  Summary,
  approveCompany,
  approveJob,
  discoverCompany,
  getCompanyDetail,
  getCompanies,
  getLatestCompanyDiscoveryRun,
  getJobDetail,
  getApplications,
  getBrowserCollectedJobs,
  getJobs,
  getProfiles,
  getSummary,
  getLatestProfileSearch,
  getNativeSearchLinks,
  importManualJob,
  markJobViewed,
  rejectJob,
  rejectCompany,
  pauseCompanyDiscoveryRun,
  resumeCompanyDiscoveryRun,
  startCompanyDiscoveryRun,
  startProfileSearch,
  updateApplication,
  updateProfileExperience,
  collectWithBrowserAgent,
  cleanupBrowserCollectedJobs,
  cleanupStaleBrowserCollectedJobs,
  dismissBrowserCollectedJob,
  restoreBrowserCollectedJob,
} from "./api";

type View = "overview" | "search" | "jobs" | "applications" | "companies";

const labels: Record<string, string> = {
  strong_apply: "Güçlü başvuru",
  apply: "Başvur",
  review: "İncele",
  skip: "Atla",
  remote: "Uzaktan",
  hybrid: "Hibrit",
  onsite: "İş yerinde",
  unknown: "Bilinmiyor",
  full_time: "Tam zamanlı",
  part_time: "Yarı zamanlı",
  contract: "Sözleşmeli",
  internship: "Staj",
  queued: "Sırada",
  running: "Aranıyor",
  pause_requested: "Duraklatılıyor",
  paused: "Duraklatıldı",
  succeeded: "Tamamlandı",
  failed: "Başarısız",
  candidates_found: "Aday bulundu",
  no_results: "Sonuç yok",
  active_review: "Aktif · inceleme bekliyor",
  activity_unknown: "Aktiflik kanıtlanamadı",
  already_approved: "Daha önce onaylandı",
  closed: "İlan kapalı",
  location_or_policy: "Konum veya profil politikasına uymuyor",
  location_unknown: "Konumu doğrulanamadı",
  profile_filtered: "Profil filtresinde elendi",
  role_or_score: "Rol veya puan eşiğinde elendi",
  previously_rejected: "Daha önce reddedildi",
  all: "Tüm durumlar",
  unprofiled: "Profil oluşturulmadı",
  candidate_found: "Aday profil bulundu",
  verified: "Doğrulandı",
  needs_review: "İnceleme gerekli",
  not_found: "Bulunamadı",
  high: "Yüksek güven",
  medium: "Orta güven",
  low: "Düşük güven",
  wrong_company: "Yanlış şirket eşleşmesi",
  unsafe_or_invalid_url: "Geçersiz veya güvensiz bağlantı",
  insufficient_evidence: "Kanıt yetersiz",
  success: "Başarılı",
  error: "Hata",
  linkedin: "LinkedIn",
  kariyer: "Kariyer.net",
  indeed: "Indeed",
  glassdoor: "Glassdoor",
  ats: "Resmî ATS",
  turkey_tech: "Türkiye teknoloji",
  remote_feeds: "Küresel remote",
  techcareer: "Techcareer.net",
  yenibiris: "Yenibiriş",
  secretcv: "SecretCV",
  toptalent: "Toptalent",
  weworkremotely: "We Work Remotely",
  remoteok: "Remote OK",
  remotive: "Remotive",
  jobicy: "Jobicy",
  to_apply: "Başvurulacak",
  applied: "Başvuruldu",
  interview: "Mülakat",
  rejected: "Reddedildi",
  offer: "Teklif",
  withdrawn: "Vazgeçildi",
};

function label(value: string): string {
  return labels[value] ?? value.replaceAll("_", " ");
}

function confidenceLevel(value: "low" | "medium" | "high" | null): string {
  if (value === "high") return "Yüksek";
  if (value === "medium") return "Orta";
  return "Düşük";
}

function displayedFitStars(item: BrowserCollectedJob): number {
  if (item.fit_score === 0) return 0;
  return Math.max(0, Math.min(5, item.fit_stars ?? 0));
}

function formatDate(value: string | null): string {
  if (!value) return "Bilinmiyor";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Bilinmiyor"
    : new Intl.DateTimeFormat("tr-TR", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
}

function todayLabel(): string {
  return new Intl.DateTimeFormat("tr-TR", {
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(new Date()).toLocaleUpperCase("tr-TR");
}

function profileSearchBudget(roleCount: number, mode: SearchMode, sourceCount: number) {
  const groups = mode === "quick" ? 1 : Math.ceil(roleCount / 3);
  const primaryQueries = groups * sourceCount;
  const queryLimit = Math.min(mode === "quick" ? 10 : 20, primaryQueries * 3);
  return { queryLimit, resultLimit: queryLimit * 10 };
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const messages: Record<string, string> = {
      operator_auth_failed: "Operatör anahtarı geçersiz.",
      operator_auth_not_configured:
        "Backend üzerinde OPERATOR_API_TOKEN yapılandırılmamış.",
      profile_not_found: "Aday profili bulunamadı.",
      database_unavailable: "Veritabanına şu anda ulaşılamıyor.",
      provider_invalid: "Bu bağlantının iş sitesi henüz güvenli içe aktarmayı desteklemiyor.",
      manual_job_invalid: "İlan bağlantısı veya alanlardan biri doğrulanamadı.",
      manual_job_storage_unavailable: "İlan şu anda kaydedilemedi. Biraz sonra tekrar dene.",
      browser_agent_not_installed: "Tarayıcı ajanı kurulu değil. Playwright ve Chrome kurulumunu tamamlamalısın.",
      browser_human_action_required: "Tarayıcı giriş veya güvenlik kontrolünde bekliyor. Açılan Chrome penceresinde işlemi tamamlayıp yeniden dene.",
      browser_agent_unavailable: "Chrome başlatılamadı veya kaynak sayfa zamanında açılamadı.",
      browser_agent_already_running: "Bir tarayıcı taraması zaten çalışıyor. Açık Chrome penceresinin tamamlanmasını bekle.",
      browser_agent_cooldown_active: "Kaynakları korumak için tarayıcı taramaları arasında bekleme uygulanıyor. Daha sonra yeniden dene.",
      browser_agent_config_invalid: "Tarayıcı bekleme ayarlarından biri geçersiz. .env değerlerini kontrol et.",
      browser_no_results: "Tarayıcı ajanı bu kapsamda okunabilir ilan bulamadı.",
      browser_request_invalid: "Tarayıcı ajanı için rol veya konum geçersiz.",
      browser_import_storage_unavailable: "Toplanan ilanlar şu anda kaydedilemedi.",
      browser_results_unavailable: "Toplanan ilanlar şu anda veritabanından okunamadı.",
      browser_cleanup_unavailable: "Bozuk ilan kayıtları şu anda ayıklanamadı.",
      candidate_data_mismatch: "İlan kanıtı güvenli doğrulamadan geçemedi.",
      search_already_running: "Bu profil için bir arama zaten çalışıyor.",
      search_roles_invalid: "Arama için 1–10 geçerli rol seçmelisin.",
      search_mode_invalid: "Geçerli bir tarama yoğunluğu seçmelisin.",
      search_cooldown: "Aynı kapsam kısa süre önce tarandı. Beş dakika dolmadan yeniden kota kullanılamaz.",
      serper_not_configured: "SERPER_API_KEY backend üzerinde yapılandırılmamış.",
      worker_interrupted: "Önceki arama backend yeniden başladığı için kesildi.",
      search_failed: "Arama güvenli şekilde sonlandırıldı. Ayrıntılar sunucu logunda.",
      search_run_unavailable: "Arama kaydı oluşturuldu ancak yeniden okunamadı.",
      cached: "Bu profil için önbellek süresi henüz dolmadı. Biraz sonra tekrar dene.",
      company_not_found: "Şirket kaydı bulunamadı.",
      company_profile_not_found: "Bu şirket için incelenecek profil bulunamadı.",
      profile_not_reviewable: "Bu şirket profili artık inceleme durumunda değil.",
      verified_profile_protected: "Doğrulanmış şirket profili red işlemine karşı korunuyor.",
      identity_confirmation_required: "Şirket kimliğini doğruladığını onaylamalısın.",
      rejection_reason_invalid: "Geçerli bir red nedeni seçmelisin.",
      company_search_in_progress: "Bu şirket için bir profil araması zaten çalışıyor.",
      company_search_cooldown: "Bu şirket kısa süre önce arandı. Beş dakika sonra tekrar deneyebilirsin.",
      evaluator_not_configured: "Şirket değerlendirme modeli backend üzerinde yapılandırılmamış.",
      company_discovery_failed: "Şirket profil araması güvenli şekilde sonlandırıldı.",
      rate_limited: "Arama sağlayıcısı geçici olarak istek sınırı uyguladı.",
      invented_url: "Modelin önerdiği bağlantı arama sonuçlarında doğrulanamadı.",
      company_discovery_run_active: "Toplu şirket taraması zaten çalışıyor veya duraklatılmış.",
      company_discovery_run_not_found: "Toplu tarama kaydı bulunamadı.",
      company_discovery_run_not_active: "Tamamlanmış bir tarama duraklatılamaz.",
      company_discovery_run_not_paused: "Yalnızca duraklatılmış bir tarama sürdürülebilir.",
      company_discovery_run_unavailable: "Toplu tarama oluşturuldu ancak yeniden okunamadı.",
      company_discovery_run_failed: "Toplu şirket taraması güvenli şekilde durduruldu.",
      application_initial_status_invalid:
        "Yeni bir kayıt yalnızca başvurulacak veya başvuruldu olarak başlatılabilir.",
      application_transition_invalid:
        "Bu başvuru durumu geçişine izin verilmiyor.",
    };
    return messages[error.code] ?? `İşlem tamamlanamadı: ${error.code}`;
  }
  return "Beklenmeyen bir bağlantı hatası oluştu.";
}

function AuthScreen({ onConnect }: { onConnect: (token: string) => void }) {
  const [value, setValue] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    const token = value.trim();
    if (token.length >= 32) onConnect(token);
  }

  return (
    <main className="auth-shell">
      <section className="auth-copy">
        <div className="brand-mark">CA</div>
        <p className="eyebrow">KİŞİSEL KARİYER OPERASYON MERKEZİ</p>
        <h1>İlan gürültüsünü azalt.<br />Doğru fırsata odaklan.</h1>
        <p>
          Career Agent yalnızca doğrulanmış ilanları öne çıkarır; konum,
          aktiflik ve profil uyumunu tek ekranda açıklar.
        </p>
        <div className="trust-row">
          <span>Yerel çalışma</span><span>İnsan onayı</span><span>Kanıt temelli</span>
        </div>
      </section>
      <section className="auth-card">
        <div className="status-pill"><i /> Backend bağlantısı bekleniyor</div>
        <h2>Operatör paneline bağlan</h2>
        <p>Anahtar yalnızca bu sekmenin belleğinde tutulur.</p>
        <form onSubmit={submit}>
          <label htmlFor="operator-token">Operatör anahtarı</label>
          <input
            id="operator-token"
            type="password"
            autoComplete="off"
            minLength={32}
            maxLength={256}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="OPERATOR_API_TOKEN"
            required
          />
          <button className="primary full" type="submit" disabled={value.trim().length < 32}>
            Güvenli bağlantı kur
          </button>
        </form>
        <small>Anahtar URL’ye, localStorage’a veya loglara yazılmaz.</small>
      </section>
    </main>
  );
}

function MetricCard({ title, value, note, tone = "plain" }: {
  title: string; value: number; note: string; tone?: "plain" | "mint" | "amber";
}) {
  return (
    <article className={`metric-card ${tone}`}>
      <span>{title}</span><strong>{value.toLocaleString("tr-TR")}</strong><small>{note}</small>
    </article>
  );
}

const applicationTransitions: Record<ApplicationStatus, ApplicationStatus[]> = {
  to_apply: ["applied", "withdrawn"],
  applied: ["interview", "rejected", "offer", "withdrawn"],
  interview: ["rejected", "offer", "withdrawn"],
  rejected: ["to_apply"],
  offer: ["withdrawn"],
  withdrawn: ["to_apply"],
};

function ApplicationQuickActions({
  status,
  busy,
  onUpdate,
}: {
  status?: ApplicationStatus;
  busy: boolean;
  onUpdate: (status: ApplicationStatus) => void;
}) {
  if (!status) {
    return (
      <div className="application-quick-actions">
        <button type="button" className="ghost" disabled={busy} onClick={() => onUpdate("to_apply")}>Başvurulacak</button>
        <button type="button" className="primary" disabled={busy} onClick={() => onUpdate("applied")}>Başvuruldu</button>
      </div>
    );
  }
  return (
    <div className="application-quick-actions">
      <span className={`application-status ${status}`}>{label(status)}</span>
      {status === "to_apply" && (
        <button type="button" className="primary" disabled={busy} onClick={() => onUpdate("applied")}>Başvuruldu</button>
      )}
    </div>
  );
}

function ApplicationsView({
  applications,
  busyCandidate,
  onUpdate,
}: {
  applications: JobApplication[];
  busyCandidate: string | null;
  onUpdate: (candidateId: string, status: ApplicationStatus) => void;
}) {
  const [statusFilter, setStatusFilter] = useState<"all" | ApplicationStatus>("all");
  const visible = statusFilter === "all"
    ? applications
    : applications.filter((item) => item.status === statusFilter);
  return (
    <section className="panel applications-panel">
      <div className="applications-head">
        <div><p className="eyebrow">KİŞİSEL TAKİP</p><h2>Başvurularım</h2><p>Arama ve ilan inceleme kararlarından bağımsız, kalıcı başvuru geçmişin.</p></div>
        <label>Durum
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as "all" | ApplicationStatus)}>
            <option value="all">Tüm durumlar</option>
            {Object.keys(applicationTransitions).map((status) => <option key={status} value={status}>{label(status)}</option>)}
          </select>
        </label>
      </div>
      {visible.length === 0 ? (
        <div className="empty-state"><div>○</div><h3>Başvuru kaydı yok</h3><p>Bir ilan kartında “Başvurulacak” veya “Başvuruldu” seçtiğinde burada görünür.</p></div>
      ) : (
        <div className="application-list">
          {visible.map((application) => (
            <article className="application-card" key={application.application_id}>
              <div className="application-card-main">
                <span className={`application-status ${application.status}`}>{label(application.status)}</span>
                <h3>{application.title}</h3>
                <p>{application.company_name}</p>
                <small>{application.location ?? "Konum bilinmiyor"} · {label(application.provider)} · {label(application.work_mode)}</small>
              </div>
              <div className="application-card-meta">
                <small>Güncelleme: {formatDate(application.updated_at)}</small>
                {application.applied_at && <small>Başvuru: {formatDate(application.applied_at)}</small>}
                <a href={application.listing_url} target="_blank" rel="noopener noreferrer">İlanı aç ↗</a>
                <label>Durumu değiştir
                  <select
                    value={application.status}
                    disabled={busyCandidate === application.candidate_id}
                    onChange={(event) => onUpdate(application.candidate_id, event.target.value as ApplicationStatus)}
                  >
                    <option value={application.status}>{label(application.status)}</option>
                    {applicationTransitions[application.status].map((status) => <option key={status} value={status}>{label(status)}</option>)}
                  </select>
                </label>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function SearchResultsModal({ candidates, onClose, onViewed, applicationByCandidate, applicationBusy, onApplication }: {
  candidates: SearchRunCandidate[];
  onClose: () => void;
  onViewed: (candidateId: string) => void;
  applicationByCandidate: Map<string, JobApplication>;
  applicationBusy: string | null;
  onApplication: (candidateId: string, status: ApplicationStatus) => void;
}) {
  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="search-results-modal" role="dialog" aria-modal="true" aria-labelledby="search-results-title" onMouseDown={(event) => event.stopPropagation()}>
        <header>
          <div><p className="eyebrow">SON ARAMA</p><h2 id="search-results-title">Profile uyan sonuçlar</h2></div>
          <button className="modal-close" onClick={onClose} aria-label="Kapat">×</button>
        </header>
        <p className="modal-explanation">Bu liste aramada role uyan tüm kayıtları gösterir. Yalnızca aktifliği doğrulanmış ve inceleme bekleyen ilanlar ana kuyruğa girer.</p>
        <div className="search-results-list">
          {candidates.map((candidate) => (
            <article className={`search-result-row ${candidate.operator_viewed_at ? "viewed" : "unviewed"}`} key={candidate.candidate_id}>
              <span className={`score score-${candidate.recommendation}`}>{candidate.score}</span>
              <div>
                <strong>{candidate.title}</strong>
                <span>{candidate.company_name}</span>
                <small>{candidate.location ?? "Konum bilinmiyor"} · {candidate.provider}</small>
              </div>
              <div className="search-result-status">
                <span className={`view-state ${candidate.operator_viewed_at ? "viewed" : "new"}`}>
                  {candidate.operator_viewed_at ? "İncelendi" : "Yeni"}
                </span>
                <b className={`disposition ${candidate.disposition}`}>{label(candidate.disposition)}</b>
                <small>{label(candidate.activity_code)}</small>
                <a href={candidate.listing_url} target="_blank" rel="noopener noreferrer" onClick={() => onViewed(candidate.candidate_id)}>İlanı aç ↗</a>
                <ApplicationQuickActions
                  status={applicationByCandidate.get(candidate.candidate_id)?.status}
                  busy={applicationBusy === candidate.candidate_id}
                  onUpdate={(status) => onApplication(candidate.candidate_id, status)}
                />
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}

const generalSearchSources: SearchSource[] = [
  "linkedin", "kariyer", "indeed", "glassdoor", "ats",
  "turkey_tech", "remote_feeds",
];
const nativeSearchSources: NativeSearchSource[] = [
  "linkedin", "kariyer", "indeed", "glassdoor", "ats",
  "turkey_tech", "remote_feeds",
];
const browserAgentProviders: BrowserAgentProvider[] = [
  "linkedin", "kariyer", "indeed", "glassdoor",
  "techcareer", "yenibiris", "secretcv", "toptalent",
  "weworkremotely", "remoteok", "remotive", "jobicy",
];
const generalSearchWorkModes: SearchWorkMode[] = [
  "remote", "hybrid", "onsite",
];
const commonSearchRoles = [
  "AI Engineer", "Machine Learning Engineer", "GenAI Engineer", "RAG Engineer",
  "LLM Engineer", "Applied AI Engineer", "AI Research Engineer", "Computer Vision Engineer",
  "Deep Learning Engineer", "NLP Engineer", "Prompt Engineer", "Data Scientist",
  "Data Engineer", "Analytics Engineer", "MLOps Engineer", "Data Analyst",
  "Business Intelligence Analyst", "BI Developer", "Database Developer",
  "Database Administrator", "Software Engineer", "Junior Software Engineer",
  "Backend Engineer", "Backend Developer", "API Developer", "Python Developer",
  ".NET Developer", "C# Developer", "Java Developer", "Frontend Developer",
  "React Developer", "Full Stack Developer", "Mobile Developer", "Flutter Developer",
  "iOS Developer", "Android Developer", "Business Analyst", "IT Business Analyst",
  "System Analyst", "Product Analyst", "Product Manager", "Technical Product Manager",
  "System Engineer", "DevOps Engineer", "Site Reliability Engineer", "Platform Engineer",
  "Cloud Engineer", "Kubernetes Engineer", "Solutions Engineer", "QA Engineer",
  "Software Test Engineer", "Test Automation Engineer", "Cyber Security Engineer",
  "Information Security Specialist", "Network Engineer", "ERP Consultant",
  "SAP Consultant", "CRM Specialist", "Implementation Consultant", "Technical Support Engineer",
];

type BrowserApplicationFilter = "all" | "untracked" | ApplicationStatus;
type BrowserResultSort = "fit_desc" | "confidence_desc" | "newest";

const confidenceRank: Record<"low" | "medium" | "high", number> = {
  low: 1,
  medium: 2,
  high: 3,
};

function GeneralSearchView({
  token,
  selectedProfile,
  roles,
  availableRoles,
  customRole,
  onCustomRoleChange,
  onAddRole,
  onToggleRole,
  mode,
  onModeChange,
  locations,
  locationValue,
  onLocationValueChange,
  onAddLocation,
  onRemoveLocation,
  workModes,
  onToggleWorkMode,
  sources,
  onToggleSource,
  maxAgeDays,
  onMaxAgeDaysChange,
  run,
  running,
  starting,
  budget,
  onStart,
  onViewed,
  applicationByCandidate,
  applicationBusy,
  onApplication,
  onManualImported,
  onProfileUpdated,
}: {
  token: string;
  selectedProfile: Profile | null;
  roles: string[];
  availableRoles: string[];
  customRole: string;
  onCustomRoleChange: (value: string) => void;
  onAddRole: (event: FormEvent) => void;
  onToggleRole: (role: string) => void;
  mode: SearchMode;
  onModeChange: (mode: SearchMode) => void;
  locations: string[];
  locationValue: string;
  onLocationValueChange: (value: string) => void;
  onAddLocation: (event: FormEvent) => void;
  onRemoveLocation: (location: string) => void;
  workModes: SearchWorkMode[];
  onToggleWorkMode: (mode: SearchWorkMode) => void;
  sources: SearchSource[];
  onToggleSource: (source: SearchSource) => void;
  maxAgeDays: number;
  onMaxAgeDaysChange: (value: number) => void;
  run: SearchRun | null;
  running: boolean;
  starting: boolean;
  budget: { queryLimit: number; resultLimit: number };
  onStart: () => void;
  onViewed: (candidateId: string) => void;
  applicationByCandidate: Map<string, JobApplication>;
  applicationBusy: string | null;
  onApplication: (candidateId: string, status: ApplicationStatus) => void;
  onManualImported: () => Promise<void>;
  onProfileUpdated: (profile: Profile) => void;
}) {
  const [resultQuery, setResultQuery] = useState("");
  const [providerFilter, setProviderFilter] = useState("all");
  const [dispositionFilter, setDispositionFilter] = useState("all");
  const [viewFilter, setViewFilter] = useState("all");
  const [nativeRole, setNativeRole] = useState(roles[0] ?? availableRoles[0] ?? "");
  const [nativeLocation, setNativeLocation] = useState(locations[0] ?? "");
  const [nativeLinks, setNativeLinks] = useState<NativeSearchLink[]>([]);
  const [nativeUnavailable, setNativeUnavailable] = useState<string[]>([]);
  const [nativeLoading, setNativeLoading] = useState(false);
  const [nativeError, setNativeError] = useState<string | null>(null);
  const [manualUrl, setManualUrl] = useState("");
  const [manualTitle, setManualTitle] = useState("");
  const [manualCompany, setManualCompany] = useState("");
  const [manualLocation, setManualLocation] = useState("");
  const [manualWorkMode, setManualWorkMode] = useState<"remote" | "hybrid" | "onsite" | "unknown">("unknown");
  const [manualConfirmed, setManualConfirmed] = useState(false);
  const [manualBusy, setManualBusy] = useState(false);
  const [manualMessage, setManualMessage] = useState<string | null>(null);
  const [manualError, setManualError] = useState<string | null>(null);
  const [browserBusy, setBrowserBusy] = useState(false);
  const [browserMessage, setBrowserMessage] = useState<string | null>(null);
  const [browserError, setBrowserError] = useState<string | null>(null);
  const [selectedBrowserProviders, setSelectedBrowserProviders] = useState<BrowserAgentProvider[]>(browserAgentProviders);
  const [browserWorkModes, setBrowserWorkModes] = useState<SearchWorkMode[]>(generalSearchWorkModes);
  const [browserDiagnostics, setBrowserDiagnostics] = useState<BrowserSourceDiagnostic[]>([]);
  const [browserCollected, setBrowserCollected] = useState<BrowserCollectedJob[]>([]);
  const [browserCollectedTotal, setBrowserCollectedTotal] = useState(0);
  const [browserResultsError, setBrowserResultsError] = useState<string | null>(null);
  const [browserCleanupBusy, setBrowserCleanupBusy] = useState(false);
  const [browserQuery, setBrowserQuery] = useState("");
  const [browserRoleFilter, setBrowserRoleFilter] = useState("all");
  const [browserProviderFilter, setBrowserProviderFilter] = useState("all");
  const [browserRecommendationFilter, setBrowserRecommendationFilter] = useState("all");
  const [browserApplicationFilter, setBrowserApplicationFilter] = useState<BrowserApplicationFilter>("all");
  const [browserSort, setBrowserSort] = useState<BrowserResultSort>("fit_desc");
  const [browserDismissBusy, setBrowserDismissBusy] = useState<string | null>(null);
  const [lastDismissed, setLastDismissed] = useState<{ candidateId: string; title: string } | null>(null);
  const [professionalYears, setProfessionalYears] = useState(
    selectedProfile?.professional_experience_years ?? 0,
  );
  const [internshipMonths, setInternshipMonths] = useState(
    selectedProfile?.internship_months ?? 0,
  );
  const [experienceBusy, setExperienceBusy] = useState(false);
  const [experienceMessage, setExperienceMessage] = useState<string | null>(null);
  const candidates = run?.result.matched_candidates ?? [];
  const sourceDiagnostics = run?.result.source_diagnostics ?? [];
  const providers = useMemo(
    () => Array.from(new Set(candidates.map((item) => item.provider))).sort(),
    [candidates],
  );
  const dispositions = useMemo(
    () => Array.from(new Set(candidates.map((item) => item.disposition))).sort(),
    [candidates],
  );
  const filteredCandidates = useMemo(() => {
    const query = resultQuery.trim().toLocaleLowerCase("tr-TR");
    return candidates.filter((candidate) => {
      if (providerFilter !== "all" && candidate.provider !== providerFilter) return false;
      if (dispositionFilter !== "all" && candidate.disposition !== dispositionFilter) return false;
      if (viewFilter === "new" && candidate.operator_viewed_at) return false;
      if (viewFilter === "viewed" && !candidate.operator_viewed_at) return false;
      if (!query) return true;
      return [candidate.title, candidate.company_name, candidate.location ?? ""]
        .some((value) => value.toLocaleLowerCase("tr-TR").includes(query));
    });
  }, [candidates, dispositionFilter, providerFilter, resultQuery, viewFilter]);
  const browserSearchRoles = useMemo(
    () => Array.from(new Set(browserCollected.flatMap((item) => item.search_roles))).sort((a, b) => a.localeCompare(b, "tr")),
    [browserCollected],
  );
  const browserProviders = useMemo(
    () => Array.from(new Set(browserCollected.map((item) => item.provider))).sort(),
    [browserCollected],
  );
  const visibleBrowserCollected = useMemo(() => {
    const query = browserQuery.trim().toLocaleLowerCase("tr-TR");
    const filtered = browserCollected.filter((item) => {
      if (browserRoleFilter !== "all" && !item.search_roles.includes(browserRoleFilter)) return false;
      if (browserProviderFilter !== "all" && item.provider !== browserProviderFilter) return false;
      if (browserRecommendationFilter === "pending" && item.assessment_state === "ready") return false;
      if (
        browserRecommendationFilter !== "all" &&
        browserRecommendationFilter !== "pending" &&
        item.fit_recommendation !== browserRecommendationFilter
      ) return false;
      const application = applicationByCandidate.get(item.candidate_id);
      if (browserApplicationFilter === "untracked" && application) return false;
      if (
        browserApplicationFilter !== "all" &&
        browserApplicationFilter !== "untracked" &&
        application?.status !== browserApplicationFilter
      ) return false;
      if (!query) return true;
      return [item.title, item.company_name, item.location ?? "", ...item.search_roles]
        .some((value) => value.toLocaleLowerCase("tr-TR").includes(query));
    });
    return filtered.sort((left, right) => {
      if (browserSort === "newest") {
        return new Date(right.collected_at).getTime() - new Date(left.collected_at).getTime();
      }
      if (browserSort === "confidence_desc") {
        const confidenceDifference = (right.fit_confidence ? confidenceRank[right.fit_confidence] : 0)
          - (left.fit_confidence ? confidenceRank[left.fit_confidence] : 0);
        if (confidenceDifference !== 0) return confidenceDifference;
      }
      const scoreDifference = (right.fit_score ?? -1) - (left.fit_score ?? -1);
      if (scoreDifference !== 0) return scoreDifference;
      return new Date(right.collected_at).getTime() - new Date(left.collected_at).getTime();
    });
  }, [
    applicationByCandidate,
    browserApplicationFilter,
    browserCollected,
    browserProviderFilter,
    browserQuery,
    browserRecommendationFilter,
    browserRoleFilter,
    browserSort,
  ]);

  const refreshBrowserCollected = useCallback(async () => {
    if (!selectedProfile) {
      setBrowserCollected([]);
      setBrowserCollectedTotal(0);
      return;
    }
    try {
      const response = await getBrowserCollectedJobs(
        token,
        selectedProfile.label,
        500,
        0,
      );
      setBrowserCollected(response.items);
      setBrowserCollectedTotal(response.total);
      setBrowserResultsError(null);
    } catch (caught) {
      setBrowserResultsError(errorMessage(caught));
    }
  }, [selectedProfile, token]);

  useEffect(() => {
    setProfessionalYears(selectedProfile?.professional_experience_years ?? 0);
    setInternshipMonths(selectedProfile?.internship_months ?? 0);
    setExperienceMessage(null);
  }, [selectedProfile]);

  async function saveExperienceProfile() {
    if (!selectedProfile || experienceBusy) return;
    setExperienceBusy(true);
    setExperienceMessage(null);
    try {
      const updated = await updateProfileExperience(
        token,
        selectedProfile.label,
        professionalYears,
        internshipMonths,
      );
      onProfileUpdated(updated);
      setBrowserCollected([]);
      await refreshBrowserCollected();
      setExperienceMessage("Deneyim profili kaydedildi; eski ajan puanları yenilenecek.");
    } catch (caught) {
      setExperienceMessage(errorMessage(caught));
    } finally {
      setExperienceBusy(false);
    }
  }

  useEffect(() => {
    void refreshBrowserCollected();
  }, [refreshBrowserCollected]);

  async function cleanupBrowserResults() {
    if (browserCleanupBusy) return;
    if (!window.confirm("Bozuk kart metinleri onarılsın ve örnek/sahte bağlantılar karantinaya alınsın mı? Hiçbir kayıt silinmez.")) return;
    setBrowserCleanupBusy(true);
    setBrowserResultsError(null);
    try {
      const result = await cleanupBrowserCollectedJobs(token);
      await refreshBrowserCollected();
      setBrowserMessage(`${result.repaired_count} kayıt düzeltildi; ${result.quarantined_count} geçersiz kayıt karantinaya alındı.`);
    } catch (caught) {
      setBrowserResultsError(errorMessage(caught));
    } finally {
      setBrowserCleanupBusy(false);
    }
  }

  async function cleanupStaleBrowserResults() {
    if (!selectedProfile || browserCleanupBusy) return;
    setBrowserCleanupBusy(true);
    setBrowserResultsError(null);
    try {
      const preview = await cleanupStaleBrowserCollectedJobs(
        token,
        selectedProfile.label,
        false,
      );
      if (preview.matched_count === 0) {
        setBrowserMessage("Açıklaması yetersiz, değerlendirmesi veya başvuru kaydı olmayan kayıt bulunamadı.");
        return;
      }
      const confirmed = window.confirm(
        `${preview.matched_count} yorumsuz kayıt listeden kaldırılacak. Yalnızca ajan değerlendirmesi ve başvuru kaydı olmayan, açıklaması 100 karakterden kısa ilanlar etkilenecek. Daha önce açılmış olmaları korunmalarına yetmez. Devam edilsin mi?`,
      );
      if (!confirmed) return;
      const result = await cleanupStaleBrowserCollectedJobs(
        token,
        selectedProfile.label,
        true,
      );
      await refreshBrowserCollected();
      setBrowserMessage(`${result.quarantined_count} açıklaması yetersiz ve değerlendirilemeyen kayıt güvenli biçimde listeden kaldırıldı.`);
    } catch (caught) {
      setBrowserResultsError(errorMessage(caught));
    } finally {
      setBrowserCleanupBusy(false);
    }
  }

  async function dismissBrowserResult(item: BrowserCollectedJob) {
    if (!selectedProfile || browserDismissBusy) return;
    if (!window.confirm(`“${item.title}” bu profilin toplanmış ilan listesinden gizlensin mi? Başvuru geçmişi ve ilan kaydı silinmez.`)) return;
    setBrowserDismissBusy(item.candidate_id);
    setBrowserResultsError(null);
    try {
      await dismissBrowserCollectedJob(token, item.candidate_id, selectedProfile.label);
      setBrowserCollected((current) => current.filter((candidate) => candidate.candidate_id !== item.candidate_id));
      setBrowserCollectedTotal((current) => Math.max(0, current - 1));
      setLastDismissed({ candidateId: item.candidate_id, title: item.title });
    } catch (caught) {
      setBrowserResultsError(errorMessage(caught));
    } finally {
      setBrowserDismissBusy(null);
    }
  }

  async function restoreLastDismissed() {
    if (!selectedProfile || !lastDismissed || browserDismissBusy) return;
    setBrowserDismissBusy(lastDismissed.candidateId);
    setBrowserResultsError(null);
    try {
      await restoreBrowserCollectedJob(token, lastDismissed.candidateId, selectedProfile.label);
      setLastDismissed(null);
      await refreshBrowserCollected();
    } catch (caught) {
      setBrowserResultsError(errorMessage(caught));
    } finally {
      setBrowserDismissBusy(null);
    }
  }

  useEffect(() => {
    if (!availableRoles.includes(nativeRole)) setNativeRole(availableRoles[0] ?? "");
  }, [availableRoles, nativeRole]);

  useEffect(() => {
    if (nativeLocation && !locations.includes(nativeLocation)) {
      setNativeLocation(locations[0] ?? "");
    }
  }, [locations, nativeLocation]);

  useEffect(() => {
    setNativeLinks([]);
    setNativeUnavailable([]);
    setNativeError(null);
  }, [nativeLocation, nativeRole]);

  async function prepareNativeLinks() {
    if (!nativeRole || nativeLoading) return;
    setNativeLoading(true);
    setNativeError(null);
    try {
      const response = await getNativeSearchLinks(
        token,
        nativeRole,
        nativeLocation || null,
        nativeSearchSources,
      );
      setNativeLinks(response.links);
      setNativeUnavailable(response.unavailable_sources);
    } catch (caught) {
      setNativeError(errorMessage(caught));
    } finally {
      setNativeLoading(false);
    }
  }

  async function submitManualJob(event: FormEvent) {
    event.preventDefault();
    if (manualBusy || !manualConfirmed) return;
    setManualBusy(true);
    setManualError(null);
    setManualMessage(null);
    try {
      const result = await importManualJob(token, {
        listing_url: manualUrl.trim(),
        title: manualTitle.trim(),
        company_name: manualCompany.trim(),
        location: manualLocation.trim() || null,
        work_mode: manualWorkMode,
        employment_type: "unknown",
        confirmed_visible: true,
      });
      await onManualImported();
      setManualMessage(result.created ? "İlan inceleme kuyruğuna eklendi." : result.changed ? "Mevcut ilan güncellendi." : "İlan daha önce karara bağlanmış; mevcut kayıt korundu.");
      setManualUrl("");
      setManualTitle("");
      setManualCompany("");
      setManualConfirmed(false);
    } catch (caught) {
      setManualError(errorMessage(caught));
    } finally {
      setManualBusy(false);
    }
  }

  async function runBrowserAgent() {
    if (!nativeRole || !selectedProfile || browserBusy || selectedBrowserProviders.length === 0) return;
    const sourceNames = selectedBrowserProviders.map((provider) => label(provider)).join(", ");
    const workModeNames = browserWorkModes.map((mode) => label(mode)).join(", ");
    if (!window.confirm(
      `Görünür tarayıcı açılarak ${sourceNames} kaynaklarında “${nativeRole}” aranacak. Yalnızca ${workModeNames} çalışma biçimleri kabul edilecek. Siteler dış istekleri görebilir ve gerekirse giriş/CAPTCHA işlemini senin tamamlaman gerekir. Tarama başlatılsın mı?`,
    )) return;
    setBrowserBusy(true);
    setBrowserError(null);
    setBrowserMessage(null);
    setBrowserDiagnostics([]);
    try {
      const result = await collectWithBrowserAgent(
        token,
        selectedProfile.label,
        nativeRole,
        nativeLocation || null,
        selectedBrowserProviders,
        browserWorkModes,
      );
      await onManualImported();
      await refreshBrowserCollected();
      setBrowserDiagnostics(result.diagnostics);
      const loginCount = result.diagnostics.filter((item) => item.outcome === "login_required").length;
      const limitedCount = result.diagnostics.filter((item) => item.outcome === "rate_limited" || item.outcome === "source_cooldown").length;
      const blockedCount = result.diagnostics.filter((item) => item.outcome === "blocked").length;
      const failedCount = result.diagnostics.filter((item) => item.outcome === "failed").length;
      const suffix = [
        loginCount ? `${loginCount} kaynak giriş istedi` : "",
        limitedCount ? `${limitedCount} kaynak beklemeye alındı` : "",
        blockedCount ? `${blockedCount} kaynak güvenlik kontrolü gösterdi` : "",
        failedCount ? `${failedCount} kaynak açılamadı` : "",
      ].filter(Boolean).join("; ");
      const agentSources = result.diagnostics.filter((item) => item.agent_used).length;
      setBrowserMessage(`${result.collected_count} ilan okundu; ${result.created_count} yeni kayıt eklendi, ${result.updated_count} kayıt güncellendi; ${agentSources} kaynakta yerel ajan çalıştı; ${result.assessment_queued_count} ilan uygunluk değerlendirmesine alındı${suffix ? `; ${suffix}` : ""}.`);
      if (result.assessment_queued_count > 0) {
        window.setTimeout(() => { void refreshBrowserCollected(); }, 4_000);
        window.setTimeout(() => { void refreshBrowserCollected(); }, 12_000);
      }
    } catch (caught) {
      setBrowserError(errorMessage(caught));
    } finally {
      setBrowserBusy(false);
    }
  }

  return (
    <div className="general-search-page">
      <section className="panel search-builder-panel">
        <div className="panel-head search-page-head">
          <div>
            <p className="eyebrow">ŞİRKET LİSTESİNDEN BAĞIMSIZ</p>
            <h2>Genel iş piyasasını tara</h2>
            <p>Seçilen roller doğrudan genel iş sitelerinde ve resmî ATS kaynaklarında aranır.</p>
          </div>
          <div className={`search-run-badge ${run?.status ?? "idle"}`}>
            {run ? label(run.status) : "Hazır"}
          </div>
        </div>

        {selectedProfile && (
          <section className="agent-profile-card">
            <div>
              <p className="eyebrow">AJAN DEĞERLENDİRME PROFİLİ</p>
              <h3>Deneyim bilgisini doğrula</h3>
              <p>CV becerileri korunur; yerel ajan deneyim açığını bu iki ayrı değerle değerlendirir.</p>
            </div>
            <label>
              Profesyonel deneyim (yıl)
              <input
                type="number"
                min={0}
                max={50}
                step={1}
                value={professionalYears}
                onChange={(event) => setProfessionalYears(Math.max(0, Math.min(50, Number(event.target.value))))}
              />
            </label>
            <label>
              Staj deneyimi (ay)
              <input
                type="number"
                min={0}
                max={120}
                step={1}
                value={internshipMonths}
                onChange={(event) => setInternshipMonths(Math.max(0, Math.min(120, Number(event.target.value))))}
              />
            </label>
            <button className="ghost" type="button" disabled={experienceBusy} onClick={() => { void saveExperienceProfile(); }}>
              {experienceBusy ? "Kaydediliyor…" : "Deneyimi kaydet"}
            </button>
            {experienceMessage && <small>{experienceMessage}</small>}
          </section>
        )}

        <div className="search-builder-grid">
          <section className="search-control-block role-control-block">
            <div className="control-title"><strong>1. Roller</strong><span>{roles.length}/10</span></div>
            <p>{selectedProfile?.label ?? "Seçili profil"} rolleri başlangıçta seçilir; istediğini kaldırabilir veya yeni rol ekleyebilirsin.</p>
            <div className="role-cloud selectable">
              {availableRoles.map((role) => {
                const selected = roles.includes(role);
                const custom = !(selectedProfile?.target_roles ?? []).includes(role);
                return (
                  <button type="button" className={selected ? "selected" : ""} key={role} onClick={() => onToggleRole(role)}>
                    {role}{custom && selected ? " ×" : ""}
                  </button>
                );
              })}
            </div>
            <form className="role-add" onSubmit={onAddRole}>
              <input value={customRole} maxLength={100} onChange={(event) => onCustomRoleChange(event.target.value)} placeholder="Örn. Platform Engineer" />
              <button className="ghost" type="submit" disabled={!customRole.trim() || roles.length >= 10}>Ekle</button>
            </form>
          </section>

          <section className="search-control-block">
            <div className="control-title"><strong>2. Konum ve çalışma</strong><span>En fazla 3 konum</span></div>
            <p>Konum girmezsen profilindeki konum tercihleri kullanılır.</p>
            <div className="location-cloud">
              {locations.map((location) => <button type="button" key={location} onClick={() => onRemoveLocation(location)}>{location} ×</button>)}
            </div>
            <form className="role-add" onSubmit={onAddLocation}>
              <input value={locationValue} maxLength={100} onChange={(event) => onLocationValueChange(event.target.value)} placeholder="Örn. İstanbul veya Türkiye" />
              <button className="ghost" type="submit" disabled={!locationValue.trim() || locations.length >= 3}>Ekle</button>
            </form>
            <div className="choice-row">
              {generalSearchWorkModes.map((workMode) => (
                <button type="button" className={workModes.includes(workMode) ? "selected" : ""} key={workMode} onClick={() => onToggleWorkMode(workMode)}>{label(workMode)}</button>
              ))}
            </div>
          </section>

          <section className="search-control-block">
            <div className="control-title"><strong>3. Kaynaklar</strong><span>{sources.length}/{generalSearchSources.length}</span></div>
            <p>Her kaynak ayrı sorgulanır; böylece tek bir platform sonuçları bastırmaz.</p>
            <div className="choice-row source-choices">
              {generalSearchSources.map((source) => (
                <button type="button" className={sources.includes(source) ? "selected" : ""} key={source} onClick={() => onToggleSource(source)}>{label(source)}</button>
              ))}
            </div>
          </section>

          <section className="search-control-block">
            <div className="control-title"><strong>4. Tarama kapsamı</strong><span>Kontrollü kota</span></div>
            <div className="search-mode-picker">
              <button type="button" className={mode === "quick" ? "selected" : ""} onClick={() => onModeChange("quick")}><b>Hızlı</b><span>Tüm roller birlikte</span></button>
              <button type="button" className={mode === "deep" ? "selected" : ""} onClick={() => onModeChange("deep")}><b>Derin</b><span>Üçlü rol grupları</span></button>
            </div>
            <label className="age-field">İlan yaşı
              <select value={maxAgeDays} onChange={(event) => onMaxAgeDaysChange(Number(event.target.value))}>
                <option value={7}>Son 7 gün</option>
                <option value={14}>Son 14 gün</option>
                <option value={30}>Son 30 gün</option>
                <option value={60}>Son 60 gün</option>
                <option value={90}>Son 90 gün</option>
              </select>
            </label>
          </section>
        </div>

        <div className="search-launch-row">
          <div><strong>En fazla {budget.queryLimit} sorgu / {budget.resultLimit} ham sonuç</strong><span>Aynı kapsam için 5 dakika, farklı kapsam için 30 saniye güvenli bekleme uygulanır.</span></div>
          <button className="primary" onClick={onStart} disabled={running || starting || roles.length === 0 || sources.length === 0 || workModes.length === 0}>
            {running ? "Arama sürüyor…" : starting ? "Başlatılıyor…" : "Genel ilan aramasını başlat"}
          </button>
        </div>
        {running && <div className="search-progress"><i /></div>}

        <section className="browser-agent-panel">
          <div>
            <p className="eyebrow">YEREL TARAYICI AJANI</p>
            <h3>Seçili iş sitelerini görünür tarayıcıda tara</h3>
            <p>Ajan kaynakların kendi arama sayfalarını açar, görünen ilan kartlarını okur ve doğrulanan bağlantıları inceleme kuyruğuna ekler. Bir kaynak giriş isterse diğerleri çalışmaya devam eder.</p>
          </div>
          <div className="browser-agent-controls">
            <label>Rol<select value={nativeRole} onChange={(event) => setNativeRole(event.target.value)}>{availableRoles.map((role) => <option key={role} value={role}>{role}</option>)}</select></label>
            <label>Konum<select value={nativeLocation} onChange={(event) => setNativeLocation(event.target.value)}><option value="">Kaynak varsayılanı</option>{locations.map((location) => <option key={location} value={location}>{location}</option>)}</select></label>
            <div>
              <b>Çalışma biçimi</b>
              <div className="choice-row compact-choice-row">
                {generalSearchWorkModes.map((mode) => (
                  <button
                    type="button"
                    className={browserWorkModes.includes(mode) ? "selected" : ""}
                    key={mode}
                    onClick={() => setBrowserWorkModes((current) => (
                      current.includes(mode)
                        ? current.filter((item) => item !== mode)
                        : [...current, mode]
                    ))}
                  >
                    {label(mode)}
                  </button>
                ))}
              </div>
              <small>Kaynak başına en fazla 10 ilan</small>
            </div>
          </div>
          <div className="browser-source-picker" aria-label="Tarayıcı ajanı kaynakları">
            {browserAgentProviders.map((provider) => {
              const selected = selectedBrowserProviders.includes(provider);
              return (
                <button
                  type="button"
                  className={selected ? "selected" : ""}
                  key={provider}
                  onClick={() => setSelectedBrowserProviders((current) => selected ? current.filter((item) => item !== provider) : [...current, provider])}
                >
                  {label(provider)}
                </button>
              );
            })}
          </div>
          <div className="browser-agent-actions">
            <div>{browserError && <span className="native-search-error">{browserError}</span>}{browserMessage && <span className="manual-job-success">{browserMessage}</span>}</div>
            <button className="primary" type="button" disabled={!nativeRole || !selectedProfile || browserBusy || selectedBrowserProviders.length === 0 || browserWorkModes.length === 0} onClick={() => { void runBrowserAgent(); }}>{browserBusy ? "Kaynaklar sırayla taranıyor…" : `${selectedBrowserProviders.length} kaynağı ajanla tara`}</button>
          </div>
          {browserDiagnostics.length > 0 && (
            <div className="browser-diagnostics">
              {browserDiagnostics.map((item) => (
                <div className={item.outcome} key={item.provider}>
                  <b>{item.label}</b>
                  <span>{item.error_code === "browser_source_work_mode_excluded" ? "Çalışma biçimi dışında" : item.outcome === "collected" ? `${item.collected_count} ilan` : item.outcome === "login_required" ? "Giriş gerekli" : item.outcome === "rate_limited" ? "İstek sınırı" : item.outcome === "blocked" ? "Güvenlik kontrolü" : item.outcome === "source_cooldown" ? "Kaynak beklemede" : item.outcome === "failed" ? "Açılamadı" : "Sonuç yok"}</span>
                  <small>{item.error_code === "browser_source_work_mode_excluded" ? "Remote seçili olmadığı için bu kaynak açılmadı" : item.outcome === "login_required" ? "Açılan pencerede giriş yap; parola uygulamaya verilmez" : item.outcome === "rate_limited" || item.outcome === "blocked" || item.outcome === "source_cooldown" ? "Kaynak korunmak için otomatik atlandı" : item.agent_used ? `Qwen ajanı · ${item.agent_action_count} araç işlemi` : item.error_code?.startsWith("local_agent_") ? "Kurallı yedek kullanıldı" : "Hazır bağlantı kullanıldı"}</small>
                </div>
              ))}
            </div>
          )}
          <small>Yalnızca burada seçilen çalışma biçimleri kaydedilir; seçilmeyenler elenir. Remote seçilmezse yalnızca remote ilan yayımlayan kaynaklar açılmadan atlanır. Uzaktan ilanlarda şehir zorunlu değildir; hibrit ve iş yerinde ilanlar seçilen şehirle eşleşmelidir. Rolü, konumu veya çalışma biçimi doğrulanamayan kart kaydedilmez. Ajan CAPTCHA veya giriş kontrolünü aşmaz; giriş isteyen kaynağı raporlayıp diğerlerine geçer.</small>
          <div className="browser-collected-head">
            <div>
              <b>Toplanan ilanlar</b>
              <span>{browserCollectedTotal} görünür kayıt · {visibleBrowserCollected.length} sonuç gösteriliyor</span>
            </div>
            <div className="browser-collected-tools">
              <button className="ghost" type="button" disabled={browserCleanupBusy} onClick={() => { void cleanupBrowserResults(); }}>{browserCleanupBusy ? "Ayıklanıyor…" : "Bozukları ayıkla"}</button>
              <button className="ghost" type="button" disabled={browserCleanupBusy || !selectedProfile} onClick={() => { void cleanupStaleBrowserResults(); }}>Yorumsuzları kaldır</button>
              <button className="ghost" type="button" onClick={() => { void refreshBrowserCollected(); }}>Yenile</button>
            </div>
          </div>
          <small className="agent-score-note">Uygunluk puanı ve yıldızlar “bu ilana ne kadar uygunum?” sorusunu yanıtlar. “Analiz kanıtı” yalnızca ajanın ilan metninden yaptığı çıkarımın güvenilirliğidir; 0 uygunluk + yüksek kanıt, ajanın olumsuz karardan emin olduğu anlamına gelir.</small>
          <div className="browser-result-filters">
            <input value={browserQuery} onChange={(event) => setBrowserQuery(event.target.value)} placeholder="İlan, şirket, konum veya rol ara" />
            <select value={browserRoleFilter} onChange={(event) => setBrowserRoleFilter(event.target.value)}>
              <option value="all">Tüm arama rolleri</option>
              {browserSearchRoles.map((role) => <option key={role} value={role}>{role}</option>)}
            </select>
            <select value={browserProviderFilter} onChange={(event) => setBrowserProviderFilter(event.target.value)}>
              <option value="all">Tüm kaynaklar</option>
              {browserProviders.map((provider) => <option key={provider} value={provider}>{label(provider)}</option>)}
            </select>
            <select value={browserRecommendationFilter} onChange={(event) => setBrowserRecommendationFilter(event.target.value)}>
              <option value="all">Tüm öneriler</option>
              <option value="strong_apply">Güçlü başvuru</option>
              <option value="apply">Başvur</option>
              <option value="review">İncele</option>
              <option value="skip">Atla</option>
              <option value="pending">Değerlendirme bekliyor</option>
            </select>
            <select value={browserApplicationFilter} onChange={(event) => setBrowserApplicationFilter(event.target.value as BrowserApplicationFilter)}>
              <option value="all">Tüm başvuru durumları</option>
              <option value="untracked">Takibe alınmamış</option>
              {Object.keys(applicationTransitions).map((status) => <option key={status} value={status}>{label(status)}</option>)}
            </select>
            <select value={browserSort} onChange={(event) => setBrowserSort(event.target.value as BrowserResultSort)}>
              <option value="fit_desc">Uygunluk: yüksekten düşüğe</option>
              <option value="confidence_desc">Analiz kanıtı: yüksekten düşüğe</option>
              <option value="newest">En yeni toplanan</option>
            </select>
          </div>
          {lastDismissed && (
            <div className="browser-dismiss-undo">
              <span>“{lastDismissed.title}” listeden gizlendi.</span>
              <button className="ghost" type="button" disabled={browserDismissBusy === lastDismissed.candidateId} onClick={() => { void restoreLastDismissed(); }}>Geri al</button>
            </div>
          )}
          {browserResultsError && <p className="native-search-error">{browserResultsError}</p>}
          {visibleBrowserCollected.length > 0 ? (
            <div className="browser-collected-list">
              {visibleBrowserCollected.map((item) => (
                <article key={item.candidate_id}>
                  <div className="browser-collected-provider">
                    <span>{label(item.provider)}</span>
                    {item.search_roles.map((role) => <small key={role}>{role}</small>)}
                  </div>
                  <div className="browser-collected-copy">
                    <b>{item.title}</b>
                    <span>{item.company_name}{item.location ? ` · ${item.location}` : ""} · {label(item.work_mode)}</span>
                    {item.fit_summary && <p>{item.fit_summary}</p>}
                    {item.assessment_state === "ready" && (
                      <details className="agent-fit-details">
                        <summary>Gereksinim karşılaştırmasını göster</summary>
                        {item.required_experience_min !== null && (
                          <span>İstenen deneyim: en az {item.required_experience_min} yıl{item.experience_gap ? ` · ${item.experience_gap} yıl açık` : " · deneyim uyuyor"}</span>
                        )}
                        {item.matched_requirements.length > 0 && <span><b>Eşleşen:</b> {item.matched_requirements.join(" · ")}</span>}
                        {item.missing_requirements.length > 0 && <span><b>Eksik:</b> {item.missing_requirements.join(" · ")}</span>}
                        {item.preferred_requirements.length > 0 && <span><b>Tercih edilen:</b> {item.preferred_requirements.join(" · ")}</span>}
                        {item.hard_blockers.length > 0 && <span><b>Engel:</b> {item.hard_blockers.join(" · ")}</span>}
                      </details>
                    )}
                  </div>
                  <div className="browser-collected-actions">
                    {item.assessment_state === "ready" ? (
                      <div className={`agent-fit-score ${item.fit_recommendation ?? "review"}`}>
                        <strong>{item.fit_score}<small>/100 uygunluk</small></strong>
                        <span className="agent-fit-stars" aria-label={`Uygunluk ${displayedFitStars(item)} / 5 yıldız`}>{"★".repeat(displayedFitStars(item))}{"☆".repeat(5 - displayedFitStars(item))}</span>
                        <small className="agent-fit-decision">Öneri: {label(item.fit_recommendation ?? "review")}</small>
                        <small className={`agent-fit-confidence ${item.fit_confidence ?? "low"}`}>Analiz kanıtı: {confidenceLevel(item.fit_confidence)}</small>
                      </div>
                    ) : (
                      <small>{item.assessment_state === "pending" ? "Yerel ajan değerlendirmesi bekleniyor" : "İlan metni okunamadı"}</small>
                    )}
                    <small>{label(item.status)}</small>
                    <a href={item.listing_url} target="_blank" rel="noopener noreferrer" onClick={() => onViewed(item.candidate_id)}>İlanı aç ↗</a>
                    <ApplicationQuickActions
                      status={applicationByCandidate.get(item.candidate_id)?.status}
                      busy={applicationBusy === item.candidate_id}
                      onUpdate={(status) => onApplication(item.candidate_id, status)}
                    />
                    <button className="browser-dismiss-button" type="button" disabled={browserDismissBusy === item.candidate_id} onClick={() => { void dismissBrowserResult(item); }}>
                      {browserDismissBusy === item.candidate_id ? "Gizleniyor…" : "Listeden gizle"}
                    </button>
                  </div>
                </article>
              ))}
            </div>
          ) : !browserResultsError ? (
            <p className="browser-collected-empty">{browserCollected.length > 0 ? "Seçili filtrelere uyan ilan yok." : "Henüz toplanmış ilan yok."}</p>
          ) : null}
        </section>

        <section className="native-search-panel">
          <div className="native-search-copy">
            <p className="eyebrow">TARAYICIDA KENDİN ARA</p>
            <h3>12 iş sitesinin kendi aramasını aç</h3>
            <p>Otomatik taramadan bağımsızdır ve arama kotası harcamaz. LinkedIn, Kariyer.net, Indeed, Glassdoor, Techcareer, Yenibiriş, SecretCV, Toptalent ve dört remote kaynak için güvenli bağlantılar hazırlanır.</p>
          </div>
          <div className="native-search-controls">
            <label>Rol
              <select value={nativeRole} onChange={(event) => setNativeRole(event.target.value)}>
                {roles.map((role) => <option key={role} value={role}>{role}</option>)}
              </select>
            </label>
            <label>Konum
              <select value={nativeLocation} onChange={(event) => setNativeLocation(event.target.value)}>
                <option value="">Kaynak sitede seç</option>
                {locations.map((location) => <option key={location} value={location}>{location}</option>)}
              </select>
            </label>
            <button className="ghost" type="button" onClick={() => { void prepareNativeLinks(); }} disabled={!nativeRole || nativeLoading}>
              {nativeLoading ? "Hazırlanıyor…" : "12 kaynak bağlantısını hazırla"}
            </button>
          </div>
          {nativeError && <p className="native-search-error">{nativeError}</p>}
          {nativeUnavailable.includes("ats") && <p className="native-search-note">Resmî ATS tek bir pazar arama sayfası olmadığı için mevcut otomatik taramada kalır.</p>}
          {nativeLinks.length > 0 && (
            <div className="native-link-grid">
              {nativeLinks.map((item) => (
                <article key={item.provider}>
                  <div>
                    <strong>{item.label}</strong>
                    <span>{item.query_prefilled ? "Rol hazır" : "Filtreyi sitede tamamla"}{item.location_prefilled ? " · Konum hazır" : ""}</span>
                  </div>
                  <p>{item.note}</p>
                  <a href={item.url} target="_blank" rel="noopener noreferrer">Kaynakta aç ↗</a>
                </article>
              ))}
            </div>
          )}
          <form className="manual-job-import" onSubmit={submitManualJob}>
            <div className="manual-job-heading">
              <div><strong>Bulduğun ilanı kuyruğa ekle</strong><span>İlan sayfasındaki bilgileri kopyala; yalnızca desteklenen kaynak bağlantıları kabul edilir.</span></div>
              <span>İnsan doğrulamalı</span>
            </div>
            <div className="manual-job-grid">
              <label className="wide">İlan bağlantısı<input type="url" required maxLength={2048} value={manualUrl} onChange={(event) => setManualUrl(event.target.value)} placeholder="https://…" /></label>
              <label>İlan başlığı<input required maxLength={300} value={manualTitle} onChange={(event) => setManualTitle(event.target.value)} /></label>
              <label>Şirket<input required maxLength={500} value={manualCompany} onChange={(event) => setManualCompany(event.target.value)} /></label>
              <label>Konum<input maxLength={500} value={manualLocation} onChange={(event) => setManualLocation(event.target.value)} placeholder="İsteğe bağlı" /></label>
              <label>Çalışma biçimi<select value={manualWorkMode} onChange={(event) => setManualWorkMode(event.target.value as typeof manualWorkMode)}><option value="unknown">Belirsiz</option><option value="remote">Uzaktan</option><option value="hybrid">Hibrit</option><option value="onsite">İş yerinde</option></select></label>
            </div>
            <label className="manual-confirm"><input type="checkbox" checked={manualConfirmed} onChange={(event) => setManualConfirmed(event.target.checked)} /><span>İlan sayfasını açtım ve ilanın şu anda görünür olduğunu doğruladım.</span></label>
            <div className="manual-job-actions">
              <div>{manualError && <span className="native-search-error">{manualError}</span>}{manualMessage && <span className="manual-job-success">{manualMessage}</span>}</div>
              <button className="primary" type="submit" disabled={manualBusy || !manualConfirmed || !manualUrl.trim() || !manualTitle.trim() || !manualCompany.trim()}>{manualBusy ? "Ekleniyor…" : "İnceleme kuyruğuna ekle"}</button>
            </div>
          </form>
        </section>
      </section>

      <section className="panel general-results-panel">
        <div className="general-results-summary">
          <div><p className="eyebrow">SON ARAMA</p><h2>Arama sonuçları</h2></div>
          <div className="result-stat-strip">
            <span><b>{run?.result.raw_result_count ?? 0}</b> tarandı</span>
            <span><b>{run?.result.matched_candidate_count ?? 0}</b> role uydu</span>
            <span><b>{run?.result.activity_counts?.active ?? 0}</b> aktif</span>
            <span><b>{run?.result.activity_counts?.unknown ?? 0}</b> belirsiz</span>
          </div>
        </div>

        {sourceDiagnostics.length > 0 && (
          <div className="source-diagnostics" aria-label="Kaynak tarama durumu">
            {sourceDiagnostics.map((item) => (
              <article className={`source-diagnostic ${item.outcome}`} key={item.source}>
                <div><strong>{label(item.source)}</strong><span>{label(item.outcome)}</span></div>
                <dl>
                  <div><dt>Sorgu</dt><dd>{item.query_count}</dd></div>
                  <div><dt>Ham</dt><dd>{item.raw_result_count}</dd></div>
                  <div><dt>Okunabilen</dt><dd>{item.normalized_result_count}</dd></div>
                  <div><dt>Uyan</dt><dd>{item.accepted_count}</dd></div>
                </dl>
                {item.fallback_query_count > 0 && <small>{item.fallback_query_count} gevşetilmiş sorgu denendi</small>}
              </article>
            ))}
          </div>
        )}

        {Object.keys(run?.result.exclusion_counts ?? {}).length > 0 && (
          <div className="exclusion-breakdown">
            <strong>Eleme nedenleri</strong>
            {Object.entries(run?.result.exclusion_counts ?? {}).map(([reason, count]) => (
              <span key={reason}><b>{count}</b> {label(reason)}</span>
            ))}
          </div>
        )}

        <div className="result-filters">
          <input value={resultQuery} maxLength={100} onChange={(event) => setResultQuery(event.target.value)} placeholder="Başlık, şirket veya konum ara" />
          <select value={providerFilter} onChange={(event) => setProviderFilter(event.target.value)}><option value="all">Tüm kaynaklar</option>{providers.map((provider) => <option key={provider} value={provider}>{label(provider)}</option>)}</select>
          <select value={dispositionFilter} onChange={(event) => setDispositionFilter(event.target.value)}><option value="all">Tüm sonuç durumları</option>{dispositions.map((value) => <option key={value} value={value}>{label(value)}</option>)}</select>
          <select value={viewFilter} onChange={(event) => setViewFilter(event.target.value)}><option value="all">Yeni + incelenen</option><option value="new">Yalnızca yeni</option><option value="viewed">Yalnızca incelenen</option></select>
        </div>

        {run?.status === "failed" ? (
          <div className="empty-state compact"><div>!</div><h3>Arama tamamlanamadı</h3><p>{errorMessage(new ApiError(500, run.error_code ?? "search_failed"))}</p></div>
        ) : candidates.length === 0 ? (
          <div className="empty-state compact"><div>⌕</div><h3>{run ? "Uygun ilan bulunamadı" : "Henüz arama yapılmadı"}</h3><p>{run ? ((run.result.raw_result_count ?? 0) === 0 ? "Seçilen kaynaklar ilk sorgularda ve kontrollü gevşetmelerde hiç ham ilan döndürmedi. Kaynak kartları hangi sitenin yanıt vermediğini gösteriyor." : `${run.result.raw_result_count ?? 0} ham sonuç rol, konum veya profil süzgecinde elendi; kaynak kartlarından ayrıntıyı görebilirsin.`) : "Rol ve kaynakları seçip ilk bağımsız genel taramayı başlatabilirsin."}</p></div>
        ) : filteredCandidates.length === 0 ? (
          <div className="empty-state compact"><div>○</div><h3>Filtreye uyan sonuç yok</h3><p>Sonuç filtrelerinden birini gevşetebilirsin.</p></div>
        ) : (
          <div className="general-result-list">
            {filteredCandidates.map((candidate) => (
              <article className={`general-result-card ${candidate.operator_viewed_at ? "viewed" : "unviewed"}`} key={candidate.candidate_id}>
                <span className={`score score-${candidate.recommendation}`}>{candidate.score}</span>
                <div className="general-result-main"><strong>{candidate.title}</strong><span>{candidate.company_name}</span><small>{candidate.location ?? "Konum bilinmiyor"} · {label(candidate.provider)}</small></div>
                <div className="general-result-actions">
                  <span className={`view-state ${candidate.operator_viewed_at ? "viewed" : "new"}`}>{candidate.operator_viewed_at ? "İncelendi" : "Yeni"}</span>
                  <b className={`disposition ${candidate.disposition}`}>{label(candidate.disposition)}</b>
                  <a href={candidate.listing_url} target="_blank" rel="noopener noreferrer" onClick={() => onViewed(candidate.candidate_id)}>İlanı aç ↗</a>
                  <ApplicationQuickActions
                    status={applicationByCandidate.get(candidate.candidate_id)?.status}
                    busy={applicationBusy === candidate.candidate_id}
                    onUpdate={(status) => onApplication(candidate.candidate_id, status)}
                  />
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function JobRow({ job, active, onSelect }: {
  job: JobItem; active: boolean; onSelect: (id: string) => void;
}) {
  return (
    <button className={`job-row ${active ? "selected" : ""} ${job.operator_viewed_at ? "viewed" : "unviewed"}`} onClick={() => onSelect(job.candidate_id)}>
      <span className={`score score-${job.recommendation}`}>{job.score}</span>
      <span className="job-main">
        <strong>{job.title}</strong>
        <span>{job.company_name}</span>
        <small>{job.location ?? "Konum bilinmiyor"} · {label(job.work_mode)}</small>
      </span>
      <span className="job-row-status">
        {!job.operator_viewed_at && <em className="view-state new">Yeni</em>}
        {job.operator_viewed_at && <em className="view-state viewed">İncelendi</em>}
        <span className={`recommendation ${job.recommendation}`}>{label(job.recommendation)}</span>
      </span>
    </button>
  );
}

function DetailPanel({ token, detail, onChanged, application, applicationBusy, onApplication }: {
  token: string;
  detail: JobDetail;
  onChanged: () => Promise<void>;
  application?: JobApplication;
  applicationBusy: boolean;
  onApplication: (status: ApplicationStatus) => void;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const [companyName, setCompanyName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setConfirmed(false);
    setCompanyName("");
    setError(null);
  }, [detail.candidate_id]);

  async function approve() {
    if (!confirmed || busy) return;
    if (detail.company_identity_required && !companyName.trim()) {
      setError("İşveren adını elle doğrulayıp yazmalısın.");
      return;
    }
    setBusy(true); setError(null);
    try {
      await approveJob(
        token,
        detail.candidate_id,
        detail.company_identity_required ? companyName.trim() : null,
      );
      await onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally { setBusy(false); }
  }

  async function reject() {
    if (busy || !window.confirm("Bu ilanı kalıcı olarak reddetmek istiyor musun?")) return;
    setBusy(true); setError(null);
    try {
      await rejectJob(token, detail.candidate_id);
      await onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally { setBusy(false); }
  }

  return (
    <aside className="detail-panel">
      <div className="detail-head">
        <div><span className="provider">{detail.provider}</span><h2>{detail.title}</h2><p>{detail.company_name}</p></div>
        <a className="external-link" href={detail.listing_url} target="_blank" rel="noopener noreferrer">İlanı aç ↗</a>
      </div>
      <div className="meta-grid">
        <div><span>Konum</span><strong>{detail.location ?? "Bilinmiyor"}</strong></div>
        <div><span>Çalışma</span><strong>{label(detail.work_mode)}</strong></div>
        <div><span>Tür</span><strong>{label(detail.employment_type)}</strong></div>
        <div><span>Yayın</span><strong>{formatDate(detail.published_at)}</strong></div>
      </div>
      <section className="detail-application-box">
        <div><strong>Başvuru takibi</strong><span>Bu kayıt ilan inceleme kararından ayrı tutulur.</span></div>
        <ApplicationQuickActions status={application?.status} busy={applicationBusy} onUpdate={onApplication} />
      </section>
      <section className="verification-box">
        <div className={`activity-dot ${detail.activity_state}`} />
        <div><strong>Aktiflik: {label(detail.activity_state)}</strong><span>{label(detail.activity_code)}</span></div>
      </section>
      {detail.snippet && <section className="detail-section"><h3>İlan özeti</h3><p>{detail.snippet}</p></section>}
      <section className="detail-section">
        <h3>Kanıt zinciri</h3>
        {detail.evidence.length === 0 ? <p className="muted">Kanıt kaydı yok.</p> : (
          <div className="evidence-list">
            {detail.evidence.map((item, index) => (
              <dl key={`${detail.candidate_id}-${index}`}>
                {Object.entries(item).map(([key, value]) => (
                  <div key={key}><dt>{label(key)}</dt><dd>{String(value ?? "—")}</dd></div>
                ))}
              </dl>
            ))}
          </div>
        )}
      </section>
      {detail.company_identity_required && (
        <label className="field">Doğrulanmış işveren adı
          <input value={companyName} maxLength={500} onChange={(event) => setCompanyName(event.target.value)} />
        </label>
      )}
      <label className="confirmation">
        <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
        <span>İlanı yeni sekmede açtım; işvereni ve hâlâ aktif olduğunu doğruladım.</span>
      </label>
      {error && <div className="inline-error">{error}</div>}
      <div className="action-row">
        <button className="danger" onClick={reject} disabled={busy}>Reddet</button>
        <button className="primary" onClick={approve} disabled={!confirmed || busy}>Onayla ve ilana dönüştür</button>
      </div>
    </aside>
  );
}

const companyStatuses: CompanyProfileStatus[] = [
  "all",
  "unprofiled",
  "candidate_found",
  "needs_review",
  "verified",
  "not_found",
];

function CompanyLinks({ company }: { company: CompanyItem }) {
  const links = [
    ["Web sitesi", company.official_website_url],
    ["Kariyer", company.careers_url],
    ["LinkedIn", company.official_linkedin_url],
  ].filter((item): item is [string, string] => Boolean(item[1]));

  if (links.length === 0) return <span className="company-no-link">Bağlantı yok</span>;
  return (
    <div className="company-links">
      {links.map(([title, url]) => (
        <a key={title} href={url} target="_blank" rel="noopener noreferrer">
          {title} ↗
        </a>
      ))}
    </div>
  );
}

const companyRejectionReasons: CompanyRejectionReason[] = [
  "wrong_company",
  "unsafe_or_invalid_url",
  "insufficient_evidence",
];

function CompanyDetailModal({ token, detail, onClose, onChanged }: {
  token: string;
  detail: CompanyDetail;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const [reason, setReason] = useState<CompanyRejectionReason>("insufficient_evidence");
  const [busy, setBusy] = useState(false);
  const [discoveryBusy, setDiscoveryBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const interactionLocked = busy || discoveryBusy;

  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape" && !interactionLocked) onClose();
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [interactionLocked, onClose]);

  async function approve() {
    if (!confirmed || interactionLocked) return;
    setBusy(true);
    setError(null);
    try {
      await approveCompany(token, detail.company_id);
      await onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function reject() {
    if (interactionLocked) return;
    const accepted = window.confirm(
      `Bu aday profili “${label(reason)}” nedeniyle reddetmek istiyor musun? Doğrulanmamış bağlantılar kayıttan kaldırılacak.`,
    );
    if (!accepted) return;
    setBusy(true);
    setError(null);
    try {
      await rejectCompany(token, detail.company_id, reason);
      await onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function discover() {
    if (interactionLocked || detail.profile_status === "verified") return;
    const accepted = window.confirm(
      "Bu şirket için kontrollü web araması ve yerel değerlendirme çalıştırılsın mı? İşlem biraz sürebilir.",
    );
    if (!accepted) return;
    setDiscoveryBusy(true);
    setError(null);
    try {
      const result = await discoverCompany(token, detail.company_id);
      if (result.status === "failed") {
        setError(errorMessage(new ApiError(502, result.error_code ?? "company_discovery_failed")));
        return;
      }
      await onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setDiscoveryBusy(false);
    }
  }

  const sourceScans = [
    {
      title: "Kariyer kaynağı taraması",
      checked: detail.career_sources_last_checked_at,
      next: detail.career_sources_next_check_at,
      outcome: detail.career_sources_last_outcome,
      error: detail.career_sources_last_error_code,
      count: detail.career_sources_candidate_count,
    },
    {
      title: "İlan panosu taraması",
      checked: detail.job_boards_last_checked_at,
      next: detail.job_boards_next_check_at,
      outcome: detail.job_boards_last_outcome,
      error: detail.job_boards_last_error_code,
      count: detail.job_boards_candidate_count,
    },
  ];

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={interactionLocked ? undefined : onClose}>
      <section
        className="company-detail-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="company-detail-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <p className="eyebrow">ŞİRKET PROFİLİ</p>
            <h2 id="company-detail-title">{detail.brand_name ?? detail.name}</h2>
            {detail.brand_name && <p>{detail.name}</p>}
          </div>
          <button className="modal-close" onClick={onClose} disabled={interactionLocked} aria-label="Kapat">×</button>
        </header>
        <div className="company-detail-body">
          <main>
            <div className="company-detail-status">
              <b className={`company-status ${detail.profile_status}`}>{label(detail.profile_status)}</b>
              {detail.confidence && <span>{label(detail.confidence)}</span>}
              {detail.needs_review && <span className="data-warning">Ana kayıt incelenmeli</span>}
            </div>
            <dl className="company-facts">
              <div><dt>Sektör</dt><dd>{detail.sector ?? "Belirtilmemiş"}</dd></div>
              <div><dt>Teknopark</dt><dd>{detail.teknoparks.join(", ") || "Kayıt yok"}</dd></div>
              <div><dt>Son profil araması</dt><dd>{formatDate(detail.last_searched_at)}</dd></div>
              <div><dt>Son doğrulama</dt><dd>{formatDate(detail.last_verified_at)}</dd></div>
              <div><dt>Arama sağlayıcısı</dt><dd>{detail.search_provider ?? "Bilinmiyor"}</dd></div>
              <div><dt>Değerlendirici</dt><dd>{detail.evaluator_model ?? "Bilinmiyor"}</dd></div>
            </dl>
            <section className="detail-section">
              <h3>Resmî bağlantılar</h3>
              <CompanyLinks company={detail} />
            </section>
            <section className="detail-section">
              <h3>Kanıtlar</h3>
              {detail.evidence.length === 0 ? <p className="muted">Kanıt kaydı yok.</p> : (
                <div className="evidence-list company-evidence-list">
                  {detail.evidence.map((item, index) => (
                    <dl key={`${detail.company_id}-${index}`}>
                      {Object.entries(item).map(([key, value]) => (
                        <div key={key}><dt>{label(key)}</dt><dd>{String(value ?? "—")}</dd></div>
                      ))}
                    </dl>
                  ))}
                </div>
              )}
            </section>
          </main>
          <aside>
            <h3>Tarama durumu</h3>
            <div className="company-scan-list">
              {sourceScans.map((scan) => (
                <article key={scan.title}>
                  <strong>{scan.title}</strong>
                  <span>{scan.outcome ? label(scan.outcome) : "Henüz çalışmadı"}</span>
                  <dl>
                    <div><dt>Son kontrol</dt><dd>{formatDate(scan.checked)}</dd></div>
                    <div><dt>Sonraki</dt><dd>{formatDate(scan.next)}</dd></div>
                    <div><dt>Aday</dt><dd>{scan.count}</dd></div>
                    {scan.error && <div><dt>Hata kodu</dt><dd>{scan.error}</dd></div>}
                  </dl>
                </article>
              ))}
            </div>
            {detail.profile_status !== "verified" && (
              <section className="company-discovery-box">
                <h3>{detail.profile_status === "unprofiled" ? "Profil keşfi" : "Profili yeniden ara"}</h3>
                <p>Yalnızca bu şirket için en fazla üç kontrollü sorgu çalıştırılır. Ham model çıktısı arayüze aktarılmaz.</p>
                <button className="ghost full" onClick={discover} disabled={interactionLocked}>
                  {discoveryBusy ? "Profil aranıyor…" : "Şirket profilini ara"}
                </button>
              </section>
            )}
            {detail.reviewable ? (
              <section className="company-review-box">
                <h3>İnsan incelemesi</h3>
                <p>Bağlantıları yeni sekmede açıp şirket adı ve alan adının aynı kuruluşa ait olduğunu doğrula.</p>
                <label className="confirmation">
                  <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
                  <span>Şirket kimliğini ve gösterilen resmî bağlantıları doğruladım.</span>
                </label>
                <button className="primary full" onClick={approve} disabled={!confirmed || interactionLocked}>
                  {busy ? "İşleniyor…" : "Profili doğrula"}
                </button>
                <label className="company-reject-reason">
                  Red nedeni
                  <select value={reason} onChange={(event) => setReason(event.target.value as CompanyRejectionReason)} disabled={interactionLocked}>
                    {companyRejectionReasons.map((value) => <option key={value} value={value}>{label(value)}</option>)}
                  </select>
                </label>
                <button className="danger full" onClick={reject} disabled={interactionLocked}>Aday profili reddet</button>
              </section>
            ) : (
              <div className="company-review-locked">
                {detail.profile_status === "verified"
                  ? "Bu profil doğrulanmış ve red işlemine karşı korunuyor."
                  : "Bu kayıt şu anda insan incelemesi beklemiyor."}
              </div>
            )}
            {error && <div className="inline-error">{error}</div>}
          </aside>
        </div>
      </section>
    </div>
  );
}

function CompaniesView({ token, onError, onReviewed }: {
  token: string;
  onError: (message: string | null) => void;
  onReviewed: () => Promise<void>;
}) {
  const [page, setPage] = useState<CompanyPage | null>(null);
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [profileStatus, setProfileStatus] = useState<CompanyProfileStatus>("all");
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<CompanyDetail | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [reloadVersion, setReloadVersion] = useState(0);
  const [discoveryRun, setDiscoveryRun] = useState<CompanyDiscoveryRun | null>(null);
  const [discoveryBusy, setDiscoveryBusy] = useState(false);

  const discoveryActive = discoveryRun?.status === "queued"
    || discoveryRun?.status === "running"
    || discoveryRun?.status === "pause_requested";

  useEffect(() => {
    let cancelled = false;
    setBusy(true);
    onError(null);
    getCompanies(token, query, profileStatus, offset)
      .then((data) => { if (!cancelled) setPage(data); })
      .catch((caught) => { if (!cancelled) onError(errorMessage(caught)); })
      .finally(() => { if (!cancelled) setBusy(false); });
    return () => { cancelled = true; };
  }, [token, query, profileStatus, offset, onError, reloadVersion]);

  useEffect(() => {
    let cancelled = false;
    getLatestCompanyDiscoveryRun(token)
      .then((run) => { if (!cancelled) setDiscoveryRun(run); })
      .catch((caught) => { if (!cancelled) onError(errorMessage(caught)); });
    return () => { cancelled = true; };
  }, [token, onError]);

  useEffect(() => {
    if (!discoveryActive) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const run = await getLatestCompanyDiscoveryRun(token);
        if (cancelled) return;
        setDiscoveryRun(run);
        if (run && (run.status === "succeeded" || run.status === "failed")) {
          setReloadVersion((current) => current + 1);
          await onReviewed();
        }
      } catch (caught) {
        if (!cancelled) onError(errorMessage(caught));
      }
    };
    const timer = window.setInterval(poll, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [token, discoveryActive, onError, onReviewed]);

  async function openCompany(companyId: string) {
    if (detailBusy) return;
    setDetailBusy(true);
    onError(null);
    try {
      setDetail(await getCompanyDetail(token, companyId));
    } catch (caught) {
      onError(errorMessage(caught));
    } finally {
      setDetailBusy(false);
    }
  }

  async function afterCompanyReview() {
    setDetail(null);
    setReloadVersion((current) => current + 1);
    await onReviewed();
  }

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    setOffset(0);
    setQuery(queryInput.trim());
  }

  async function startBulkDiscovery() {
    if (discoveryBusy || discoveryActive) return;
    if (!window.confirm(
      "Profili olmayan tüm şirketler taransın mı? En fazla 2.200 harici arama sorgusu kullanılacak; işlem duraklatılabilir.",
    )) return;
    setDiscoveryBusy(true);
    onError(null);
    try {
      setDiscoveryRun(await startCompanyDiscoveryRun(token));
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "company_discovery_run_active") {
        setDiscoveryRun(await getLatestCompanyDiscoveryRun(token));
      } else {
        onError(errorMessage(caught));
      }
    } finally {
      setDiscoveryBusy(false);
    }
  }

  async function pauseBulkDiscovery() {
    if (!discoveryRun || discoveryBusy || !discoveryActive) return;
    setDiscoveryBusy(true);
    onError(null);
    try {
      setDiscoveryRun(await pauseCompanyDiscoveryRun(token, discoveryRun.run_id));
    } catch (caught) {
      onError(errorMessage(caught));
    } finally {
      setDiscoveryBusy(false);
    }
  }

  async function resumeBulkDiscovery() {
    if (!discoveryRun || discoveryBusy || discoveryRun.status !== "paused") return;
    setDiscoveryBusy(true);
    onError(null);
    try {
      setDiscoveryRun(await resumeCompanyDiscoveryRun(token, discoveryRun.run_id));
    } catch (caught) {
      onError(errorMessage(caught));
    } finally {
      setDiscoveryBusy(false);
    }
  }

  const limit = page?.limit ?? 25;
  const total = page?.total ?? 0;
  const rangeStart = total === 0 ? 0 : (page?.offset ?? 0) + 1;
  const rangeEnd = Math.min((page?.offset ?? 0) + (page?.items.length ?? 0), total);
  const discoveryProcessed = (discoveryRun?.succeeded_count ?? 0)
    + (discoveryRun?.failed_count ?? 0);
  const discoveryProgress = discoveryRun?.total_count
    ? Math.min(100, Math.round((discoveryProcessed / discoveryRun.total_count) * 100))
    : 0;
  const discoveredProfiles = discoveryRun
    ? Object.values(discoveryRun.profile_counts).reduce((sum, count) => sum + count, 0)
    : 0;

  return (
    <section className="companies-panel">
      <div className="companies-toolbar">
        <div>
          <p className="eyebrow">ŞİRKET VERİ TABANI</p>
          <h2>Şirketler</h2>
          <span>{total.toLocaleString("tr-TR")} kayıt</span>
        </div>
        <form className="company-filters" onSubmit={submitSearch}>
          <label>
            Profil durumu
            <select
              value={profileStatus}
              onChange={(event) => {
                setOffset(0);
                setProfileStatus(event.target.value as CompanyProfileStatus);
              }}
            >
              {companyStatuses.map((status) => <option value={status} key={status}>{label(status)}</option>)}
            </select>
          </label>
          <label>
            Şirket ara
            <span className="search-field">
              <input
                type="search"
                value={queryInput}
                maxLength={100}
                onChange={(event) => setQueryInput(event.target.value)}
                placeholder="Şirket veya marka adı"
              />
              <button className="ghost" type="submit">Ara</button>
            </span>
          </label>
        </form>
      </div>
      <section className={`bulk-discovery ${discoveryRun?.status ?? "idle"}`}>
        <div className="bulk-discovery-head">
          <div>
            <p className="eyebrow">TOPLU PROFİL KEŞFİ</p>
            <h3>Profilsiz şirketleri güvenli ve kalıcı kuyrukta tara</h3>
            <span>
              Kesin eşleşmeler doğrudan işlenir; değerlendirme modeli yalnızca belirsiz sonuçlarda kullanılır.
            </span>
          </div>
          <div className="bulk-discovery-actions">
            {discoveryRun?.status === "paused" ? (
              <button className="primary" onClick={resumeBulkDiscovery} disabled={discoveryBusy}>Taramayı sürdür</button>
            ) : discoveryActive ? (
              <button className="ghost" onClick={pauseBulkDiscovery} disabled={discoveryBusy || discoveryRun?.status === "pause_requested"}>
                {discoveryRun?.status === "pause_requested" ? "Duraklatılıyor…" : "Duraklat"}
              </button>
            ) : (
              <button className="primary" onClick={startBulkDiscovery} disabled={discoveryBusy}>
                {discoveryBusy ? "Başlatılıyor…" : "Tüm profilsiz şirketleri tara"}
              </button>
            )}
          </div>
        </div>
        {discoveryRun && (
          <div className="bulk-discovery-status">
            <div className="bulk-progress-label">
              <strong>{label(discoveryRun.status)}</strong>
              <span>{discoveryProcessed} / {discoveryRun.total_count} şirket · %{discoveryProgress}</span>
            </div>
            <div className="bulk-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={discoveryProgress}>
              <i style={{ width: `${discoveryProgress}%` }} />
            </div>
            <div className="bulk-stat-grid">
              <span><b>{discoveryRun.queued_count}</b>Sırada</span>
              <span><b>{discoveryRun.running_count}</b>İşleniyor</span>
              <span><b>{discoveredProfiles}</b>Profil sonucu</span>
              <span><b>{discoveryRun.failed_count}</b>Hata</span>
              <span><b>{discoveryRun.query_count}</b>{discoveryRun.query_budget} sorgudan</span>
            </div>
            {discoveryRun.error_code && (
              <p className="bulk-run-error">{errorMessage(new ApiError(500, discoveryRun.error_code))}</p>
            )}
          </div>
        )}
      </section>
      <div className="company-readonly-note">
        Şirket satırındaki “İncele” ile kaynakları ve kanıtları görebilir; yalnızca bekleyen profilleri açık onayla doğrulayabilir veya reddedebilirsin.
      </div>
      {busy && <div className="loading-line company-loading" />}
      <div className="company-table-wrap">
        <table className="company-table">
          <thead><tr><th>Şirket</th><th>Teknopark</th><th>Profil</th><th>Kaynaklar</th><th>Güncelleme</th><th /></tr></thead>
          <tbody>
            {page?.items.map((company) => (
              <tr key={company.company_id}>
                <td>
                  <strong>{company.brand_name ?? company.name}</strong>
                  {company.brand_name && <span>{company.name}</span>}
                  <small>{company.sector ?? "Sektör belirtilmemiş"}</small>
                  {company.needs_review && <b className="data-warning">Ana kayıt incelenmeli</b>}
                </td>
                <td>
                  {company.teknoparks.length > 0
                    ? company.teknoparks.map((park) => <span className="park-tag" key={park}>{park}</span>)
                    : <span className="company-no-link">Kayıt yok</span>}
                </td>
                <td>
                  <b className={`company-status ${company.profile_status}`}>{label(company.profile_status)}</b>
                  {company.confidence && <small>{label(company.confidence)}</small>}
                </td>
                <td><CompanyLinks company={company} /></td>
                <td><small>{formatDate(company.updated_at ?? company.last_verified_at)}</small></td>
                <td><button className="ghost company-open" type="button" disabled={detailBusy} onClick={() => openCompany(company.company_id)}>İncele</button></td>
              </tr>
            ))}
          </tbody>
        </table>
        {!busy && page?.items.length === 0 && (
          <div className="empty-state compact"><div>○</div><h3>Şirket bulunamadı</h3><p>Arama metnini veya profil durumu filtresini değiştirebilirsin.</p></div>
        )}
      </div>
      <footer className="company-pagination">
        <span>{rangeStart}–{rangeEnd} / {total.toLocaleString("tr-TR")}</span>
        <div>
          <button className="ghost" disabled={offset === 0 || busy} onClick={() => setOffset(Math.max(0, offset - limit))}>← Önceki</button>
          <button className="ghost" disabled={offset + limit >= total || busy} onClick={() => setOffset(offset + limit)}>Sonraki →</button>
        </div>
      </footer>
      {detail && (
        <CompanyDetailModal
          token={token}
          detail={detail}
          onClose={() => setDetail(null)}
          onChanged={afterCompanyReview}
        />
      )}
    </section>
  );
}

export default function App() {
  const [token, setToken] = useState("");
  const [view, setView] = useState<View>("overview");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [profile, setProfile] = useState("");
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [applications, setApplications] = useState<JobApplication[]>([]);
  const [applicationBusy, setApplicationBusy] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [includeUnverified, setIncludeUnverified] = useState(false);
  const [searchRun, setSearchRun] = useState<SearchRun | null>(null);
  const [searchStarting, setSearchStarting] = useState(false);
  const [showSearchResults, setShowSearchResults] = useState(false);
  const [searchRoles, setSearchRoles] = useState<string[]>([]);
  const [searchMode, setSearchMode] = useState<SearchMode>("quick");
  const [customRole, setCustomRole] = useState("");
  const [searchLocations, setSearchLocations] = useState<string[]>([]);
  const [locationValue, setLocationValue] = useState("");
  const [searchWorkModes, setSearchWorkModes] = useState<SearchWorkMode[]>([
    "remote", "hybrid", "onsite",
  ]);
  const [searchSources, setSearchSources] = useState<SearchSource[]>(
    generalSearchSources,
  );
  const [maxAgeDays, setMaxAgeDays] = useState(30);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedProfile = useMemo(
    () => profiles.find((item) => item.label === profile) ?? null,
    [profiles, profile],
  );
  const searchRunning = searchRun?.status === "queued" || searchRun?.status === "running";
  const matchedSearchCandidates = searchRun?.result.matched_candidates ?? [];
  const profileRoleKey = [
    ...(selectedProfile?.target_roles ?? []),
    ...(selectedProfile?.secondary_roles ?? []),
    ...(selectedProfile?.tertiary_roles ?? []),
  ].join("\u001f");
  const availableSearchRoles = useMemo(() => {
    const candidates = [
      ...(selectedProfile?.target_roles ?? []),
      ...(selectedProfile?.secondary_roles ?? []),
      ...(selectedProfile?.tertiary_roles ?? []),
      ...commonSearchRoles,
      ...searchRoles,
    ];
    return candidates.filter((role, index) => (
      candidates.findIndex((item) => item.toLocaleLowerCase("tr-TR") === role.toLocaleLowerCase("tr-TR")) === index
    ));
  }, [selectedProfile, searchRoles]);
  const searchBudget = profileSearchBudget(
    searchRoles.length,
    searchMode,
    searchSources.length,
  );
  const applicationByCandidate = useMemo(
    () => new Map(applications.map((item) => [item.candidate_id, item])),
    [applications],
  );

  const refreshBase = useCallback(async (activeToken: string) => {
    const [summaryData, profileData] = await Promise.all([
      getSummary(activeToken), getProfiles(activeToken),
    ]);
    setSummary(summaryData); setProfiles(profileData);
    setProfile((current) => current || profileData[0]?.label || "");
  }, []);

  const refreshJobs = useCallback(async () => {
    if (!token || !profile) { setJobs([]); return; }
    const page = await getJobs(token, profile, includeUnverified);
    setJobs(page.items);
    setSelected((current) => page.items.some((item) => item.candidate_id === current) ? current : null);
  }, [token, profile, includeUnverified]);

  const refreshApplications = useCallback(async () => {
    if (!token || !profile) { setApplications([]); return; }
    const page = await getApplications(token, profile);
    setApplications(page.items);
  }, [token, profile]);

  useEffect(() => {
    if (!token) return;
    setLoading(true); setError(null);
    refreshBase(token).catch((caught) => {
      setError(errorMessage(caught));
      if (caught instanceof ApiError && caught.status === 401) setToken("");
    }).finally(() => setLoading(false));
  }, [token, refreshBase]);

  useEffect(() => {
    if (!token || !profile) return;
    setLoading(true); setError(null);
    refreshJobs().catch((caught) => setError(errorMessage(caught))).finally(() => setLoading(false));
  }, [token, profile, includeUnverified, refreshJobs]);

  useEffect(() => {
    if (!token || !profile) return;
    refreshApplications().catch((caught) => setError(errorMessage(caught)));
  }, [token, profile, refreshApplications]);

  useEffect(() => {
    if (!token || !profile) { setSearchRun(null); return; }
    let cancelled = false;
    getLatestProfileSearch(token, profile)
      .then((run) => {
        if (cancelled) return;
        setSearchRun(run);
        setShowSearchResults(false);
        if (run?.result.requested_roles?.length) {
          setSearchRoles(run.result.requested_roles.slice(0, 10));
        }
        if (run?.result.requested_locations) {
          setSearchLocations(run.result.requested_locations.slice(0, 3));
        }
        if (run?.result.requested_work_modes?.length) {
          setSearchWorkModes(run.result.requested_work_modes);
        }
        if (run?.result.requested_sources?.length) {
          setSearchSources(run.result.requested_sources);
        }
        if (run?.result.max_listing_age_days) {
          setMaxAgeDays(run.result.max_listing_age_days);
        }
        if (run?.result.search_mode) setSearchMode(run.result.search_mode);
      })
      .catch((caught) => { if (!cancelled) setError(errorMessage(caught)); });
    return () => { cancelled = true; };
  }, [token, profile]);

  useEffect(() => {
    setSearchRoles((selectedProfile?.target_roles ?? []).slice(0, 10));
    setCustomRole("");
  }, [profile, profileRoleKey]);

  useEffect(() => {
    if (!token || !profile || !searchRunning) return;
    const timer = window.setInterval(() => {
      getLatestProfileSearch(token, profile).then(async (run) => {
        setSearchRun(run);
        if (run && (run.status === "succeeded" || run.status === "failed")) {
          await Promise.all([refreshBase(token), refreshJobs()]);
        }
      }).catch((caught) => setError(errorMessage(caught)));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [token, profile, searchRunning, refreshBase, refreshJobs]);

  const applyViewedState = useCallback((candidateId: string, viewedAt: string) => {
    setJobs((current) => current.map((job) => (
      job.candidate_id === candidateId
        ? { ...job, operator_viewed_at: viewedAt }
        : job
    )));
    setDetail((current) => (
      current?.candidate_id === candidateId
        ? { ...current, operator_viewed_at: viewedAt }
        : current
    ));
    setSearchRun((current) => {
      if (!current?.result.matched_candidates) return current;
      return {
        ...current,
        result: {
          ...current.result,
          matched_candidates: current.result.matched_candidates.map((candidate) => (
            candidate.candidate_id === candidateId
              ? { ...candidate, operator_viewed_at: viewedAt }
              : candidate
          )),
        },
      };
    });
  }, []);

  const recordViewed = useCallback(async (candidateId: string) => {
    if (!token) return;
    try {
      const result = await markJobViewed(token, candidateId);
      applyViewedState(candidateId, result.operator_viewed_at);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }, [token, applyViewedState]);

  const changeApplication = useCallback(async (
    candidateId: string,
    nextStatus: ApplicationStatus,
  ) => {
    if (!token || !profile || applicationBusy) return;
    setApplicationBusy(candidateId);
    setError(null);
    try {
      const updated = await updateApplication(
        token,
        candidateId,
        profile,
        nextStatus,
      );
      setApplications((current) => {
        const exists = current.some(
          (item) => item.application_id === updated.application_id,
        );
        return exists
          ? current.map((item) => (
              item.application_id === updated.application_id ? updated : item
            ))
          : [updated, ...current];
      });
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setApplicationBusy(null);
    }
  }, [token, profile, applicationBusy]);

  useEffect(() => {
    if (!token || !selected) { setDetail(null); return; }
    let cancelled = false;
    Promise.all([
      getJobDetail(token, selected),
      markJobViewed(token, selected),
    ]).then(([jobDetail, viewed]) => {
      if (cancelled) return;
      setDetail({ ...jobDetail, operator_viewed_at: viewed.operator_viewed_at });
      applyViewedState(selected, viewed.operator_viewed_at);
    }).catch((caught) => {
      if (!cancelled) setError(errorMessage(caught));
    });
    return () => { cancelled = true; };
  }, [token, selected, applyViewedState]);

  async function afterReview() {
    setDetail(null); setSelected(null);
    await Promise.all([refreshBase(token), refreshJobs()]);
  }

  async function startSearch() {
    if (!token || !profile || searchRunning || searchStarting) return;
    if (searchRoles.length < 1 || searchRoles.length > 10) {
      setError("Arama için 1–10 rol seçmelisin.");
      return;
    }
    if (searchSources.length < 1 || searchWorkModes.length < 1) {
      setError("En az bir kaynak ve çalışma biçimi seçmelisin.");
      return;
    }
    const modeLabel = searchMode === "quick" ? "Hızlı" : "Derin";
    if (!window.confirm(`${modeLabel} taramada ${searchRoles.length} rol ve ${searchSources.length} kaynak için en fazla ${searchBudget.queryLimit} sorgu ve ${searchBudget.resultLimit} ham sonuç taransın mı?`)) return;
    setSearchStarting(true); setError(null);
    try {
      setSearchRun(await startProfileSearch(token, profile, searchRoles, searchMode, {
        locations: searchLocations,
        workModes: searchWorkModes,
        sources: searchSources,
        maxAgeDays,
      }));
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "search_already_running") {
        setSearchRun(await getLatestProfileSearch(token, profile));
      } else {
        setError(errorMessage(caught));
      }
    } finally {
      setSearchStarting(false);
    }
  }

  function toggleSearchRole(role: string) {
    setSearchRoles((current) => (
      current.some((item) => item === role)
        ? current.filter((item) => item !== role)
        : current.length < 10 ? [...current, role] : current
    ));
  }

  function addCustomRole(event: FormEvent) {
    event.preventDefault();
    const role = customRole.trim().replace(/\s+/g, " ").slice(0, 100);
    if (!role || searchRoles.length >= 10) return;
    if (!searchRoles.some((item) => item.toLocaleLowerCase("tr-TR") === role.toLocaleLowerCase("tr-TR"))) {
      setSearchRoles((current) => [...current, role]);
    }
    setCustomRole("");
  }

  function addSearchLocation(event: FormEvent) {
    event.preventDefault();
    const location = locationValue.trim().replace(/\s+/g, " ").slice(0, 100);
    if (!location || searchLocations.length >= 3) return;
    if (!searchLocations.some((item) => item.toLocaleLowerCase("tr-TR") === location.toLocaleLowerCase("tr-TR"))) {
      setSearchLocations((current) => [...current, location]);
    }
    setLocationValue("");
  }

  function toggleSearchWorkMode(workMode: SearchWorkMode) {
    setSearchWorkModes((current) => (
      current.includes(workMode)
        ? current.length > 1 ? current.filter((item) => item !== workMode) : current
        : [...current, workMode]
    ));
  }

  function toggleSearchSource(source: SearchSource) {
    setSearchSources((current) => (
      current.includes(source)
        ? current.length > 1 ? current.filter((item) => item !== source) : current
        : generalSearchSources.filter((item) => item === source || current.includes(item))
    ));
  }

  if (!token) return <AuthScreen onConnect={setToken} />;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark small">CA</div><div><strong>Career Agent</strong><span>Operator Console</span></div></div>
        <nav>
          <button className={view === "overview" ? "active" : ""} onClick={() => setView("overview")}><span>⌂</span>Genel bakış</button>
          <button className={view === "search" ? "active" : ""} onClick={() => setView("search")}><span>⌕</span>Genel ilan arama<b>{matchedSearchCandidates.length}</b></button>
          <button className={view === "jobs" ? "active" : ""} onClick={() => setView("jobs")}><span>◎</span>İlan kuyruğu<b>{jobs.length}</b></button>
          <button className={view === "applications" ? "active" : ""} onClick={() => setView("applications")}><span>✓</span>Başvurularım<b>{applications.length}</b></button>
          <button className={view === "companies" ? "active" : ""} onClick={() => setView("companies")}><span>▦</span>Şirketler<b>{summary?.companies ?? 0}</b></button>
        </nav>
        <div className="sidebar-foot"><i /><div><strong>Yerel sistem</strong><span>İnsan onayı etkin</span></div></div>
      </aside>
      <main className="workspace">
        <header className="topbar">
          <div><p className="eyebrow">{todayLabel()}</p><h1>{view === "overview" ? "Günaydın, Aslınur" : view === "search" ? "Genel ilan arama" : view === "jobs" ? "İlan inceleme kuyruğu" : view === "applications" ? "Başvurularım" : "Şirket yönetimi"}</h1></div>
          <div className="top-actions">
            {view !== "companies" && <label>Profil<select value={profile} onChange={(event) => setProfile(event.target.value)}>{profiles.map((item) => <option key={item.label}>{item.label}</option>)}</select></label>}
            <button className="ghost" onClick={() => setToken("")}>Kilitle</button>
          </div>
        </header>
        {error && <div className="global-error">{error}<button onClick={() => setError(null)}>×</button></div>}
        {loading && <div className="loading-line" />}
        {view === "overview" ? (
          <div className="overview-content">
            <section className="metric-grid">
              <MetricCard title="Doğrulanmış şirket" value={summary?.verified_companies ?? 0} note={`${summary?.companies ?? 0} şirket içinde`} tone="mint" />
              <MetricCard title="Aktif ilan" value={summary?.active_postings ?? 0} note="Onaylanmış ilan kaydı" />
              <MetricCard title="İnceleme bekleyen" value={summary?.pending_candidates ?? 0} note={`${summary?.verified_active_candidates ?? 0} aktifliği doğrulanmış`} tone="amber" />
              <MetricCard title="Yeni eşleşme" value={summary?.new_matches ?? 0} note="Profil puanı hesaplanmış" />
            </section>
            <section className="dashboard-grid">
              <article className="panel focus-panel">
                <div className="panel-head"><div><p className="eyebrow">BUGÜNÜN ODAĞI</p><h2>Doğrulanmış fırsatlar</h2></div><button className="text-button" onClick={() => setView("jobs")}>Tümünü gör →</button></div>
                {jobs.length === 0 ? (
                  <div className="empty-state"><div>✓</div><h3>Aktif ve doğrulanmış ilan yok</h3><p>Kapalı veya konumu uygunsuz ilanlar bu kuyruğa alınmadı.</p></div>
                ) : jobs.slice(0, 4).map((job) => <JobRow key={job.candidate_id} job={job} active={false} onSelect={(id) => { setSelected(id); setView("jobs"); }} />)}
              </article>
              <article className="panel profile-panel">
                <p className="eyebrow">AKTİF PROFİL</p><h2>{selectedProfile?.label ?? "Profil yok"}</h2>
                <div className="role-search-editor">
                  <div className="role-editor-head"><span>Genel tarama rolleri</span><small>{searchRoles.length}/10 seçili</small></div>
                  <div className="role-cloud selectable">{availableSearchRoles.map((role) => {
                    const selectedRole = searchRoles.includes(role);
                    const custom = !(selectedProfile?.target_roles ?? []).includes(role);
                    return <button type="button" className={selectedRole ? "selected" : ""} key={role} onClick={() => toggleSearchRole(role)}>{role}{custom && selectedRole ? " ×" : ""}</button>;
                  })}</div>
                  <form className="role-add" onSubmit={addCustomRole}>
                    <input value={customRole} maxLength={100} onChange={(event) => setCustomRole(event.target.value)} placeholder="Başka bir rol ekle" />
                    <button className="ghost" type="submit" disabled={!customRole.trim() || searchRoles.length >= 10}>Ekle</button>
                  </form>
                  <div className="search-mode-picker" role="group" aria-label="Tarama yoğunluğu">
                    <button type="button" className={searchMode === "quick" ? "selected" : ""} onClick={() => setSearchMode("quick")}><b>Hızlı</b><span>Roller birlikte</span></button>
                    <button type="button" className={searchMode === "deep" ? "selected" : ""} onClick={() => setSearchMode("deep")}><b>Derin</b><span>Üçlü rol grupları</span></button>
                  </div>
                  <small className="role-budget">Bağımsız genel aramada rol, konum, çalışma biçimi, tarih ve kaynakları ayrıntılı seçebilirsin.</small>
                </div>
                <dl><div><dt>Son arama</dt><dd>{selectedProfile?.last_search_outcome ? label(selectedProfile.last_search_outcome) : "Henüz yok"}</dd></div><div><dt>Sonraki kontrol</dt><dd>{formatDate(selectedProfile?.next_search_at ?? null)}</dd></div><div><dt>Aktif kaynak</dt><dd>{summary?.active_sources ?? 0}</dd></div></dl>
                <div className={`search-run ${searchRun?.status ?? "idle"}`}>
                  <div>
                    <span>Profil ilan araması</span>
                    <strong>{searchRun ? label(searchRun.status) : "Hazır"}</strong>
                  </div>
                  {searchRunning && <div className="search-progress"><i /></div>}
                  {searchRun?.status === "succeeded" && (
                    <div className="search-result-grid">
                      <span><b>{searchRun.result.raw_result_count ?? 0}</b> tarandı</span>
                      <span><b>{searchRun.result.matched_candidate_count ?? 0}</b> profile uydu</span>
                      <span><b>{searchRun.result.activity_counts?.active ?? 0}</b> aktif doğrulandı</span>
                      <span><b>{searchRun.result.activity_counts?.closed ?? 0}</b> kapalı elendi</span>
                      <span><b>{searchRun.result.activity_counts?.unknown ?? 0}</b> belirsiz kaldı</span>
                      <span><b>{searchRun.result.candidate_count ?? 0}</b> kayıt güncellendi</span>
                    </div>
                  )}
                  {(searchRun?.result.suppressed_candidates ?? 0) > 0 && (
                    <p className="search-note">{searchRun?.result.suppressed_candidates} eşleşme önceki kararı nedeniyle yeniden kuyruğa alınmadı.</p>
                  )}
                  {matchedSearchCandidates.length > 0 && (
                    <button className="ghost full search-results-button" onClick={() => setShowSearchResults(true)}>Eşleşmeleri gör ({matchedSearchCandidates.length})</button>
                  )}
                  {searchRun?.status === "failed" && <p>{errorMessage(new ApiError(500, searchRun.error_code ?? "search_failed"))}</p>}
                  <button className="primary full" onClick={() => setView("search")} disabled={!profile}>
                    {searchRunning ? "Aramayı görüntüle" : "Genel aramaya geç"}
                  </button>
                </div>
              </article>
            </section>
          </div>
        ) : view === "search" ? (
          <GeneralSearchView
            token={token}
            selectedProfile={selectedProfile}
            roles={searchRoles}
            availableRoles={availableSearchRoles}
            customRole={customRole}
            onCustomRoleChange={setCustomRole}
            onAddRole={addCustomRole}
            onToggleRole={toggleSearchRole}
            mode={searchMode}
            onModeChange={setSearchMode}
            locations={searchLocations}
            locationValue={locationValue}
            onLocationValueChange={setLocationValue}
            onAddLocation={addSearchLocation}
            onRemoveLocation={(location) => setSearchLocations((current) => current.filter((item) => item !== location))}
            workModes={searchWorkModes}
            onToggleWorkMode={toggleSearchWorkMode}
            sources={searchSources}
            onToggleSource={toggleSearchSource}
            maxAgeDays={maxAgeDays}
            onMaxAgeDaysChange={setMaxAgeDays}
            run={searchRun}
            running={searchRunning}
            starting={searchStarting}
            budget={searchBudget}
            onStart={startSearch}
            onViewed={(candidateId) => { void recordViewed(candidateId); }}
            applicationByCandidate={applicationByCandidate}
            applicationBusy={applicationBusy}
            onApplication={(candidateId, status) => { void changeApplication(candidateId, status); }}
            onManualImported={() => refreshBase(token)}
            onProfileUpdated={(updated) => setProfiles((current) => current.map((item) => item.label === updated.label ? updated : item))}
          />
        ) : view === "jobs" ? (
          <div className="jobs-layout">
            <section className="queue-panel">
              <div className="queue-toolbar"><div><h2>İlanlar</h2><span>{jobs.length} kayıt</span></div><label className="switch"><input type="checkbox" checked={includeUnverified} onChange={(event) => setIncludeUnverified(event.target.checked)} /><span />Doğrulanmamışları göster</label></div>
              <div className="queue-warning">Yalnızca sayfa üzerinden aktifliği doğrulanan ilanlar varsayılan olarak gösterilir.</div>
              <div className="job-list">{jobs.length === 0 ? <div className="empty-state compact"><div>○</div><h3>Kuyruk boş</h3><p>Bu filtrelerle incelenecek ilan bulunmuyor.</p></div> : jobs.map((job) => <JobRow key={job.candidate_id} job={job} active={selected === job.candidate_id} onSelect={setSelected} />)}</div>
            </section>
            {detail ? <DetailPanel token={token} detail={detail} onChanged={afterReview} application={applicationByCandidate.get(detail.candidate_id)} applicationBusy={applicationBusy === detail.candidate_id} onApplication={(status) => { void changeApplication(detail.candidate_id, status); }} /> : <aside className="detail-placeholder"><div>↗</div><h2>Bir ilan seç</h2><p>Kanıtları, aktiflik kontrolünü ve profil uyumunu burada inceleyebilirsin.</p></aside>}
          </div>
        ) : view === "applications" ? (
          <ApplicationsView applications={applications} busyCandidate={applicationBusy} onUpdate={(candidateId, status) => { void changeApplication(candidateId, status); }} />
        ) : <CompaniesView token={token} onError={setError} onReviewed={() => refreshBase(token)} />}
      </main>
      {showSearchResults && <SearchResultsModal candidates={matchedSearchCandidates} onClose={() => setShowSearchResults(false)} onViewed={(candidateId) => { void recordViewed(candidateId); }} applicationByCandidate={applicationByCandidate} applicationBusy={applicationBusy} onApplication={(candidateId, status) => { void changeApplication(candidateId, status); }} />}
    </div>
  );
}
