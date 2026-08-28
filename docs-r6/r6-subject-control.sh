#!/usr/bin/env bash
# Asserts the subject has not drifted under the run. §6b: ten instances this
# wave of a control drifting onto its subject, EVERY ONE failing toward "all
# clear" -- so this prints DRIFT loudly and exits non-zero rather than staying
# quiet. The subject is the SKILL DIRECTORY, not the worktree: the run writes
# its own notes file at the repo root by convention, and that must not read as
# the subject changing.
cd /home/cognizac/Projects/wysiwyg_grilling_skill.worktrees/capability_assessment || exit 2
WANT=1e528a0ddd270ff20c5d2496954f1a19010dc013
GOT=$(git rev-parse HEAD:skills/wysiwyg-grilling)
DIRTY=$(git status --porcelain -- skills/wysiwyg-grilling | wc -l)
if [ "$GOT" = "$WANT" ] && [ "$DIRTY" -eq 0 ]; then
  echo "SUBJECT-INTACT $GOT"
else
  echo "SUBJECT-DRIFT want=$WANT got=$GOT dirty=$DIRTY"; exit 1
fi
