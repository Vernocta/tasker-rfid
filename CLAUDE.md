# Working notes for Claude Code

## Context files

`.claude/context/` is this project's working memory, written by Christian
from planning conversations:

| File | What it holds |
|---|---|
| `project.md` | What Tasker is, who uses it, the stack |
| `requirements.md` | What must be true, marked [E]xplicit / [D]erived / [A]ssumption |
| `decisions.md` | Settled decisions and the reasoning behind them |
| `design.md` | The dashboard's design system and principles |
| `current-state.md` | What works, what is verified, what is blocked |
| `tasks.md` | NOW / NEXT / LATER / BLOCKED |
| `feedback.md` | Problems found, and whether they are resolved |

**Keep `current-state.md`, `tasks.md` and `feedback.md` up to date after any
meaningful change, in the same commit as the change itself.** A commit that
moves the project forward and leaves these stale has only done half the job.

The other four change less often and describe intent rather than state. Do
not rewrite them without asking — if the code has drifted from what they
say, say so rather than quietly editing them to match.

## The one rule that matters most

`SPEC.md` is the authoritative specification. If a change contradicts it,
say so rather than diverging silently; if the spec turns out to be wrong,
correct the spec in the same commit and explain why.
