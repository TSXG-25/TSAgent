#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
export TSAGENT_LLM_PROVIDER="${TSAGENT_LLM_PROVIDER:-deepseek}"
case "$TSAGENT_LLM_PROVIDER" in
  deepseek|ollama) ;;
  *) echo '人工验收必须显式选择 deepseek 或 ollama，不能使用 auto。' >&2; exit 2 ;;
esac
# This launcher explicitly opts into local command execution for human acceptance.
export TSAGENT_ALLOW_LOCAL_EXECUTION=1
DB="/private/tmp/tsagent-human-$(date +%Y%m%d-%H%M%S).sqlite"
echo "Provider: $TSAGENT_LLM_PROVIDER"
echo "Database: $DB"
echo '本次人工验收允许在本机执行任务命令。'
exec .venv/bin/python main.py --db "$DB" --tenant-id human-acceptance --user-id tester --session-id task-s1
