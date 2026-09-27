# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- **Etsy's trademark notice** is shown on every screen.
- **Digital products.** A template listing of type `download` (or `both`) makes drafts of
  that type, in the app and with `drop run` / `drop auto`. After its images each draft gets
  the files the buyer downloads (Etsy's `uploadListingFile`): a loose design is delivered
  as the design file itself; a product folder delivers the files in its `dosyalar` (or
  `files`) subfolder, while the photos in the folder stay the listing's images. At most 5
  files per listing and 20 MB per file (Etsy's seller limits; the API spec states none);
  programs and scripts are refused. A download-only template needs no shipping profile.
  A product with nothing to deliver, too many files or an oversized file stops with its
  own message before its draft is made; the upload history records the files sent.
  **Tasarım Yükle** and **Şablon İlan** show the product type and what the buyer gets.

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
  main folder, and the folders it left inside `2-PRODUCTS` are never taken for products.

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

[Unreleased]: https://github.com/MoneyPrintLabs/etsyprinting/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/MoneyPrintLabs/etsyprinting/releases/tag/v0.3.0
[0.2.0]: https://github.com/MoneyPrintLabs/etsyprinting/releases/tag/v0.2.0
[0.1.0]: https://github.com/MoneyPrintLabs/etsyprinting/releases/tag/v0.1.0
