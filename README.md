# career-agent

## Kariyer kaynaklarını keşfetme

Önce `PYTHONPATH=backend python -m alembic upgrade head` komutunu çalıştırın.
Doğrulanmış şirket profillerinden aday kariyer kaynaklarını incelemek için:

```bash
PYTHONPATH=backend python -m app.discover_career_sources --dry-run --limit 3
PYTHONPATH=backend python -m app.discover_career_sources --limit 1
```

Belirli bir doğrulanmış şirketi tekrar taramak için `--company-id UUID` kullanın.
Bu komut yalnızca şirketin doğrulanmış sitesinin ana sayfasını okur; şirket
sitesinde doğrudan bağlı olan ATS adreslerini ve aynı alan adındaki kariyer
sayfalarını `needs_review` durumunda kaydeder. Veritabanındaki mevcut kaynak
kayıtlarını ve onay durumlarını değiştirmez. `public_api` erişim stratejisi
yalnızca ATS biçimini belirtir; kaynak şirketle ilişkilendirilip onaylanmadan
otomatik ilan kontrolü yapılmamalıdır.
