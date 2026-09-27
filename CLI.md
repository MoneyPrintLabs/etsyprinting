# stallkit on the command line

Everything the [app](README.md) does is also a command, for scripts, scheduled jobs and
people who prefer a terminal. The commands and the app share the same keys, sign-in and
folders in `~/.stallkit`, so you can switch between them freely.

Install from source as described in the README under
[Run from source](README.md#run-from-source-windows-macos-linux), then check it:

```bash
stallkit --version
stallkit --help          # every command supports --help
```

The downloaded app runs any command when given arguments, for example
`stallkit.exe pinterest post` from Windows Task Scheduler. It is a windowed program:
in a terminal its output appears after the prompt has already come back, and with no
terminal at all it goes to `~/.stallkit/logs/`. For everyday terminal use, install from
source.

---

## Contents

- [First run](#first-run)
- [Several shops](#several-shops)
- [Bulk listings](#bulk-listings)
- [Drop designs, get drafts](#drop-designs-get-drafts)
- [Orders and tracking](#orders-and-tracking)
- [SEO](#seo)
- [Pinterest (optional)](#pinterest-optional)
- [Command reference](#command-reference)
- [How it behaves](#how-it-behaves)
- [Troubleshooting](#troubleshooting)

---

## First run

You need your own Etsy app, its **Keystring** and **Shared secret**, and the callback
`http://localhost:3003/oauth/redirect` registered on it. The README walks through that
in [Connect your Etsy shop](README.md#connect-your-etsy-shop-once), and
[SETUP.md](SETUP.md) explains why each step matters. If you already did it in the app,
skip to `stallkit auth status`: the keys and the sign-in are already there.

> Every v3 request must carry the keystring and the shared secret colon-joined in the
> `x-api-key` header, including the unauthenticated ones. PKCE removes the secret from
> the **token exchange** (`client_id` is the bare keystring), not from the **API key
> header**. stallkit joins them for you; you just set `ETSY_KEYSTRING` and
> `ETSY_SHARED_SECRET`.

> **While you wait for Etsy's approval**, `stallkit listings push --dry-run` and
> `stallkit orders ship --dry-run` validate your CSVs entirely offline, with no key and
> no login.

```bash
stallkit init
```

It asks for your keystring, your shared secret and your callback URL, writes them to
`~/.stallkit/.env` (the selected shop's home with `--shop`) with `0600` permissions — the
same file the app reads — and checks the credential against Etsy before you go
further.
**The shared secret is typed hidden** — it does not appear on screen or in your shell
history. Nothing is sent anywhere except Etsy.

```
Keystring: abc123def456ghi789jkl012
Shared secret:
Redirect URI (must match a callback registered on your app) [http://localhost:3003/oauth/redirect]:
✓ Wrote /home/you/.stallkit/.env (keystring abc123…, shared secret 10 chars)
✓ Etsy accepted the credential.
```

Prefer to write the file yourself? Copy `.env.example` to `~/.stallkit/.env` and fill in the
three values.
A `.env` in the folder you run commands from is read too, and wins over `~/.stallkit/.env`
for the first shop; `--shop` shops only ever read their own. The app ignores that folder
`.env` and reads only `~/.stallkit`, so keys you want in both belong there.

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
| `transactions_r` | reading orders (`orders pull`) and the payment ledger (**Kâr-Zarar**) |
| `transactions_w` | submitting tracking numbers (`orders ship`) |

If you only want read access, narrow it in `.env` before logging in:

```bash
ETSY_SCOPES=shops_r listings_r transactions_r
```

There is deliberately **no `listings_d`** — stallkit never deletes a listing.

If Etsy says the callback is *not permitted*, the address in your app and the one in
stallkit differ. Any other callback works too: Etsy then sends the browser to it, that
page does not have to load, and you paste the address back in once (`--paste` forces
this flow; an `https://` callback always uses it).

---

## Several shops

One computer can run several shops. Each gets its own keys, its own sign-in and its own
products folder, so nothing crosses over — Etsy ties a sign-in to one account, and one
account has one shop.

In the app, open the shop menu at the bottom of the sidebar → **Mağaza ekle** (Add a shop)
and connect it; switch shops from the same menu. From the command line:

```bash
stallkit shops add                    # makes shop-2
stallkit --shop shop-2 auth login     # every command takes --shop
stallkit shops list
```

The first shop keeps everything in `~/.stallkit` as before; further shops live in
`~/.stallkit/shops/<id>/`.

---

## Bulk listings

### The CSV

```bash
stallkit listings template -o listings.csv
```

One row per listing. Two rules:

- **`listing_id` empty → create.** **`listing_id` filled → update.**
- Multi-value cells (`tags`, `materials`, `images`, `files`) are separated by `|`, not
  commas, so a tag containing a comma survives a trip through Excel.

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
| `files` | — | What the buyer downloads, relative to the CSV like `images`: at most 5, each up to 20 MB. New `download` / `both` drafts only; empty for `physical` |
| `state` | — | Update only: `active` or `inactive` |

Get the IDs you need:

```bash
stallkit shop profiles                 # shipping_profile_id, return_policy_id, shop_section_id
stallkit shop taxonomy "mug"           # taxonomy_id, ranked with leaf categories first
```

A `download` row needs no `shipping_profile_id` (nothing is shipped); shipping and parcel
columns on it are left out and the row says so. Without `files` a digital draft is still
created, with a warning: Etsy will not let you publish it until its file is attached. A
CSV written before the `files` column existed pushes exactly as before.

> [`examples/listings.csv`](examples/listings.csv) ships with a **placeholder**
> `shipping_profile_id` of `123456789` so that it passes `--dry-run` out of the box.
> Replace it with a real id from `stallkit shop profiles` before pushing for real, or
> Etsy will reject the row.

### Validate, then push

Always dry-run first. It validates every row locally — title lengths, tag charset and
count, enum values, more than twenty images on a row, missing image files, download
files that are missing, too many, too large or a program — and sends nothing.

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

Images go up after the draft is created, then a digital draft's `files`, in order; the
row line counts both (`2 image(s), 1 download file(s)`), and `--out` has an
`images_uploaded` and a `files_uploaded` column.

If a listing is created but one of its images or files fails to upload, the row is
reported as **`partial`**, not as an error: the draft exists in your shop and you need to
know about it. stallkit holds no delete scope, so it cannot undo the create — it tells you
instead.

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

Use `--path "C:\path\to\Etsy Studio"` to select another workspace. A folder *inside*
a workspace — `2-PRODUCTS`, a product folder in it, `1-MOCKUPS`, `3-DRAFTS` — means that
workspace: the command says `... is a folder of the workspace ...; using ...` and never
builds a second workspace inside the first. `stallkit doctor --path` follows the same rule.
Each command processes the current batch once; it does not watch the folder in the
background.
`auto` uploads immediately without another confirmation prompt. It never publishes.
Run it again after adding more product folders. `drop run` still offers the existing
CSV-only review workflow and now also understands ready-photo folders.

- One immediate child folder = one listing, with up to 20 images in natural filename
  order (`1`, `2`, `10`). Extra images cause an error, not silent truncation. Keep
  finished listing images directly inside each product folder. The one subfolder that is
  read is `dosyalar` (or `files`, any case): a digital product's downloads, below. A
  product folder with only a `dosyalar` folder and no photos is reported, not skipped
  silently: every listing needs at least one photo, so `drop run` skips it with that
  reason and `drop auto` stops before uploading anything.
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
- The template listing's type is kept: `physical`, `download` or `both`. Its variations
  (options, prices, quantities, processing profile) are copied onto every draft. Source
  files remain in place; there is no automatic archive move.

### Digital products

When the template listing is a digital download (`download`, or `both` for a physical
item that also comes as a file), every draft gets what the buyer downloads, after its
images:

```text
Etsy Studio/
  2-PRODUCTS/
    sunset-poster.png        one listing; the buyer downloads this very file
    Planner 2027/            one listing
      01.jpg  02.jpg         its photos, in this order
      dosyalar/              (or files/)
        planner-a4.pdf       what the buyer downloads, in name order
        planner-letter.pdf
```

- **A loose design** is delivered as the original file, byte for byte — never a mockup
  or the flat render. Its mockups and a 1200 px flat preview are the listing's photos,
  even for an opaque JPG: the file being sold never goes up as a photo.
- **A product folder** delivers every file in its `dosyalar` (or `files`) subfolder:
  PDF, ZIP, PNG, JPG, SVG and the like. Hidden and system files are left out. A
  subfolder of `dosyalar` holding files stops the product (Etsy takes files, not
  folders): zip it into one file.
- **Made to order** (`when_made` of the template): Etsy activates such a digital listing
  without a file, so none is required. `dosyalar` files still go when there are some; a
  loose design is not attached (it is a sample); add the buyer's file in Etsy once made.
- Etsy takes **at most 5 files per listing, each up to 20 MB**. Programs and scripts
  (`.exe`, `.bat`, ...) cannot be sold as downloads. A product with no download, too
  many, an empty or an oversized file is stopped with its own reason before its draft
  exists; nothing is dropped or cut to fit.
- A `download` template needs no shipping profile. `review.csv` has the files in its
  `files` column, and `upload-history.json` records `files_uploaded` next to
  `images_uploaded`. A file that fails after the draft exists leaves the product
  `partial`: finish it in Etsy. When an image fails, the download files are still sent
  and the message says whether they went up.
- `drop run` counts the download files in its request estimate.

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

`drop run` composites each design onto the mockups switched on in the app's
**Mockuplar** page, in that order — the first is the listing's main image — appends the
flat artwork, works out the product concept, researches it against listings that
actually rank, and writes titles and 13 tags inside Etsy's limits. `drop auto` uses the
same selection. `--mockups N` keeps the first N of it; a mockup switched off there is
not used, and the run says how many were left out. A listing holds twenty images, so at
most 19 mockups are used and the flat render is the twentieth; any more switched on are
left out, and the run says so rather than building a batch Etsy would only half-accept.

The research searches the concept together with the template's product when the file
name does not say it: `dog-dad-paw-print.png` on a shirt template is searched as
`dog dad paw print shirt`, not as a poster ("print"). Free tag slots take the template
listing's own tags only when they suit any design of that product (`graphic tee`,
`gift for her`); its tags about its own design (`retro mountain sunset`, `hiking gift`)
stay on it. **Nothing is sent to
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
rectangle drawn on it — and saves nothing. Four numbers do not tell you where a design
will sit on a photograph of a shirt, so look at the picture, repeat until it looks right,
then run the same command without `--dry-run`. In the app, **Mockuplar** has a visual
print-area editor that saves to the same `positions.json`.

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

> **Not every shop can upload tracking through the API.** Since June 2024 Etsy has
> withdrawn tracking uploads (and buyer addresses on receipts) from newer API keys,
> country by country — Türkiye first, then the US, Canada and much of Europe. If Etsy
> answers `403 Unauthorized` on a tracking upload, stallkit says so; add the tracking in
> Shop Manager or through a shipping service Etsy has approved for your country.

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
| `stallkit doctor` | Check config, key and connectivity (`--path`: which workspace) |
| `stallkit desktop` | Start the app and open it in the browser (`--port N`, `--no-browser`) |
| `stallkit shops list` / `add` / `remove` | Several shops on one computer; use one with `--shop <id>` |
| `stallkit auth login` | OAuth consent flow (PKCE) |
| `stallkit auth status` | Token, scopes, shop, remaining daily quota |
| `stallkit auth refresh` | Force a token refresh |
| `stallkit auth logout` | Delete the stored token |
| `stallkit shop info` | Shop identifiers and headline numbers |
| `stallkit shop profiles` | Shipping profiles, return policies, sections |
| `stallkit shop taxonomy <word>` | Find a `taxonomy_id` |
| `stallkit drop init` | Create the designs-in workspace folder |
| `stallkit drop template` | Copy settings from a listing you built by hand |
| `stallkit drop run` | Designs → mockups (the Mockuplar selection), titles, tags → `review.csv` |
| `stallkit drop calibrate` | Move a mockup's print area, with a preview image to check it |
| `stallkit drop auto` | Designs and product folders → Etsy drafts (with their download files for a digital template), with upload history |
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

**Secrets.** The keys live in `~/.stallkit/.env` (or a git-ignored `.env`). The token lives in
`~/.stallkit/token.json`, written `0600`. Neither is ever printed in full.

**Sharing output.** Terminal output gets screenshotted more often than anyone plans
for, and a listing title is enough to find the shop it belongs to. `--anonymise`
(or `STALLKIT_ANONYMISE=1`) hides your shop name, ids, titles, URLs and tags while
leaving the findings readable — so a screenshot can be posted without exposing the shop:

```bash
stallkit --anonymise seo audit
```

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
