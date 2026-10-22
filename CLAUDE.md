# CLAUDE.md

Rules for coding agents in this repo. Short and blunt on purpose.
`AGENTS.md` is a symlink to this file — one source, every agent.

## ⚠️ The real heat pump is read-only — never write to it

**This is the single most important operational rule.** The library talks to a production heating plant: the owner's Stiebel Eltron WPE-I 07.1 Plus H 400, which has an internal WPM-G (Thermia Genesis) controller and serves Modbus TCP on port 502, unit 1. A wrong read is a bug. A wrong write can damage a borehole or a compressor.

- **Never send Modbus function codes 5, 6, 15 or 16 to the real device**: not from tests, not from a probe, not "just once to check".
- Exercise write paths **only against the emulator** (`genesis_wp.emulator`) or the pymodbus simulator on the local host.
- Live reads (function codes 1–4) are allowed only as an explicit, owner-approved probe that captures fixtures. Guard the probe script so every write method raises before it connects.
- Use the device host name in lower case (the old pyModbusTCP transport rejects upper-case). The name is topology: keep it out of committed files, records and logs, and pass it in through the environment.
- The test suite never needs the real device. If a test seems to need it, the emulator is missing a feature; extend the emulator.

## Conceptional

- **Prefer long term benefits over short term win.**
- **Never harm the plant.** Read-only is the default at every layer: library, emulator profile, and later the Home Assistant integration.
- **The register map is data.** Names, scales, units, sentinels and enums are verified per firmware profile against a source. Never guess a scale.
- **Emulator first.** Every behavior the library depends on must be reproducible in the emulator before it is trusted on the device.
- **Observability is key. No feedback loop without logs.**
- **Prefer strict data handling.** Validate inputs early, fail closed, never coerce a register value silently.
- **Prefer git operations over raw operations.**
- **Expect parallel work to be ongoing.** Work on branches, or in git worktrees under `.claude/worktrees/` where feasible. Simple markdown edits are probably not worth a worktree.

## Language

- **Use concise language.**
- **Use software engineering language.** Name things with the vocabulary of the discipline — interface, boundary, contract, coupling, invariant, call path. Do not reach into another domain for a metaphor when a standard term exists.
- **Use US English spelling in all written output** — code, comments, commit messages, records, chat. Some gold schema enums accept only US spelling (`close_reason: "canceled"` passes, `"cancelled"` is rejected with `enum_mismatch`).
- German register names from the Stiebel documentation may be kept as data (for example a `name_de` field). Identifiers and prose stay English.

### Words to avoid

| Avoid | Use instead |
|---|---|
| seam | boundary |

### Abbreviations

Use these only as chat shorthand. **Records always carry the full name.**

| Abbreviation | Definition |
|---|---|
| obc | openbrain-client |
| HA | Home Assistant |
| CR | change_request |
| FC | Modbus function code |
| WPM-G | Stiebel's controller in the WPE-I (Thermia Genesis lineage) |
| KSM | Kältemittelsicherheitsmodul (refrigerant safety module) |

## What genesis-wp is

- An async Python library (module `genesis_wp`, distribution `genesis-wp`) for heat pumps on the Thermia Genesis controller platform, over Modbus TCP. The first target is the Stiebel Eltron WPE-I 07.1 Plus.
- A Modbus TCP emulator (`genesis_wp.emulator`, CLI `genesis-wp-emulator`) serving captured firmware-profile snapshots, so nothing has to be tested against the live plant.
- A fork of [ulrichwi/pythermiagenesis17](https://github.com/ulrichwi/pythermiagenesis17), itself a fork of [CJNE/pythermiagenesis](https://github.com/CJNE/pythermiagenesis). Remotes: `origin` (ours), `upstream` (ulrichwi), `cjne`.
- **Not** a Home Assistant integration. That is a separate, not-yet-named product that consumes this library. Don't build it here.
- **Not** in charge of rollout. The homelab product owns deployment to its VMs and its exploration-8. When a stage lands, tell homelab.

Direction and principles live in gold, in genesis-wp vision-1.

## License and third-party material

- **MIT, same as the source.** Keep `Copyright (c) 2020 Johan Isacsson` and `Copyright (c) 2026 Ulrich Wirleitner` in `LICENSE` verbatim. Our notice `Copyright (c) 2026 David Aggeler` goes alongside them and never replaces them.
- New dependencies must be MIT-compatible (pymodbus is BSD-3-Clause). Check the license before adding one.
- **Never commit manufacturer documents**: no PDFs, manuals, screenshots or display photos. Commit register *facts* with a source reference, for example "Stiebel doc 372257-47978-0071" or "probe 2026-10-22, fw 6.1.5".
- **Keep the device serial number out of** the repo, fixtures, records and logs. Strip it from captures before committing.

## Operational conventions

- Sessions are driven remotely (Claude Desktop → `/rc` → `claude`, kept alive by `zx`). A permission prompt may go unanswered — don't park work behind one.
- Batch related git operations with `&&` so they land atomically.
- Prefer a reversible move (new commit, branch, stash) over a destructive in-place edit.
- **Outward-facing actions need the owner's OK every time.** That covers `git push`, GitHub releases, PyPI uploads, and upstream issues, PRs or comments (ulrichwi/pythermiagenesis17, CJNE/pythermiagenesis, pail23/stiebel_eltron_isg_component #684).

### Access model

| Path | Reaches | Use for |
|---|---|---|
| `mcp__gold__*` | gold (openbrain PROD) | **all** genesis-wp record authoring |
| `mcp__copper__*` | copper (openbrain DEV) | integration diagnostics only — never author genesis-wp records there |
| local Python / pytest | DEV VM | build, tests, emulator |
| real heat pump | Modbus TCP, **FC1–4 only** | owner-approved fixture probes; see the rule at the top |

### Standup

genesis-wp has no `<prefix>-standup` events pane of its own yet. Ask cross-session questions of `homelab-standup`, or of the homelab session that handed this product over. Peer projects (homelab, openbrain, zx) have their own standup sessions.

### Scratchpad

Put working files (drafts, one-off scripts, intermediate outputs the user may want to read) in `scratchpad/` at the repo root. It is gitignored and survives the session. Session-private tmp dirs are fine only for files the user never needs to open.

## Skill trees — know which one you're editing

- **Local skills** under `.claude/skills/` that have no `x-openbrain-*` frontmatter are repo files. Edit them directly, under a CR, and add a `!` exception to `.gitignore` so they get tracked.
- **Materialized skills** carry `x-openbrain-*` in their `SKILL.md` frontmatter. Never edit them here. They are authored and released in gold, then pulled with `openbrain-client init-skills`. `.gitignore` ignores `.claude/skills/`, `.claude/agents/` and `.claude/materialization.json` as whole directories for that reason.
- `.claude/agents/<slug>.md` is derived from the gold skill's agent block by the same `init-skills` run. Never hand-edit it.

## genesis-wp Development Documents — Use Gold

Every HELIX01 record type for this product is owned by the **gold** instance, product `genesis-wp`: vision, dd_plan, epic, change_request, requirement, ac, issue, issue_fix, design_decision, and the rest.

- **Author only through `mcp__gold__*`** — `create_<type>` / `update_<type>` / `draft_<type>`; `catalog_*` is read-only from here (the catalog is OpenBrain's). Never as local markdown.
- **Every call takes `product: "genesis-wp"`.**
- **Cross-product refs are rejected.** A genesis-wp record cannot link a `homelab/...` record. Mention it in text instead, for example "homelab exploration-8".
- **Never hand-pick a sequence number.** IDs are allocated server-side.
- **Never cache record status.** It is live data — query it at time of use.
- **Search before creating**, so you update the existing record instead of duplicating it: `search_<type>`, cross-type `search_records`, `list_<type>` (`include_summary=true`), `get_<type>`, and `list_inbound_refs` / `list_relations_for` for links.
- **Then pull the template** with `get_template_for` and write into its structure. The template is what makes a record reviewable, not merely valid.
- **Fill every required field in the create call** — the schema's required set plus whatever `x-status-requires` gates for the target status; `describe_dto_type` gives the current shape. Some fields freeze once written.
- **Feature CRs need requirements.** A `kind: feature` change_request must list at least one requirement. Acceptance criteria are separate `ac` records parented to the requirement, never inline.
- **`summary` is the search surface.** Title + summary are embedded together and truncated at 256 word-piece tokens. Don't repeat the title; keep the summary to ~100–120 words, distinctive nouns first; no refs, status, dates, history, or link lists. Traceability and decisions go in `details`.
- **Closing needs `close_reason`** — a `closed` record without one always rejects.

Anchors:

- Vision: vision-1. Stage-1 plan: dd_plan-1 (owns the stage-1 epic and its CRs). Requirements requirement-1…8 with their `ac` records.
- Evidence: homelab exploration-8 (rounds 4 and 7–13) and homelab prototype-2, the register review page. Cite these in text only.

### Link records, don't cite refs

Render every gold record named in chat output as a link: `https://openbrain.local.aggeler.com/app/records/<product>/<type>/<seq>` — e.g. `[requirement-1](https://openbrain.local.aggeler.com/app/records/genesis-wp/requirement/1)`. That host is gold; never link to copper.

### Records with an HTML body

A record written with `details_format: html` is expected to *show* its content. The full rule set (theme-safe SVG, sanitizer constraints) lives in openbrain's `CLAUDE.md`; the short form:

- Add one inline `<svg>` for each larger paragraph that describes structure, flow, sequence or magnitude. Give it a `viewBox` and no fixed pixel width.
- Every SVG carries a legend drawn with SVG primitives (`<text>` + `<rect>`/`<line>` swatches). No `foreignObject`, no `<style>`, no `<img>`, no `style=` on the root — the sanitizer strips them.
- Text and connectors use `fill="currentColor"` / `stroke="currentColor"`. No opaque background. Tint shapes with their stroke hue at `fill-opacity="0.15"`.
- One palette per record. State colors: good `#5fb88a`, warning `#d4a14d`, bad `#d46161`, neutral `currentColor` at `stroke-opacity="0.5"`.

### Charts — every axis carries a unit

- Every axis is labeled `name [unit]` — `temperature [°C]`, `power [kW]`, `pressure [bar(g)]`, `registers [count]`.
- A bar chart states what bar length encodes and whether the scale is linear or log.
- A categorical axis still names its dimension — `[register block]`, `[firmware profile]`.

## Change control

EVERY repo change goes through a CR. No silent fixes.

**A SoR record update is NOT a code change and needs NO CR.** Editing a gold record of any HELIX01 type (requirement, ac, issue, design_decision, epic, and so on) is record-plane work with its own change control. Do it directly through `mcp__gold__*`. Do not open a CR for it and do not wait for approval to make the edit.

The loop, always:

1. File records first. Defect or gap → `issue`. Then a `change_request`, parented to the epic it belongs to.
2. Get the CR approved BEFORE writing code.
3. Implement on a branch, test-first against the emulator. Gate green (see *The gate*).
4. Before closing: check the diff against EVERY acceptance criterion, one by one, write down the evidence. Green gate alone is NOT proof.
5. Close the CR with the merge SHA. If an issue drove it, close the issue via an `issue_fix` that references the CR and the SHA.

Small change? Still a CR. Repo docs-only? Still a CR (`transition_gate: prose`).

The rule covers the **repo working tree**: code, tests, fixtures, packaging, repo markdown. It does NOT cover:

- **SoR records** (see above).
- **Catalog items.** Materialized skills, templates, methods, runbooks and executors are owned by OpenBrain. Fix them at the source.
- **Local, gitignored state**, such as `openbrain-client.local.yaml` and `.logs/`.

Workflow entry points: `/change-request` (manual, owner-driven), `/automated-change-request` and `/run-queue` (autonomous tail).

## Transition gates

The field is `transition_gate`. Its values are five cumulative evidence classes — `prose` ⊂ `static` ⊂ `unit` ⊂ `integration` ⊂ `system`.

Commands are NEVER read from a record. Every invoker resolves them from the committed `openbrain-client.yaml` at the repo root, under `transition_gates.<rung>`. `openbrain-client.yaml` does not declare `transition_gates` yet. Until it does, run the rung's stages from *The gate* below, in session.

The merge is the git operation ONLY. It runs no gate and tears nothing down.

## The gate

Cumulative by rung — each rung runs everything below it.

| Rung | Stages |
|---|---|
| `prose` | none — diff review only |
| `static` | byte-compile the package (`python -m compileall`); linters once the repo adopts them |
| `unit` | `python -m pytest` — hermetic, loopback only |
| `integration` | `python -m pytest` including the tests that start emulator instances and run full updates against every firmware profile |
| `system` | an owner-approved, read-only (FC1–4) probe of the real heat pump compared with the current profile — never automated, never a write |

- **Hosted CI is not a gate.** `.github/workflows/python-publish.yml` (inherited from upstream) builds and uploads to PyPI whenever a GitHub release is created. It runs no tests, and creating a release is outward-facing: owner's OK only. Every gate runs locally on the DEV VM.
- **The rungs are not substitutes for one another.** Never satisfy a rung with a lower rung's evidence.

### Reading a gate's result

**Never pipe a gate into `grep`, `head`, or `tail` and then read the exit code.** The shell reports the *filter's* status, so a red gate reads as a pass. Capture first, narrow after:

```zsh
python -m pytest > scratchpad/gate.out 2>&1; rc=$?
rg -i 'failed|error' scratchpad/gate.out; print "gate status: $rc"
```

Copy this form. The capture variable is `rc`, never `status` — in zsh `status` is a readonly alias of `?`, so `status=$?` aborts the compound and loses the result.

## Command surfaces — ask the tool, not this file

This file names commands only by intent. Arguments, flags and exit codes come from the tool when you use it; copies in prose drift.

- `openbrain-client` (from PATH) — `openbrain-client --help-json` for record ops, run-queue, and worktree lifecycle; `<command> --help` for one topic. Agents run it with `--mode ai`.
- `genesis-wp-emulator --help` once the emulator exists.

## Tests

- pytest with pytest-asyncio. Tests stay hermetic: loopback networking only, no real device, no secrets. `tests/conftest.py` enforces this for the whole pytest process: any non-loopback connect, datagram or name lookup raises. Don't weaken it; point the test at the emulator instead. `unshare -rn` runs the suite in a loopback-only network namespace when you want proof.
- Shared harness: `tests/harness.py` (profiles, expected values, `library_full_update`) and the `start_emulator` / `connect` fixtures. `tests/test_library_against_emulator.py` is the regression net every transport or decoding change must keep green.
- Fixtures are firmware-profile snapshots captured by read-only probes, one per controller software / Genesis version (currently 6.0.6 / 17.2.40 and 6.1.5 / 17.3.145). Each fixture records its source and capture date, and carries no serial number.
- A new firmware on the device means a new profile, captured by a read-only probe and diffed against the previous one. Never edit an existing profile in place to make a test pass.

## Code conventions

- Python, asyncio throughout. No blocking I/O in async code paths.
- Modbus I/O goes through Home Assistant's `modbus-connection` (async, at a version compatible with what the current HA release ships). The client accepts an injected modbus-connection unit so HA can share one connection. Never call pymodbus, tmodbus or pyModbusTCP clients directly from the library.
- The emulator is the exception: it is a pymodbus server (`pymodbus.server.ModbusTcpServer` with `pymodbus.simulator.SimDevice`), shipped behind an `emulator` extra. Don't use the deprecated `ModbusServerContext` / `ModbusDeviceContext` datastore classes: in pymodbus 3.13.1 they swap holding and input registers.
- Register definitions carry address, block, scale, signedness, unit, and an optional sentinel and enum. Decoding is driven by those fields, never by special cases in code.
- Write paths stay behind the explicit read-only opt-in. A change that weakens that guard needs its own CR and the owner's approval.

### Never commit

`openbrain-client.local.yaml`, `.logs/`, `scratchpad/`, manufacturer PDFs or photos, the device host name or serial number, anything from the homelab `docs/stiebel/` folder other than extracted register facts.

## Key files

- `LICENSE` — MIT with the upstream notices; see *License and third-party material*.
- `openbrain-client.yaml` — committed product truth for `openbrain-client` (product, process, placeholders).
- `genesis_wp/__init__.py`, `genesis_wp/const.py` — the library (inherited from pythermiagenesis17; `const.py` keeps upstream's layout and register names so fixes stay portable).
- `genesis_wp/emulator/` — Modbus TCP emulator and firmware profiles; `scripts/probe_to_profile.py` converts read-only probe captures into profiles.
- `setup.py`, `requirements.txt` — packaging (inherited, still pyModbusTCP until the transport CR lands).
- `.github/workflows/python-publish.yml` — PyPI upload on GitHub release; see *The gate*.
