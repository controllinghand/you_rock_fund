# Why the dashboard has no login

A design note, not a to-do. It records a decision and the reasoning behind it, so
the question doesn't get reopened from scratch every few months.

**Short version:** the dashboard has no password because the *network* is the
security boundary, and that is a stronger boundary than a login we would have to
write and maintain ourselves. If you want the dashboard on your phone, see
[remote-access.md](remote-access.md) — a private overlay network solves it
without a login, without inbound ports, and without any code.

---

## The boundary is the network

Every published port in `docker-compose.yml` binds to `127.0.0.1`. The dashboard
is not reachable from your local network, let alone the internet. You reach it
from the box — physically, or via Screen Sharing — and that has been the access
model since the beginning.

**This premise is load-bearing, and it is exactly one typo deep.** A single
missing `127.0.0.1:` prefix in one line of YAML silently converts "loopback only"
into "trusts the local network," with no error, no log entry, and nothing visible
in the UI. That is not hypothetical: the Secrets Setup service shipped that way
from v1.3.0-beta until **v5.2.61**, and it was found by accident.

So: **verify, don't assume.** Every published port must start with `127.0.0.1:`:

```bash
grep -E '^\s+- "' docker-compose.yml
```

If you are running older than v5.2.61, upgrade.

## Why not just add a password?

We could. Password hashing plus TOTP plus a session cookie is a well-trodden path
and not much code. The reason not to isn't difficulty.

**Auth is a front door, and a front door only helps on a house that faces the
street.** The moment the dashboard is internet-reachable, we own the entire
surface behind that door — every endpoint being correct, every dependency's CVEs,
TLS renewal, rate limiting, session handling — forever, on a machine wired to a
live brokerage account. A private overlay network means we never open the door at
all: no inbound ports, no public surface, and authentication handled by an
identity layer that is somebody else's full-time job rather than our side project.

Login-on-the-dashboard is a reasonable *second* layer someday. It is not a
substitute for the first one, and it is dramatically more expensive.

## Why we can't copy IBKR's login

The obvious question: IB Gateway does username + password + 2FA, so why not do
what it does?

Because the roles are reversed. **IBKR is the identity provider and we are the
client** — we log *in* to *them*. Authenticating your phone to your dashboard
would require us to *be* the identity provider.

IB Key, specifically, is not something we could reproduce. IBKR's servers push a
challenge to your phone; the IBKR Mobile app signs it with a key held in the
phone's secure enclave; the response goes back to IBKR. That takes a push
backend, device enrollment, key attestation, and a challenge/response service.
That's a product, not a feature.

And "IBKR does it" argues the other way. They can face the internet with money
behind them because they have a security team, penetration testing, a bug bounty,
and compliance audits. That is the entry fee. A Mac Mini in a house doesn't pay
it — and the prize would be checking a dashboard from the couch.

## Every deployment is standalone — decided 2026-07-14

The one scenario that *would* have forced app-level auth: a shared overlay network
spanning several operators' boxes. Once someone else's box is on your network,
"on the network" stops meaning "authorized," and network isolation alone stops
being the right answer.

**That is not going to happen, and this is not a deferral.**

Every YRVI deployment is fully isolated — its own IBKR account, its own secrets,
its own state, its own money. Operators self-host, self-upgrade, and self-recover.
Nobody has access to anyone else's app, and nobody wants it. A shared network
would quietly walk that back.

The consequence: **your overlay network contains only your own devices.** The
overlay's identity layer *is* the authentication, because there is no second party
to distinguish. App-level auth would be defending against a situation that has
been structurally eliminated.

If you want remote access to your own box, you run your own overlay on your own
account and you own it. See [remote-access.md](remote-access.md).

## Where that leaves it

Password + TOTP, with a step-up tier separating routine operational actions
(restart a wedged gateway, trigger a run) from sensitive ones (credentials,
capital settings), remains a *reasonable* defense-in-depth layer. A design exists.

It has **no trigger**, and it is not scheduled. Roughly a week of work against no
threat that isn't already closed off by other means.

Tracked at [issue #110](https://github.com/controllinghand/you_rock_fund/issues/110)
as a record of the reasoning, not as pending work.

## If the premise ever changes

Build the overlay network first regardless — it is an afternoon and it makes the
auth work optional rather than load-bearing. If auth is genuinely needed after
that, two notes worth carrying forward:

- **Use one default-deny middleware, not a decorator per endpoint.** With
  decorators, the endpoint someone adds six months later ships unauthenticated by
  omission. Default-deny with an explicit allowlist fails the safe way.
- **Credentials belong in the secrets container**, reached via
  `secrets_client.get_secret` — not in `settings.json`, which is a plain config
  file the UI reads and writes. Reuse the existing architecture rather than
  inventing a second credential store.

And budget for the part that isn't the auth: several internal callers reach the
API with no browser and no cookie, and every one of them has to be found and
handled. Miss one and a box silently loses the ability to upgrade or start
itself — possibly a box you have no access to.
