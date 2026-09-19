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
sayfaları aday kabul edilir. Dış ATS sonucu ise yalnızca pano anahtarı
doğrulanmış şirket veya marka kimliğiyle eşleşiyorsa aday olur. Yeni kaynaklar
`needs_review` durumunda kaydedilir; mevcut kayıtlar ve inceleme kararları
değiştirilmez. `public_api` yalnızca ATS biçimini belirtir. Kaynak onaylanmadan
otomatik ilan kontrolü yapılmamalıdır.

Başarılı fakat sonuçsuz kaynak taramaları yedi gün, aday bulunan taramalar otuz
gün sonra yeniden kontrol edilir. Geçici hatalar bir saatten başlayıp en fazla
yirmi dört saate çıkan geri çekilme süresiyle tekrar denenir. Böylece toplu
çalıştırmalar aynı şirketler için gereksiz arama kotası tüketmez.

Şirket web profili keşfindeki geçici arama ve model hataları da mevcut deneme
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
