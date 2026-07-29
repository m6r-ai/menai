# Contributing to Menai

Thank you for your interest in contributing. This document covers what you need to know
before submitting changes.

## Coding standards

Consistency is more better than better. A change that makes one part of the
codebase "better" but leaves it inconsistent with the rest will be rejected. If you
want to improve a pattern or convention, that improvement must be applied uniformly
across the entire codebase — not just in the files you happen to be touching. The
burden for identifying and resolving any inconsistency a change introduces rests
entirely with the contributor.

- Don't use block comments delimiting sections within a file. Functions and classes have
  docstrings; grouping comments just add clutter.
- Use modern Python: `type | None` instead of `Optional` and `type1 | type2` instead of
  `Union`.
- Use builtins (`dict`, `list`, `set`, `tuple`, `type`, `frozenset`) and
  `collections.abc` (`Callable`, `Awaitable`, `AsyncGenerator`, `Generator`,
  `Iterator`, `Sequence`, `Coroutine`) instead of legacy `typing.Dict`, `typing.List`, etc.
- Don't use `@property`.  Simple getter methods are much easier to reason about.
- Tests must reflect correct and desired behaviour. Never write or patch a test to mask
  broken implementation logic — if the logic is wrong, the test must fail.
- Test docstrings describe expected behaviour only. They must not reference historical
  bugs, previously broken behaviour, or implementation details of past fixes. A test is
  a specification, not a changelog.
- **YAGNI (You Aren't Gonna Need It).** Do not add code speculatively. Every method,
  function, class, and module must have a concrete reason to exist: it must be used
  somewhere in Humbug or its supporting tools. If code cannot be reached at runtime it
  should not exist. Do not add "helper" functions, convenience methods, or abstraction
  layers unless they are called by real code in this contribution. If a reviewer asks
  "where is this used?" and the answer is "it might be useful someday", the code will be
  rejected. Pylint's built-in checks (unused-import, unused-argument, unused-variable,
  unused-private-member) catch some of this automatically, but they do not catch
  unreachable public methods or speculative abstraction layers — that is the contributor's
  responsibility.

## Dependency rules

Menai has no external runtime dependencies beyond the Python standard library. This is a
core design principle and the bar for adding any other dependencies is extremely high.
If you think a new external dependency is genuinely necessary, raise it for discussion
on Discord before writing any code that relies on it.  Do not add new external dependencies
without explicit agreement.

## AI assistance

Menai has been built largely by AIs, so AI implementations aren't just welcome, but
actually expected!  With this said, the philosophy is to avoid being tied to any one framework
or coding harness, so if your patch adds files for a coding framework it will be rejected.

## Do one thing per contribution

Only implement one specific improvement or feature per contribution.  If there are two or
more you will be asked to split them into two or more contributions.  Patches are carefully
reviewed and it's really hard to review something that tries to do more than one thing.

## Verifying your changes

Before submitting any change, run the full suite of static analysis tools:

```bash
python -m tools.code_checker
```

All checks must pass cleanly. This runs:

- **mypy** — full static type checking across `src/`. All code must be fully typed.
- **pylint** — linting across `src/`. The codebase is held to a 10.00/10 rating.  Humbug
  has custom style checker rules to enforce the coding standard wherever it can.

## Getting involved

Join the [Discord server](https://discord.gg/GZhJ7ZtgwN) if you want to discuss ideas or
coordinate on larger changes before writing code.
