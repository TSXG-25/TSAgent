---
name: coding
description: 编写、修改和调试代码文件；在需要修改已有代码或生成代码产物时加载
---

# Coding

- 修改已有文件前先 read_file 读取原文；小范围改动优先用 edit_file 精确替换，
  大范围重写再用 write_file 输出完整内容。
- edit_file 的 old_string 必须与文件中现有内容完全一致且在文件内唯一。
- 只有用户明确要求运行代码时才调用 run_python / shell。
- 保持未请求修改的导入、函数和代码区域原样不动。
