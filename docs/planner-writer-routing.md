# Planner and Writer routing

`ops/run-growth.sh` selects Responses API with weight 100 and Codex with weight 0 independently for each stage. Normal API turns use GPT-6.1 Sol. Writer retries carrying QA gate feedback go directly to GPT-6 Astra. A normal Sol turn that returns an invalid/refused/incomplete structured output is retried once with Astra; transport/config failures fall through without an expensive model retry. If the API route still fails, the existing local Codex → certified Claude chain remains available. Zero Codex weight disables random selection, not fallback.

Stage settings use `SELLEMY_PLANNER_` and `SELLEMY_WRITER_` prefixes:

- `API_WEIGHT` / `CODEX_WEIGHT`: finite nonnegative relative weights with a positive total; defaults 100 / 0.
- `OPENAI_API_KEY`: dedicated stage credential, loaded from existing `planner.env` / `writer.env` under `~/Library/Application Support/Sellemy/secrets`.
- `OPENAI_MODEL`: normal model; defaults to `gpt-6.1-sol`.
- `OPENAI_ESCALATION_MODEL`: quality-escalation model; defaults to `gpt-6-astra`.
- `OPENAI_TIMEOUT_SECONDS`: defaults to 300.

The API adapter does not reuse Designer credentials or send a project override. The runner preserves `unset SELLEMY_OPENAI_PROJECT_ID`. Secrets and raw HTTP error bodies are not logged.

Requests use `/v1/responses`, `store: false`, and `text.format` with a strict JSON schema. Nested objects are closed and all declared properties required. Array cardinality constraints remain intact; parsed responses are validated before use. Existing downstream editorial and publication gates still apply. See [OpenAI structured outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs).

Returned stage metadata records route weights, selection, provider used, fallback, and attempt count. Runtime logs also record selection and fallback transitions, including failure when no provider succeeds.

Offline verification:

```sh
PYTHONPATH=tests:pipeline .venv/bin/python -m pytest -q tests/test_responses_provider.py tests/test_writer_providers.py tests/test_planning_and_selection.py tests/test_codex_provider.py
zsh -n ops/run-growth.sh
```
