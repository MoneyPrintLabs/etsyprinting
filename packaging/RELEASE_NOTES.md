**stallkit now opens in your browser.** Double-click it and it opens at
`http://localhost:3000` with the screens from the video: Panel, Tasarım Yükle, İlanlar,
SEO, Siparişler, Kâr-Zarar, Pinterest, Mockuplar, Şablon İlan, Mağaza Bağlantısı and
Ayarlar. Connecting a shop now shows the callback address with a copy button and explains
Etsy's error messages. The print-area editor and the choice of mockups for drafts are
easy to find.

## Download

| Your computer | File |
|---|---|
| **Windows 10 / 11** | `stallkit-…-windows.exe`: one file, nothing to install. Double-click it. |
| **Mac (Apple Silicon: M1 and newer)** | `stallkit-…-macos.zip`: unzip it, then move `stallkit.app` to Applications. |

You do not need Python. Your keys and your shop connection stay on your computer, in
`~/.stallkit`, the same place the command line version uses. Settings and the Etsy
connection from 0.2.0 carry over.

**The first time you open it:**

- **Windows** may say *"Windows protected your PC"*, because the app is not code-signed.
  Click **More info → Run anyway**.
- **macOS** may refuse to open it because Apple could not verify the developer. Open
  **System Settings → Privacy & Security**, scroll down, and click **Open Anyway**.

Your browser then opens on **Mağaza Bağlantısı** (Shop connection). Follow it from top to
bottom. stallkit has no window of its own: to quit, use **Ayarlar → Kapat**, or close the
tab and it stops by itself about 90 seconds later.

Running from source on Windows, macOS or Linux (Python 3.9–3.13): `pip install -e .`,
then `stallkit desktop`. See the
[README](https://github.com/MoneyPrintLabs/etsyprinting#run-from-source-windows-macos-linux).

## Troubleshooting

- **"The requested redirect URL is not permitted"**: add exactly
  `http://localhost:3003/oauth/redirect` under your app's **⋮ → Edit callback URLs** on
  <https://www.etsy.com/developers/your-apps> (http, localhost, no slash at the end), then
  press **Bağlan** (Connect) again. No such menu yet means the app is still waiting for
  approval.
- **Keys refused**: copy both the Keystring and the Shared secret from the same, approved
  app.
- **"This site can't be reached" on localhost:3003 after approving**: keep stallkit open
  while you approve (it waits 5 minutes), then press **Bağlan** again. If another program
  uses port 3003, close it.
- **App name refused**: Etsy does not accept names containing "Etsy". Do not switch on
  Developer Mode.
- **Tracking upload refused (403)**: Etsy restricts adding tracking with newer API keys in
  many countries. Enter tracking in Shop Manager.
- **"This page only opens from the stallkit app"** or **"Cannot reach stallkit"**:
  double-click stallkit again. It opens a new tab, in the running app or in a fresh one.

---

**stallkit artık tarayıcıda açılıyor.** Çift tıklayın, `http://localhost:3000` adresinde
videodaki ekranlarla açılır: Panel, Tasarım Yükle, İlanlar, SEO, Siparişler, Kâr-Zarar,
Pinterest, Mockuplar, Şablon İlan, Mağaza Bağlantısı ve Ayarlar. Mağaza bağlarken
geri dönüş adresi kopyalama düğmesiyle gösteriliyor ve Etsy'nin hata mesajları
açıklanıyor. Baskı alanı ayarı ve taslaklara girecek mockup seçimi artık kolayca
bulunuyor.

## İndir (Türkçe)

| Bilgisayarınız | Dosya |
|---|---|
| **Windows 10 / 11** | `stallkit-…-windows.exe`: tek dosya, kurulum yok. Çift tıklayın. |
| **Mac (Apple Silicon: M1 ve sonrası)** | `stallkit-…-macos.zip`: zip'i açın, `stallkit.app`'i Uygulamalar klasörüne taşıyın. |

Python gerekmez. Anahtarlarınız ve mağaza bağlantınız yalnızca sizin bilgisayarınızda,
`~/.stallkit` klasöründe durur. 0.2.0'daki ayarlarınız ve Etsy bağlantınız aynen devam
eder.

**İlk açılışta:**

- **Windows** *"Windows kişisel bilgisayarınızı korudu"* diyebilir, çünkü uygulama imzalı
  değil. **Ek bilgi → Yine de çalıştır**'a tıklayın.
- **macOS**, geliştiriciyi doğrulayamadığı için uygulamayı açmayabilir. **Sistem Ayarları →
  Gizlilik ve Güvenlik**'e girin, aşağı kaydırın ve **Yine de Aç**'a tıklayın.

Sonra tarayıcınız **Mağaza Bağlantısı** sayfasında açılır; yukarıdan aşağı takip edin.
stallkit'in kendi penceresi yoktur. Kapatmak için **Ayarlar → Kapat**'ı kullanın ya da
sekmeyi kapatın, yaklaşık 90 saniye sonra kendiliğinden kapanır.

Kaynak koddan çalıştırmak için (Windows, macOS, Linux; Python 3.9–3.13): `pip install -e .`,
sonra `stallkit desktop`. Ayrıntılar
[README'de](https://github.com/MoneyPrintLabs/etsyprinting#kaynak-koddan-çalıştırma-windows-macos-linux).

## Sık karşılaşılan hatalar

**Etsy "İstenen yönlendirme URL'sine izin verilmiyor" diyor** (*The requested redirect URL is not permitted*)
Geri dönüş adresi Etsy uygulamanızda kayıtlı değil. <https://www.etsy.com/developers/your-apps> → uygulamanızın yanındaki **⋮** → **Edit callback URLs** → şu adresi **birebir** ekleyip kaydedin: `http://localhost:3003/oauth/redirect`
`https` değil `http`, `127.0.0.1` değil `localhost`, sonunda `/` yok. En kolayı: **Mağaza Bağlantısı**'ndaki adresin yanındaki kopyalama düğmesi. Sonra **Bağlan**'a tekrar basın.

**"Edit callback URLs" menüsü yok**: Uygulamanız henüz onaylanmamış. Onay genelde birkaç dakika sürer; onaylanınca menü çıkar.

**"Etsy bu anahtarları kabul etmedi"**: Keystring ve Shared secret aynı uygulamadan, boşluksuz kopyalanmalı. İkisi birden gerekir. Uygulama onaylanmadan anahtarlar çalışmaz.

**Etsy'de izin verdim ama tarayıcı "Bu siteye ulaşılamıyor" (localhost:3003) diyor**: İzin verirken stallkit açık olmalı; uygulama 5 dakika bekler. stallkit'e dönüp **Bağlan**'a tekrar basın. 3003 portunu başka bir program kullanıyorsa o programı kapatın.

**Uygulama adı reddedildi**: Etsy, adında "Etsy" geçen uygulamaları kabul etmiyor. Başka bir ad seçin. Developer Mode'u açmayın.

**Takip numarası yüklenmiyor (403)**: Etsy, 2024'ten beri yeni API anahtarlarıyla takip numarası eklemeyi Türkiye dahil birçok ülkede kısıtlıyor. Takip numaralarını Etsy Mağaza Yöneticisi'nden girin.

**"Bu sayfa yalnızca stallkit uygulamasından açılır"** ya da **"stallkit'e ulaşılamıyor"**: stallkit'e yeniden çift tıklayın. Yeni bir sekme açar; uygulama çalışıyorsa onda, kapanmışsa yeniden başlatarak.

---

What changed: see [CHANGELOG.md](https://github.com/MoneyPrintLabs/etsyprinting/blob/main/CHANGELOG.md).
