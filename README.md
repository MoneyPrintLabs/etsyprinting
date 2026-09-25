# stallkit

[![CI](https://github.com/MoneyPrintLabs/etsyprinting/actions/workflows/ci.yml/badge.svg)](https://github.com/MoneyPrintLabs/etsyprinting/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/downloads/)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-green)](LICENSE)
[![Etsy Open API v3](https://img.shields.io/badge/Etsy-Open%20API%20v3-orange)](https://developers.etsy.com/documentation/)

Open-source command line automation for Etsy sellers, built on the official
**Etsy Open API v3**. Bulk listing management, order and tracking sync, and SEO
analysis — running locally, on your own machine, against your own Etsy app.

There is no hosted service, no account, and no middleman. You create an Etsy API
app, paste the keystring into a `.env` file, and everything runs from your terminal.
Your data never leaves your computer.

```bash
stallkit listings pull -o my-listings.csv     # export what you have
stallkit listings push new-products.csv       # create 200 drafts from a spreadsheet
stallkit orders pull --unshipped -o today.csv # today's orders to fulfil
stallkit orders ship tracking.csv             # upload tracking, notify every buyer
stallkit seo audit                            # score every listing, worst first
stallkit seo keywords "ceramic mug"           # what actually ranks, and why
```

---

## Contents

- [Why this exists](#why-this-exists)
- [Install](#install)
- [Getting an Etsy API key](#getting-an-etsy-api-key)
- [First run](#first-run)
- [Bulk listings](#bulk-listings)
- [Orders and tracking](#orders-and-tracking)
- [SEO](#seo)
- [Pinterest (optional)](#pinterest-optional)
- [Command reference](#command-reference)
- [How it behaves](#how-it-behaves)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)

---

## Why this exists

Most Etsy tools are SaaS: monthly fee, your shop token on someone else's server,
and a black box between you and the API. stallkit is the opposite — a small Python
package you can read end to end in an afternoon. Everyone runs their own copy with
their own credentials, so nobody pays for anybody else's usage.

**What it does not do:** it does not scrape Etsy. Every number it reports comes from
the official API. It also does not invent search volume figures — Etsy does not
publish them, and any tool that shows them is guessing. What `seo keywords` gives you
is measured from the listings Etsy actually returns for a term.

---

## Install

Requires Python 3.9 or newer.

> **Setting up for the first time? Read [SETUP.md](SETUP.md)** — it lists every
> prerequisite and why each one is not optional, in English and Turkish. Or just run
> **`stallkit setup`**, which walks the list, asks about the parts no program can check,
> and tells you the single next command to run.

```bash
git clone https://github.com/MoneyPrintLabs/etsyprinting.git
cd etsyprinting
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e .
```

Check it:

```bash
stallkit --version
```

---

## Getting an Etsy API key

1. Go to **<https://www.etsy.com/developers/your-apps>** and click *Create a New App*.
2. Fill in the form. For personal use on your own shop, describe what you are building
   honestly — "personal tools for managing my own shop listings and orders" is fine.
3. Once approved you get a **Keystring** and a **Shared secret**. You need **both**.

   > Every v3 request must carry them colon-joined in the `x-api-key` header, including
   > the unauthenticated ones. With the keystring alone Etsy replies
   > `403 {"error":"Invalid API key: should be in the format 'keystring:shared_secret'."}`
   >
   > This trips people up because PKCE is described as removing the client secret — and
   > it does, from the **token exchange** (`client_id` is the bare keystring). It does
   > not remove the shared secret from the **API key header**. stallkit joins them for you;
   > you just set `ETSY_KEYSTRING` and `ETSY_SHARED_SECRET`.
4. In the app settings, add a **callback URL**. Etsy's own settings screen states the
   rules, and they are narrower than the prose docs suggest:

   > - Must start with `http://` or `https://`
   > - Host must be a domain name (e.g. `example.com`)
   > - **IP addresses are not allowed** (e.g. `127.0.0.1`)

   `localhost` is a domain name, so **a local callback works** — which makes this the
   easy path. Register:

   ```
   http://localhost:3003/oauth/redirect
   ```

   and put the identical string in `.env` as `ETSY_REDIRECT_URI`. stallkit starts a
   one-shot listener on that port and catches the code for you.

   > Etsy's narrative documentation says the callback "must implement TLS and use an
   > `https://` prefix". Taken literally that rules out a local listener — but the app
   > settings screen accepts `http://localhost:PORT/...`, and that is what is actually
   > enforced. Use `127.0.0.1` and it *will* be rejected; that is the real constraint.

   Any other host works too: Etsy redirects your browser there, that page does not need
   to exist, and you paste the address back in once (`--paste` forces this flow).

> **Approval times vary.** Personal-use apps are usually approved quickly; apps that
> ask for broad commercial distribution take longer.
>
> **While you wait**, `stallkit listings push --dry-run` and `stallkit orders ship --dry-run`
> validate your CSVs entirely offline — no key, no login. You can have 300 products
> checked and ready before your app is approved. Once you have a keystring,
> `stallkit seo keywords` works too, without logging in.

---

## First run

```bash
stallkit init
```

It asks for your keystring, your shared secret and your callback URL, writes a `.env`
with `0600` permissions, and checks the credential against Etsy before you go further.
**The shared secret is typed hidden** — it does not appear on screen or in your shell
history. Nothing is sent anywhere except Etsy.

```
Keystring: abc123def456ghi789jkl012
Shared secret:
Redirect URI (must match a callback registered on your app) [http://localhost:3003/oauth/redirect]:
✓ Wrote .env (keystring abc123…, shared secret 10 chars)
✓ Etsy accepted the credential.
```

Prefer to write the file yourself? `cp .env.example .env` and fill in the three values.

Verify your setup before trusting it with anything bulk:

```bash
stallkit doctor
```

```
✓ App credentials found (keystring abc123…, shared secret 10 chars)
✓ Redirect URI looks valid (http://localhost:3003/oauth/redirect)
  rate limit:   4 req/sec
✓ API reachable and keystring accepted
! No token stored — only `seo keywords` will work. Run: stallkit auth login
```

Then authorise:

```bash
stallkit auth login
```

A browser opens and you approve the scopes. With a `localhost` callback, stallkit catches
the redirect itself and you are done:

```
Listening on port 3003 for the redirect to http://localhost:3003/oauth/redirect ...
✓ Authorised. Token saved to /home/you/.stallkit/token.json
  scopes: shops_r listings_r listings_w transactions_r transactions_w
  shop:   YourShop (id 12345678)
```

With any other callback host, Etsy sends the browser to your registered URL — that page
does not have to load — and you paste the address from the bar back into the terminal.

The token is saved to `~/.stallkit/token.json` with `0600` permissions. Access tokens
last an hour and stallkit refreshes them automatically; the refresh token lasts 90 days,
so you do this once a quarter at most.

```bash
stallkit auth status     # who am I, which shop, how long is the token good for
stallkit shop info       # shop id, currency, listing counts
```

### Scopes

The default is the minimum needed for everything in this tool:

| Scope | Used for |
|---|---|
| `shops_r` | discovering your shop; required even for `auth status` |
| `listings_r` | reading your listings (`listings pull`, `seo audit`) |
| `listings_w` | creating and updating listings, uploading images |
| `transactions_r` | reading orders (`orders pull`) |
| `transactions_w` | submitting tracking numbers (`orders ship`) |

If you only want read access, narrow it in `.env` before logging in:

```bash
ETSY_SCOPES=shops_r listings_r transactions_r
```

There is deliberately **no `listings_d`** — stallkit never deletes a listing.

---

## Bulk listings

### The CSV

```bash
stallkit listings template -o listings.csv
```

One row per listing. Two rules:

- **`listing_id` empty → create.** **`listing_id` filled → update.**
- Multi-value cells (`tags`, `materials`, `images`) are separated by `|`, not commas,
  so a tag containing a comma survives a trip through Excel.

| Column | Required to create | Notes |
|---|---|---|
| `listing_id` | — | Leave blank to create a new draft |
| `title` | ✔ | Max 140 characters |
| `description` | ✔ | |
| `price` | ✔ | `19.90` or `19,90` both work |
| `quantity` | ✔ | |
| `who_made` | ✔ | `i_did`, `someone_else`, `collective` |
| `when_made` | ✔ | `made_to_order`, `2020_2026`, `2010_2019`, … |
| `taxonomy_id` | ✔ | Find it with `stallkit shop taxonomy <word>` |
| `shipping_profile_id` | ✔ for physical | Find it with `stallkit shop profiles` |
| `type` | — | `physical` (default), `download`, `both` |
| `tags` | — | Max 13, each max 20 chars |
| `materials` | — | Max 13 |
| `images` | — | Paths **relative to the CSV file**, in display order |
| `state` | — | Update only: `active` or `inactive` |

Get the IDs you need:

```bash
stallkit shop profiles                 # shipping_profile_id, return_policy_id, shop_section_id
stallkit shop taxonomy "mug"           # taxonomy_id, ranked with leaf categories first
```

> [`examples/listings.csv`](examples/listings.csv) ships with a **placeholder**
> `shipping_profile_id` of `123456789` so that it passes `--dry-run` out of the box.
> Replace it with a real id from `stallkit shop profiles` before pushing for real, or
> Etsy will reject the row.

### Validate, then push

Always dry-run first. It validates every row locally — title lengths, tag charset and
count, enum values, more than twenty images on a row, missing image files — and sends
nothing.

```bash
stallkit listings push listings.csv --dry-run
```

```
· row 2  create: Handmade Ceramic Coffee Mug (11 fields, 2 image(s))
✗ row 3  Wooden Lamp — tag 'scandinavian minimalist lamp' is 28 chars, max 20
· row 4  create: Linen Table Runner (10 fields, 1 image(s))

✓ Dry run: 2 row(s) valid, 1 with problems. Nothing was sent.
```

Fix row 3, then push for real:

```bash
stallkit listings push listings.csv --out results.csv
```

**New listings are always created as drafts.** Nothing goes public until you publish
it from your Etsy dashboard — so a mistake in a 300-row CSV is recoverable.
`results.csv` contains the new `listing_id` for every created row; paste that column
back into your source CSV and subsequent pushes become updates.

**The whole file is validated before anything is sent.** If any row fails, the run stops
with nothing written — because discovering that row 40 is invalid *after* rows 1–39 became
real drafts leaves your shop half-populated from a file you would never have pushed.
Fix the reported rows and run again, or pass `--partial` to push the valid ones anyway.

If a listing is created but one of its images fails to upload, the row is reported as
**`partial`**, not as an error: the draft exists in your shop and you need to know about
it. stallkit holds no delete scope, so it cannot undo the create — it tells you instead.

### Round-tripping existing listings

```bash
stallkit listings pull -o current.csv        # edit titles/tags in a spreadsheet
stallkit listings push current.csv           # push the edits back
```

`pull` writes the same columns `push` reads, with `listing_id` already filled in.

> `price` and `quantity` are **not** sent on updates. On a listing with variations they
> live in Etsy's separate inventory endpoint, and patching them here would flatten your
> variation pricing. Change those in Etsy.
>
> **New drafts can carry variations.** `listings push --inventory-from <listing_id>` copies
> that listing's options — every material and size, with its own price, quantity and
> processing profile — onto each draft it creates. Build one listing properly in Etsy and
> every draft after it can have the same option grid.

---

## Drop designs, get drafts

### Ready mockups: one folder, one listing

Put all finished photos for a product in its own folder. The folder name describes
the product and supplies the title/keyword concept; image names set their order.
Ready images are uploaded unchanged, even when a PNG has transparency. Etsy accepts
only JPG, PNG and GIF, so a `.webp`, `.tif` or `.bmp` photo is converted to JPEG in
the batch folder first and the conversion is listed in `review.csv`. Anything over
Etsy's 20MB per-image limit fails `--dry-run` rather than the upload.

```text
Etsy Studio/
  product.json
  2-PRODUCTS/
    mountain sunset shirt/
      01-front.jpg
      02-back.jpg
      03-detail.png
    ceramic coffee mug/
      01-cover.jpg
      02-detail.jpg
```

After the usual Etsy login and one-time `stallkit drop template --from-listing ID`:

```bash
stallkit drop auto --dry-run   # offline preparation and validation
stallkit drop auto            # prepare and upload new products as Etsy drafts
```

Use `--path "C:\path\to\Etsy Studio"` to select another workspace. Each command
processes the current batch once; it does not watch the folder in the background.
`auto` uploads immediately without another confirmation prompt. It never publishes.
Run it again after adding more product folders. `drop run` still offers the existing
CSV-only review workflow and now also understands ready-photo folders.

- One immediate child folder = one listing, with up to 20 images in natural filename
  order (`1`, `2`, `10`). Extra images cause an error, not silent truncation. Keep
  finished listing images directly inside each product folder, without nested folders.
- Loose images retain the original one-design-per-listing mockup workflow below.
- Titles and tags use the product name and available Etsy research. Descriptions
  inherit your template; this does not analyze images with AI. Name folders
  descriptively and use a template matching the product, price and shipping settings.
- `upload-history.json` records attempted products per shop. Keep this file and
  keep product folder names stable: completed products are skipped even if edited.
  Renaming/moving a product or deleting its history can create a duplicate.
- Interrupted, failed or partial uploads are **not retried automatically**. The
  command reports that Etsy review is needed and exits with an error. Check the
  saved listing ID in the history and complete that draft in Etsy; only reset its
  history entry after confirming no draft was created. A stale `.auto-upload.lock`
  may be removed only after confirming the previous process is stopped.
- Automatic upload supports physical-product templates. The template listing's
  variations (options, prices, quantities, processing profile) are copied onto every
  draft. Digital delivery file uploads are not implemented. Source files remain in
  place; there is no automatic archive move.

### Compositing loose designs

For print-on-demand: put designs in a folder, get composited mockups and a ready-to-push
CSV. No spreadsheet to fill in by hand.

```bash
stallkit drop init                                # creates ~/Desktop/Etsy Studio
stallkit drop template --from-listing 1234567890  # copy settings from a listing you built
stallkit drop run                                 # designs in → review.csv out
```

**You build the first listing yourself, in Etsy, properly.** Everything after copies it.
That is not laziness on the tool's part: `taxonomy_id`, `shipping_profile_id`,
`return_policy_id`, `who_made`, `when_made`, processing times and price are decisions
about a business, not facts about a picture. Guessing them would put wrong listings in a
real shop.

The workspace is three folders:

| Folder | What goes in it |
|---|---|
| `1-MOCKUPS` | Your mockup templates — a blank shirt, mug, poster. Once. |
| `2-PRODUCTS` | The designs you want listed. This is the one you use every time. |
| `3-DRAFTS` | What comes out: composited images and `review.csv`. |

`drop run` composites each design onto every mockup, appends the flat artwork, works out
the product concept, researches it against listings that actually rank, and writes titles
and 13 tags inside Etsy's limits. A listing holds twenty images, so `--mockups 20` and the
flat render together are one too many: the run says so and stops before compositing
anything, instead of building a batch Etsy would only half-accept. **Nothing is sent to
Etsy.** Check `review.csv`, then:

```bash
stallkit listings push "3-DRAFTS/2026-09-25-120000-000000/review.csv" --dry-run
stallkit listings push "3-DRAFTS/2026-09-25-120000-000000/review.csv"      # creates drafts
```

Print areas are stored as **fractions** of the mockup, not pixels, in
`1-MOCKUPS/positions.json`. One calibrated rectangle therefore covers every sibling
mockup of the same dimensions, and a sensible default works before you calibrate anything.

### Moving the print area

When a design lands in the wrong place, move the rectangle and look at it before you
keep it:

```bash
stallkit drop calibrate                              # what every mockup uses today
stallkit drop calibrate --mockup shirt-white.jpg --area 0.30,0.26,0.40,0.36 --dry-run
stallkit drop calibrate --mockup shirt-white.jpg --area 0.30,0.26,0.40,0.36 --same-size
```

`--dry-run` writes `3-DRAFTS/calibration/shirt-white--area.jpg` — the mockup with the
rectangle drawn on it — and saves nothing. That picture is the whole interface: there is
no GUI here, and four numbers do not tell you where a design will sit on a photograph of
a shirt. Repeat until it looks right, then run the same command without `--dry-run`.

| Option | What it does |
|---|---|
| `--mockup NAME` | Which template. The extension is optional when the stem is unique. |
| `--area x,y,w,h` | The rectangle, as fractions of that mockup, each between 0 and 1. |
| `--same-size` | Give it to every mockup of the same pixel dimensions — the reason fractions are stored at all. |
| `--reset` | Forget one saved area and go back to the default. |
| `--import FILE` | Convert a pixel-based `mockup-positions.json` from an older tool. |
| `--preview` | Draw the areas without changing them. |

`positions.json` is plain enough to edit by hand if you would rather. Keys are mockup
filenames *with* their extension — that is what the compositor looks up — and values are
fractions of that mockup:

```json
{
  "shirt-white.jpg": { "x": 0.30, "y": 0.26, "w": 0.40, "h": 0.36 }
}
```

A mockup with no entry uses the default, `0.30,0.26,0.40,0.36`. Rename a mockup and its
entry stops applying; `stallkit drop calibrate` says so rather than letting you wonder.

Two things it will tell you rather than hide:

- **A filename it cannot read is skipped, not guessed.** `mountain-sunset.png` gives a
  concept; `IMG_2043.png` does not, and inventing a confident title for it would put the
  wrong listing in your shop. A subfolder is a *ready-photo product*, not a set of loose
  designs — its name becomes the concept and its images are uploaded as they are, so put
  artwork that still needs a mockup directly in `2-PRODUCTS`. If a transparent file turns
  up inside a product folder, the row says so rather than shipping it uncomposited.
- **Thin or absent market data is stated on the row.** Without research the titles are
  shorter and fewer of the 13 tag slots fill — and the CSV says so in its `warnings`
  column instead of padding them out with something invented.

The drop flow never writes a `listing_id`, and Etsy only accepts a state change on an
update — so it is structurally incapable of publishing anything.

---

## Orders and tracking

```bash
stallkit orders pull --since 30d -o orders.csv
stallkit orders pull --unshipped -o to-ship.csv
```

`--since` accepts `30d`, `6w`, `3m`, `1y`, or a date like `2026-01-01`.

One row per order, with line items collapsed into a readable cell and the shipping
address split into its own columns.

> ⚠️ **This file contains your customers' personal data** — names, email addresses,
> postal addresses and gift messages. Do not commit it, paste it into an issue, or share
> it. `.gitignore` covers `*.csv` for exactly this reason, but a file you move elsewhere
> is no longer protected.

### Uploading tracking

Add `tracking_code` and `carrier_name` columns (the exported file already has
`receipt_id`), or start from [`examples/tracking.csv`](examples/tracking.csv):

```csv
receipt_id,tracking_code,carrier_name,note_to_buyer,send_bcc
3021456789,1Z999AA10123456784,ups,Thanks! On its way.,false
```

`carrier_name` must be a value Etsy recognises for your shipping origin:

```bash
stallkit orders carriers --country TR
stallkit orders ship tracking.csv --dry-run --country TR   # validate carriers too
stallkit orders ship tracking.csv
```

> **This one is not reversible.** Etsy emails every buyer and marks each order shipped.
> stallkit asks for confirmation first and supports `--dry-run`; use both.

---

## SEO

### Audit your own listings

```bash
stallkit seo audit -o seo-report.csv
```

Scores every listing out of 100 and prints the weakest first. The checks are Etsy's
documented limits plus how its search surface actually behaves:

- **Tags** — unused slots out of 13, over-length tags, exact duplicates, near-duplicates
  that burn two slots on one query (`gift` / `gifts`), too many single-word tags,
  and tags sharing no word with the title.
- **Titles** — length, keyword buried past the ~40-character truncation point,
  repeated words, comma chains, shouting.
- **Descriptions** — thin content, and openings that repeat none of the title keywords
  (that first paragraph is the snippet Google shows).
- **Housekeeping** — missing materials, auto-renew off, expired listings.

```
  score  listing_id  title                            issues
     45  1234567890  Mug                              title is only 3 chars…; 4/13 tags used…
     62  1234567891  Handmade Ceramic Coffee Mug…     9/13 tags used — 4 slot(s) left…
```

### Research a keyword

```bash
stallkit seo keywords "ceramic mug" --sample 300 -o mug-tags.csv
```

Samples the listings Etsy ranks for a term and reports what they have in common:
tag frequency with the share of listings using each, recurring title phrases
(1–3 word n-grams), the price band, and the most-favourited listings in the sample.

**This works with only your keystring — no login required.**

### Both at once, for one listing

```bash
stallkit seo suggest 1234567890
```

Audits that listing, then researches its own keyword and lists tags used by ranking
competitors that you are not using yet, with the share of ranking listings that use each.

Add tags only if they honestly describe your item. Irrelevant tags pull in traffic that
does not convert, and Etsy weights conversion heavily.

---

## Pinterest (optional)

Once a listing is live, `stallkit pinterest` turns its photos into Pins that link back
to it — on **your own** Pinterest account, through **your own** Pinterest app. It is
entirely optional: nothing Pinterest-related runs unless you set it up.

**Setup, once:**

1. Use a Pinterest **business** account (free to convert).
2. Create an app at <https://developers.pinterest.com/apps/> and add the redirect URI
   `http://localhost:8085/` to it.
3. Put the app's id and secret in `.env`:

   ```bash
   PINTEREST_APP_ID=...
   PINTEREST_APP_SECRET=...
   ```

4. Connect: `stallkit pinterest login`, then `stallkit pinterest boards` to see your boards.

> New Pinterest apps start on **trial access**. If Pinterest only lets your app write to
> its sandbox, generate a sandbox token in the developer portal and set
> `PINTEREST_SANDBOX=1` and `PINTEREST_ACCESS_TOKEN=...` until standard access is granted.

**Queue, then post a few a day.** Pinterest treats a burst of Pins pointing at one link
as spam, so Pins are queued first and posted gradually:

```bash
stallkit pinterest queue 4001 4002 --board "Bedroom Wallpaper" --images 1-6 --per-day 2
stallkit pinterest post          # posts whatever is due today — run it once a day
stallkit pinterest list          # what is posted, waiting, or needs a look
```

- `--per-day` counts the **whole queue**, so queueing several listings in one sitting
  still comes out at that many Pins a day.
- Each Pin's title is the listing title cut to Pinterest's 100 characters on a `|`
  boundary; the description is built from the title's phrases and the listing's tags.
  `--description` overrides it.
- `--images 1-6` picks which listing photos become Pins — leave out size charts and info
  cards, which make poor Pins.
- `--ai-modified` declares the imagery as AI-created or AI-modified, which Pinterest asks
  creators to disclose. If your designs are AI-assisted, use it.
- Only **active** listings can be queued: a Pin to a draft would lead nowhere.
- The same image is never queued twice for the same board.
- A Pin that was sent but not confirmed (a timeout, a server error) is marked
  **uncertain** and never re-sent automatically — it may already exist. Check the board,
  then `stallkit pinterest retry <listing_id>` if it did not land.

To post daily without thinking about it, schedule `stallkit pinterest post` with Windows
Task Scheduler or cron.

---

## Command reference

| Command | What it does |
|---|---|
| `stallkit init` | Write `.env` interactively and verify the credential |
| `stallkit doctor` | Check config, key and connectivity |
| `stallkit auth login` | OAuth consent flow (PKCE) |
| `stallkit auth status` | Token, scopes, shop, remaining daily quota |
| `stallkit auth refresh` | Force a token refresh |
| `stallkit auth logout` | Delete the stored token |
| `stallkit shop info` | Shop identifiers and headline numbers |
| `stallkit shop profiles` | Shipping profiles, return policies, sections |
| `stallkit shop taxonomy <word>` | Find a `taxonomy_id` |
| `stallkit drop init` | Create the designs-in workspace folder |
| `stallkit drop template` | Copy settings from a listing you built by hand |
| `stallkit drop run` | Designs → mockups, titles, tags → `review.csv` |
| `stallkit drop calibrate` | Move a mockup's print area, with a preview image to check it |
| `stallkit drop auto` | Product folders → prepared copy and images → Etsy drafts, with upload history |
| `stallkit listings template` | Write a starter CSV |
| `stallkit listings pull` | Export listings to CSV |
| `stallkit listings push` | Bulk create/update from CSV |
| `stallkit orders pull` | Export orders to CSV |
| `stallkit orders carriers` | Valid `carrier_name` values for a country |
| `stallkit orders ship` | Bulk tracking upload |
| `stallkit seo audit` | Score all listings |
| `stallkit seo keywords` | Market research for a term |
| `stallkit seo suggest` | Audit + tag suggestions for one listing |
| `stallkit pinterest login` | Connect your own Pinterest account (optional) |
| `stallkit pinterest boards` | List your Pinterest boards |
| `stallkit pinterest queue` | Queue Pins for active listings, spread over days |
| `stallkit pinterest post` | Post the Pins due today |
| `stallkit pinterest list` | Show the Pin queue |
| `stallkit pinterest retry` | Re-queue failed or uncertain Pins after checking |

Every command supports `--help`.

---

## How it behaves

**Rate limiting.** Limits are **per app**, and your app's real allowance is printed on its
row at [your-apps](https://www.etsy.com/developers/your-apps). A **Personal Access** app
gets **5 QPS / 5,000 per day** — not the 10/sec, 10,000/day the general docs quote, which
applies to apps granted commercial access. stallkit defaults to **4/second** so it is safe
on the personal tier; raise it with `STALLKIT_RATE_PER_SEC` if your app is allowed more.
`auth status` shows the remaining daily quota.

**Retries.** `429` is retried on any request — it means Etsy refused, not that it acted.
`5xx` and network errors are retried **only on reads**. Etsy has no idempotency key, so a
write that may have landed is never repeated: a retried `POST` would mean a duplicate
draft, or a second "your order shipped" email to the same buyer. Those are reported
instead, with a warning that the request may have been accepted. Up to 5 attempts,
exponential backoff with jitter, honouring `Retry-After`. Other `4xx` errors are not
retried — they are reported with a hint about the likely cause.

**Token refresh.** Handled transparently, including a re-refresh if a token expires
mid-batch.

**All-or-nothing by default.** `listings push` validates every row before it sends
anything. One bad row stops the run with nothing written; `--partial` opts back into
row-by-row. You get a per-row report and a non-zero exit code if anything failed. For
cron or CI, pass `--yes` (`-y`) to `listings push` and `orders ship` — without it they
stop at an interactive confirmation and a scheduled job would hang.

**Local validation is strict about numbers.** A negative price, a zero price, a negative
quantity or a fractional `quantity` like `3.9` are all rejected here rather than rounded
or forwarded. On an update, fields Etsy's `updateListing` does not accept — `price` and
`quantity` among them — are reported as ignored instead of silently dropped.

**Encoding.** CSVs are read and written as UTF-8 with BOM so Excel on Windows does not
mangle `ç`, `ğ`, `ü`, `é` or `ß`. Prices accept a decimal comma.

**Secrets.** The keystring lives in `.env` (git-ignored). The token lives in
`~/.stallkit/token.json`, written `0600`. Neither is ever printed in full.

**Sharing output.** Terminal output gets screenshotted more often than anyone plans
for, and a listing title is enough to find the shop it belongs to. `--anonymise`
(or `STALLKIT_ANONYMISE=1`) hides your shop name, ids, titles, URLs and tags while
leaving the findings readable — so a screenshot can be posted without exposing the shop:

```bash
stallkit --anonymise seo audit
```

### Development

```bash
pip install -e ".[dev]"
pytest          # unit tests — no network, no credentials needed
ruff check .
```

The test suite covers CSV validation, Etsy's tag and title rules, form encoding,
receipt flattening and the SEO scoring, so you can refactor without a live shop.

---

## Troubleshooting

**`ETSY_KEYSTRING is not set`** — copy `.env.example` to `.env` and paste your keystring,
or export it in your shell.

**`Etsy does not accept IP addresses in a callback URL`** — use `localhost`, not
`127.0.0.1`. Etsy requires a domain-name host; `localhost` qualifies, a bare IP does not.

**Etsy shows "redirect_uri is not valid"** — the callback registered on your app does not
match `ETSY_REDIRECT_URI` byte for byte. Compare them character by character, including
the scheme, the port, any trailing slash, and the path.

**`Cannot listen on 127.0.0.1:3003`** — something else holds that port. Pick another,
change it in **both** `.env` and your Etsy app's callback list, or use `--paste`.

**The callback page shows an error / does not load** — harmless when you are using the
paste flow. The authorization code is in the browser's address bar; copy the whole
address and paste it in.

**`403 Forbidden`** — usually a missing scope. `stallkit auth status` shows what you granted;
widen `ETSY_SCOPES` and run `stallkit auth login` again.

**`400` on listing create** — the most common causes are a `taxonomy_id` that is not a leaf
category, a missing `shipping_profile_id` on a physical listing, or a shop that requires a
`return_policy_id`. Run with `--dry-run` first; it catches most of these locally.

**Turkish/German characters look wrong in Excel** — your spreadsheet saved the file as
something other than UTF-8. Re-export as UTF-8 CSV; stallkit always writes UTF-8 with BOM.

---

## Contributing

Issues and pull requests welcome — see **[CONTRIBUTING.md](CONTRIBUTING.md)** for setup,
the rules that matter, and where help would go furthest. You need no Etsy account and no
network connection to contribute: the whole test suite is offline by design.

Useful directions: bulk inventory edits on existing listings, digital
downloads, shop section management, listing translations, and a renewal helper.

- **[SECURITY.md](SECURITY.md)** — what stallkit stores, where, and how to report a
  vulnerability privately
- **[CHANGELOG.md](CHANGELOG.md)** — release history, including the Etsy API gotchas
  this project had to establish the hard way
- **[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)**

Please keep the no-scraping rule: official API endpoints only.

---

## Licence

MIT — see [LICENSE](LICENSE). Trademark and third-party notices are in
[NOTICE.md](NOTICE.md).

stallkit is an independent project, **not affiliated with or endorsed by Etsy, Inc.**
You remain responsible for complying with the
[Etsy API Terms of Use](https://www.etsy.com/legal/api) and Etsy's seller policies.
