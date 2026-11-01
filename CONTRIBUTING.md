# Contributing

Thanks for your interest! Please read this first.

## License of contributions

agentcost is licensed under the PolyForm Noncommercial License 1.0.0
(see [LICENSE](LICENSE) and [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md)).
By submitting a pull request you confirm that you wrote the contribution (or
have the right to submit it) and that you license it to the maintainer under
the same license **and** grant the maintainer the right to also distribute it
under other terms, including commercial licenses. If you do not agree, please
do not submit code.

## Ground rules

- Keep the principles: local-first, metadata only, no LLM calls in the core
  pipeline, no runtime dependencies.
- Never include real client names, tokens, logs or prompts in code, tests,
  issues or screenshots. Use synthetic data (`gen_sample.py`).
- Add or update tests; run them before opening a PR:

```bash
pip install pytest
python -m pytest
cd cloud/worker && node --test test/worker.test.mjs
```

- Small, focused pull requests are easier to review. Open an issue first for
  larger changes.
- All changes go through pull requests and need maintainer approval.
