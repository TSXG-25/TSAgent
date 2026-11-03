"""Run the bounded HA-R3 regression set, without real Provider/API calls."""
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
REPORT = Path(__file__).resolve().parent
TESTS = [
    'test_ha_r1_harness.py', 'test_ha_r2_goal_pipeline.py', 'test_ha_r3_file_evidence.py', 'test_write_truthfulness.py', 'test_p2_lh1_workspace_boundary.py', 'test_v23d_runtime_propagation.py', 'test_p1_h1_capability_closeout.py', 'test_llm_provider_mode.py', 'test_provider_input_hotfix.py', 'test_h2_effect_truthfulness.py', 'test_h3_output_freshness.py',
    'test_h3_cli_output.py', 'test_h4a_requested_outcomes.py', 'test_h4b_effect_authorization.py',
    'test_h4c_execution_evidence.py', 'test_h41a_effect_contract.py', 'test_h41b_execution_routing.py',
    'test_h5a_workspace_resolution.py', 'test_h5b_partial_outcome.py', 'test_h6a_context_policy.py',
    'test_goal_action_contract.py', 'test_h8_single_runtime_spine.py', 'test_v23c_service_e2e.py',
    'test_v23c_service_contract.py', 'test_v23c_agent_service.py', 'test_v23d_cancellation_service.py',
    'test_conversation_runtime.py', 'test_cognitive_layer.py', 'test_context_architecture.py', 'test_runtime_context_isolation.py',
    'test_planner_contract.py', 'test_v24a_planner_evidence.py', 'test_architecture_verification.py',
    'test_v23b_architecture_gate.py', 'test_plan_executor_errors.py', 'test_security_boundary.py',
]
env = dict(os.environ, PYTHONPATH=f'{ROOT / "realtest_reports/ha_r1"}:{ROOT}', TSAGENT_LLM_PROVIDER='deepseek',
           HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
command = [str(ROOT/'.venv/bin/python'), '-m', 'pytest', '-p', 'offline_network_guard', '-q',
           *('tests/'+name for name in TESTS), '--disable-warnings', '--tb=short',
           f'--junitxml={REPORT / "offline-results.xml"}']
with (REPORT/'offline-tests.log').open('w') as output:
    result = subprocess.run(command, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
print((REPORT/'offline-tests.log').read_text())
raise SystemExit(result.returncode)
