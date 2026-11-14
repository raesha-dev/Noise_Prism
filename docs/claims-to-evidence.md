# Claims to evidence and demo outline

## README claims

| Claim | Evidence | Reproduction / status |
|---|---|---|
| Upload processing and candidate generation are available | FastAPI routes and pipeline implementation | `make test`; synthetic audio only |
| C0 is the current fast-route fallback | `bharatnoise_api/config/router.json` and route guard | `pytest bharatnoise_api/test_server.py` |
| C1/C2 and simplified C2B/C2C are experiments, not measured improvements | Candidate code, `config/*.json`, [algorithm notes](./algorithm-notes.md) | No ASR improvement claim |
| Team data are synthetic TTS and split by confirmed voice | Owner-reviewed local CSV and aggregate data provenance | `python scripts/check_team_set.py --review data/team/voice_group_review_FILLED.csv` |
| GramVaani is the real-telephone corpus | Local aggregate provenance and organizer Dev/Eval manifests | Source audio/references remain local; no recognition results |
| WER/CER, confidence intervals, latency, and candidate effects | No benchmark output file currently exists | **Not measured**; see [RESULTS.md](./RESULTS.md) |
| C2B/C2C fidelity to cited papers | Not established | **Unverified**; see [algorithm-notes.md](./algorithm-notes.md) |

## 60-second demo outline

1. **0–10 s:** Show the workstation in mock mode and clearly identify the mock
   banner. Select an uploaded synthetic/local example; do not show corpus
   audio or transcripts.
2. **10–25 s:** Show original C0 and the candidate controls. State that
   processing candidates are experimental, their settings are unfitted, and
   no WER improvement is measured.
3. **25–40 s:** Show a deliberately empty or failed mock response and the
   candidate-level error/empty status. Explain that no measured failure
   diagnosis or fallback chain is available yet.
4. **40–52 s:** Show the evaluation/report section displaying “not measured”
   and the local-only, split-separated measurement runbook.
5. **52–60 s:** State what is needed next: owner-approved budget, Dev
   condition runs, repeat baseline, and only then a frozen one-time held-out
   run. Do not imply a fallback rescued the example.
