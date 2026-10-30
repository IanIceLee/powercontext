#!/usr/bin/env bash
# ci/runner.sh — 实例端常驻轮询器（B 方案）
#
# 做三件事：
#   1. 每 POLL 秒 fetch 一次 origin/competition/2026
#   2. 有新 commit 就强制同步到该 commit，读 ci/job.txt 取 make 目标，跑 `make -C competition <target>`
#   3. 把 rc + 日志尾部写到 ci/log 分支的 ci/results/，push 回 GitHub
#
# 所有状态都落在 /workspace（唯一持久卷），实例销毁即失效。
# 用法：nohup bash ci/runner.sh > /workspace/w/runner.log 2>&1 &

set -uo pipefail

REPO_DIR="${REPO_DIR:-/workspace/w/powercontext}"
LOG_DIR="${LOG_DIR:-/workspace/w/ci-logs}"
REMOTE="${REMOTE:-https://github.com/IanIceLee/powercontext.git}"
BRANCH="${BRANCH:-competition/2026}"
LOG_BRANCH="${LOG_BRANCH:-ci/log}"
POLL="${POLL:-45}"                 # 轮询间隔（秒）
MAX_RUNTIME="${MAX_RUNTIME:-2400}" # 单次任务上限（秒），防止挂死
STATE="${STATE:-/workspace/w/.ci_last_sha}"

cd "$REPO_DIR" || { echo "[runner] no repo at $REPO_DIR"; exit 1; }

if [ ! -d "$LOG_DIR/.git" ]; then
  mkdir -p "$LOG_DIR"
  git -C "$LOG_DIR" init -q
  git -C "$LOG_DIR" checkout -q -b "$LOG_BRANCH"
fi
git -C "$LOG_DIR" config user.name "ci-runner"
git -C "$LOG_DIR" config user.email "ci-runner@local"

last="$(cat "$STATE" 2>/dev/null || true)"
echo "[runner] $(date -u +%FT%TZ) watch $REMOTE $BRANCH (last=${last:0:8})"

while true; do
  if git fetch -q origin "$BRANCH" 2>/dev/null; then
    sha="$(git rev-parse FETCH_HEAD)"
    if [ "$sha" != "$last" ]; then
      target="$(git show "$sha:ci/job.txt" 2>/dev/null | head -1 | tr -d '[:space:]')"
      target="${target:-test-csv}"
      stamp="$(date -u +%FT%TZ)"
      echo "[runner] $stamp run '$target' @ ${sha:0:8}"
      git reset -q --hard "$sha"

      out="$(mktemp)"
      timeout "$MAX_RUNTIME" make -C competition "$target" >"$out" 2>&1
      rc=$?

      mkdir -p "$LOG_DIR/ci/results"
      {
        echo "# $target"
        echo
        echo "- commit: \`$sha\`"
        echo "- target: \`make -C competition $target\`"
        echo "- rc: **$rc**"
        echo "- finished: $stamp"
        echo
        echo '```'
        tail -n 300 "$out"
        echo '```'
      } >"$LOG_DIR/ci/results/latest.md"
      cp "$LOG_DIR/ci/results/latest.md" "$LOG_DIR/ci/results/${sha:0:8}-${target}.md"

      git -C "$LOG_DIR" add -A
      git -C "$LOG_DIR" commit -q -m "ci: $target @ ${sha:0:8} rc=$rc" || true
      if git -C "$LOG_DIR" push -q "$REMOTE" "HEAD:$LOG_BRANCH" 2>/dev/null; then
        echo "[runner] result pushed to $LOG_BRANCH"
      else
        echo "[runner] WARN push failed (check ~/.netrc)"
      fi

      echo "$sha" >"$STATE"
      last="$sha"
      echo "[runner] done rc=$rc"
      rm -f "$out"
    fi
  fi
  sleep "$POLL"
done
