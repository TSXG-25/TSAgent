import { test } from "vitest";
import assert from "node:assert/strict";
import type { ReactElement, ReactNode } from "react";

/** FE-02: 共享 Markdown 渲染器的离线回归 — token 高亮、HTML 不执行、
 *  工具卡片分类。 */

const { MarkdownText, highlightCode } = await import("../src/app/ui");
const { toolKind } = await import("../src/workspaces/sun/ThinkingChain");

/** flatten a React element tree into [tag/class/text] leaves */
function leaves(node: ReactNode): unknown[] {
  const out: unknown[] = [];
  const walk = (n: ReactNode) => {
    if (n == null || typeof n === "boolean") return;
    if (typeof n === "string" || typeof n === "number") { out.push(n); return; }
    if (Array.isArray(n)) { n.forEach(walk); return; }
    const el = n as ReactElement<{ children?: ReactNode; className?: string }>;
    const tag = typeof el.type === "function" ? `[${el.type.name}]` : `<${String(el.type)}${el.props.className ? `.${el.props.className}` : ""}>`;
    out.push(tag);
    walk(el.props.children);
  };
  walk(node);
  return out;
}

test("highlightCode classifies comments, strings, numbers, keywords", () => {
  const nodes = leaves(highlightCode("// c\nconst x = \"s\" + 42"));
  const joined = nodes.join("|");
  assert.match(joined, /<span\.md-tok-com>/);
  assert.match(joined, /<span\.md-tok-str>/);
  assert.match(joined, /<span\.md-tok-num>/);
  assert.match(joined, /<span\.md-tok-kw>/);
});

test("MarkdownText never turns model-output HTML into elements", () => {
  const tree = leaves(MarkdownText({ text: "<script>alert(1)</script> and <b>bold</b>" }));
  // raw markup survives as text — no script/b elements in the tree
  assert.ok(!tree.some((x) => typeof x === "string" && /^<(?:script|b)>$/.test(x)));
  assert.ok(tree.some((x) => typeof x === "string" && x.includes("<script>alert(1)</script>")));
});

test("MarkdownText renders fenced code as a copyable block", () => {
  const tree = leaves(MarkdownText({ text: "before\n```python\nprint(1)\n```\nafter" }));
  assert.ok(tree.some((x) => x === "[MdCodeBlock]"));
});

test("MarkdownText renders GFM tables", () => {
  const tree = leaves(MarkdownText({ text: "| a | b |\n|---|---|\n| 1 | 2 |" }));
  assert.ok(tree.some((x) => x === "<table.md-table>"));
});

test("toolKind maps tool names to file/search/command cards, else raw", () => {
  assert.equal(toolKind("write_file"), "file");
  assert.equal(toolKind("read_workspace_file"), "file");
  assert.equal(toolKind("web_search"), "search");
  assert.equal(toolKind("ripgrep"), "search");
  assert.equal(toolKind("run_command"), "command");
  assert.equal(toolKind("bash"), "command");
  assert.equal(toolKind("mystery_tool"), "other");
});
