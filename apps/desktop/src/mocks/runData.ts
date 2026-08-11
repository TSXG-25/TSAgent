import type {
  ArtifactView,
  ConversationMessageView,
  RunEventView,
  RunView,
  StageView,
  StageStatus,
  WorkflowView,
} from "../types";

const solutionSource = `from pathlib import Path


def square(value: int) -> int:
    return value * value


if __name__ == "__main__":
    result = square(7)
    Path("output/result.txt").write_text(str(result), encoding="utf-8")
    print(result)
`;

const verificationSource = `{
  "run_id": "run-2048",
  "command": "python output/solution.py",
  "exit_code": 0,
  "stdout": "49\\n",
  "checks": ["exit_code", "stdout", "artifact"]
}`;

function stagesForInterruptedRun(): StageView[] {
  return [
    {
      stageId: "analysis",
      name: "Analysis",
      eyebrow: "01 / PLAN",
      description: "Extract the request into an executable spec.",
      status: "completed",
      duration: "00:18",
      tasks: [
        { taskId: "analysis.spec", title: "Generate spec", status: "completed" },
        { taskId: "analysis.scope", title: "Resolve workspace", status: "completed" },
        { taskId: "analysis.guard", title: "Lock constraints", status: "completed" },
      ],
    },
    {
      stageId: "implementation",
      name: "Implementation",
      eyebrow: "02 / BUILD",
      description: "Generate the Python artifact in the run workspace.",
      status: "interrupted",
      duration: "02:56",
      tasks: [
        { taskId: "implementation.write", title: "Write solution.py", status: "completed" },
        { taskId: "implementation.execute", title: "Execute program", status: "interrupted" },
        { taskId: "implementation.capture", title: "Capture stdout", status: "queued" },
      ],
    },
    {
      stageId: "verification",
      name: "Verification",
      eyebrow: "03 / PROVE",
      description: "Run deterministic checks against the produced artifacts.",
      status: "queued",
      duration: "—",
      tasks: [
        { taskId: "verification.exit", title: "Check exit code", status: "queued" },
        { taskId: "verification.stdout", title: "Match stdout", status: "queued" },
        { taskId: "verification.artifact", title: "Verify artifact", status: "queued" },
      ],
    },
  ];
}

function completedStages(): StageView[] {
  return stagesForInterruptedRun().map((stage) => ({
    ...stage,
    status: (stage.stageId === "verification" ? "verified" : "completed") as StageStatus,
    duration: stage.stageId === "verification" ? "00:35" : stage.duration,
    tasks: stage.tasks.map((task) => ({ ...task, status: "completed" as StageStatus })),
  }));
}

function workflow(
  status: RunView["status"],
  progress: number,
  stages: StageView[],
  workflowId = "code-generation-v2",
): WorkflowView {
  return {
    workflowId,
    name: "Code Generation",
    version: "v2.2C",
    status,
    progress,
    stages,
  };
}

function artifacts(runId: string, includeVerification = false): ArtifactView[] {
  return [
    {
      artifactId: `${runId}.solution`,
      path: "output/solution.py",
      kind: "python",
      size: "248 B",
      updatedAt: includeVerification ? "just now" : "2 min ago",
      status: includeVerification ? "verified" : "generated",
      content: solutionSource,
    },
    {
      artifactId: `${runId}.verification`,
      path: "output/verification.json",
      kind: "json",
      size: "172 B",
      updatedAt: includeVerification ? "just now" : "waiting",
      status: includeVerification ? "verified" : "pending",
      content: includeVerification
        ? verificationSource
        : "{\n  \"status\": \"awaiting_resume\",\n  \"checks\": []\n}\n",
    },
    {
      artifactId: `${runId}.stdout`,
      path: "logs/stdout.txt",
      kind: "text",
      size: includeVerification ? "3 B" : "0 B",
      updatedAt: includeVerification ? "just now" : "waiting",
      status: includeVerification ? "verified" : "pending",
      content: includeVerification ? "49\n" : "No output captured yet.\n",
    },
  ];
}

function artifactsForInterruption(runId: string): ArtifactView[] {
  return artifacts(runId).map((artifact, index) =>
    index === 0 ? { ...artifact, status: "verified" as const, updatedAt: "just now" } : artifact,
  );
}

function conversation(
  runId: string,
  request: string,
  includeResume = false,
): ConversationMessageView[] {
  const messages: ConversationMessageView[] = [
    {
      messageId: `${runId}.request`,
      role: "user",
      content: request,
      at: "09:41:12",
    },
    {
      messageId: `${runId}.analysis`,
      role: "assistant",
      content:
        "已创建执行计划：先生成 spec，再写入 solution.py，最后运行并验证 stdout。当前工作区约束已锁定。",
      at: "09:41:30",
    },
  ];

  if (includeResume) {
    messages.push({
      messageId: `${runId}.resume`,
      role: "assistant",
      content:
        "已从 cp-123 恢复执行。跳过已完成的 Analysis，重新进入 Implementation 并完成 Verification。",
      at: "09:44:59",
    });
  }

  return messages;
}

function events(runId: string, includeVerification = false): RunEventView[] {
  const base: RunEventView[] = [
    {
      eventId: `${runId}.created`,
      type: "run.created",
      title: "Run created",
      description: "Request accepted and attached to session-11af.",
      at: "09:41:12",
      tone: "info",
    },
    {
      eventId: `${runId}.workflow`,
      type: "workflow.started",
      title: "Workflow started",
      description: "code-generation-v2 · 3 stages planned.",
      at: "09:41:14",
      tone: "info",
    },
    {
      eventId: `${runId}.analysis`,
      type: "stage.completed",
      title: "Analysis completed",
      description: "Spec and workspace constraints committed.",
      at: "09:41:30",
      tone: "success",
    },
    {
      eventId: `${runId}.checkpoint`,
      type: "checkpoint.created",
      title: "Checkpoint persisted",
      description: "cp-123 · active stage: Implementation.",
      at: "09:44:24",
      tone: "warning",
    },
    {
      eventId: `${runId}.interrupted`,
      type: "stage.interrupted",
      title: "Implementation interrupted",
      description: "Execution boundary reached before stdout capture.",
      at: "09:44:26",
      tone: "warning",
    },
  ];

  if (includeVerification) {
    base.push(
      {
        eventId: `${runId}.resumed`,
        type: "run.resumed",
        title: "Resume exact accepted",
        description: "cp-123 · resumed without replaying Analysis.",
        at: "09:44:59",
        tone: "info",
      },
      {
        eventId: `${runId}.verified`,
        type: "run.verified",
        title: "Run verified",
        description: "exit_code 0 · stdout matched expected value 49.",
        at: "09:45:34",
        tone: "success",
      },
    );
  }

  return base;
}

export function createMockRuns(): RunView[] {
  const blockedRequest = "生成一个 Python 程序，计算 7 的平方并保存";
  const completedRequest = "读取 architecture.md，整理 v2.2C 的恢复边界";
  const activeRequest = "为 SessionContext 增加一组隔离性回归测试";
  const failedRequest = "扫描 output/，生成一份 artifact 索引";

  return [
    {
      runId: "run-2048",
      status: "blocked",
      request: blockedRequest,
      activeWorkflowId: "code-generation-v2",
      createdAt: "今天 09:41",
      updatedAt: "2 分钟前",
      duration: "03:14",
      sessionId: "session-11af",
      workflows: [workflow("blocked", 62, stagesForInterruptedRun())],
      artifacts: artifacts("run-2048"),
      events: events("run-2048"),
      conversation: conversation("run-2048", blockedRequest),
      resume: {
        checkpointId: "cp-123",
        action: "RESUME_EXACT",
        reason: "Implementation interruption is resumable and no external state drift was detected.",
        sourceStage: "Implementation",
        outcome: "ready",
      },
      verifier: {
        status: "waiting",
        checks: "0 / 3 checks",
        stdout: "—",
        detail: "Resume the checkpoint to continue deterministic verification.",
      },
    },
    {
      runId: "run-2047",
      status: "completed",
      request: completedRequest,
      activeWorkflowId: "research-summary-v1",
      createdAt: "今天 09:18",
      updatedAt: "28 分钟前",
      duration: "01:42",
      sessionId: "session-11af",
      workflows: [
        workflow(
          "completed",
          100,
          completedStages().map((stage) => ({
            ...stage,
            name: stage.stageId === "analysis" ? "Research" : stage.name,
          })),
          "research-summary-v1",
        ),
      ],
      artifacts: [
        {
          artifactId: "run-2047.summary",
          path: "output/architecture-summary.md",
          kind: "text",
          size: "4.8 KB",
          updatedAt: "28 min ago",
          status: "verified",
          content:
            "# v2.2C Resume Boundary\n\nRun-level coordination restores the active workflow while keeping stage facts in the checkpoint chain.\n",
        },
      ],
      events: [
        {
          eventId: "run-2047.created",
          type: "run.completed",
          title: "Run completed",
          description: "Summary artifact generated and verified.",
          at: "09:19:42",
          tone: "success",
        },
        {
          eventId: "run-2047.artifact",
          type: "artifact.created",
          title: "Artifact created",
          description: "output/architecture-summary.md · 4.8 KB.",
          at: "09:19:40",
          tone: "info",
        },
      ],
      conversation: conversation("run-2047", completedRequest, true),
      verifier: {
        status: "verified",
        checks: "3 / 3 checks",
        stdout: "summary matched",
        detail: "All deterministic content and artifact checks passed.",
      },
    },
    {
      runId: "run-2044",
      status: "active",
      request: activeRequest,
      activeWorkflowId: "test-generation-v1",
      createdAt: "今天 08:56",
      updatedAt: "6 分钟前",
      duration: "04:08",
      sessionId: "session-0b27",
      workflows: [
        workflow(
          "active",
          38,
          stagesForInterruptedRun().map((stage) => ({
            ...stage,
            status: (stage.stageId === "analysis" ? "completed" : stage.stageId === "implementation" ? "running" : "queued") as StageStatus,
            tasks: stage.tasks.map((task, index) => ({
              ...task,
              status: (stage.stageId === "implementation" && index === 0 ? "running" : stage.stageId === "analysis" ? "completed" : "queued") as StageStatus,
            })),
          })),
          "test-generation-v1",
        ),
      ],
      artifacts: [
        {
          artifactId: "run-2044.plan",
          path: "tests/test_context_isolation.py",
          kind: "python",
          size: "1.2 KB",
          updatedAt: "6 min ago",
          // A prior stage has already committed this artifact. Cancelling the
          // active Run must preserve and expose it as verified progress.
          status: "verified",
          content: "def test_session_context_does_not_leak_run_state():\n    assert context_a.run_id != context_b.run_id\n",
        },
      ],
      events: [
        {
          eventId: "run-2044.started",
          type: "stage.running",
          title: "Implementation running",
          description: "Generating isolated Runtime Context fixtures.",
          at: "08:59:03",
          tone: "info",
        },
        {
          eventId: "run-2044.analysis",
          type: "stage.completed",
          title: "Analysis completed",
          description: "Test boundaries and ownership rules committed.",
          at: "08:58:11",
          tone: "success",
        },
      ],
      conversation: conversation("run-2044", activeRequest),
      verifier: {
        status: "waiting",
        checks: "running",
        stdout: "—",
        detail: "Verifier will start after implementation completes.",
      },
    },
    {
      runId: "run-2039",
      status: "timed_out",
      request: "分析当前工作区中的测试结果并生成摘要",
      activeWorkflowId: "analysis-summary-v1",
      createdAt: "今天 07:42",
      updatedAt: "今天 07:45",
      duration: "03:00",
      sessionId: "session-03dd",
      workflows: [workflow("timed_out", 48, stagesForInterruptedRun(), "analysis-summary-v1")],
      artifacts: artifactsForInterruption("run-2039"),
      events: [
        ...events("run-2039"),
        {
          eventId: "run-2039.timed-out",
          type: "run.timed_out",
          title: "Run timed out",
          description: "The watchdog stopped the Run before verification; committed artifacts remain available.",
          at: "07:45:12",
          tone: "error",
        },
      ],
      conversation: conversation("run-2039", "分析当前工作区中的测试结果并生成摘要"),
      verifier: {
        status: "failed",
        checks: "blocked",
        stdout: "—",
        detail: "The Run timed out before deterministic verification completed.",
      },
    },
    {
      runId: "run-2041",
      status: "failed",
      request: failedRequest,
      activeWorkflowId: "artifact-index-v1",
      createdAt: "昨天 17:22",
      updatedAt: "昨天",
      duration: "00:51",
      sessionId: "session-08c1",
      workflows: [
        workflow(
          "failed",
          44,
          stagesForInterruptedRun().map((stage) => ({
            ...stage,
            status: (stage.stageId === "analysis" ? "completed" : stage.stageId === "implementation" ? "failed" : "queued") as StageStatus,
          })),
          "artifact-index-v1",
        ),
      ],
      artifacts: [],
      events: [
        {
          eventId: "run-2041.failed",
          type: "run.failed",
          title: "Run failed",
          description: "Workspace permission denied while indexing output/.",
          at: "17:23:04",
          tone: "error",
        },
      ],
      conversation: conversation("run-2041", failedRequest),
      verifier: {
        status: "failed",
        checks: "blocked",
        stdout: "—",
        detail: "Verification did not start because the artifact index was not created.",
      },
    },
  ];
}

export function createNewRun(request: string, sequence: number): RunView {
  const runId = `run-${sequence}`;
  const workflowId = "code-generation-v2";
  const stages = stagesForInterruptedRun().map((stage, index) => ({
    ...stage,
    status: (index === 0 ? "running" : "queued") as StageStatus,
    duration: index === 0 ? "00:00" : "—",
    tasks: stage.tasks.map((task, taskIndex) => ({
      ...task,
      status: (index === 0 && taskIndex === 0 ? "running" : "queued") as StageStatus,
    })),
  }));

  return {
    runId,
    status: "active",
    request,
    activeWorkflowId: workflowId,
    createdAt: "刚刚",
    updatedAt: "刚刚",
    duration: "00:00",
    sessionId: `session-${sequence.toString(16).padStart(4, "0")}`,
    workflows: [workflow("active", 8, stages)],
    artifacts: [],
    events: [
      {
        eventId: `${runId}.created`,
        type: "run.created",
        title: "Run created",
        description: "Mock request accepted and attached to a new RunContext.",
        at: "just now",
        tone: "info",
      },
      {
        eventId: `${runId}.started`,
        type: "workflow.started",
        title: "Analysis started",
        description: "code-generation-v2 · extracting a structured spec.",
        at: "just now",
        tone: "info",
      },
    ],
    conversation: [
      {
        messageId: `${runId}.request`,
        role: "user",
        content: request,
        at: "just now",
      },
      {
        messageId: `${runId}.ack`,
        role: "assistant",
        content: "Run 已创建。Mock Runtime 正在执行 Analysis，完成后会把结构化事件写入时间线。",
        at: "just now",
      },
    ],
    verifier: {
      status: "waiting",
      checks: "queued",
      stdout: "—",
      detail: "Verification will appear after the workflow produces an artifact.",
    },
  };
}
