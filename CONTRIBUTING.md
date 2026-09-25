# Contributing to stallkit

Thanks for wanting to help. This is a small, deliberately readable project — you can
read the whole thing in an afternoon, and that is a feature worth protecting.

## Getting set up

```bash
git clone https://github.com/MoneyPrintLabs/etsyprinting.git
cd etsyprinting
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
ruff check .
```

**You do not need an Etsy account, an API key, or a network connection to contribute.**
The whole test suite is offline by design. If a change can only be tested against the
live API, say so in the pull request and describe what you ran manually.

## The rules that matter

**1. Official API only. No scraping.**
Every fact stallkit reports comes from `openapi.etsy.com`. No HTML fetching, no
browser automation, no third-party keyword services behind a login. This is not
squeamishness: driving Etsy's seller UI violates its Terms of Use and gets shops
suspended, and the people using this tool have real shops.

**2. Never invent numbers.**
Etsy does not publish search volume. If a metric cannot be measured from an API
response, stallkit does not show it. `seo keywords` reports what the returned listings
actually contain — nothing more.

**3. Writes are not retried.**
Etsy has no idempotency key, so a repeated `POST` is a real duplicate: a second draft,
a second shipment, a second "your order shipped" email to a buyer. `client.request()`
only retries a write when the request provably never arrived or was provably refused.
If you touch the retry logic, keep that property and keep its tests.

**4. Validate locally before you call.**
A 400 from Etsy costs a round trip and names no field. `listings.build_payload` checks
titles, tags, enums and required fields on this machine first, so `--dry-run` works with
no key at all. New fields should be validated the same way.

**5. Never widen scopes casually.**
`DEFAULT_SCOPES` is the minimum the tool needs, and there is deliberately no delete
scope. Adding one needs a very good reason in the pull request.

**6. Credentials stay out of the repo and out of the output.**
No key, token, secret or shop id in a tracked file, a test fixture, a log line or an
error message. Tests use obvious dummies like `KEY123`.

## When Etsy's docs and Etsy's behaviour disagree

Several defects in this project came from trusting the prose documentation over what
the API and the app settings screen actually do. Two examples now recorded in the code:

- `x-api-key` needs `keystring:shared_secret`, not the keystring alone.
- Callbacks may be `http://`, and `localhost` is fine — but an IP address is not.

If you find another, **write the evidence into a comment next to the code**, and add a
test that names the failure it prevents. That comment is the most valuable part of the
change; the next person will otherwise re-derive it the hard way.

## Pull requests

- One concern per pull request.
- Add a test that fails without your change. Name it after the failure it prevents.
- Run `pytest` and `ruff check .` before pushing. CI runs both on Ubuntu and Windows.
- Windows matters: status markers must survive a redirected, non-UTF-8 console.
- Update the README in the same commit if you change a command, a flag, or a CSV column.

## Things that would genuinely help

- Inventory and variations (`updateListingInventory`) — the biggest gap.
- Digital downloads: `uploadListingFile` and the digital listing flow.
- Shop sections and listing translations.
- A renewal helper for listings about to expire.
- Better taxonomy search: the current match is a plain substring.

## Reporting bugs

Open an issue with the command you ran, the output, your OS, and your Python version.
**Redact your keystring, shared secret and tokens** — and if you have already pasted
one anywhere, rotate it on your Etsy app page.

## Licence

By contributing you agree that your work is licensed under the [MIT Licence](LICENSE).
