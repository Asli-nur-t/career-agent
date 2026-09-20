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

Bekleyen adayları özel aday profiline göre puanlanmış biçimde listelemek için:

```bash
PYTHONPATH=backend python -m app.review_job_board_queue \
  --profile aslinur-default \
  --minimum-score 35 \
  --limit 100
```

Bu komut kayıtları onaylamaz, reddetmez veya değiştirmez. Yalnızca manuel
incelemede önce bakılacak ilanları öne çıkarır. Aktifliği tarayıcıda doğrulanan
ilan yine `review_job_board_candidate --approve --confirmed-active` komutuyla
onaylanır.

## Aday profili ve ilan eşleştirme

Eşleştirme ilk aşamada harici model veya ücretli API çağırmaz. Hedef rol,
beceri, konum, uzaktan çalışma, kıdem, deneyim şartı ve hariç tutulan terimleri
kullanarak etkin ilanlara açıklanabilir bir 0-100 puan verir. Sonuçlar
`job_matches` tablosunda `strong_apply`, `apply`, `review` veya `skip` olarak
saklanır.

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
