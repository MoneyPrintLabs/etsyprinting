# Security policy

stallkit holds credentials that can create, edit and publish listings in a real shop,
read customer names and addresses, and mark orders shipped. This page states exactly
what it does with them.

## Reporting a vulnerability

Please report privately through
[GitHub's private vulnerability reporting](https://github.com/MoneyPrintLabs/etsyprinting/security/advisories/new)
rather than opening a public issue.

Include what you did, what happened, and what you expected. A proof of concept helps.
Expect an initial reply within a week. There is no bug bounty — this is an unfunded
project — but you will be credited in the release notes unless you prefer otherwise.

## What stallkit stores, and where

| Secret | Where | Permissions |
|---|---|---|
| Keystring, shared secret, redirect URI | `.env` in your working directory | `0600` when written by `stallkit init` |
| OAuth access + refresh token | `~/.stallkit/token.json` (override with `STALLKIT_HOME`) | `0600` |

POSIX modes are not enforced on Windows; the files still sit inside your user profile.

`.gitignore` covers `.env`, `.stallkit/`, and `token.json`. Check `git status` before
your first commit anyway.

## What stallkit does not do

- **No telemetry.** It makes no network call except to `openapi.etsy.com`,
  `api.etsy.com` and `www.etsy.com/oauth/connect`. Nothing is reported anywhere.
- **No credential in output.** `doctor` and `auth status` print a truncated keystring
  and the *length* of the shared secret, never the values. `init` reads the secret with
  hidden input, so it does not reach your screen or your shell history.
- **No delete scope.** `DEFAULT_SCOPES` excludes `listings_d`. stallkit cannot delete a
  listing even if it is compromised or buggy.
- **No scraping and no stored session cookies.** Access is OAuth only.

## Things worth knowing

**Both halves of the app credential are secret in practice.** Etsy requires
`keystring:shared_secret` in every `x-api-key` header. Treat the pair like a password.

**The refresh token is the valuable one.** It lasts 90 days and mints access tokens
without further consent. If `~/.stallkit/token.json` is exposed, run
`stallkit auth logout` and revoke the app's access in your Etsy account settings.

**`orders ship` is irreversible.** It emails buyers and marks orders shipped, and the
API offers no undo. It asks for confirmation and supports `--dry-run`. Keep both.

**Listings are created as drafts, never published.** No code path sets a new listing to
`active`. If you find one, that is a security issue — report it.

**Narrow your scopes if you only read.** Before your first login:

```bash
ETSY_SCOPES=shops_r listings_r transactions_r
```

## If you leak a credential

1. Regenerate the app credential at <https://www.etsy.com/developers/your-apps>.
2. Run `stallkit auth logout` and revoke the app in your Etsy account settings.
3. If it reached a git commit, rotating is what fixes it — rewriting history does not,
   because the old value may already have been fetched.

## Supported versions

This project is pre-1.0. Fixes land on `main` and in the next release; there are no
backports.
