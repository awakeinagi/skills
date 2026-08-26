# v0.9 branch disposition — why 37 branches were folded or dropped

**This file exists because the record nearly did not.** The close ran its
ledgers under `.unlazy/`, which is gitignored, so the reasons below lived in
one worktree's filesystem and were never on `main`. A whole-branch reviewer
caught it: the branches and the record were on opposite sides of the merge,
and pruning the branches would have destroyed the only account of why.

Every branch carrying unabsorbed work was reviewed at its head and then FOLDED
or DROPPED with a measured reason. The census was 39 names across 26 unique
heads — two pairs were the same commit under two names. **Exactly ONE head
carried unabsorbed release-grade content**; every other was superseded, and
about a dozen would have REGRESSED the tip if folded.

Three method rules earned during the review, stated because the reasons below
rest on them:

- **`git cherry` and patch-id can BOTH be wrong about whether work landed.**
  Decide it by comparing added CONTENT, not by patch-id.
- **Measure a branch against its MERGE-BASE, never the tip.** Comparing to the
  tip manufactured a phantom finding out of commits a branch predated.
- **An added-line overlap scan over-reports absence AND presence.** It reads
  code as missing when it is at tip in a better shape, and reads prose as
  present when both hits pre-date the fork. Use it to order the work; decide
  each branch by an AST pre-flight.

Line numbers cited below are pinned to the sha stated beside them, because
line numbers drift and this file outlives them.


## DROPPED `curator-40-candfix`

all three commits absorbed by content (40e superseded; 40f byte-identical on the tip via efc7247; 40g's er-caveat method hashes equal to the tip). Its only unfolded value was the reverted roster guard, cherry-picked at 3faa772.

## DROPPED `worktree-agent-a41021fac6987f466`

1 of 10 reds survived (the pin gate), salvaged and fixed at 7eb7e81. 7 duplicates covered on the tip with working live halves, 2 flips unearned (their scene aged out of triggering the path), 1 class instructed its own deletion.

## DROPPED `worktree-agent-a64d00431c219f4fc`

absorbed; the _composed_part refactor landed under a different commit message and canvas.py carries the comment saying so.

## DROPPED `curator-42-emitter-prose`

strict ancestor of agent-42-known-limit.

## DROPPED `agent-42-halfpixel-prose`

strict ancestor of agent-42-known-limit.

## DROPPED `curator-batch-36-textovertext`

Unit A of the batch-36 family (with worktree-agent-a7f35eaa24a151ba2, which literally contains its two commits; canvas.py byte-identical md5 540457de721d, identical 55-class/573-method roster). Superseded: its whole test contribution, TestTextDrawnOnText, exists at tip by name and tip's copy is BIGGER — 8 methods against the branch's 6, derived by AST. (An earlier wording called it "the 10-method TestTextDrawnOnText", which matches neither side). Folding its canvas.py would REGRESS the line-height stream — it writes NUNITO_LINE_HEIGHT=1.35 as a literal and spells `or <number>` at ~25 sites, the form tip's line_height_of docstring says "was wrong in all" cases; 54 top-level symbols tip defines are absent from it, line_height_of and font_line_height among them. Its mutant calibration is also stale: dy=45 reads 1800.0 at tip but its control pole label_y=49 FIRES at 1080.0, so its control is a second mutant, not a quiet pole. Credit banked, not lost: its "four reds flipped" is real and the live halves run 12 real `ok`s at tip.

## DROPPED `worktree-agent-a7f35eaa24a151ba2`

same unit as curator-batch-36-textovertext and the superset by 148 lines of in-method prose only; same drop reasons. Its own claim that _labelled_shape's Virgil default cannot witness the font-aware fix PROBED TRUE at tip (diamond_label_overflows_shape reads 17.0 under both the live rule and a forced font-blind 1.25) — correct, and already reflected in the tip's registration.

## DROPPED `curator-36-restore`

Unit B; ALREADY FOLDED as curator-36-restore-v2. Tip carries the v2 report at the same path, its _name_over_row builder and both test methods are byte-identical to tip's, and folding it would OVERWRITE the v2 report with the superseded v1. Its one name absent from tip, the mutant diamond_label_measured_at_its_own_spacing, was transplanted verbatim onto tip and FAILS there (596 tests, failures=1, "no finding of check='label_overflows_shape' element='t1'"): that scene emits text_overflow on d1 at tip, not label_overflows_shape on t1, and its neighbour passes vacuously because the check is quiet across that width range. Nothing is lost by dropping it — tip's dragged_label_measured_at_its_own_spacing does the discriminating job (22.0 live vs 14.0 font-blind, band [18.7, 25.3] rejects 14).

## DROPPED `impl-c36-fixes`

Unit B, strictly worse than its sibling and would regress tip. Zero unique module-level names, zero unique mutants. Its head commit claims to "recalibrate against the bar that shipped" and registers dy=45 → 1200.0 with band [1020, 1380], while the shipped bar yields 1800.0 — the stated cause is a different _name_over_row scene (240x100, no fontFamily=6).

## FOLDED `curator-45-dupfix-pins`

leaf-1.2.2 APPROVED its bottom commit `f28a799` and it is folded. The decisive finding is about the merge-base, not the branch: `60bd619` IS the fix commit (`_rehost_duplicated_parts`, plus `body` in the new-host gate) and it touched canvas.py + AGENTS.md ONLY, no tests — so the shipped repair had NO acceptance test. Proved by ablation, not argued: dropping the tie-break is caught by 0 of 1760 existing tests, and disabling `_rehost_duplicated_parts` entirely is caught by 0 of 1760. Only these pins object. Its other two commits `5d6ccb3` and `9fdedd5` are already landed and were NOT folded — verified by AST name walk at tip, not by patch-id (which is wrong about this branch and is recorded as such in PLAN.md): `5d6ccb3` introduces 6 names, 0 absent from tip; `9fdedd5` introduces 0. The fold also corrected a FALSE comment above the tie-break that called it "a belt this file has not yet proved it needs ... never reached"; three independent readings say it is reached and load-bearing.

## FOLDED `worktree-agent-a7531ef2ca391f4af`

leaf-1.2.4 APPROVED both commits; folded at 54c36a4. ADDITIVE (EXPECTED_SKIPS, skip_drift, RENDER_RUN_RECORD, render_evidence_currency, _GATED_CLASS all had zero occurrences at tip). The dispatch question — can browser-gated pins speak in an ordinary run, or do they pass by not running — is answered YES with a control: with MUTANTS_RENDER unset, no chromium on PATH and HOME an empty dir, TestRenderTierEvidence runs 4 tests OK and skips none, while the very test its record cites reports `skipped` in the same command. Old guard silent on all five degradations the new one fires on. One Important prose fix applied in the fold: a "31 call sites" figure that is a SUBSTRING count (real answer 12, all in TestOperators, zero catalogue mutants) — re-derived by me, not taken from the report. Record re-recorded against this canvas.py. Known and not closed: `_GATED_CLASS` is a source-text regex brittle to three legitimate decorator spellings; it fails conservatively and belongs with the anchor-as-prescription doctrine (leaf-1.3.1).

## FOLDED `worktree-agent-a50ff69bbc8e720c0`

leaf-1.1.4's fix, folded at b4c9299. `_rigid_copy_hosts` gives the UNGROUPED duplicated copy a second witness. The lead I handed the leaf (the editingGroupId corner) was the wrong corner and it said so; the real defect is Ctrl+Shift+G/save/Ctrl+D/save, damaging 7 of 8 composed kinds where the lint names only 4. 24 readings through Store.commit: 7 damaged -> 0, ink at the copy UNCHANGED. Ablation (`return {}` spliced into the new function) reproduces the reverted damage reading-for-reading, which rules out a silent no-op. G1 blocked-by-ownership, not by impossibility: the pin's pole is hand-built and never traverses the save path.

## DROPPED `worktree-agent-a629fbadbe9948f94`

leaf-1.2.7, TASK-COLORPARSE. Fully absorbed (6 canvas + 16 test_backend + 2 test_mutants names all at tip; `unreadable_color` byte-identical; tip's TestUnreadableColourIsReported holds all 15 of its methods plus 2). Folding would REGRESS: its `_parse_color_uncached` uses `lstrip("#")`, which tip replaced with `#`-anchored slicing. VERIFIED BY ME in-process at the tip: `parse_color('#000000')` -> (0,0,0), `parse_color('000000')` -> None, `parse_color('##000000')` -> None. Under the branch's form the last two parse as black. Transplant onto tip: failures=4, incl. "a '000000' fill minted #ffffff label ink".

## DROPPED `worktree-agent-a996b413a2d48f400`

leaf-1.2.7, TASK-VOCAB. Fully absorbed (`drawn_path_changed` and TestAOnePixelRedrawIsStillARedraw byte-identical; TestTidyCountsTheArrowsItRedrew is a tip superset at +65/-3). Folding would REGRESS the narration: it reverts tip's "re-routed %d of %d server-routed connector(s)" to "legacy arrow(s)", and tip's note now covers bound LINES that "arrow(s)" misnames. Transplant: failures=5.

## DROPPED `worktree-agent-aa5c668390052b542`

leaf-1.2.7, SPIKE-CORRIDOR. ZERO DELTA: `_lane_overlap`, `_lane_bow`, `_cross_at` and TestCorridorKind are byte-identical to tip. The reviewer's own transplant probe REFUSED on all three with "PROBE IS A NO-OP: head and ctrl versions are identical" — a probe that declines to run rather than reporting a clean pass. Only a stale livedoc value remains, which is staleness and not content.

## DROPPED `worktree-agent-aa729e75b3b20c786`

leaf-1.2.7, TASK-MICROFIX-3. Fully absorbed (`mermaid_edge_label`, TestRenderSvgHonoursTheClientArrowheadDefault byte-identical). Folding would REGRESS `arrowhead_of` past its 2026-08-18 type gate. VERIFIED BY ME in-process at the tip: `arrowhead_of({'type':'freedraw'}, True)` -> None and `{'type':'line'}` -> None, while `{'type':'arrow'}` -> 'arrow'. Under the branch's form a freedraw answers 'arrow' and a line with two explicit heads reads bidirectional. Transplant: failures=4. This is the branch that CONFIRMS the census caveat: its `arrowhead_of` is at tip in the better shape — `-> str | None` AND the type gate — where the head's is `-> Any` without it.

## DROPPED `fold-lineheight-prerebase`

leaf-1.2.8 Unit A (SAME COMMIT 2f60c0f as worktree-agent-a543cb84ae1b2700d; one disposition, two names). SUPERSEDED, and deliberately NOT for leaf-1.2.1's reason — this branch is the ORIGIN of tip's line-height derivation, not a retreat from it. It INTRODUCES NUNITO_LINE_HEIGHT, line_height_of, font_line_height, detected_line_height, FONT_METRICS, ink_clearance and _compose_attribute_rows, and all seven bodies are BYTE-IDENTICAL to tip by AST body compare with docstrings stripped. 22/22 of its test methods are at tip. Its one genuinely-absent item is a run-6 requirements ROW in the e2e README ("Does the app AUTO-GROW a container instead of spilling its label?") — but the QUESTION is not lost: tip carries it verbatim in TestOneLineHeightHasTwoReaders' docstring (tests/test_mutants.py:28400), assigning it to the e2e tier beside placement.spec.ts. VERIFIED BY ME; my first check missed it because I grepped only *.md, where the ROW lives, not the *.py docstring where the QUESTION lives. Not carry-forward. Its own UNCOVERED detector (text_over_text) is CLOSED at tip by entity_name_dragged_onto_its_row; tip's single remaining UNCOVERED is label_spills_past_body, a different detector from a different task.

## DROPPED `worktree-agent-a543cb84ae1b2700d`

same commit as fold-lineheight-prerebase (2f60c0f); same disposition.

## DROPPED `worktree-agent-a5d7a511ca04f6053`

strict ANCESTOR of worktree-agent-ac4f76b6c7bba7a17 (merge-base --is-ancestor YES): the curator half that pins 4 reds, where aceb4da+253b231 are the impl half that flips them. One deliverable, dispositioned at 253b231 which covers it entirely.

## DROPPED `worktree-agent-ac4f76b6c7bba7a17`

leaf-1.2.8 Unit B. Superseded AND folding it would REGRESS the tip (Critical). Its server_routed_connectors body carries the SUPERSEDED both-ends binding rule; tip's docstring says "ONE END IS ENOUGH, SINCE 2026-08-20 (C-3 fold review, C-1)" and calls the old rule the thing that "made the count above the list a LIE" — VERIFIED BY ME at `canvas.py:7209` as of b4c9299 (cite the sha: line numbers drift). Folding reverts `any(...)` to `... and ...`, so a line bound at only ONE end drops out of the population: tip reads ['c1'], the branch body reads []. The two agreeing rows (both-ends-bound, and bound-to-nothing) are the control proving the rules are not trivially different. Consequence: the pass still moves a one-end-bound line 13px and stops telling anyone, re-entering C-3 through the denominator. Also reverts reroute_scene from tip's routable_arrows population plus pinned_to_canvas guard to an inline `type == "arrow"` spelling.

## DROPPED `worktree-agent-a58ad2503767c0390`

leaf-1.2.9 Unit A, "TASK-ELBOX". Landed at tip on every one of its six REAL files; the 127-file diff is 121 rebuilt scripts/web/ bundle assets, not 127 files of work. pin_spot drops `frame` at tip as here; elBox in App.tsx byte-identical to tip; placement.spec.ts down to 1 test.fail() at tip as here; tip's 1.4MB app chunk carries the fixed `["arrow","line","freedraw"].includes` gate (fork 0 / A 1 / tip 1). Folding REGRESSES twice: pin_spot re-inlines size=26 and dx=8,dy=-8 where tip has PIN_SLOT_PX/PIN_HUG_PX, and App.tsx reverts 685 lines including fontsSettled. Its `npm ci` disclosure is salvaged separately (see status.log) rather than lost with it.

## DROPPED `worktree-agent-a8c46bfb6c4e62282`

leaf-1.2.9 Unit B, curator batch 32/32b. All 8 methods of TestTheElectedRouteIsReadableWhereItLands are at tip, 6 code-identical; the 2 that differ are BOTH tip-ahead — its red is flipped green at tip (the fix `_grazing_terminal_legs` is defined and referenced twice in scoring at tip, absent at the fork), and tip's `_drawn_arrival` raises a documented AssertionError with a `Raises:` section where the head uses assertIsNotNone and no `Raises:`, so tip is the one meeting the repo's docstring standard. Batch 32b's `_leg_quote` repair is at tip verbatim.

## DROPPED `curator-39-textruth-catalogue`

leaf-1.2.9 Unit C, which STRICTLY CONTAINS worktree-agent-ad66261837ef4d530 (merge-base --is-ancestor returns 0, so that head is this one minus its top commit). Both halves landed; tip ahead on all four points of contact; all five client-geometry helpers it introduced are at tip. THREE CRITICAL regressions if folded: (C-1) reverts `js_round` to Python `round` at five sites — measured, they disagree on 145 values over widths 20..600, every one an exact .5, control agreeing at 141.42; (C-2) reverts the `shape_band_span` fix its OWN top commit filed the red for; (C-3) deletes tip's entire kind-vocabulary block in lint_layout plus the lineHeight normalisation in validate_scene — 150 code-only diff lines, and that is leaf-1.2.3's work.

## DROPPED `worktree-agent-ad66261837ef4d530`

strict ancestor of curator-39-textruth-catalogue; same disposition, dispositioned at a25c964 which covers it entirely.

## DROPPED `curator-39-batch39`

leaf-1.2.9 Unit D (SAME COMMIT 14f1d32 as worktree-agent-ab84afe1cae1a5605). All three stated causes repaired at tip and all four reds flipped: EMPTY_SAVE_HEADLINE fork 0 -> tip 3, content_drift_count fork 0 -> tip 4, and both open-coded copies now call `_composed_part`. 17 of its 22 functions code-identical at tip. Folding would also make one test VACUOUS: its `unfacted_drift` drifts `lineHeight = 1.5`, but tip added `lineHeight` to `content_units`' skip set, so at tip that scene produces NO drift, never reaches the guard, and passes without asserting anything — tip's swap to `verticalAlign` was forced, not cosmetic, and tip carries a referee that catches exactly this.

## DROPPED `worktree-agent-ab84afe1cae1a5605`

same commit as curator-39-batch39 (14f1d32); same disposition.

## DROPPED `worktree-agent-a1ec170c8c9f1198c`

leaf-1.2.10. THREE folding regressions, each reproduced: (1) re-masks TestAFreedrawKeepsTheGeometryItWasGiven's two reds that tip flipped on 2026-08-18 -> `FAILED (unexpected successes=2)`, and tip's class is one method LONGER; (2) lands a premise pin asserting `arrowhead_of` answers "arrow" for a line -> `failures=1`, `None != 'arrow'`, which tip supersedes with a type-gate reader, and the head's sibling PROSE ("arrowhead_of is type-blind") would land FALSE with it; (3) lands two pins on a `refresh_files` corruption tip repaired -> `failures=3`. Also a `mutants_sweep.json` "swept" date ROLLBACK 2026-08-19 -> 2026-08-18. Its `mutants_mortality.py` row is byte-identical to tip.

## DROPPED `worktree-agent-abf7ac701ad3ae1c1`

leaf-1.2.10. All FOUR of its reds are green at tip; folding re-masks them -> `unexpected successes=3` (decorator restore) plus `unexpected successes=1` (graft of the fourth). Both defects it pins are fixed: the entity-label drift and the lineHeight 1.25-vs-1.35 divergence. Its one apparently-absent method is superseded by tip's STRICTLY WIDER `test_both_verbs_draw_the_same_entity`, which compares the whole element list minus VOLATILE_ATTRS rather than the head's (verticalAlign, y) pair. NOTE: its second finding, "no check owns the picture", is STILL OPEN at tip and is handed to a curator — see status.log. It deliberately declined to pin that one because a red naming a non-existent check has no owner, which is the right call.

## DROPPED `worktree-agent-a6968e773cc39f0d5`

leaf-1.2.10, ZERO content delta, confirmed two independent ways: 560 added lines in tests/test_mutants_render.py with 0 absent from tip, and an AST roster of 1 name + 7 methods all present at tip. Its only other delta is one livedoc line (1445 -> 1449), which is machine-maintained staleness and never foldable.

## DROPPED `worktree-agent-a4163fed977e2ce9a`

leaf-1.2.10. Two regressions. (1) Reverts tip's GENERALISED graze sentence (connector_noun / END_VERB / waive_hint, headless phrasing) to a hard-coded "arrow %s arrives at ..." with a hand-spelled waive envelope -> `FAILED (failures=8)`, 0 errors, against a control of `Ran 31 ... OK` on unmodified tip. Sharpest arm: the whole tearsheet-pipeline corpus emits graze findings whose waive no longer round-trips ("no finding on tp offers a waive"). (2) Re-masks three reds inherited from its fork that are green at tip -> `unexpected successes=3`.

## DROPPED `rebase-attempt`

leaf-1.2.11. NOT a mechanical artefact, proved on CONTENT rather than on the name: two substantive TASK-ARRIVALLINT commits, both landed at tip as f4262e9 and c6454b6 with BYTE-IDENTICAL messages; 579 of 627 added lines verbatim at tip; GRAZE_COS = 0.342 identical (verified by me). Superseded, and folding would REGRESS three things. (I-1) It carries `CATALOGUE_RED_IDS: set[str] = set()` against tip's `{"duplicated_widget_reported_as_bare"}` — VERIFIED BY ME at `test_mutants.py:25973` as of b4c9299, where leaf-1.2.11 ran (cite the sha: it is now line 26541 and holds a different id, which is expected drift from the fixes that landed after) — so folding BLANKS the declared-red set that leaf-1.1.4's in-flight red depends on; counterfactual control rc=0, transplant rc=1 FAILED. (I-2) Its `_ARRIVAL_SIDE_RE` reads `arrives at` where tip reads `(?:arrives at|leaves)`, narrowing the regex to one end of the arrow. (I-3) Its finding opens with the literal "arrow" where tip uses `connector_noun` — folding makes the lint call a line an arrow again, which is this wave's own headline defect class.

## DROPPED `worktree-agent-ad597ad6ed5b962cd`

leaf-1.2.11. THE GENERALISATION COVERS THE BRANCH'S CASE, verified by RUNNING the lint rather than reading it: on the branch's own diagonal scene tip emits `arrow a runs diagonally (1140x46px)`, identical to the branch's sentence, and the same scene typed `line` emits `line a ...` where the branch still says `arrow`. All three commits at tip byte-identical; canvas.py has ZERO absent added lines; all four canvas names byte-identical. IMPORTANT: folding its test_backend.py would strip `# ty: ignore[invalid-assignment]` from three lines and take `uvx ty@0.0.21` from clean to 3 diagnostics — one of the three instruments run at every fold. Disclosure check CLEARED: its message holds a SECOND, distinct environment trap (a `tsc` failure from the main checkout sitting on a `main` whose package.json predates @mermaid-js/layout-elk) — NOT the npm-ci omission salvaged at 3308ed9, different failure and cause — and e7bdb8e at tip carries that message byte-for-byte, so nothing is lost.

## DROPPED `line-binding-error`

leaf-1.2.11. This was the ONLY head in the entire 26-head census with a genuinely absent CODE residual, and the residual says something unsaid at tip but NOT TRUE. Its comment claims "`lint_layout` remains the load-path answer, and already names such a scene precisely". Probed: a bound `line` whose end sits 264px off its node draws ZERO findings at the branch head AND at its own merge-base when it carries `role: decoration` — which every line in the corpus carries — while the `role: edge` control draws 1 finding in the same tree, so the probe earned its silence. Tip does not merely drop those two lines: it REPLACES them with an explicit correction, VERIFIED BY ME at `canvas.py:9225` as of b4c9299 (cite the sha: line numbers drift) — "THAT LAST CLAIM WAS WRONG WHEN IT WAS FIRST WRITTEN and is worth keeping as a correction rather than an edit ... that check walks `arrows`, which filters `role: decoration` out first ... the net the silence above leans on had a hole shaped like the whole population" — plus a role-blind, geometry-blind check landed in 5adbfd9. Folding the residual would DELETE A RECORDED CORRECTION and reinstate a disproved claim.

## DROPPED `fix-handover-roster-prose`

leaf-1.2.11. Fully landed: 422 of 423 added lines verbatim at tip, both commits present as f32b7a7 and 61ba123 with byte-identical messages, all 8 added module-level names byte-identical at tip. It is the ORIGIN of that code rather than a competitor to it — the probe REFUSED at its merge-base because `handover_hand_authored_red_classes` does not exist there. THE LIVE-VALUE RISK I FLAGGED AT DISPATCH DOES NOT SURVIVE SCRUTINY: census_probes.py 109/109, livedoc.py 9/9, test_livedoc.py 26/26 and SESSION-HANDOVER.md 52/52 are all at tip, and the single absent line in the whole branch is a `unittest_suite_cases` marker body (1922 vs tip's 2033) — machine-maintained, never folded. No findings above Minor.

## FOLDED `spike/fragile-regexes`

leaf-1.2.12, CHANGES NEEDED then FOLD; landed at ee23446. THE ONLY UNABSORBED RELEASE-GRADE HEAD IN THE 26-HEAD CENSUS. Eight of nine detectors opened with the literal `arrow` while lint_layout opens those sentences with connector_noun, so a line-typed scene made them match nothing and Silence became indistinguishable from coverage — reproduced at tip: 8 detectors fire on 8/7/7/5/4/4/3/2 of 34 arrow-bearing scenes and lose EVERY one on the twin, while 4 other checks survive it (the control that makes the twin a variable rather than a wrecking ball). As built it changes findings on 0 of 71 scenes. IT EARNS THE FOLD ON ITS OWN GUARD: the anti-rot arm caught its author's staleness 205 commits later, naming shared_corridor and false_bidi as rows whose narrowing instruments._arrows had since repaired; both deleted at the fold on its say-so. G4 verified 8 of 8 ablations FAIL, each an AssertionError not an error-red, each naming exactly that detector and every scene it loses; green under five differently-worded honest repairs, so it prescribes none. FOLDED WITH FOUR CORRECTIONS, each measured: the "45 call sites" figure DROPPED (AST says 46 here, 42 at its own fork — never right rather than stale; grep counts the def line, which is where my own published 47 came from); the _FALSE_BIDI_LINT_RE "loosening" reason corrected to a NO-OP (0 of 105 scene-forms move); the _arrows grouping sentence corrected (wrong twice — _arrows is no longer arrow-only, and float_diamond never called it); and the rot test's "a proof rather than a ratchet" claim corrected (narrow-and-declare turns both arms green, so the pair accepts a false arrow-only claim). Residue reported CLOSED (crossings_count moved on 6 scenes at the fork, 0 of 34 here) with the doctrinal half kept and labelled UNGUARDED. Two holes in its own pins closed: _CONN's pin was blind to a fourth noun on a NEW type (the likelier path), and the rot guard buried its instruction behind a 1200-char dict dump.

## FOLDED `curator-41-kindvocab`

PARTIALLY, via leaf-1.2.6's salvage at 6afd248, and the rest DROPPED. leaf-1.2.3 reviewed it and found most superseded; leaf-1.2.6 landed only what it re-derived as still true: `run_probe`'s error-as-its-own-verdict split (before, a guard that FAILS and a guard that RAISES both returned True — the same disease as expectedFailure swallowing errors), the suite test rewritten to a TOKEN reader, and the symlink write-through stop. DROPPED from it: all four census probes, because every one targets `test_the_wireframe_kind_vocabulary_is_the_documented_set`, which has ZERO hits anywhere in the tree — four probes aimed at a test that does not exist. Also dropped: the `Its kinds (...)` regex, the anchor-as-prescription shape the tip had already rejected deliberately in favour of the token reader. The token reader's value is measured, not argued: over a message burying every kind inside a longer word it names 0 of 17 where the branch's `assertIn` sweep names 17 of 17, and that false clean bill is what the salvage removed.

## DROPPED `task-client-profile-docs`

fully absorbed, measured two ways. An AST method walk of `tests/test_mutants_render.py` says the branch adds ZERO methods its fork did not have, and every name it carries is at tip (`new <= tip` is True). An added-line scan agrees independently: 0 of its 57 substantive added lines are absent from tip. Nothing to fold.

## DROPPED `fix-refusal-kinds-a172`

leaf-1.2.3 probed all three claims: the "third WIREFRAME_KINDS in Inspector.tsx" is NOW FALSE by two surviving routes: no array literal names two or more kinds in Inspector.tsx, and the SERVED bundle (resolved from index.html, because a glob picks the wrong one of the two) holds kind_vocabulary x1 and sticky-bar x0. **A THIRD ROUTE I RECORDED HERE WAS FALSE** — "zero kind-name hits across src/" was disproved by leaf-1.2.6 when it went to re-derive it: "button" x8, "checkbox" x4, "toggle" x3. I transcribed it from a review instead of measuring it. The conclusion survives on the other two; "no frontend/backend parity check exists" is NOW FALSE (TestTheKindVocabularyHasOneOwner + TestTheVocabularyReachesTheClient, 6 tests green); and its `Its kinds (...)` regex is ALREADY COVERED by a token-based reader the tip chose deliberately over exactly that regex. Salvage: two Minor doc repairs only, rewritten against tip prose (leaf-1.2.6).
