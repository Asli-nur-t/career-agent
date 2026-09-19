# career-agent

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
sayfaları aday kabul edilir. Dış ATS sonucu ise yalnızca arama başlığı veya
özetinde doğrulanmış şirket ya da marka adı geçiyorsa aday olur. Yeni kaynaklar
`needs_review` durumunda kaydedilir; mevcut kayıtlar ve inceleme kararları
değiştirilmez. `public_api` yalnızca ATS biçimini belirtir. Kaynak onaylanmadan
otomatik ilan kontrolü yapılmamalıdır.

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
