# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/MoneyPrintLabs/etsyprinting/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/MoneyPrintLabs/etsyprinting/releases/tag/v0.2.0
[0.1.0]: https://github.com/MoneyPrintLabs/etsyprinting/releases/tag/v0.1.0
