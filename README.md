# career-agent

## Testler

Geliştirme bağımlılıklarını bir kez kurun ve testleri pytest ile çalıştırın:

```bash
python -m pip install -r backend/requirements-dev.txt
python -m pytest
```

Mevcut `unittest.TestCase` testleri pytest tarafından doğrudan toplanır; yeni
testler pytest işlevleri ve fixture'larıyla yazılabilir. `main` dalına yapılan
her push ve her pull request, Python 3.14 üzerinde derleme, test ve coverage
raporunu GitHub Actions içinde otomatik çalıştırır. Workflow herhangi bir API
anahtarı veya veritabanı parolası kullanmaz.

## Kariyer kaynaklarını keşfetme

Önce `PYTHONPATH=backend python -m alembic upgrade head` komutunu çalıştırın.
Doğrulanmış şirket profillerinden aday kariyer kaynaklarını incelemek için:

```bash
PYTHONPATH=backend python -m app.discover_career_sources --dry-run --limit 3
PYTHONPATH=backend python -m app.discover_career_sources --limit 1
```

Belirli bir doğrulanmış şirketi tekrar taramak için `--company-id UUID` kullanın.
Komut önce şirketin doğrulanmış ana sayfasını okur. Kaynak bulamazsa ayarlı
`SERPER_API_KEY` ile iki sınırlı arama yapar. Aynı alan adındaki kariyer
sayfaları aday kabul edilir. Dış ATS sonucu ise yalnızca pano anahtarı
doğrulanmış şirket veya marka kimliğiyle eşleşiyorsa aday olur. Yeni kaynaklar
`needs_review` durumunda kaydedilir; mevcut kayıtlar ve inceleme kararları
değiştirilmez. `public_api` yalnızca ATS biçimini belirtir. Kaynak onaylanmadan
otomatik ilan kontrolü yapılmamalıdır.

Başarılı fakat sonuçsuz kaynak taramaları yedi gün, aday bulunan taramalar otuz
gün sonra yeniden kontrol edilir. Geçici hatalar bir saatten başlayıp en fazla
yirmi dört saate çıkan geri çekilme süresiyle tekrar denenir. Böylece toplu
çalıştırmalar aynı şirketler için gereksiz arama kotası tüketmez.

Şirket web profili değerlendirmesinde varsayılan sağlayıcı yerel Ollama'dır.
Ollama yalnızca `http://127.0.0.1:11434` adresindeki `qwen3:8b` modeliyle
çalışır; farklı ağ adresleri ve modeller reddedilir. `.env` yapılandırması:

```dotenv
EVALUATOR_PROVIDER=ollama
OLLAMA_MODEL=qwen3:8b
OLLAMA_BASE_URL=http://127.0.0.1:11434
```

Gemini'yi isteğe bağlı kullanmak için `EVALUATOR_PROVIDER=gemini` seçilir ve
`GEMINI_API_KEY` tanımlanır. Model çıktısı sağlayıcıdan bağımsız olarak aynı
Pydantic şeması, URL allowlist'i ve web doğrulamasından geçirilir. Yerel model
tek başına bir şirket profilini doğrulanmış duruma getiremez.

Şirket web profili keşfindeki geçici arama ve model hataları mevcut deneme
kayıtlarından hesaplanan 1, 2, 4, 8, 16 ve en fazla 24 saatlik geri çekilme
süresiyle yeniden denenir. Güvenlik doğrulamasından reddedilen çıktılar için
bekleme süresi yedi gündür.

Gemini istemcisindeki otomatik HTTP tekrarları kapalıdır. Toplu keşif komutu
ilk kota sınırı, kimlik doğrulama veya model bulunamadı hatasında çalışmayı
durdurur; iki ardışık bağlantı, zaman aşımı veya servis hatasında da devreyi
açar. İşlenmeyen şirketler değiştirilmez. Kota sınırı altı saat, yapılandırma
hataları yedi gün sonra yeniden seçilebilir. Sağlayıcının ham hata metni ve
anahtarlar günlük çıktısına yazılmaz.

## Kaynak onayı ve ilan senkronizasyonu

Bir kaynağın kanıtını veritabanından kontrol ettikten sonra kaynağı açıkça
onaylayın veya reddedin:

```bash
PYTHONPATH=backend python -m app.review_career_source --source-id UUID --approve
PYTHONPATH=backend python -m app.review_career_source --source-id UUID --reject
```

Yalnızca doğrulanmış şirkete bağlı, `active` durumundaki Greenhouse, Lever ve
Ashby kaynakları okunur. Önce seçimi görün, ardından ilanları eşitleyin:

```bash
PYTHONPATH=backend python -m app.ingest_jobs --dry-run --limit 3
PYTHONPATH=backend python -m app.ingest_jobs --limit 3 --delay-seconds 3
```

Başarılı tam okumada yeni ilanlar eklenir, değişenler güncellenir ve artık
kaynakta görünmeyenler `closed` yapılır. Hatalar artan bekleme süresiyle yeniden
denenir; beş ardışık hatada kaynak tekrar incelemeye alınır.

## Üçüncü taraf ilan platformları

LinkedIn, Kariyer.net, Indeed ve Glassdoor sonuçları resmî şirket kariyer
kaynaklarından ayrı tutulur. Serper'ın herkese açık arama sonuçlarındaki izin
verilen ilan URL biçimleri normalize edilir. Kariyer.net sonuçlarında herkese
açık ilan sayfası, SSRF korumalı ve boyutu sınırlı istemciyle yalnızca açık
kapanma mesajı için okunur. Kapanma mesajı doğrulanan kayıt `rejected`, belirsiz
veya okunamayan kayıt `needs_review` durumunda yazılır. Mesajın bulunmaması
ilanın aktif olduğu anlamına gelmez. Sistem giriş/CAPTCHA kontrollerini aşmaz.
Bu kayıtlar onaylanmadan `job_postings` tablosuna veya başvuru akışına girmez.

Arama başlığı ve özetinde açıkça görülen konum, çalışma biçimi, istihdam türü,
göreli yayın tarihi ve aktif/kapalı işaretleri aday kaydına eklenir. Eksik bilgi
tahmin edilmez; `unknown` veya `null` olarak kalır. Bir arama özeti aktiflik
işareti taşısa bile ilan otomatik onaylanmaz. Açık kapanma işareti ise adayın
yanlışlıkla başvuru kuyruğuna girmemesi için otomatik ret sebebidir.
Kariyer.net ilanı aynı platformdaki farklı bir ilan kimliğine yönlenirse özgün
ilan artık erişilebilir kabul edilmez ve aday kapalı olarak işaretlenir. Giriş,
ana sayfa veya biçimi tanınmayan yönlendirmeler ise yanlış ret üretmemek için
`unknown` kalır.

İlk aşamada kota kullanımını ve yanlış şirket eşleşmesini sınırlamak için arama
tek bir açık şirket kimliğiyle çalışır:

```bash
PYTHONPATH=backend python -m app.discover_job_board_jobs \
  --company-id UUID \
  --dry-run

PYTHONPATH=backend python -m app.discover_job_board_jobs \
  --company-id UUID \
  --max-results 10
```

Şirket profili web içeriğiyle doğrulanmış ve bir marka adı kaydedilmişse arama
ticari unvan yerine bu marka adıyla yapılır. Doğrulanmamış profillerde CSV'den
gelen şirket adı korunur.

URL alan adları ve ilan yolu allowlist ile doğrulanır; takip parametreleri
atılır ve aynı platformdaki aynı ilan tekrar eklenmez. Arama başlığı ve özeti
güvenilmeyen dış veri kabul edilir. Adayın mevcut şirket kaydı değişmişse işlem
transaction içinde durdurulur. Şirket birleştirmelerinde aday kayıtlarının yeni
şirket kimliğine taşınması zorunludur.

### İlan adayını inceleme

Önce `needs_review` kaydının URL'sini tarayıcıda elle açın ve ilanın doğru
şirkete ait, erişilebilir ve hâlâ aktif olduğunu kontrol edin. Aktifliği
doğrulanan aday tek transaction içinde onaylanır ve `job_postings` tablosuna
aktarılır:

```bash
PYTHONPATH=backend python -m app.review_job_board_candidate \
  --candidate-id UUID \
  --approve \
  --confirmed-active
```

Yanlış şirkete ait, kapanmış veya şüpheli adaylar reddedilir:

```bash
PYTHONPATH=backend python -m app.review_job_board_candidate \
  --candidate-id UUID \
  --reject
```

Onay sırasında URL tekrar allowlist ve kanıt doğrulamasından geçirilir. Bir
adayın tam olarak bir `job_postings` kaydı olabilir. ATS ilanları
`career_source_id`, üçüncü taraf platform ilanları `job_board_candidate_id`
üzerinden bağlanır; veritabanı ikisinin aynı anda dolu veya boş olmasını
engeller. Daha önce onaylanmış aday basit bir ret işlemiyle silinmez ya da
kapatılmaz. Sonraki aramalar onaylanmış veya reddedilmiş kaydın incelenen URL,
başlık ve özetini değiştiremez; yalnızca son görülme zamanını yeniler.

Doğrulanmış şirketleri tek tek UUID ile çağırmak yerine, süresi gelen şirketleri
toplu tarayın:

```bash
PYTHONPATH=backend python -m app.discover_job_board_jobs \
  --dry-run \
  --limit 10

PYTHONPATH=backend python -m app.discover_job_board_jobs \
  --limit 10 \
  --max-results 10 \
  --delay-seconds 3
```

Aday bulunan şirketler bir gün, sonuç bulunmayanlar yedi gün sonra tekrar
taranır. Sağlayıcı hataları sınırlı geri çekilme süresiyle kaydedilir; kota veya
kimlik doğrulama hatasında batch işlemi hemen durur. Böylece aynı şirket için
gereksiz Serper sorguları yapılmaz.

Arama sonucu yalnızca izin verilen ilan URL'sine sahip olduğu için kabul
edilmez; başlık, özet veya URL içinde doğrulanmış şirket kimliği de aranır.
`Inmanage` ve `4ARC` gibi tek kelimelik veya kısa markalarda arama ticari
kimlikle yapılır. Eski kayıtları bu kurala göre önce değişiklik yapmadan
denetleyin, ardından yalnızca eşleşmeyenleri `filtered_out` durumuna taşıyın:

```bash
PYTHONPATH=backend python -m app.audit_job_board_candidates
PYTHONPATH=backend python -m app.audit_job_board_candidates --apply
```

Denetim hiçbir kaydı silmez. `--apply` verilmediğinde transaction içinde durum
değişikliği yapılmaz. `filtered_out` kayıtları onay kuyruğuna veya ilan
eşleştirmesine girmez; denetim kanıtı kayıt üzerinde korunur.

Bekleyen adayları özel aday profiline göre puanlanmış biçimde listelemek için:

```bash
PYTHONPATH=backend python -m app.audit_job_board_activity \
  --limit 100 \
  --apply

PYTHONPATH=backend python -m app.review_job_board_queue \
  --profile aslinur-default \
  --minimum-score 35 \
  --limit 100
```

İlk komut bekleyen ilanları en fazla dört eşzamanlı, SSRF korumalı istekle
denetler; başlık ve özetten konumu yeniden çıkarır, açık kapanış sinyali bulunan
ilanı `rejected` yapar ve açık başvuru sinyali bulunan ilanı `active` olarak
işaretler. Sayfadaki sınırlı JSON-LD `JobPosting.validThrough` alanı da
doğrulanır: geçmiş tarih kapanış, makul bir gelecek tarih aktiflik kanıtıdır;
bozuk, çelişkili veya aşırı ileri tarihler güvenilmez kabul edilir. Arama
özetindeki tarih ya da “aktif işe alım” ifadesi tek başına aktiflik kanıtı
sayılmaz. İşlem hiçbir kaydı silmez ve sonucu kanıta ekler. İkinci komut
varsayılan olarak yalnızca aktifliği doğrulanmış ilanları gösterir; erişimi
engellenen veya aktifliği belirsiz kayıtlar normal başvuru kuyruğuna girmez.
Tanılama gerektiğinde `--include-unverified` ile ayrıca görülebilirler.
Kuyruk komutu kayıtları değiştirmez. Aktif ilan yine
`review_job_board_candidate --approve --confirmed-active` komutuyla insan
onayından geçirilir.

### Profil bazlı genel ilan keşfi

Şirket listesinde bulunmayan işverenlerin ilanlarını da hedef rol, konum ve
uzaktan çalışma tercihleriyle aramak için önce maliyetsiz sorgu planını görün:

```bash
PYTHONPATH=backend python -m app.discover_profile_jobs \
  --profile aslinur-default \
  --dry-run
```

Aramayı çalıştırmak için:

```bash
PYTHONPATH=backend python -m app.discover_profile_jobs \
  --profile aslinur-default \
  --max-queries 6 \
  --max-results 10 \
  --minimum-score 20
```

Komut önce birincil, ikincil ve üçüncül rolleri Greenhouse, Lever ve Ashby'nin
resmî ilan sayfalarında; ardından ikincil kaynak olan LinkedIn, Kariyer.net,
Indeed ve Glassdoor'da arar. ATS URL keşfi, arama indeksindeki eksik konum ve
tarih metadatası nedeniyle konum/tarih operatörleriyle daraltılmaz. Bulunan ATS
ilanının güncelliği, konumu ve çalışma biçimi filtrelemeden önce sağlayıcının
canlı public API verisinden alınır. Aynı ilan kimliği API listesinde hâlâ
bulunuyorsa `active`, listeden kaldırılmışsa kapalı kabul edilir. LinkedIn ve
diğer ikincil kaynaklar bu aşamada sayfa isteğiyle otomatik doğrulanmaz.
Komutun `query_stats` çıktısı her sorgunun normalize edilen sağlayıcı ve
aktiflik sayılarını gösterir; bunlar ham arama sonuçlarıdır ve aday kabul
edildikleri anlamına gelmez. `accepted_count`, `activity_code_counts` ve
`exclusion_counts` alanları kayıt kapısının sonucunu açıklar. Kapalı ilanlar,
zorunlu konum filtresinde konumu bilinmeyen ilanlar ve tercih edilen uzaktan
çalışma coğrafyası dışında kalan ilanlar kaydedilmez. `Worldwide` gibi global
uzaktan çalışma kapsamları ancak profilin `preferred_remote_locations`
alanında açıkça listelenirse kabul edilir. Desteklenen URL'leri normalize eder,
yinelenen ilanları tekilleştirir ve rol eşleşmesi olmayan sonuçları kaydetmeden eler.
Varsayılan olarak en fazla altı Serper sorgusu yapar. Aday
bulunan profil 12 saat, sonuç bulunmayan profil 24 saat boyunca
cache'ten çalışır; profil değişirse beklemeden yeniden taranabilir. `--force`
yalnızca bilinçli bir erken yeniden tarama gerektiğinde kullanılmalıdır.
Arama motorundaki resmî ATS ilanı kapanmış olsa bile pano kimliği güvenli
biçimde çıkarılır. Sistem aynı Greenhouse, Lever veya Ashby panosunun public
API listesini pano başına en fazla 200 ilanla açar; yalnızca aynı doğrulanmış
pano URL'sine ait canlı ilanları aday havuzuna ekler. Sorgu başına genişletilen
ilan sayısı ayrıca 500 ile sınırlandırılır. Böylece eski indeks kaydı başvuru
adayı olmaz, yalnızca güncel pano keşfi için kullanılır.
Önceki taramalarda birikmiş kapalı veya konum politikasına uymayan profil
adaylarını yeni Serper sorgusu harcamadan yeniden değerlendirmek için:

```bash
PYTHONPATH=backend python -m app.discover_profile_jobs \
  --profile aslinur-default \
  --reconcile-only
```

Genel aramada şirket adı güvenilir biçimde çıkarılamazsa aday yine manuel
incelemeye bırakılır. İlan tarayıcıda açılıp aktifliği ve işvereni doğrulandıktan
sonra şirket adı açıkça verilerek onaylanabilir:

```bash
PYTHONPATH=backend python -m app.review_job_board_candidate \
  --candidate-id UUID \
  --approve \
  --confirmed-active \
  --company-name "Doğrulanmış İşveren"
```

Bu sırada mevcut şirket kaydı yeniden kullanılır; yoksa `needs_review` işaretli
bir şirket kaydı oluşturulur. Ücretli model çağrısı yapılmaz ve ilan insan
onayı olmadan `job_postings` tablosuna geçirilmez.

## Aday profili ve ilan eşleştirme

Eşleştirme ilk aşamada harici model veya ücretli API çağırmaz. Hedef rol,
beceri, konum, çalışma biçimi, ilan yaşı, kıdem, deneyim şartı ve hariç tutulan terimleri
kullanarak etkin ilanlara açıklanabilir bir 0-100 puan verir. Sonuçlar
`job_matches` tablosunda `strong_apply`, `apply`, `review` veya `skip` olarak
saklanır.

Rol önceliği üç seviyelidir: `target_roles` ana hedefleri,
`secondary_roles` güçlü alternatifleri, `tertiary_roles` ise yalnızca haberdar
olunmak istenen düşük öncelikli alanları temsil eder. Üçüncül rol başlıkta
eşleştiğinde ilan en fazla manuel inceleme seviyesine taşınır; tek başına güçlü
başvuru önerisi üretmez.

Profilde `preferred_locations` yerinde/hibrit şehirleri,
`preferred_remote_locations` ise uzaktan çalışılabilecek ülke veya bölgeleri
belirler. `excluded_locations` açık ret kurallarını, `allowed_work_modes` ise
`remote`, `hybrid` ve `onsite` seçeneklerini belirler.
`location_filter_mode` değeri `prefer` olduğunda konum yalnızca puanı etkiler;
`require` olduğunda bilinen ve tercih dışı konumlar elenir. Konumu bilinmeyen
ilanlar sessizce elenmez, `location_unknown` riskiyle manuel incelemeye kalır.
Uzaktan çalışma biçimi coğrafi uygunluk anlamına gelmez. Örnek profilde
`preferred_remote_locations` değeri `Türkiye` ve `Turkey` olduğu için Türkiye
genelindeki remote ilanlar kabul edilir; ABD, APAC, Orta Doğu veya Azerbaycan
gibi farklı kapsamlar `require` modunda elenir. `Worldwide`, `International`,
`Anywhere` veya `Global` açıkça yazıyorsa Türkiye'den çalışmaya uygun kabul
edilir. Ülke kapsamı bilinmeyen remote kayıtlar `remote_location_unknown`
uyarısıyla saklanır fakat normal başvuru kuyruğuna girmez.
`max_listing_age_days` sınırından eski olduğu açıkça bilinen ilanlar `skip`
olur; yayın tarihi bilinmeyenler `published_date_unknown` olarak işaretlenir.
Örnek profil yalnızca İstanbul ve Kocaeli'deki yerinde/hibrit ilanları veya
konumdan bağımsız uzaktan ilanları kabul edecek şekilde düzenlenebilir.

Örnek profili özel alana kopyalayıp düzenleyin; `private/` Git tarafından
yok sayılır:

```bash
cp config/candidate_profile.example.json private/candidate_profile.json

PYTHONPATH=backend python -m alembic upgrade head

PYTHONPATH=backend python -m app.configure_candidate_profile \
  --file private/candidate_profile.json
```

Önce yeniden puanlanması gereken ilanları görün, sonra eşleştirmeyi çalıştırın:

```bash
PYTHONPATH=backend python -m app.match_jobs \
  --profile aslinur-default \
  --dry-run \
  --limit 100

PYTHONPATH=backend python -m app.match_jobs \
  --profile aslinur-default \
  --limit 100
```

İlan içeriği, profil veya eşleştirici sürümü değişmedikçe kayıt tekrar
hesaplanmaz. `--refresh` bütün etkin ilanları yeniden puanlar. Yeniden puanlama
`shortlisted`, `dismissed` ve `applied` gibi insan inceleme kararlarını
değiştirmez. Bu puan bir başvuru kararı değildir; sonraki yerel model aşamasına
gidecek küçük aday kümesini maliyetsiz biçimde daraltır.

## CV'den yerel profil taslağı

PDF ve DOCX CV dosyaları yalnızca yerel makinede işlenir. Dosya uzantısı tek
başına yeterli kabul edilmez; imza, boyut, PDF sayfa sınırı ve DOCX arşiv yapısı
doğrulanır. Şifreli veya aktif davranış içeren PDF'ler ile şüpheli, aşırı
sıkıştırılmış ya da yol geçişi içeren DOCX arşivleri reddedilir. CV dosyasının
kendisi veritabanına veya repoya kopyalanmaz.

Önce yalnızca taslağı görüntüleyin:

```bash
PYTHONPATH=backend python -m app.import_candidate_cv \
  --file private/AslinurTopcuCV.pdf \
  --profile-file private/candidate_profile.json
```

Metin yerel Ollama'ya gönderilmeden önce e-posta, telefon, URL ve olası kimlik
numaraları temizlenir. CV içeriği güvenilmeyen veri kabul edilir. Yerel modelin
çıkardığı her rol, beceri, eğitim, dil ve deneyim değeri CV metninden birebir
kanıt göstermek zorundadır; kanıtsız çıktı tümüyle reddedilir.

Taslak incelendikten sonra özel profil dosyasına açıkça uygulamak için:

```bash
PYTHONPATH=backend python -m app.import_candidate_cv \
  --file private/AslinurTopcuCV.pdf \
  --profile-file private/candidate_profile.json \
  --apply

PYTHONPATH=backend python -m app.configure_candidate_profile \
  --file private/candidate_profile.json
```

`--apply` mevcut hedef, ikincil ve üçüncül rolleri, konumları, hariç tutulan terimleri
veya çalışma biçimi tercihlerini değiştirmez. CV'deki roller geçmiş deneyimi
gösterebilir; iş tercihi sayılmaz ve yalnızca önizlemede gösterilir. Yalnızca
kanıtlanan ve normalize edilen somut beceriler eklenir; işlemden önce
`candidate_profile.before_cv_import.json` yedeği oluşturulur. Veritabanı ikinci
komut çalıştırılana kadar güncellenmez.
