# Bundled data for `app.auth`

## `breached-passwords-top100k.txt`

The 100,000 most common passwords from Mark Burnett's "ten million passwords" dataset,
as packaged in SecLists. `app.auth.policy` rejects any new password that matches an entry
(case-insensitive, after NFKC normalisation). It ships with the image, so the check
needs no network access (ADR-006, ADR-034).

| | |
|---|---|
| Source | [SecLists](https://github.com/danielmiessler/SecLists), `Passwords/Common-Credentials/xato-net-10-million-passwords-100000.txt` |
| Original dataset | Mark Burnett, ["Today I Am Releasing Ten Million Passwords"](https://medium.com/xato-security/today-i-am-releasing-ten-million-passwords-b6278bbe7495) (2015), released for research and defensive use |
| Licence | SecLists is MIT-licensed (Copyright (c) 2018 Daniel Miessler) |
| Retrieved | 2026-10-08 |
| SHA-256 | `1472aafa2561df5e3293aee252aee3ca660c12b399a283cf808bb01b39be388b` |
| Format | One password per line, ASCII, most common first; 100,000 lines (one blank line, skipped on load) |

The file is bundled verbatim. Only its 488 entries of 12+ characters can ever match,
because shorter passwords are already rejected by the length rule; the whole list is kept
so the source stays verifiable by its hash.

To replace it (e.g. with a larger list), keep one password per line in UTF-8 and point
`APP_AUTH_BREACHED_PASSWORDS_PATH` at the new file, or replace this file and update the
table above.
