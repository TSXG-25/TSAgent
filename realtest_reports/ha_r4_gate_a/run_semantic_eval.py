"""Explicit real Provider Gate A probe; no task execution and no E2E claim."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# First six are production-prompt examples; remaining positives are held out.
CASES = [
    ('prompt-example', '帮我写个计算器', True),
    ('prompt-example', '做个简单网页', True),
    ('prompt-example', '实现一个 CSV 转换工具', True),
    ('prompt-example', '给我弄个 hello world 项目', True),
    ('prompt-example', '整一个能读 JSON 的脚本', True),
    ('prompt-example', '做两份报告给我', True),
    ('held-out', '帮我写个汇率转换计算器', True),
    ('held-out', '我需要一个把表格转成 JSON 的小工具，帮我做好', True),
    ('held-out', '给团队弄一页能用的倒计时页面', True),
    ('negative', '算一下 12×13', False),
    ('negative', '解释 CSV 格式', False),
    ('negative', '只在聊天里展示计算器代码，不创建文件', False),
    ('negative', '告诉我如何搭建 hello world 项目', False),
    ('negative', '写一段新闻摘要', False),
]


async def run(provider):
    from agent.cognition.cognitive_context import CognitiveContext
    from agent.cognition.intent_engine import IntentEngine
    from agent.llm import LLMRouter

    engine = IntentEngine()
    engine._llm = LLMRouter(provider_mode=provider)
    report = Path(__file__).parent / ('semantic-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    rows = []
    for index, (group, request, expected) in enumerate(CASES, 1):
        start = time.monotonic()
        result = await engine.analyze_async(CognitiveContext(query=request))
        outcomes = [o.value for o in result.requested_outcomes]
        passed = (result.artifact_required == expected and result.requires_execution == expected
                  and (('FILE_MUTATION' in outcomes) == expected))
        status = ('PROVIDER_ERROR' if result.failure_code.startswith('PROVIDER_') else
                  'FAIL' if result.failure_code or not passed else 'PASS')
        rows.append(dict(case_id=f'gate-a-{index:02d}', group=group, request=request,
                         expected_artifact=expected, expected_execution=expected,
                         artifact_required=result.artifact_required,
                         requires_execution=result.requires_execution,
                         requested_outcomes=outcomes, domain=result.domain, action=result.action,
                         failure_code=result.failure_code, status=status,
                         seconds=round(time.monotonic()-start, 3)))
        report.write_text(json.dumps(dict(provider=provider, scope='intent-only; no Run or task executed',
                                          cases=rows), ensure_ascii=False, indent=2))
        print(f'gate-a-{index:02d}: {status}', flush=True)
    print(report)
    return 0 if all(row['status'] == 'PASS' for row in rows) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', required=True, choices=['deepseek', 'ollama'])
    args = parser.parse_args()
    os.environ['TSAGENT_LLM_PROVIDER'] = args.provider
    raise SystemExit(asyncio.run(run(args.provider)))
