"""Window text in English and Turkish.

Only the window is translated. Command output in the log stays in English: it is
what the README, the issue tracker and every screenshot someone shares for help
are written against, so one wording is worth more than a translated one.
"""

from __future__ import annotations

import locale
import os
import re
import subprocess
import sys

LANGUAGES = (("en", "English"), ("tr", "Türkçe"))

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "tagline": "Etsy automation on your own computer",
        "help": "Help",
        "show": "Show",
        "browse": "Browse…",
        "copy": "Copy",
        "save": "Save",
        "status": "Status",
        "disconnect": "Disconnect",
        "preview": "Preview",
        "all_files": "All files",
        # setup
        "tab_setup": "1 · Setup",
        "setup_app_title": "Step 1 — Create your Etsy seller app (once)",
        "setup_app_hint": "Everyone connects their own shop through their own free Etsy app. Etsy's "
                          "\"Seller App\" for your own shop is usually approved within minutes.",
        "callback_label": "Callback address:",
        "open_seller_app": "Create a seller app",
        "open_dashboard": "Open the Dashboard",
        "setup_keys_title": "Step 2 — Your keys",
        "setup_keys_hint": "Paste both values from your Etsy app page. They stay on this computer.",
        "callback_field": "Callback address",
        "save_verify": "Save and check with Etsy",
        "saved_to": "Saved in: {path}",
        "setup_connect_title": "Step 3 — Connect your shop",
        "setup_connect_hint": "Your browser opens Etsy's own permission page; approve it and come back. The "
                              "app asks to read your listings and create drafts, read orders and add tracking "
                              "numbers — never to delete anything. Unused for 90 days, it needs connecting again.",
        "connect_shop": "Connect my Etsy shop",
        "run_checks": "Check everything",
        "shop_info": "Shop details",
        "shop_profiles": "Shipping & return profiles",
        # shops and connection
        "shop": "Shop",
        "shop_n": "Shop {n}",
        "add_shop": "＋ Add a shop",
        "shop_added": "✓ New shop added. Do the three setup steps for it; your other shops are unchanged.",
        "remove_shop": "Remove this shop from this computer",
        "confirm_remove_shop": "Remove {shop} from this computer?\n\nIts keys and sign-in stored here are deleted. "
                               "The shop on Etsy, its listings and its orders are not touched.",
        "shop_removed": "✓ {shop} was removed from this computer.",
        "wait_for_task": "Wait until the running task finishes, then switch shops.",
        "setup_app_steps": "1. Sign in to Etsy with the shop's account and press “Create a seller app”.\n"
                           "2. App name: anything without the word “Etsy” — e.g. your shop's name + Tools.\n"
                           "3. “Why you want to use the API”: paste the text below, then press "
                           "“Read Terms and Create App”.\n"
                           "4. Once approved (usually minutes), open the Dashboard, open your app's ⋮ menu → "
                           "“Edit callback URLs”, add the callback address below and save.\n"
                           "5. On the same page, copy the Keystring and the Shared secret (eye icon) into step 2.",
        "app_description_label": "Why (paste):",
        "app_description_value": "I manage my own shop with a tool that runs on my own computer: creating draft listings in "
                                 "bulk from my product photos, updating my listings and adding tracking numbers to my "
                                 "orders. It connects only to my shop, and the keys stay on my computer.",
        "setup_app_wait": "Already have an Etsy app? Etsy allows one per account — use its keys instead. "
                          "Do not switch on “Developer Mode” in the developer settings: it hides your shop "
                          "from search.",
        "setup_tools_title": "Tools",
        "setup_tools_hint": "“Check everything” lists what is still missing, one step at a time.",
        "keys_accepted": "✓ Etsy accepts these keys.",
        "keys_rejected": "✗ Etsy refused these keys: {detail}\nCheck both values on your Etsy app page: the "
                         "Keystring and the Shared secret are two different values. A brand-new app may still be "
                         "waiting for Etsy's approval.",
        "connecting": "Connecting your shop",
        "connected_as": "✓ Connected: {shop}",
        "scopes_missing": "! Etsy granted fewer permissions than needed (missing: {scopes}). Connect again and "
                          "approve everything.",
        "go_to_upload": "Next: Upload products →",
        "reconnect_needed": "! The sign-in has expired or was withdrawn. Press “Connect my Etsy shop” again.",
        "offline_detail": "! Etsy cannot be reached right now. Check the internet connection.",
        "token_cleared": "! The keys changed, so the old sign-in was removed. Connect the shop again.",
        "cancel": "Cancel",
        "cancelling": "Cancelling…",
        "please_wait": "waiting for the current check to finish",
        "connecting_pinterest": "Connecting Pinterest",
        "pinterest_connected": "✓ Pinterest connected.",
        "cut": "Cut",
        "paste": "Paste",
        "select_all": "Select all",
        "status_bad_keys": "✗ Keys refused",
        "status_reconnect": "! Reconnect needed",
        "status_offline": "● No connection",
        # drop
        "tab_drop": "2 · Upload products",
        "drop_folder_title": "Your products folder",
        "drop_folder_hint": "Inside 2-PRODUCTS, make one folder per product and put its photos in it, "
                            "named 01, 02, 03… in the order you want. The folder's name becomes the "
                            "product's name.",
        "folder": "Folder",
        "create_folder": "Create the folder",
        "open_folder": "Open the folder",
        "drop_template_title": "Template listing (once)",
        "drop_template_hint": "The number of one listing you built completely by hand in Etsy. Price, "
                              "category, shipping, processing time and variations are copied from it "
                              "onto every new draft. The number is in the listing's address: "
                              "etsy.com/listing/NUMBER/…",
        "listing_number": "Listing number",
        "copy_settings": "Copy its settings",
        "template_ready": "Template ready — copied from listing {listing}.",
        "template_missing": "No template yet.",
        "drop_upload_title": "Upload",
        "drop_upload_hint": "Products are created as DRAFTS: nobody sees them until you publish them in "
                            "Etsy. A product that was already uploaded is never uploaded twice.",
        "check_only": "Check (sends nothing)",
        "upload_drafts": "Upload as drafts",
        "drop_mockup_title": "Advanced: put designs on mockups",
        "drop_mockup_hint": "For loose designs: places each design onto the templates in 1-MOCKUPS and "
                            "writes review.csv. Nothing is sent to Etsy.",
        "prepare_mockups": "Prepare",
        "preview_print_area": "Preview print area",
        # listings
        "tab_listings": "Listings (CSV)",
        "export_title": "Export listings",
        "export_hint": "Saves your listings as a spreadsheet you can edit and send back.",
        "which_listings": "Which listings",
        "save_as_csv": "Save as CSV…",
        "push_title": "Create or update in bulk",
        "push_hint": "An empty listing_id creates a new draft; a filled one updates that listing. "
                     "Results are written next to the file as …-results.csv.",
        "csv_file": "CSV file",
        "copy_variations_from": "Copy variations from listing",
        "send_to_etsy": "Send to Etsy",
        "blank_template": "Blank template…",
        # orders
        "tab_orders": "Orders",
        "orders_pull_title": "Download orders",
        "orders_pull_hint": "Period: 30d, 6w, 3m or a date such as 2026-01-01.",
        "since": "Period",
        "unshipped_only": "Only orders not shipped yet",
        "ship_title": "Upload tracking numbers",
        "ship_hint": "The CSV needs receipt_id, tracking_code and carrier_name columns. Etsy emails "
                     "each buyer and marks the order shipped — this cannot be undone.",
        "country_code": "Ship-from country (TR, US…)",
        "send_tracking": "Send tracking",
        "list_carriers": "Carrier names",
        # seo
        "tab_seo": "SEO",
        "audit_title": "Score my listings",
        "audit_hint": "Checks every active listing's title, tags and description and lists the "
                      "weakest first.",
        "score_listings": "Score",
        "save_report": "Save report as CSV…",
        "keywords_title": "Research a search term",
        "keywords_hint": "Looks at the listings Etsy returns for a term and what they have in common.",
        "keyword": "Search term",
        "research": "Research",
        "suggest_title": "Suggestions for one listing",
        "suggest_hint": "Compares one of your listings with what ranks for its main term.",
        "keyword_optional": "Search term (optional)",
        "get_suggestions": "Get suggestions",
        # pinterest
        "tab_pinterest": "Pinterest (optional)",
        "pin_app_title": "Your Pinterest app",
        "pin_app_hint": "Only if you want Pins. Create an app on Pinterest's developer page with a "
                        "business account, then paste its ID and secret here.",
        "pin_sandbox": "Sandbox (for apps still on trial access)",
        "open_pinterest_apps": "Open Pinterest's app page",
        "pin_account_title": "Your Pinterest account",
        "connect_pinterest": "Connect Pinterest",
        "my_boards": "My boards",
        "pin_queue_title": "Queue Pins",
        "pin_queue_hint": "Pins link back to the Etsy listing and are spread over the following days. "
                          "Only active (published) listings can be pinned.",
        "listing_numbers": "Listing numbers",
        "board": "Board",
        "images": "Images (e.g. 1-6)",
        "per_day": "Pins per day",
        "pin_ai": "Images are AI-made or AI-edited",
        "add_to_queue": "Add to queue",
        "pin_post_title": "Post",
        "pin_post_hint": "Posts the Pins due today. Press it once a day.",
        "post_due": "Post today's Pins",
        "show_queue": "Show queue",
        # log
        "log": "Activity",
        "clear": "Clear",
        "copy_log": "Copy",
        "anonymise": "Hide shop name (for screenshots)",
        "welcome": "Ready. Start with the Setup tab. Everything you run is shown here.",
        "done_ok": "✓ Done.",
        "done_fail": "✗ Finished with an error (code {code}). The reason is above.",
        "running": "Working: {what}",
        "input_needed": "The command needs an answer:",
        "copied": "Copied to the clipboard.",
        # status
        "status_checking": "Checking…",
        "status_connected": "● Connected: {shop}",
        "status_keys": "○ Keys needed",
        "status_disconnected": "○ Shop not connected",
        "status_error": "● Cannot reach Etsy",
        # messages
        "need_both_keys": "Both the Keystring and the Shared secret are needed.",
        "keys_saved": "✓ Saved to {path} (keystring {key}…, shared secret {n} characters).",
        "verifying": "Checking the keys with Etsy",
        "keys_ok": "✓ Etsy accepted the keys. Next: Connect my Etsy shop.",
        "login_browser": "A browser window opens. Approve on Etsy, then come back here.",
        "confirm_disconnect": "Disconnect your shop from this computer? You can connect it again at any time.",
        "folder_missing": "That folder does not exist yet. Press “Create the folder” first.",
        "need_listing_number": "Type a listing number (digits only).",
        "confirm_upload": "Upload the new products in the folder to your shop as drafts?\n\n"
                          "They stay invisible until you publish them in Etsy.",
        "pick_csv": "Choose a CSV file first.",
        "confirm_push": "Send this file to your live Etsy shop?\n\nNew listings are created as drafts; "
                        "rows with a listing_id update that listing.",
        "confirm_ship": "Submit tracking for these orders?\n\nEtsy emails every buyer and marks the "
                        "order shipped. This cannot be undone.",
        "need_country": "Type the country you ship from, e.g. TR or US.",
        "need_keyword": "Type a search term.",
        "pin_saved": "✓ Pinterest settings saved to {path}.",
        "confirm_disconnect_pin": "Disconnect Pinterest from this computer?",
        "need_listing_numbers": "Type one or more listing numbers, separated by spaces or commas.",
        "need_board": "Type the board's name (see “My boards”).",
        "confirm_close": "Something is still running. Closing now may leave a half-finished draft.\n\nClose anyway?",
    },
    "tr": {
        "tagline": "Kendi bilgisayarında Etsy otomasyonu",
        "help": "Yardım",
        "show": "Göster",
        "browse": "Seç…",
        "copy": "Kopyala",
        "save": "Kaydet",
        "status": "Durum",
        "disconnect": "Bağlantıyı kes",
        "preview": "Önizle",
        "all_files": "Tüm dosyalar",
        # setup
        "tab_setup": "1 · Kurulum",
        "setup_app_title": "Adım 1: Etsy'de satıcı uygulamanı aç (bir kez)",
        "setup_app_hint": "Herkes kendi mağazasını kendi ücretsiz Etsy uygulamasıyla bağlar. Etsy'nin "
                          "kendi mağazan için verdiği \"Seller App\" genelde birkaç dakikada onaylanır.",
        "callback_label": "Geri dönüş adresi:",
        "open_seller_app": "Satıcı uygulaması oluştur",
        "open_dashboard": "Dashboard'u aç",
        "setup_keys_title": "Adım 2: Anahtarların",
        "setup_keys_hint": "İki değeri de Etsy uygulama sayfandan yapıştır. Sadece bu bilgisayarda kalırlar.",
        "callback_field": "Geri dönüş adresi",
        "save_verify": "Kaydet ve Etsy ile kontrol et",
        "saved_to": "Kaydedildiği yer: {path}",
        "setup_connect_title": "Adım 3: Mağazanı bağla",
        "setup_connect_hint": "Tarayıcıda Etsy'nin kendi izin sayfası açılır, onaylayıp buraya dönersin. "
                              "İstenen izinler: ilanlarını okuma ve taslak oluşturma, siparişleri okuma ve kargo "
                              "takip numarası ekleme. Silme izni istenmez. 90 gün kullanmazsan yeniden bağlaman gerekir.",
        "connect_shop": "Etsy mağazamı bağla",
        "run_checks": "Her şeyi kontrol et",
        "shop_info": "Mağaza bilgileri",
        "shop_profiles": "Kargo ve iade profilleri",
        # shops and connection
        "shop": "Mağaza",
        "shop_n": "Mağaza {n}",
        "add_shop": "＋ Mağaza ekle",
        "shop_added": "✓ Yeni mağaza eklendi. Onun için 3 kurulum adımını yap, diğer mağazaların değişmedi.",
        "remove_shop": "Bu mağazayı bu bilgisayardan kaldır",
        "confirm_remove_shop": "{shop} bu bilgisayardan kaldırılsın mı?\n\nBurada saklanan anahtarları ve "
                               "bağlantısı silinir. Etsy'deki mağazaya, listinglerine ve siparişlerine dokunulmaz.",
        "shop_removed": "✓ {shop} bu bilgisayardan kaldırıldı.",
        "wait_for_task": "Çalışan iş bitince mağaza değiştirebilirsin.",
        "setup_app_steps": "1. Mağazanın hesabıyla Etsy'ye giriş yap ve “Satıcı uygulaması oluştur”a bas.\n"
                           "2. App name: içinde “Etsy” geçmeyen bir isim, örneğin mağazanın adı + Tools.\n"
                           "3. “Why you want to use the API” kutusuna aşağıdaki metni yapıştır, sonra "
                           "“Read Terms and Create App”e bas.\n"
                           "4. Onaylanınca (genelde birkaç dakika) Dashboard'u aç, uygulamanın ⋮ menüsünden "
                           "“Edit callback URLs”e gir, aşağıdaki geri dönüş adresini ekleyip kaydet.\n"
                           "5. Aynı sayfadaki Keystring'i ve Shared secret'ı (göz ikonu) 2. adıma kopyala.",
        "app_description_label": "Kullanım amacı:",
        "app_description_value": "I manage my own shop with a tool that runs on my own computer: creating draft listings in "
                                 "bulk from my product photos, updating my listings and adding tracking numbers to my "
                                 "orders. It connects only to my shop, and the keys stay on my computer.",
        "setup_app_wait": "Zaten bir Etsy uygulaman var mı? Etsy hesap başına bir tane izin veriyor, onun "
                          "anahtarlarını kullan. Geliştirici ayarlarındaki “Developer Mode”u açma, mağazanı "
                          "aramada gizler.",
        "setup_tools_title": "Araçlar",
        "setup_tools_hint": "“Her şeyi kontrol et” eksik kalanları adım adım listeler.",
        "keys_accepted": "✓ Etsy bu anahtarları kabul ediyor.",
        "keys_rejected": "✗ Etsy bu anahtarları kabul etmedi: {detail}\nEtsy uygulama sayfandaki iki değeri de "
                         "kontrol et: Keystring ve Shared secret farklı değerlerdir. Yeni açılan bir uygulama "
                         "henüz Etsy'nin onayını bekliyor olabilir.",
        "connecting": "Mağazan bağlanıyor",
        "connected_as": "✓ Bağlandı: {shop}",
        "scopes_missing": "! Etsy gerekenden az izin verdi (eksik: {scopes}). Yeniden bağlan ve hepsine izin ver.",
        "go_to_upload": "Sıradaki: Ürün yükle →",
        "reconnect_needed": "! Bağlantının süresi doldu ya da izin geri alındı. “Etsy mağazamı bağla”ya tekrar bas.",
        "offline_detail": "! Şu an Etsy'ye ulaşılamıyor. İnternet bağlantını kontrol et.",
        "token_cleared": "! Anahtarlar değiştiği için eski bağlantı silindi. Mağazayı yeniden bağla.",
        "cancel": "İptal",
        "cancelling": "İptal ediliyor…",
        "please_wait": "süren kontrolün bitmesi bekleniyor",
        "connecting_pinterest": "Pinterest bağlanıyor",
        "pinterest_connected": "✓ Pinterest bağlandı.",
        "cut": "Kes",
        "paste": "Yapıştır",
        "select_all": "Tümünü seç",
        "status_bad_keys": "✗ Anahtarlar hatalı",
        "status_reconnect": "! Yeniden bağlan",
        "status_offline": "● Bağlantı yok",
        # drop
        "tab_drop": "2 · Ürün yükle",
        "drop_folder_title": "Ürün klasörün",
        "drop_folder_hint": "2-PRODUCTS içinde her ürün için bir klasör aç ve fotoğraflarını istediğin "
                            "sırayla 01, 02, 03… diye adlandırıp içine koy. Klasörün adı ürünün adı olur.",
        "folder": "Klasör",
        "create_folder": "Klasörü oluştur",
        "open_folder": "Klasörü aç",
        "drop_template_title": "Şablon listing (bir kez)",
        "drop_template_hint": "Etsy'de elle, eksiksiz kurduğun bir listingin numarası. Fiyat, kategori, "
                              "kargo, hazırlama süresi ve varyasyonlar ondan her yeni taslağa kopyalanır. "
                              "Numara listingin adresinde yazar: etsy.com/listing/NUMARA/…",
        "listing_number": "Listing numarası",
        "copy_settings": "Ayarlarını kopyala",
        "template_ready": "Şablon hazır: {listing} numaralı listingden kopyalandı.",
        "template_missing": "Henüz şablon yok.",
        "drop_upload_title": "Yükle",
        "drop_upload_hint": "Ürünler TASLAK olarak oluşturulur, sen Etsy'de yayınlayana kadar kimse "
                            "görmez. Daha önce yüklenen bir ürün asla ikinci kez yüklenmez.",
        "check_only": "Kontrol et (hiçbir şey göndermez)",
        "upload_drafts": "Taslak olarak yükle",
        "drop_mockup_title": "Gelişmiş: tasarımları mockup'a yerleştir",
        "drop_mockup_hint": "Tek tek tasarımlar için: her tasarımı 1-MOCKUPS'taki şablonlara yerleştirir "
                            "ve review.csv dosyasını yazar. Etsy'ye hiçbir şey gönderilmez.",
        "prepare_mockups": "Hazırla",
        "preview_print_area": "Baskı alanını önizle",
        # listings
        "tab_listings": "Listingler (CSV)",
        "export_title": "Listingleri dışa aktar",
        "export_hint": "Listinglerini düzenleyip geri gönderebileceğin bir tablo olarak kaydeder.",
        "which_listings": "Hangi listingler",
        "save_as_csv": "CSV olarak kaydet…",
        "push_title": "Toplu oluştur veya güncelle",
        "push_hint": "listing_id boşsa yeni taslak oluşturulur, doluysa o listing güncellenir. "
                     "Sonuçlar dosyanın yanına …-results.csv olarak yazılır.",
        "csv_file": "CSV dosyası",
        "copy_variations_from": "Varyasyonları şu listingden kopyala",
        "send_to_etsy": "Etsy'ye gönder",
        "blank_template": "Boş şablon…",
        # orders
        "tab_orders": "Siparişler",
        "orders_pull_title": "Siparişleri indir",
        "orders_pull_hint": "Dönem: 30d (30 gün), 6w (6 hafta), 3m (3 ay) ya da 2026-01-01 gibi bir tarih.",
        "since": "Dönem",
        "unshipped_only": "Sadece henüz kargolanmamış siparişler",
        "ship_title": "Kargo takip numaralarını yükle",
        "ship_hint": "CSV'de receipt_id, tracking_code ve carrier_name sütunları olmalı. Etsy her alıcıya "
                     "e-posta atar ve siparişi kargolandı olarak işaretler. Geri alınamaz.",
        "country_code": "Gönderim ülkesi (TR, US…)",
        "send_tracking": "Takip numaralarını gönder",
        "list_carriers": "Kargo firması adları",
        # seo
        "tab_seo": "SEO",
        "audit_title": "Listinglerimi puanla",
        "audit_hint": "Aktif listinglerinin başlık, etiket ve açıklamalarını kontrol eder, en zayıfları "
                      "en üstte listeler.",
        "score_listings": "Puanla",
        "save_report": "Raporu CSV olarak kaydet…",
        "keywords_title": "Arama kelimesi araştır",
        "keywords_hint": "Etsy'nin bir arama için getirdiği listingleri ve ortak noktalarını inceler.",
        "keyword": "Arama kelimesi",
        "research": "Araştır",
        "suggest_title": "Tek bir listing için öneriler",
        "suggest_hint": "Listingini, ana kelimesinde üst sıralarda çıkan listinglerle karşılaştırır.",
        "keyword_optional": "Arama kelimesi (isteğe bağlı)",
        "get_suggestions": "Öneri al",
        # pinterest
        "tab_pinterest": "Pinterest (isteğe bağlı)",
        "pin_app_title": "Pinterest uygulaman",
        "pin_app_hint": "Sadece pin atmak istiyorsan. İşletme hesabınla Pinterest'in geliştirici "
                        "sayfasında bir uygulama aç, kimliğini ve şifresini buraya yapıştır.",
        "pin_sandbox": "Deneme ortamı (uygulaman henüz deneme erişimindeyse)",
        "open_pinterest_apps": "Pinterest uygulama sayfasını aç",
        "pin_account_title": "Pinterest hesabın",
        "connect_pinterest": "Pinterest'i bağla",
        "my_boards": "Panolarım",
        "pin_queue_title": "Pinleri sıraya al",
        "pin_queue_hint": "Pinler Etsy listingine bağlanır ve sonraki günlere yayılır. Sadece aktif "
                          "(yayındaki) listingler pinlenebilir.",
        "listing_numbers": "Listing numaraları",
        "board": "Pano",
        "images": "Görseller (örn. 1-6)",
        "per_day": "Günde kaç pin",
        "pin_ai": "Görseller yapay zekâ ile üretildi veya düzenlendi",
        "add_to_queue": "Sıraya ekle",
        "pin_post_title": "Paylaş",
        "pin_post_hint": "Bugün sırası gelen pinleri paylaşır. Günde bir kez bas.",
        "post_due": "Bugünün pinlerini paylaş",
        "show_queue": "Sırayı göster",
        # log
        "log": "İşlem kaydı",
        "clear": "Temizle",
        "copy_log": "Kopyala",
        "anonymise": "Mağaza adını gizle (ekran görüntüsü için)",
        "welcome": "Hazır. Kurulum sekmesinden başla. Çalıştırdığın her şey burada görünür.",
        "done_ok": "✓ Bitti.",
        "done_fail": "✗ Hatayla bitti (kod {code}). Nedeni yukarıda yazıyor.",
        "running": "Çalışıyor: {what}",
        "input_needed": "Komut bir cevap bekliyor:",
        "copied": "Panoya kopyalandı.",
        # status
        "status_checking": "Kontrol ediliyor…",
        "status_connected": "● Bağlı: {shop}",
        "status_keys": "○ Anahtar gerekli",
        "status_disconnected": "○ Mağaza bağlı değil",
        "status_error": "● Etsy'ye ulaşılamıyor",
        # messages
        "need_both_keys": "Keystring ve Shared secret'ın ikisi de gerekli.",
        "keys_saved": "✓ {path} dosyasına kaydedildi (keystring {key}…, shared secret {n} karakter).",
        "verifying": "Anahtarlar Etsy ile kontrol ediliyor",
        "keys_ok": "✓ Etsy anahtarları kabul etti. Sıradaki adım: Etsy mağazamı bağla.",
        "login_browser": "Bir tarayıcı penceresi açılacak. Etsy'de izin ver, sonra buraya dön.",
        "confirm_disconnect": "Mağazanın bu bilgisayarla bağlantısı kesilsin mi? İstediğin zaman yeniden bağlayabilirsin.",
        "folder_missing": "Bu klasör henüz yok. Önce “Klasörü oluştur”a bas.",
        "need_listing_number": "Bir listing numarası yaz (sadece rakam).",
        "confirm_upload": "Klasördeki yeni ürünler mağazana taslak olarak yüklensin mi?\n\n"
                          "Sen Etsy'de yayınlayana kadar görünmezler.",
        "pick_csv": "Önce bir CSV dosyası seç.",
        "confirm_push": "Bu dosya canlı Etsy mağazana gönderilsin mi?\n\nYeni listingler taslak olarak "
                        "oluşturulur, listing_id'si olan satırlar o listingi günceller.",
        "confirm_ship": "Bu siparişlerin takip numaraları gönderilsin mi?\n\nEtsy her alıcıya e-posta atar "
                        "ve siparişi kargolandı yapar. Geri alınamaz.",
        "need_country": "Gönderim yaptığın ülkeyi yaz, örneğin TR ya da US.",
        "need_keyword": "Bir arama kelimesi yaz.",
        "pin_saved": "✓ Pinterest ayarları {path} dosyasına kaydedildi.",
        "confirm_disconnect_pin": "Pinterest'in bu bilgisayarla bağlantısı kesilsin mi?",
        "need_listing_numbers": "Bir veya daha fazla listing numarası yaz, aralarına boşluk ya da virgül koy.",
        "need_board": "Panonun adını yaz (“Panolarım”a basarak görebilirsin).",
        "confirm_close": "Hâlâ çalışan bir işlem var. Şimdi kapatırsan yarım kalmış bir taslak oluşabilir.\n\n"
                         "Yine de kapatılsın mı?",
    },
}


def text(language: str, key: str, /, **kwargs: object) -> str:
    """The string for `key`, in `language` if it has one, else in English.

    Positional-only, so a placeholder may be called anything — including `key` or
    `language` — without colliding with the parameters.
    """
    table = STRINGS.get(language) or STRINGS["en"]
    value = table.get(key) or STRINGS["en"].get(key) or key
    return value.format(**kwargs) if kwargs else value


def _mac_language() -> str:
    """The first of macOS's preferred languages, as a two-letter code, or ""."""
    try:
        out = subprocess.run(
            ["defaults", "read", "-g", "AppleLanguages"],
            capture_output=True, text=True, timeout=3,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    found = re.search(r"[A-Za-z]{2}", out.partition("(")[2])
    return found.group(0).lower() if found else ""


def detect_language() -> str:
    """Turkish for a Turkish system, English for everything else."""
    if sys.platform == "win32":
        try:
            import ctypes

            # Low 10 bits are the primary language; 0x1F is Turkish.
            if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x1F:
                return "tr"
            return "en"
        except (AttributeError, OSError):
            pass
    if sys.platform == "darwin":
        # An app opened from Finder gets no LANG; the language the person chose
        # lives in the user defaults instead.
        preferred = _mac_language()
        if preferred:
            return "tr" if preferred == "tr" else "en"
    for name in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(name, "")
        if value:
            return "tr" if value.lower().startswith("tr") else "en"
    try:
        current = locale.getlocale()[0] or ""
    except ValueError:
        current = ""
    return "tr" if current.lower().startswith(("tr", "turkish")) else "en"
