# LH001 计算服务

配置解析服务：`parser.py` 解析 `key=value` 配置，`main.py` 提供入口。

## 目录结构

- `parser.py`：配置解析模块，负责解析 `key=value` 格式的配置。
- `main.py`：程序入口，接收配置文件路径并启动解析流程。

## 使用

```bash
python main.py config.txt
```

其中 `config.txt` 为配置文件。

## 配置格式

配置文件每行一个配置项，采用 `key=value` 格式，以 `=` 分隔键名与键值。