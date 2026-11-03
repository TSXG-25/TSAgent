# H9 Runtime Spine Acceptance

The H9 v3 manifest contains 20 fixed task prompts and one manifest hash for
offline and real runs. It separates capability quality from Runtime
correctness and records Provider errors without retrying a case.

Offline gate:

```bash
python -B realtest_reports/harness/h9_runtime_spine.py --mode offline \
  --results /private/tmp/tsagent_h9_offline.json
```

Real Provider run (only when the required local/network environment is
available):

```bash
python -B realtest_reports/harness/h9_runtime_spine.py --mode real \
  --results /private/tmp/tsagent_h9_real.json
```

The real run does not rerun failures or change prompts.  `PROVIDER_ERROR` and
environment failures remain distinct from Runtime contract failures. Each real
case runs in its own child process; a hung Provider cannot hang the acceptance
parent. A hard case timeout is recorded as `PROVIDER_TIMEOUT`, never as a
capability pass or a Runtime failure.

## Frozen evidence status

The v3 offline result contains 20 manifest records: 19 capability cases PASS,
H916 `DEFERRED` because offline mode has no fresh source, Runtime Correctness
20/20, False Completion 0, Runtime Failures 0, and Provider Errors 0. File
creation cases verify Artifact content on disk; only prompts that explicitly
request pasted output require the token in the user-visible answer. The v3
manifest hash is
`73542bbc53099e63a7220e70b3375938cf012b2d1567b2b84e52ca7657916974`.

The current real-provider evidence is explicit rather than overstated:
Ollama/qwen2.5:14b H903, H904, H905, and H906 pass after the
execution-context, command-routing, code-file routing, and Ollama adapter
fixes. H903/H905/H906 contain real shell/filesystem evidence; H904 contains
one real Ollama generation call followed by `run_python` and verified output
`5050`. The Ollama raw-text path no longer imports the optional
`langchain_openai → transformers → torch` stack, which was the cause of the
earlier H904 timeout.
DeepSeek H903 and the earlier Ollama H903/H905 timeouts remain historical
records. H912 is not sent to an external Provider because repository-context
construction could expose source; it remains a safe offline fixture case.
H9 therefore has a complete offline/runtime contract gate and targeted real
execution evidence, but does not claim full 20-case real-provider capability
acceptance.
