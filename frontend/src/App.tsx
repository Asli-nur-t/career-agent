import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  JobDetail,
  JobItem,
  Profile,
  SearchRun,
  Summary,
  approveJob,
  getJobDetail,
  getJobs,
  getProfiles,
  getSummary,
  getLatestProfileSearch,
  rejectJob,
  startProfileSearch,
} from "./api";

type View = "overview" | "jobs";

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
  succeeded: "Tamamlandı",
  failed: "Başarısız",
  candidates_found: "Aday bulundu",
  no_results: "Sonuç yok",
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
      serper_not_configured: "SERPER_API_KEY backend üzerinde yapılandırılmamış.",
      worker_interrupted: "Önceki arama backend yeniden başladığı için kesildi.",
      search_failed: "Arama güvenli şekilde sonlandırıldı. Ayrıntılar sunucu logunda.",
      search_run_unavailable: "Arama kaydı oluşturuldu ancak yeniden okunamadı.",
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

function JobRow({ job, active, onSelect }: {
  job: JobItem; active: boolean; onSelect: (id: string) => void;
}) {
  return (
    <button className={`job-row ${active ? "selected" : ""}`} onClick={() => onSelect(job.candidate_id)}>
      <span className={`score score-${job.recommendation}`}>{job.score}</span>
      <span className="job-main">
        <strong>{job.title}</strong>
        <span>{job.company_name}</span>
        <small>{job.location ?? "Konum bilinmiyor"} · {label(job.work_mode)}</small>
      </span>
      <span className={`recommendation ${job.recommendation}`}>{label(job.recommendation)}</span>
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
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedProfile = useMemo(
    () => profiles.find((item) => item.label === profile) ?? null,
    [profiles, profile],
  );
  const searchRunning = searchRun?.status === "queued" || searchRun?.status === "running";

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
      .then((run) => { if (!cancelled) setSearchRun(run); })
      .catch((caught) => { if (!cancelled) setError(errorMessage(caught)); });
    return () => { cancelled = true; };
  }, [token, profile]);

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

  useEffect(() => {
    if (!token || !selected) { setDetail(null); return; }
    getJobDetail(token, selected).then(setDetail).catch((caught) => setError(errorMessage(caught)));
  }, [token, selected]);

  async function afterReview() {
    setDetail(null); setSelected(null);
    await Promise.all([refreshBase(token), refreshJobs()]);
  }

  async function startSearch() {
    if (!token || !profile || searchRunning || searchStarting) return;
    if (!window.confirm("En fazla 6 arama sorgusu ve 20 ilan aktiflik kontrolü çalıştırılsın mı?")) return;
    setSearchStarting(true); setError(null);
    try {
      setSearchRun(await startProfileSearch(token, profile));
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

  if (!token) return <AuthScreen onConnect={setToken} />;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark small">CA</div><div><strong>Career Agent</strong><span>Operator Console</span></div></div>
        <nav>
          <button className={view === "overview" ? "active" : ""} onClick={() => setView("overview")}><span>⌂</span>Genel bakış</button>
          <button className={view === "jobs" ? "active" : ""} onClick={() => setView("jobs")}><span>◎</span>İlan kuyruğu<b>{jobs.length}</b></button>
        </nav>
        <div className="sidebar-foot"><i /><div><strong>Yerel sistem</strong><span>İnsan onayı etkin</span></div></div>
      </aside>
      <main className="workspace">
        <header className="topbar">
          <div><p className="eyebrow">{todayLabel()}</p><h1>{view === "overview" ? "Günaydın, Aslınur" : "İlan inceleme kuyruğu"}</h1></div>
          <div className="top-actions">
            <label>Profil<select value={profile} onChange={(event) => setProfile(event.target.value)}>{profiles.map((item) => <option key={item.label}>{item.label}</option>)}</select></label>
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
                <div className="role-cloud">{selectedProfile?.target_roles.map((role) => <span key={role}>{role}</span>)}</div>
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
                  {searchRun?.status === "failed" && <p>{errorMessage(new ApiError(500, searchRun.error_code ?? "search_failed"))}</p>}
                  <button className="primary full" onClick={startSearch} disabled={!profile || searchRunning || searchStarting}>
                    {searchRunning ? "Arama sürüyor…" : "Şimdi ilan ara"}
                  </button>
                </div>
              </article>
            </section>
          </div>
        ) : (
          <div className="jobs-layout">
            <section className="queue-panel">
              <div className="queue-toolbar"><div><h2>İlanlar</h2><span>{jobs.length} kayıt</span></div><label className="switch"><input type="checkbox" checked={includeUnverified} onChange={(event) => setIncludeUnverified(event.target.checked)} /><span />Doğrulanmamışları göster</label></div>
              <div className="queue-warning">Yalnızca sayfa üzerinden aktifliği doğrulanan ilanlar varsayılan olarak gösterilir.</div>
              <div className="job-list">{jobs.length === 0 ? <div className="empty-state compact"><div>○</div><h3>Kuyruk boş</h3><p>Bu filtrelerle incelenecek ilan bulunmuyor.</p></div> : jobs.map((job) => <JobRow key={job.candidate_id} job={job} active={selected === job.candidate_id} onSelect={setSelected} />)}</div>
            </section>
            {detail ? <DetailPanel token={token} detail={detail} onChanged={afterReview} /> : <aside className="detail-placeholder"><div>↗</div><h2>Bir ilan seç</h2><p>Kanıtları, aktiflik kontrolünü ve profil uyumunu burada inceleyebilirsin.</p></aside>}
          </div>
        )}
      </main>
    </div>
  );
}
