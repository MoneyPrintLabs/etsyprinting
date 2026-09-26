# Setup

**Türkçe için [aşağı kaydırın](#kurulum-türkçe).**

Nothing here is optional. Miss any one of these and every command fails, usually with
an unhelpful `403`. The good news: you never have to work out *which* one you missed.

```bash
stallkit setup
```

That walks the whole list, asks you about the parts no program can check, and ends with
the single next command to run. Run it again any time something stops working.

> **Using the [desktop app](README.md#desktop-app-no-terminal)?** Steps 1–2 are done for
> you. Steps 3–9 are the **1 · Setup** tab, top to bottom; **Check everything** runs the
> same checklist as `stallkit setup`.

---

## What you need before you start

| # | Thing | Why it cannot be skipped |
|---|---|---|
| 1 | **Python 3.9+** | The tool is a Python package. |
| 2 | **stallkit installed** | `pip install -e .` from the repo. |
| 3 | **An Etsy shop that is open** | stallkit manages a shop. It cannot create one. |
| 4 | **An Etsy API app** | Free: [Create a seller app](https://www.etsy.com/developers/register-seller-app). |
| 5 | **Keystring AND shared secret** | Etsy needs **both**, colon-joined, on every request. |
| 6 | **A callback URL in your `.env`** | OAuth cannot start without one. |
| 7 | **That same URL registered on your app** | Etsy checks it byte for byte. |
| 8 | **Etsy accepting the credential** | Proves 5 is right before you trust it with a batch. |
| 9 | **Your shop connected** | `stallkit auth login`. |
| 10 | *(only for `stallkit drop`)* **a workspace and a template listing** | Fields no image can supply. |

---

## Step by step

### 1–2. Install

```bash
git clone https://github.com/MoneyPrintLabs/etsyprinting.git
cd etsyprinting
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e .
stallkit --version
```

### 3. Have an Etsy shop

If you do not have one yet, open it at <https://www.etsy.com/sell> first. This tool
works on an existing shop; it does not create one, and it never publishes anything.

### 4. Create an API app

Since July 2026 Etsy offers a **Seller App**: an app for your own shop, two fields,
usually approved within minutes. Signed in with the shop's account, open
<https://www.etsy.com/developers/register-seller-app> (*Create a seller app*).

- **App name:** anything without the word "Etsy" — Etsy's trademark rules refuse it.
- **Why you want to use the API:** say plainly that it is your own tool for your own
  shop, running on your computer — for example *"I manage my own shop with a tool that
  runs on my own computer: creating draft listings in bulk from my product photos,
  updating my listings and adding tracking numbers to my orders. It connects only to my
  shop, and the keys stay on my computer."* Then **Read Terms and Create App**.

Etsy allows one app per account. If you already have one (an older *personal* key), use
its keys instead. Do not switch on **Developer Mode** in the developer settings — it hides
your shop from search.

Once approved, the Dashboard (<https://www.etsy.com/developers/>) shows two values:

- a **Keystring** — like a username, semi-public
- a **Shared secret** — a real secret, treat it like a password

> **You need both.** This trips almost everyone up, because OAuth with PKCE is described
> as removing the client secret — and it does, from the *token exchange*. It does **not**
> remove the shared secret from the `x-api-key` header, which every single request
> carries. With the keystring alone, Etsy replies:
>
> ```
> 403 {"error":"Invalid API key: should be in the format 'keystring:shared_secret'."}
> ```

### 5. Put them in a `.env`

```bash
stallkit init
```

It asks for the keystring, then the shared secret **with the typing hidden** — so the
secret never appears on screen or in your shell history — then writes
`~/.stallkit/.env` with `0600` permissions (the file the desktop app reads too) and
verifies the credential against Etsy before you go further.

`.env` is git-ignored. Never commit it, never paste it into an issue.

Prefer to do it by hand? `cp .env.example .env` and fill in three values.

### 6–7. The callback URL

Etsy's own app settings screen states the rules:

> - Must start with `http://` or `https://`
> - Host must be a **domain name** (e.g. `example.com`)
> - **IP addresses are not allowed** (e.g. `127.0.0.1`)

`localhost` counts as a domain name, so the easy option is:

```
http://localhost:3003/oauth/redirect
```

Put that in `.env` as `ETSY_REDIRECT_URI`, **and** add the identical string to your app on
Etsy: Dashboard → the app's **⋮** menu → *Edit callback URLs*. The Seller App form has no
callback field; this is done after approval. stallkit then catches the redirect itself
and you copy nothing.

> Etsy's written documentation says the callback must use `https`. Taken literally that
> rules out a local listener — but the settings screen accepts `http://localhost:PORT/…`
> and that is what is actually enforced. Use `127.0.0.1` and it *will* be rejected. That
> is the real constraint, and it cost this project a rewrite to establish.

Any other host works too: Etsy sends your browser there, the page does not need to exist,
and you paste the address back in once.

### 8–9. Connect

```bash
stallkit setup          # confirms 1-8
stallkit auth login     # connects your shop
```

A browser opens. Approve the scopes. The token lands in `~/.stallkit/token.json` with
`0600` permissions, lasts an hour, and refreshes itself. The refresh token lasts 90 days,
so you do this about once a quarter.

stallkit asks for the minimum it needs and **never asks for a delete scope**. It cannot
delete a listing even if it is compromised.

### 10. Only if you want `stallkit drop`

```bash
stallkit drop init
stallkit drop template --from-listing <listing_id>
```

**You must build one listing in Etsy by hand first.** `taxonomy_id`,
`shipping_profile_id`, `return_policy_id`, `who_made`, `when_made`, processing times and
price are decisions about a business, not facts about a picture. Guessing them would put
wrong listings in a real shop, so stallkit copies yours instead.

Then put mockup templates in `1-MOCKUPS`, designs in `2-PRODUCTS`, and run
`stallkit drop run`.

---

## Before your API app is approved

You are not blocked. These work with no key and no network at all:

```bash
stallkit listings template -o products.csv     # a starter CSV
stallkit listings push products.csv --dry-run  # validates every row offline
stallkit orders ship tracking.csv --dry-run
```

The dry run checks title length, the 13-tag ceiling, 20 characters per tag, Etsy's tag
character set, every enum, required fields, and whether each image file exists. You can
have 300 products validated and ready before Etsy replies.

---

## When something fails

Run `stallkit setup` first — it usually names the problem outright.

| Symptom | Cause |
|---|---|
| `403 Invalid API key: should be in the format 'keystring:shared_secret'` | Only one half is set. See step 5. |
| `403 API key not found or not active` | Both halves are set but one is wrong. Re-check both on your app page. |
| `403 Forbidden` on a specific command | A missing scope. `stallkit auth status` shows what Etsy *granted*. Log in again. |
| Etsy says `redirect_uri is not valid` | Step 7. Compare character by character, including the port and any trailing slash. |
| `Etsy does not accept IP addresses` | Use `localhost`, not `127.0.0.1`. |
| `Cannot listen on 127.0.0.1:3003` | Another program holds the port. Pick another and change it in **both** places. |
| `400` when creating a listing | Usually a `taxonomy_id` that is not a leaf category, or a missing `shipping_profile_id`. `--dry-run` catches most of it. |
| Turkish characters look wrong in Excel | Your spreadsheet did not save as UTF-8. stallkit always writes UTF-8 with BOM. |

---

## Two things worth knowing

**Rate limits are per app.** A Personal Access app gets **5 requests/second and 5,000 per
day** — not the 10/sec, 10,000/day the general docs quote, which is the commercial tier.
Your app's real allowance is printed on its row at
[your-apps](https://www.etsy.com/developers/your-apps). stallkit defaults to 4/second.

**Order exports contain your customers' personal data** — names, email addresses, postal
addresses, gift messages. `.gitignore` covers `*.csv` for exactly this reason, but a file
you move elsewhere is no longer protected.

---
---

# Kurulum (Türkçe)

Buradaki hiçbir adım isteğe bağlı değil. Biri eksikse her komut hata verir, genelde de
işe yaramaz bir `403` ile. İyi haber: **hangisinin eksik olduğunu bulmak sana kalmıyor.**

```bash
stallkit setup
```

Tüm listeyi tek tek gezer, programın kontrol edemeyeceği kısımları sana sorar ve sonunda
çalıştırman gereken **tek komutu** yazar. Bir şey bozulduğunda tekrar çalıştır.

> **[Masaüstü uygulamasını](README.md#desktop-app-no-terminal) mı kullanıyorsun?** 1–2.
> adımlar zaten hazır. 3–9. adımlar **1 · Kurulum** sekmesinde, yukarıdan aşağı sırayla.
> **Her şeyi kontrol et** düğmesi `stallkit setup` ile aynı listeyi çalıştırır.

## Gerekenler

| # | Ne | Neden atlanamaz |
|---|---|---|
| 1 | **Python 3.9+** | Araç bir Python paketi. |
| 2 | **stallkit kurulu** | Depo içinde `pip install -e .` |
| 3 | **Açık bir Etsy mağazan** | Araç mağaza yönetir, mağaza açmaz. |
| 4 | **Etsy API uygulaması** | Ücretsiz: [Seller App oluştur](https://www.etsy.com/developers/register-seller-app). |
| 5 | **Keystring VE shared secret** | Etsy her istekte **ikisini birden** ister. |
| 6 | **`.env` içinde callback adresi** | OAuth onsuz başlamaz. |
| 7 | **Aynı adresin uygulamaya kayıtlı olması** | Etsy harfi harfine karşılaştırır. |
| 8 | **Etsy'nin anahtarı kabul etmesi** | Toplu işe girişmeden önce 5. adımın doğruluğunu kanıtlar. |
| 9 | **Mağazanın bağlı olması** | `stallkit auth login` |
| 10 | *(sadece `stallkit drop` için)* **çalışma klasörü ve şablon listing** | Görselden türetilemeyen alanlar. |

## Adımlar

**1–2.** Depoyu klonla, sanal ortam kur, `pip install -e .`, `stallkit --version` ile doğrula.

**3.** Etsy mağazan yoksa önce <https://www.etsy.com/sell> adresinden aç. Bu araç var olan
bir mağaza üzerinde çalışır ve **hiçbir şeyi yayınlamaz** — hepsi taslak kalır.

**4.** Temmuz 2026'dan beri Etsy satıcılara kendi mağazaları için **Seller App** veriyor:
iki alanlı bir form, onay genelde birkaç dakika. Mağazanın hesabıyla giriş yapıp
<https://www.etsy.com/developers/register-seller-app> adresini aç (*Create a seller app*).

- **App name:** içinde "Etsy" geçmeyen herhangi bir isim; Etsy'nin marka kuralları
  "Etsy" içeren ismi reddeder.
- **Why you want to use the API:** kendi bilgisayarında çalışan, sadece kendi mağazana
  bağlanan kendi aracın olduğunu açıkça yaz (yukarıdaki İngilizce örnek metni
  kullanabilirsin). Sonra **Read Terms and Create App**.

Etsy hesap başına bir uygulamaya izin veriyor. Zaten bir uygulaman (eski bir *personal*
anahtar) varsa onun anahtarlarını kullan. Geliştirici ayarlarındaki **Developer Mode**'u
açma, mağazanı aramada gizler.

Onaylanınca Dashboard'da (<https://www.etsy.com/developers/>) **Keystring** ve
**Shared secret** görünür (secret için göz ikonu).

> **İkisi de gerekli.** Herkesin takıldığı yer burası: PKCE'nin "client secret'ı
> kaldırdığı" söylenir — doğru, ama sadece *token değişiminden* kaldırır. Her isteğin
> taşıdığı `x-api-key` başlığından kaldırmaz. Tek keystring ile Etsy şunu döner:
>
> ```
> 403 {"error":"Invalid API key: should be in the format 'keystring:shared_secret'."}
> ```

**5.** `stallkit init` çalıştır. Keystring'i sorar, sonra shared secret'ı **gizli girişle**
alır — ekranda da komut geçmişinde de görünmez — `~/.stallkit/.env` dosyasını `0600`
izinle yazar (masaüstü uygulaması da bu dosyayı okur) ve devam etmeden önce anahtarı
Etsy'ye doğrulatır. `.env` git tarafından yok sayılır; asla
commit etme, asla bir issue'ya yapıştırma.

**6–7.** Etsy'nin kendi ayar ekranındaki kurallar: `http://` veya `https://` olacak, host
bir **alan adı** olacak, **IP adresi kabul edilmiyor**. `localhost` bir alan adı sayılır,
o yüzden en kolayı:

```
http://localhost:3003/oauth/redirect
```

Bunu `.env`'e `ETSY_REDIRECT_URI` olarak yaz **ve** birebir aynısını Etsy'de uygulamana
ekle: Dashboard → uygulamanın **⋮** menüsü → *Edit callback URLs*. Seller App formunda bu
alan yok, onaydan sonra eklenir. Sonra stallkit yönlendirmeyi kendi yakalar, sen hiçbir
şey kopyalamazsın.

> Etsy'nin yazılı dokümanı "https şart" diyor. Harfiyen alırsan yerel dinleyici imkânsız
> görünür — ama ayar ekranı `http://localhost:PORT/…` adresini kabul ediyor ve asıl
> uygulanan bu. `127.0.0.1` yazarsan **reddedilir.** Gerçek kısıt bu, ve bu projeye bir
> yeniden yazıma mal oldu.

**8–9.** `stallkit setup` ile 1–8'i doğrula, sonra `stallkit auth login` ile mağazanı bağla.
Tarayıcı açılır, izin verirsin. Token `~/.stallkit/token.json` içine `0600` izinle yazılır,
bir saat yaşar, kendini yeniler. Yenileme anahtarı 90 gün geçerli.

stallkit sadece ihtiyacı olan izinleri ister ve **silme izni hiç istemez** — ele geçirilse
bile bir ilanı silemez.

**10.** `stallkit drop` kullanacaksan: `stallkit drop init`, sonra
`stallkit drop template --from-listing <listing_id>`.

**Önce Etsy'de bir ilanı elinle, düzgünce açman gerekiyor.** Kategori, kargo profili, iade
politikası, üretim bilgisi, işleme süresi ve fiyat bir işletmeye dair kararlardır, bir
görsele dair olgular değil. Bunları tahmin etmek senin mağazana yanlış ürün sokar; o yüzden
stallkit seninkini kopyalar.

## API onayın gelmeden de çalışır

```bash
stallkit listings push urunler.csv --dry-run
```

Anahtarsız ve internetsiz çalışır: başlık uzunluğu, 13 tag sınırı, tag başına 20 karakter,
Etsy'nin izin verdiği karakter seti, tüm enum'lar, zorunlu alanlar ve görsel dosyalarının
gerçekten var olup olmadığı kontrol edilir. Etsy cevap vermeden 300 ürünü hazır edebilirsin.

## Bir şey çalışmazsa

Önce `stallkit setup`. Genelde sorunu doğrudan söyler. Ayrıntılı hata tablosu için yukarıdaki
İngilizce [When something fails](#when-something-fails) bölümüne bak.

## İki önemli not

**Hız sınırı uygulama başına.** Personal Access uygulaması **saniyede 5, günde 5.000**
istek alır — genel dokümandaki 10/sn ve 10.000/gün ticari erişim içindir. Uygulamanın
gerçek sınırı [your-apps](https://www.etsy.com/developers/your-apps) sayfasında yazıyor.

**Sipariş dosyaları müşterilerinin kişisel verisini içerir** — ad, e-posta, adres, hediye
notu. `.gitignore` tam da bu yüzden `*.csv` dosyalarını yok sayıyor; ama başka bir yere
taşıdığın dosya artık korumasız.
