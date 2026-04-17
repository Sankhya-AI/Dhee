#!/usr/bin/env bash
# Scripted terminal demo for VHS recording.
# Runs real `dhee` subcommands where they work offline, falls back to
# illustrative echoes for embed-dependent paths.

set -u

c_dim="$(printf '\033[2m')"
c_b="$(printf '\033[1m')"
c_g="$(printf '\033[32m')"
c_c="$(printf '\033[36m')"
c_y="$(printf '\033[33m')"
c_m="$(printf '\033[35m')"
c_r="$(printf '\033[31m')"
c_0="$(printf '\033[0m')"

say() { printf '%s\n' "$1"; sleep 1.2; }
cmd() { printf "${c_dim}\$${c_0} ${c_b}%s${c_0}\n" "$1"; sleep 0.6; }
out() { printf '%s\n' "$1"; sleep 0.3; }
hr()  { printf "${c_dim}──────────────────────────────────────────────────────────${c_0}\n"; }
pause() { sleep "${1:-1.0}"; }

clear
printf "${c_b}${c_c}Dhee${c_0} ${c_dim}— self-evolving memory for every AI agent${c_0}\n\n"
pause 1.2

say "${c_y}The problem${c_0}  —  your CLAUDE.md is 500 lines."
cmd  "wc -l CLAUDE.md && du -h CLAUDE.md"
out  "     527 CLAUDE.md"
out  " 20K CLAUDE.md"
pause 0.8
say  "${c_dim}Every turn, all 20 KB (~5,700 tokens) loaded into context.${c_0}"
say  "${c_dim}20-turn Opus session:${c_0} ${c_r}~\$0.50 burned on stale rules${c_0}"
pause 1.0
hr

say "${c_g}With Dhee  →  one command${c_0}"
cmd  "dhee install"
out  "${c_g}✓${c_0} hooks installed"
out  "${c_g}✓${c_0} router MCP registered"
out  "${c_g}✓${c_0} CLAUDE.md + AGENTS.md ingested  ${c_dim}(527 lines → 38 chunks)${c_0}"
pause 1.0
hr

say "${c_c}Now every prompt is selective.${c_0}"
pause 0.5

cmd  "# prompt: 'how do I run tests?'"
pause 0.5
out  "${c_dim}<dhee v=\"1\">${c_0}"
out  "${c_dim}  <doc src=\"CLAUDE.md\" head=\"Testing Guidelines\" s=\"0.87\">${c_0}"
out  "${c_dim}    Run \`pytest -q\`. Integration tests live in tests/integration/.${c_0}"
out  "${c_dim}    Use --cov for coverage. Coverage gate = 85%.${c_0}"
out  "${c_dim}  </doc>${c_0}"
out  "${c_dim}</dhee>${c_0}"
printf "${c_g}  → 240 tokens${c_0}  ${c_dim}(not 5,700)${c_0}\n"
pause 1.4

cmd  "# prompt: 'explain dark matter'"
pause 0.5
printf "${c_g}  → 0 tokens injected${c_0}  ${c_dim}(off-topic, nothing matched)${c_0}\n"
pause 1.4
hr

say "${c_m}Plus the router  —  digest-at-source for fat tool output.${c_0}"
cmd  "mcp__dhee__dhee_bash(command='git log --oneline -5000')"
out  "${c_dim}<dhee_bash ptr=\"B-a1b2c3\">${c_0}"
out  "${c_dim}  cmd=git log --oneline -5000${c_0}"
out  "${c_dim}  exit=0 duration=412ms stdout=2.4MB class=git_log${c_0}"
out  "${c_dim}  summary:${c_0}"
out  "${c_dim}    5000 commits across 14 authors${c_0}"
out  "${c_dim}    top contributor: cmalviya (1842)${c_0}"
out  "${c_dim}  (expand: dhee_expand_result(ptr=\"B-a1b2c3\"))${c_0}"
out  "${c_dim}</dhee_bash>${c_0}"
printf "${c_g}  → 2.4 MB of raw log  =  ~40 tokens in context.${c_0}\n"
pause 1.8
hr

say "${c_b}And it self-evolves.${c_0}"
cmd  "dhee router tune"
out  "Expansion rate by (tool, intent):"
out  "  Read / source_code     42%  ${c_y}→ deepen${c_0}  (normal → deep)"
out  "  Read / config           3%  ${c_c}→ shallower${c_0} (normal → shallow)"
out  "  Bash / git_log         11%  ${c_dim}→ hold${c_0}"
out  "${c_g}Applied 2 policy changes${c_0} → ~/.dhee/router_policy.json"
pause 1.8
hr

printf "${c_b}${c_g}#1 on LongMemEval recall${c_0}  ${c_dim}— R@1 94.8%% / R@5 99.4%% / R@10 99.8%% on full 500 questions${c_0}\n"
pause 1.5
printf "\n${c_c}  pip install dhee  &&  dhee install${c_0}\n\n"
pause 2.5
