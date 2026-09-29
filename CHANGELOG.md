# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **The video's fonts are bundled**: Inter, Plus Jakarta Sans and JetBrains Mono (SIL
  Open Font License 1.1, see NOTICE.md) ship with the app, so every computer draws the
  same text. On Windows a medium weight no longer looks as bold as a semibold one (the
  active menu item stands out again), and headings and numbers use the display face.
- **Tasarım Yükle**: a design saved without transparency is no longer uploaded silently
  as a finished photo. Its row warns, and when it sits on a solid background the start
  card offers to place it on the mockups instead (the background joined to the edges is
  removed; white inside the design stays).
- The Kontrol step warns about pictures that would look soft on Etsy: a short side under
  1000 px, or a design enlarged more than twice onto a mockup.
- Every picture a draft gets carries an alt text with the design, the product and its
  colour ("Retro Mountain Sunset t-shirt, white").
- The start card says when the template's product differs from the main mockup (a mug
  template with a T-shirt as the first mockup).
- **Siparişler**: for a shop that ships from Türkiye, a note before the first send says
  that Etsy may refuse tracking numbers from newer API keys, with the CSV download and
  Etsy's orders page. It goes for good once Etsy has accepted a number.
- **Şablon İlan → Açıklama şablonu** (description template): the text every new draft's
  description is written from, starting as the template listing's own description with
  its title turned into `{başlık}`. `{başlık}` is each draft's title and `{tasarım}` its
  design's name (English: `{title}`, `{design}`). Sentences holding a word of the template
  listing's own design (from its title and tags, such as "lemon"; never a product
  word such as "wallpaper" or "sample") are highlighted: they would be copied onto every
  draft. It is saved in `product.json`; saving the same listing again keeps it.
- Until such sentences are gone, each product of a run warns ("Açıklamada şablon ilanın
  desenine özel 1 cümle kaldı"), and the start card says so once, with a link that opens
  the description template. `stallkit drop template` lists them too.

### Fixed

- A new draft's description no longer carries the template listing's own title: the
  title, and a line made of its `|`- or `,`-separated parts, become the draft's title
  (a "Sage Lemon Wallpaper | Olive Citrus Mural" line no longer reaches a woodland
  mural's draft).

### Changed

- **Şablon İlan** shows how many of each listing sold, best sellers first, counted from
  the months **Kâr-Zarar** has already read (no extra Etsy calls; the tooltip names the
  months). Without those months it shows the favourites in Etsy's order, as before.
- **Şablon İlan** names the category by its first and last level in your language
  ("Giyim › Tişörtler"), shows only whole listing rows until "daha göster" is pressed,
  and keeps each field's grey bar until its value has faded in.
- **Mockuplar**: Kaydet stays dimmed until a new mockup's print area is drawn (it can
  still save the default area), the "Baskı alanı" label shows while drawing once the
  rectangle is big enough, and the chips, notices and buttons match the video.
- A download-only template's drafts use only the mockups that show no physical product
  (posters, canvases, frames) plus the flat preview; the start card names them and the
  ones left out.
- **İlanlar**: "N yeni taslak" counts only the drafts of stallkit's latest run; older
  drafts read "N taslak".
- **SEO → Düzelt** offers a title change only when removing whole repeated phrases ends
  the repetition.
- **Taslak İlan** names each picture's product and colour from the file the run sent,
  counts the pictures the run uploaded ("7/7 görsel"), and shows a transparent design on
  a checkerboard. The Kontrol step's toast sits top right, as in the video.
- The bell's badge stays clear after a connect the page itself already announced.
- Font weights are the video's 400/500/600/700/800 only; table column heads are 11.4 px,
  and a few labels were tightened so Inter fits a 1280 px window.

## [0.3.1] — 2026-09-28

### Fixed

- Drafts made from a template listing whose weight or size was never filled in were
  refused by Etsy: Etsy reports such a weight as 0 but accepts only values above 0. A
  weight or size of 0, or one without its unit, is now left out (with a note) instead
  of failing every draft, both for templates and for CSV rows.

### Changed

- The repository is now `MoneyPrintLabs/stallkit`; the old address redirects.

## [0.3.0] — 2026-09-27

### Changed

- **The app now runs in your browser.** Double-clicking the download, or running
  `stallkit desktop`, starts a small server on this computer and opens
  `http://localhost:3000` (the next free port if 3000 is taken) instead of a Tk window.
  Double-clicking again opens a tab in the app that is already running. Closing the last
  tab stops the app about 90 seconds later unless a task is still running, and
  **Ayarlar → Kapat** quits at once. Turkish comes first and English second; the app
  follows the system language, and you can switch it in **Ayarlar**.
- **Only this computer can reach it.** The server listens on `127.0.0.1` and `::1` only.
  Only the tab the app opened has a session. Requests with a foreign Host or Origin are
  refused, and every response carries a strict Content Security Policy.
- **`stallkit desktop --port N --no-browser`.** `python -m stallkit desktop` works the same
  way. The downloaded app still runs any command when given arguments, for scheduled
  jobs.
- **The README is about the app now.** The command line documentation moved to
  `CLI.md`, and SETUP.md names the app's screens.
- The release check starts the packaged app's web server and fetches every file of the
  interface from it.
- **Command line:**
  - `stallkit drop run` and `stallkit drop auto` use the mockups switched on in
    **Mockuplar**, in that order (the first is the main image), as the app does. `--mockups N`
    now means the first N of that selection rather than the first N files in the folder;
    with no saved selection the result is the same as before. `drop auto` gained
    `--mockups`.
  - `stallkit listings pull` no longer writes the `views` column. Etsy's API has no view
    count, so it was always empty.
  - `stallkit seo suggest` reads the listing as plain text, so a tag such as
    `mother's day gift` is no longer measured (and flagged) as `mother&#39;s day gift`.
  - `stallkit doctor --path` treats a folder inside a products folder (`2-PRODUCTS`, ...)
    the way the `drop` commands do: it checks the products folder around it and says so.
  - `stallkit drop run` counts a digital template's download files in its request
    estimate.

### Added

The screens from the video, all in Turkish and English:

- **Mağaza Bağlantısı** (Shop connection) walks through the Seller App steps with copy
  buttons. The keys are checked with Etsy the moment they are saved. Connecting opens
  Etsy's consent page in a new tab, which closes itself when done. The screen then shows
  the permissions Etsy granted and offers a disconnect.
- **Mockuplar** (Mockups) uploads mockups and has a print-area editor. One area can be
  applied to every mockup of the same size, and you choose which mockups go into drafts.
- **Şablon İlan** (Template listing) picks the listing whose price, category, shipping,
  return policy, processing time, description and variations every new draft copies.
- **Tasarım Yükle** (Upload designs) takes designs or folders of finished photos by drag
  and drop. Each one goes through six steps (mockup, research, title, tags, check, draft),
  three at a time, with live progress, and is sent to Etsy as a draft. A run can be
  stopped. The upload history prevents duplicates, and nothing is published.
- **İlanlar** and **Taslak İlan** (Listings, Draft listing) show drafts and live
  listings. You can edit the title, tags and description, and publish one listing or many
  after a confirmation that states Etsy's listing fee. Listings can be downloaded as CSV
  or updated from one.
- **SEO** scores every listing out of 100, weakest first, and offers tag research for any
  keyword. **Düzelt** (Fix) sends the suggested change after a confirmation, and refuses
  if the listing changed on Etsy since the audit. The report downloads as CSV.
- **Siparişler** (Orders) lists waiting, shipped and delivered orders. Tracking numbers
  can be typed or loaded from a CSV, and they are sent after a confirmation.
- **Kâr-Zarar** (Profit & loss) shows revenue, Etsy fees from the payment ledger, the
  product and shipping costs you enter, and net profit and margin per month. Amounts are
  in the shop's currency and in TRY, using the Central Bank of Turkey's rate (fetched at
  most once a day) or one you type.
- **Panel** (Dashboard) shows today's numbers and quick actions.
- **Pinterest** connects your account, queues Pins from live listings and posts the ones
  that are due.
- **Ayarlar** (Settings) covers language, hiding the shop name for screenshots, several
  shops, the products folder, the ten-step setup checklist, and quitting.
- **Across the app:** a notification bell, long tasks that keep running when you move
  between screens, and a question before you leave a page with unsaved edits.
- **New-version notice.** About 10 seconds after it starts and then once a day, the app
  asks GitHub for the latest release: one GET to `api.github.com` with a
  `stallkit/<version>` User-Agent, and nothing else is sent. A newer version shows as a
  small pill in the top bar, "Yeni sürüm v0.3.1 · İndir", which opens the release page,
  with **Neler yeni?** for the release notes; the × hides it for that version. It is
  announced once in the bell. **Ayarlar** shows the version, when it last checked, a
  **Şimdi kontrol et** button and a toggle for the daily check;
  `STALLKIT_NO_UPDATE_CHECK=1` turns every check off. A failed check (offline, GitHub's
  rate limit) is only a line in the log.
- **Etsy's trademark notice** is shown on every screen.
- **Digital products.** A template listing of type `download` (or `both`) makes drafts of
  that type, in the app and with `drop run` / `drop auto`. After its images each draft gets
  the files the buyer downloads (Etsy's `uploadListingFile`): a loose design is delivered
  as the design file itself, and its listing photos are always the mockups plus a
  1200 px preview, never the file being sold (an opaque JPG printable too); a product
  folder delivers the files in its `dosyalar` (or `files`) subfolder, while the photos in
  the folder stay the listing's images. Files in a subfolder of `dosyalar` are not sent
  (Etsy takes files, not folders): the product stops and asks for a zip. At most 5
  files per listing and 20 MB per file (Etsy's seller limits; the API spec states none);
  programs and scripts are refused. A download-only template needs no shipping profile.
  A product with nothing to deliver, too many files or an oversized file stops with its
  own message before its draft is made; the upload history records the files sent.
  A **made-to-order** template (a custom portrait, an invitation) needs no download file,
  as Etsy allows: `dosyalar` files still go when there are some, a loose design is not
  attached (it is a sample), and the draft says to add the buyer's file in Etsy.
  **Tasarım Yükle** shows **Ürün: Dijital** (*Product: Digital*; **Fiziksel + dijital** for
  *both*), its **Başlat** (*Start*) window opens with **Dijital ürün · tasarım dosyası
  indirilebilir dosya olarak eklenir** (*Digital product · the design file is attached as
  the download*), and **Şablon İlan** marks the listing **Dijital** and says **Dijital
  ürün: her taslağa tasarım dosyası indirilebilir dosya olarak eklenir** under it.
  A product folder with only its `dosyalar` and no photos is listed as *Fotoğraf yok*
  (*No photos*) and stops at the **Kontrol** (*Check*) step instead of being skipped
  silently.
- **CSV `files` column.** `listings template`, `listings pull`, the app's CSV download and
  `examples/listings.csv` have a `files` column after `images`: what a new `download` or
  `both` draft attaches, relative to the CSV. A CSV without it pushes as before.
  `listings push` and `drop auto` print the download files each row sent, and
  `push --out` has a `files_uploaded` column.

### Fixed

From reports on 0.2.0:

- **"The requested redirect URL is not permitted."** **Mağaza Bağlantısı** shows the
  callback address with a copy button and the exact place to add it in Etsy (**⋮ → Edit
  callback URLs**, which appears only after Etsy approves the app). It asks once, before
  opening Etsy, whether the address has been added. While it waits, it lists Etsy's usual
  errors and how to fix each one.
- **Checks before connecting.** The keys, the callback address and port 3003 are checked
  first, with a plain message when another program holds the port.
- **Pasted keys are cleaned up.** Spaces, line breaks and quotes are removed, and a
  single `keystring:shared_secret` line is accepted.
- **The print area is easy to find.** Every mockup card has a visible "Baskı alanını
  ayarla" (Set print area) action. A banner says how many mockups still use the default
  area, which puts the design in the middle.
- **Choosing mockups is explicit.** You pick which mockups go into drafts in a selection
  mode, with select all and none, type and colour filters, and a counter. Etsy allows 20
  images per listing, so up to 19 mockups plus the flat design. Nothing is left out
  silently, and you can change the order, and with it the main image.
- **Running from source and troubleshooting are documented.** The README and SETUP.md
  cover running from source on Windows, macOS and Linux with Python 3.9–3.13, and both
  have a troubleshooting section.
- **Digital templates were refused.** `drop auto` stopped with "Automatic upload currently
  supports physical products only" when the template listing was a digital download.
  Digital products are now supported (see Added).
- **A products folder inside the products folder.** Choosing `2-PRODUCTS` (or
  `1-MOCKUPS`, `3-DRAFTS`, or any folder inside them) as the products folder made a second
  one inside the first (`Etsy Studio\2-PRODUCTS\2-PRODUCTS`), and the mockups already in
  `Etsy Studio\1-MOCKUPS` were no longer found. **Ayarlar** and the `--path` option of the
  `drop` commands now use the products folder those belong to and say so. A folder an
  older version nested that way is pointed out in **Ayarlar** with a button to use the
  main folder, which carries its template listing and upload history over (so nothing
  already drafted is drafted again), and the folders it left inside `2-PRODUCTS`, its
  `archive` included, are never taken for products. A new folder that is only *named*
  `2-PRODUCTS` makes its parent the products folder only when that parent holds nothing
  else, and a `README.txt` stallkit did not write is never replaced.

From the review before this release:

- **Text with `&` or an apostrophe is handled correctly.** Etsy returns a seller's own
  titles, tags and descriptions HTML-escaped (`Mom&#39;s Mug &amp; Gift`). They are now
  decoded once, where they are read. As a result, edits, SEO fixes, new drafts, Pins and
  CSV files carry plain text instead of sending the entities back to Etsy.
- **Tracking numbers and Pins need the confirmation on the server too,** as publishing,
  edits and SEO fixes already did. Nothing reaches a buyer without it.
- **Kâr-Zarar counts refunds correctly.** A refund comes off revenue without its tax
  share, and a fully refunded order counts as nothing. The Panel counts revenue the same
  way. A month too large to read whole is marked as partial instead of being shown as
  complete.
- **Receipts, the payment ledger and listings are read in full.** Only Etsy's marketplace
  search stops at its first 12,000 results.
- **Titles and tags for new drafts.** The market research searches the design's name
  with the template's product when the name does not say it (`dog dad paw print` on a
  shirt template searches shirts, not posters, and the title is no longer just the name
  and "Shirt"). Free tag slots take only the template listing's tags that suit any design
  of its product, never the ones about its own design. A phrase too long for a tag leaves
  a shorter one (`leaf phone case` from `monstera leaf phone case`). A Turkish name is
  capitalised the Turkish way (`Kedi Pati İzi`) when it, or the template's text, is
  Turkish. Every screen checks a title with the same rule as a CSV push. A market phrase
  with a size such as `8.5x11` or `3/4 sleeve` is no longer cut into `8 5x11` or `3 4`.
  A tag written with a capital `İ` (`İstanbul poster`) is the same tag as `istanbul
  poster` when the SEO page suggests tags and when tags are checked for duplicates.
- **Dropping files on Tasarım Yükle.** A product folder dropped again with its new
  `dosyalar` no longer turns into a `-2` copy (the photos go up before the download
  files), and dropping only the missing `dosyalar` of a product adds it to that product.
  A corrected download file replaces the old one, which is kept in `archive`. A
  `dosyalar` folder means download files only for a digital template. A product folder
  named `1-MOCKUPS`, `2-PRODUCTS` or `3-DRAFTS` is refused instead of vanishing.
- **An image that fails on a digital draft** no longer leaves out its download files:
  they are still sent, and the message says what happened to them.

### Removed

- The Tk window, and with it the need for `tkinter`.

## [0.2.0] — 2026-09-26

### Added

- **A desktop app — no terminal, no Python.** Download one file from the Releases page:
  a single `.exe` for Windows, or `stallkit.app` for Apple Silicon Macs. The everyday
  commands have buttons, grouped into Setup, Upload products, Listings, Orders, SEO and
  Pinterest tabs, with a log underneath showing exactly what ran and what came back. The buttons
  run the same commands as the terminal, so validation, dry runs and error messages are
  identical. Anything that reaches the live shop asks first in a dialog. The window is
  in English and Turkish and follows the system language. Keys and tokens live in
  `~/.stallkit`, shared with the command line.
- **`stallkit desktop`** opens the same window from an installed copy, and the
  downloaded app runs any command when given arguments (`stallkit.exe pinterest post`),
  so it can be scheduled without Python installed.
- **Connecting a shop, step by step.** The Setup tab walks through Etsy's *Seller App*
  (July 2026: two fields, usually approved in minutes) with everything to paste into
  Etsy's form behind a Copy button, checks the keys the moment they are saved, and ticks
  each step as it is done. It tells apart keys Etsy refused, a shop not yet connected, a
  sign-in that expired, and Etsy being unreachable. Waiting for the browser can be
  cancelled. The page Etsy sends the browser back to now says, in English and Turkish,
  to return to the app.
- **Several shops on one computer.** Each shop has its own keys, sign-in, Pin queue and
  products folder. The window has a shop picker with *Add a shop*; the command line has
  `stallkit shops list|add|remove` and a global `--shop <id>` (or STALLKIT_SHOP, checked
  the same way). One shop keeps everything in `~/.stallkit`, exactly as before; a further
  shop never reads a `.env` from the working directory, so it cannot borrow another
  shop's keys.

### Changed

- **`stallkit init` writes `~/.stallkit/.env`** (the selected shop's home with `--shop`)
  instead of `./.env`, so the keys it saves are the ones the desktop app reads. `--path`
  still writes anywhere, and a `./.env` is still read for the first shop.
- **Etsy's trademark notice** is shown in the window and the README, as Etsy's API Terms
  require of every application.
- **Release builds on GitHub.** Pushing a version tag builds both apps on GitHub's
  runners, checks that each packaged app starts and builds its window, and publishes
  them to a GitHub Release.
- **Pinterest, optional.** `stallkit pinterest` turns an active listing's photos into
  Pins linking back to it, on the seller's own Pinterest account through their own app.
  Pins are queued and posted a few a day across the whole queue, never twice for the
  same image and board, and a Pin that was sent but not confirmed is parked for a human
  rather than re-sent. `--ai-modified` sets Pinterest's AI disclosure.
- **Variations on new drafts.** `listings push --inventory-from <listing_id>` copies that
  listing's options — properties, per-option prices and quantities, and processing
  profile — onto every draft it creates, and `drop auto` does the same from its template
  listing. A draft whose options cannot be set is reported as partial, never as done.
- **Processing profiles.** `readiness_state_id` is a listing column, captured by
  `drop template` and sent on create. When it is present the older processing day counts
  are not sent alongside it.

### Fixed

- **An `https://localhost` callback no longer hangs the sign-in.** The local listener
  speaks plain HTTP, so it is now used only for `http://localhost`; an https callback
  takes the paste flow.
- **A token request Etsy refuses for its format is retried as JSON.** Some apps get a
  403 "should be in the format 'keystring:shared_secret'" for a form-encoded token
  request that Etsy accepts as JSON (etsy/open-api#1678).
- **A refused tracking upload says why.** Etsy restricts tracking uploads for newer API
  keys in many countries, Türkiye included; the 403 now says so instead of suggesting a
  missing scope.
- **Etsy's error text on the local sign-in page is escaped.**
- **A listing takes twenty images, not ten.** Etsy's API schema allows up to 20, and a
  listing with more than ten photos was refused locally for no reason.
- **Physical drafts are accepted by Etsy again.** Etsy now refuses a physical create
  without a processing profile; the template's profile is carried onto the draft.
- **A template with variations no longer poisons every draft's quantity.** Etsy reports
  a varied listing's quantity as the total across its options, far above the 999 it
  accepts on a create. The copied figure is capped, and anything over 999 is caught
  locally before it is sent.

## [0.1.0] — 2026-09-25

First public release.

### Added

- **Setup that checks as it goes.** `stallkit init` writes `.env` with the shared secret
  typed hidden and verifies the credential against Etsy. `stallkit setup` walks every
  prerequisite and names the single next command; `stallkit doctor` runs the same
  checklist without asking anything, for scripts.
- **OAuth 2.0 with PKCE.** A one-shot `localhost` listener catches the redirect, or you
  paste the address back. Tokens live in `~/.stallkit/token.json` with `0600`
  permissions and refresh themselves, including mid-batch.
- **Bulk listings from a spreadsheet.** `listings template`, `listings pull` and
  `listings push`. An empty `listing_id` creates a draft, a filled one updates. The whole
  file is validated before anything is sent — titles, the 13-tag and 20-character rules,
  enum values, prices, image count, format and size — and one bad row stops the run
  unless `--partial` is given. New listings are always drafts.
- **Orders and tracking.** `orders pull` exports orders to CSV (`--since`, `--unshipped`),
  `orders carriers` lists valid carrier names per country, and `orders ship` uploads
  tracking in bulk after a dry run and a confirmation.
- **SEO.** `seo audit` scores every listing out of 100, worst first, plus shop-level
  checks for listings competing for the same searches. `seo keywords` samples what
  actually ranks for a term — tags, title phrases, price band — with only a keystring.
  `seo suggest` combines both for one listing.
- **Designs in, drafts out.** `drop init` creates a workspace folder; `drop template`
  copies business settings from a listing you built by hand; `drop run` composites
  loose artwork onto your mockups, researches each concept, writes titles and tags
  within Etsy's limits and produces a `review.csv` without sending anything; `drop auto`
  uploads folders of finished photos as drafts, with an upload history that prevents
  duplicates.
- **Print-area calibration.** `drop calibrate` sets where a design lands on each mockup,
  shares it with every mockup of the same size, imports a pixel-based file, and draws
  the rectangle on the mockup so it can be checked before it is saved.
- **Images that match what the seller sees.** Phone photos are turned upright from their
  EXIF orientation, colour profiles are converted to sRGB, transparent templates are
  flattened onto white, 16-bit greyscale keeps its tone, and JPEGs keep full colour
  resolution. Formats Etsy refuses are converted, and the row says so.
- **`--anonymise`** hides shop name, ids, titles, URLs and tags in terminal output so a
  screenshot can be shared.

### Notes on Etsy's API

Recorded here and in the code so nobody has to re-derive them:

- `x-api-key` must carry **both** the keystring and the shared secret, colon-joined, on
  every request including unauthenticated ones. PKCE removes the client secret from the
  *token exchange* only — not from this header.
- Callback URLs may be `http://` or `https://`, and the host must be a **domain name**.
  `localhost` is accepted; `127.0.0.1` is rejected. Etsy's prose docs say https-only,
  which is narrower than what is enforced and leads you to build the wrong flow.
- Rate limits are **per app**. A Personal Access app gets 5 requests/second and 5,000
  per day — not the 10/sec and 10,000/day the general documentation quotes.
- Array form fields such as `tags` and `materials` are **comma-joined strings**, not
  repeated keys. Repeated keys silently drop all but one value.
- `createDraftListing` takes form encoding; `createReceiptShipment` takes JSON.
- Listing images must be JPG, PNG or GIF, at most 20MB, and at most twenty per listing.
- A physical listing needs a processing profile (`readiness_state_id`) on create, and a
  listing's quantity may not exceed 999 — though a listing with variations *reports* the
  total across its options, which usually does.
- There is no idempotency key, so non-idempotent writes are never retried on a timeout
  or a 5xx — a repeat would mean a duplicate listing, or a second email to a buyer.

[Unreleased]: https://github.com/MoneyPrintLabs/stallkit/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/MoneyPrintLabs/stallkit/releases/tag/v0.3.0
[0.2.0]: https://github.com/MoneyPrintLabs/stallkit/releases/tag/v0.2.0
[0.1.0]: https://github.com/MoneyPrintLabs/stallkit/releases/tag/v0.1.0
