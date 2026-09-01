# Fix Notes

This repair focuses on benchmark integrity rather than prompt tuning.

## Root causes fixed
1. Infrastructure failures were written as `__ERROR__ ...` and could be scored as model failures.
2. `run_matrix.ps1` could skip bad raw files because it relied on file existence/line count.
3. `tcb score` did not isolate infrastructure failures from model scoring.
4. Runtime paths depended on the current working directory.
5. The Ollama endpoint was hard-coded.
6. Several helper scripts and onboarding notes described an older repository state.

## New behavior
- Raw rows use `status=ok|infra_error`; failures also store `error_type` and `error_message`.
- Any inference failure makes `tcb run` exit non-zero after preserving diagnostics.
- `tcb check-raw <file>` validates exact task coverage, duplicates, malformed rows, and infra failures.
- `run_matrix.ps1` only skips a raw file if `tcb check-raw` says it is healthy.
- `tcb score` excludes infra failures and returns non-zero on contaminated/incomplete raw files.
- Legacy `__ERROR__` rows are treated as infrastructure failures.
- `TCB_BASE_URL` / `--base-url` configures the Ollama endpoint.
- Project assets are resolved from the repository root instead of the caller's CWD.

## Verification
- 43/43 unit tests pass.
- Dataset validation passes for 122 graded tasks + 4 demo tasks.
- All 18 historical raw files pass the new health check.
- Scoring from outside the repository produces 2,196 healthy rows (= 122 × 18).
- A deliberately contaminated legacy `__ERROR__` raw file is rejected with exit code 1.


## V2: remote Ollama / HTTP 502 hardening

- `httpx` now ignores `HTTP_PROXY`/`HTTPS_PROXY` by default for Ollama calls (`trust_env=False`).
  Use `--trust-env-proxy` only when a proxy is intentionally required.
- Added `tcb doctor --model ... --base-url ...` for a one-request connectivity/model check.
- `tcb run` performs `/v1/models` preflight before the 122-task benchmark, so 502,
  connection errors, invalid responses, and missing models fail immediately.
- HTTP errors preserve a short response body plus `server`/`via` headers when available.
- Raw rows record `base_url` and proxy mode.
- `tcb check-raw --base-url ...` can enforce endpoint provenance.
- `run_matrix.ps1 -BaseUrl ...` forwards the endpoint consistently, preflights all models,
  and reruns old raw files whose endpoint provenance does not match.
- Regression suite: 43/43 tests pass.
