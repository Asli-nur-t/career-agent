import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  CompanyDetail,
  CompanyDiscoveryRun,
  CompanyItem,
  CompanyPage,
  CompanyProfileStatus,
  CompanyRejectionReason,
  JobDetail,
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
  getJobs,
  getProfiles,
  getSummary,
  getLatestProfileSearch,
  getNativeSearchLinks,
  markJobViewed,
  rejectJob,
  rejectCompany,
  pauseCompanyDiscoveryRun,
  resumeCompanyDiscoveryRun,
  startCompanyDiscoveryRun,
  startProfileSearch,
} from "./api";

type View = "overview" | "search" | "jobs" | "companies";

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
};

function label(value: string): string {
  return labels[value] ?? value.replaceAll("_", " ");
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

function SearchResultsModal({ candidates, onClose, onViewed }: {
  candidates: SearchRunCandidate[];
  onClose: () => void;
  onViewed: (candidateId: string) => void;
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
];
const nativeSearchSources: NativeSearchSource[] = [
  "linkedin", "kariyer", "indeed", "glassdoor", "ats",
  "turkey_tech", "remote_feeds",
];
const generalSearchWorkModes: SearchWorkMode[] = [
  "remote", "hybrid", "onsite",
];

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
}) {
  const [resultQuery, setResultQuery] = useState("");
  const [providerFilter, setProviderFilter] = useState("all");
  const [dispositionFilter, setDispositionFilter] = useState("all");
  const [viewFilter, setViewFilter] = useState("all");
  const [nativeRole, setNativeRole] = useState(roles[0] ?? "");
  const [nativeLocation, setNativeLocation] = useState(locations[0] ?? "");
  const [nativeLinks, setNativeLinks] = useState<NativeSearchLink[]>([]);
  const [nativeUnavailable, setNativeUnavailable] = useState<string[]>([]);
  const [nativeLoading, setNativeLoading] = useState(false);
  const [nativeError, setNativeError] = useState<string | null>(null);
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

  useEffect(() => {
    if (!roles.includes(nativeRole)) setNativeRole(roles[0] ?? "");
  }, [nativeRole, roles]);

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
            <div className="control-title"><strong>3. Kaynaklar</strong><span>{sources.length}/5</span></div>
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
                <div className="general-result-actions"><span className={`view-state ${candidate.operator_viewed_at ? "viewed" : "new"}`}>{candidate.operator_viewed_at ? "İncelendi" : "Yeni"}</span><b className={`disposition ${candidate.disposition}`}>{label(candidate.disposition)}</b><a href={candidate.listing_url} target="_blank" rel="noopener noreferrer" onClick={() => onViewed(candidate.candidate_id)}>İlanı aç ↗</a></div>
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

function DetailPanel({ token, detail, onChanged }: {
  token: string; detail: JobDetail; onChanged: () => Promise<void>;
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
  const [searchSources, setSearchSources] = useState<SearchSource[]>([
    "linkedin", "kariyer", "indeed", "glassdoor", "ats",
  ]);
  const [maxAgeDays, setMaxAgeDays] = useState(30);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedProfile = useMemo(
    () => profiles.find((item) => item.label === profile) ?? null,
    [profiles, profile],
  );
  const searchRunning = searchRun?.status === "queued" || searchRun?.status === "running";
  const matchedSearchCandidates = searchRun?.result.matched_candidates ?? [];
  const profileRoleKey = selectedProfile?.target_roles.join("\u001f") ?? "";
  const availableSearchRoles = useMemo(() => {
    const base = selectedProfile?.target_roles ?? [];
    return [
      ...base,
      ...searchRoles.filter(
        (role) => !base.some((item) => item.toLocaleLowerCase("tr-TR") === role.toLocaleLowerCase("tr-TR")),
      ),
    ];
  }, [selectedProfile, searchRoles]);
  const searchBudget = profileSearchBudget(
    searchRoles.length,
    searchMode,
    searchSources.length,
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
          <button className={view === "companies" ? "active" : ""} onClick={() => setView("companies")}><span>▦</span>Şirketler<b>{summary?.companies ?? 0}</b></button>
        </nav>
        <div className="sidebar-foot"><i /><div><strong>Yerel sistem</strong><span>İnsan onayı etkin</span></div></div>
      </aside>
      <main className="workspace">
        <header className="topbar">
          <div><p className="eyebrow">{todayLabel()}</p><h1>{view === "overview" ? "Günaydın, Aslınur" : view === "search" ? "Genel ilan arama" : view === "jobs" ? "İlan inceleme kuyruğu" : "Şirket yönetimi"}</h1></div>
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
          />
        ) : view === "jobs" ? (
          <div className="jobs-layout">
            <section className="queue-panel">
              <div className="queue-toolbar"><div><h2>İlanlar</h2><span>{jobs.length} kayıt</span></div><label className="switch"><input type="checkbox" checked={includeUnverified} onChange={(event) => setIncludeUnverified(event.target.checked)} /><span />Doğrulanmamışları göster</label></div>
              <div className="queue-warning">Yalnızca sayfa üzerinden aktifliği doğrulanan ilanlar varsayılan olarak gösterilir.</div>
              <div className="job-list">{jobs.length === 0 ? <div className="empty-state compact"><div>○</div><h3>Kuyruk boş</h3><p>Bu filtrelerle incelenecek ilan bulunmuyor.</p></div> : jobs.map((job) => <JobRow key={job.candidate_id} job={job} active={selected === job.candidate_id} onSelect={setSelected} />)}</div>
            </section>
            {detail ? <DetailPanel token={token} detail={detail} onChanged={afterReview} /> : <aside className="detail-placeholder"><div>↗</div><h2>Bir ilan seç</h2><p>Kanıtları, aktiflik kontrolünü ve profil uyumunu burada inceleyebilirsin.</p></aside>}
          </div>
        ) : <CompaniesView token={token} onError={setError} onReviewed={() => refreshBase(token)} />}
      </main>
      {showSearchResults && <SearchResultsModal candidates={matchedSearchCandidates} onClose={() => setShowSearchResults(false)} onViewed={(candidateId) => { void recordViewed(candidateId); }} />}
    </div>
  );
}
