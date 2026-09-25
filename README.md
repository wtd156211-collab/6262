# 请求录制与回放（从 0 实现）

仓库里只有这份说明、`samples/**` 与 `.gitignore`，没有实现代码。交付：`replaydeck/` 包（Python 3.13 标准库，库 + `python -m replaydeck` 入口）、样例生成的 `replay.html`、`tests/`（`unittest`）。

## 1. 范围

要做：把「请求 + 响应」配对写成录制文件；回放时按请求找响应；找不到就报错并给出最接近的候选与差异；把批量回放的结果写成自包含 HTML 报告。

不做：不发真实请求，不做代理与流量镜像；不做缓存落盘、重试与默认响应；不做并发与跨进程共享；不读系统时钟与随机数；不解析 URL 文本；不做通配符与模糊匹配；不比对响应；不用第三方依赖与构建步骤。

## 2. 口径与公式

### 2.1 请求对象

录制与回放共用同一种请求对象：

- `method`：非空字符串，比较前转大写（`get` 与 `GET` 等价）。
- `path`：区分大小写、逐码点比较，不做归一化（末尾斜杠、`.`/`..`、百分号编码都原样比较）。
- `query`：`[名, 值]` 二元组数组，不做解码（`+` 不当空格、`%2F` 不当 `/`），名与值区分大小写，缺值与空值都写 `""`。
- `headers`：小写头名到字符串值；只有 `accept` 与 `content-type` 参与匹配，其余头（如 `x-trace-id`、`user-agent`）忽略。
- `body`：字符串，空串表示没有请求体。

### 2.2 相等口径

- 方法：两侧转大写后相等；路径：逐码点相等。
- 查询：`(名, 值)` 的多重集合相等，顺序无关，重复名按值的重数比较。
- 头：值先归一化（取第一个 `;` 前、去首尾空白、转小写），故 `APPLICATION/JSON; charset=utf-8` 等于 `application/json`。记录里出现的允许清单头是约束，请求里必须存在同名头且归一化值相等；没出现的不约束。
- 体：模式由请求的 `content-type` 决定（归一化后含 `json` 走 JSON 模式，否则字节模式）。JSON 模式两侧解析后递归比较：对象键序无关、数组顺序敏感、数字按数值比（`2`=`2.0`=`2e0`）、布尔与数字不等、字符串区分大小写；任一侧解析失败就退化为字节模式。字节模式按 UTF-8 逐字节比。

### 2.3 命中与挑选

五个维度（方法、路径、查询、头、体）全等即命中。多条记录同时命中按序取第一条：① 记录里出现的允许清单头个数（0–2）多的优先；② 录制顺序更早的优先（文件名字典序 → 行号）。

## 3. 未命中的行为

匹配不上必须报错，不许返回响应或兜底值。错误里给出最接近的候选（最多 3 条），每条含 `id`、`score`（不一致的维度数 1–5）、`constraints`（该记录的允许清单头个数）与 `diff`（见 4.3）。候选排序键：① 路径相等优先；② `score` 小优先；③ `constraints` 大优先；④ 录制顺序更早优先。

## 4. 输入输出与文件格式

所有文件 UTF-8 无 BOM、LF 行尾、末行有换行；JSON 一律键按字典序、分隔符后不带空格、非 ASCII 不转义。

### 4.1 录制文件 `samples/recordings/part-NN.jsonl`

一行一条 `{"id":"rec-01-0001","request":{…},"response":{"body":"…","headers":{…},"status":200}}`。文件名 `part-01.jsonl` 起两位序号，字典序即读取顺序；`id` 由位置派生 `rec-<两位文件序号>-<四位行号>`，同一批输入录两遍要逐字节相同。`status` 是 100–599 的整数；响应的 `headers`、`body` 不参与匹配，只进报告。

### 4.2 回放请求 `samples/queries/<名>.jsonl`

一行一条 `{"id":"q-00001","request":{…}}`；`id` 按「文件名字典序 → 行号」从 1 起全局编号，5 位补 0。

### 4.3 期望结果 `samples/expected/<名>.jsonl`

与 `samples/queries/` 同名、等长、同序。命中：`{"hit":"rec-01-0001","query":"q-00001","status":200}`；未命中：`{"candidates":[…],"hit":null,"query":"q-00002"}`。`diff` 只放有差异的维度：

- `"method"`、`"path"`：`{"record":"POST","request":"GET"}` 形式，两侧原样值；
- `"query"`：`{"only_record":[["a","2"]],"only_request":[["a","1"]]}`，差集按 `(名, 值)` 排序，没有就 `[]`；
- `"headers"`：`{"content-type":{"record":"application/json","request":null}}`，只列记录里有、归一化后不等的允许清单头；`null` 表示请求里没有；
- `"body"`：`{"record":"…","request":"…"}`，两侧原始字符串。

### 4.4 命令行

在仓库根目录执行：

    python -m replaydeck record --in <录制文件或目录> --out <输出目录>
    python -m replaydeck replay --recordings samples/recordings --queries samples/queries [--out var/results.jsonl]
    python -m replaydeck report --recordings samples/recordings --queries samples/queries --html replay.html

`record` 读输入行（目录按文件名字典序），丢掉 `id` 后按固定分片大小（默认 1000）重新分片。`replay` 按文件名字典序读 `--queries` 下所有 `.jsonl`，stdout 每行一条 4.3 的结果（未命中也写一行），`--out` 写一份与 stdout 逐字节相同的文件。`report` 跑同一次回放并写 `replay.html`。退出码 0/1/2 分别是成功 / 输入不可用（缺文件、解析失败、`id` 重复、字段越界）/ 用法错误；非 0 不写输出。

### 4.5 报告 `replay.html`

自包含单文件：不引外部资源、不含 `<script>`、双击可开、无交互；命中与差异只能来自引擎结果。

1. `id="rows"` 的 `<table>` 每条回放一行 `<tr data-query data-hit data-status data-ms data-candidates>`：`data-hit` 是命中的录制 id（未命中写 `-`），`data-status` 未命中写 `-`，`data-candidates` 未命中时是候选 id 用 `;` 连接（命中写 `-`），`data-ms` 是这条回放的整数毫秒。行序与结果行一致；未命中的行整行标红，行内要能看到候选的 id、`score`、`constraints` 与差异。
2. `id="totals"` 的 `<table>` 每行 `<tr data-key data-count>`，`data-key` 覆盖 `hit` 与 `miss`，计数与结果行一致。

## 5. 性能与验收口径

单进程单线程、只用标准库。本仓库 3000 条录制、12000 条回放：`replay` 与 `report` 各 ≤ 20 s；评测规模（2 万条录制、20 万次回放）≤ 120 s；内存 ≤ 256 MB。

1. `replay` 的 stdout 与 `--out`，和按文件名字典序拼接的 `samples/expected/*.jsonl` 逐字节相同。
2. `record` 重写 `samples/recordings` 的输出逐字节相同；不许写时间戳、随机数、进程 id、绝对路径。
3. 未命中 `hit` 为 `null`、候选 ≤ 3 条，排序与 `diff` 与期望逐条相同；不许有兜底响应。
4. 报告与结果行一致、未命中行标红；耗时换成 `-` 后两次运行的 `replay.html` 逐字节相同。
5. `python -m unittest` 能跑通，测试只读 `samples/`。

## 6. 样例说明

查询与期望同名一一对应；`samples/notes.md` 是现场记录。

| 素材 | 条数 | 覆盖 |
| --- | --- | --- |
| `recordings/part-01.jsonl` | 1000 | 常规方法、路径、查询、头、体 |
| `recordings/part-02.jsonl` | 800 | 同路径不同查询：顺序、重复名、空值、编码、名大小写 |
| `recordings/part-03.jsonl` | 800 | 同路径不同体：JSON 与字节模式，含 10 条坏体 |
| `recordings/part-04.jsonl` | 400 | 只差无关头的 60 对、只差约束头个数的 40 对、200 条孤立条目 |
| `queries/q-01-hits.jsonl` | 2000 | 全命中 |
| `queries/q-02-query.jsonl` | 1500 | 查询：命中 1100 / 未命中 400 |
| `queries/q-03-headers.jsonl` | 1500 | 头：命中 940 / 未命中 560 |
| `queries/q-04-body.jsonl` | 2000 | 体：命中 1349 / 未命中 651 |
| `queries/q-05-miss.jsonl` | 1500 | 全未命中：值、体、方法、路径、孤立条目、约束头 |
| `queries/q-06-scale.jsonl` | 3500 | 混合：命中 2843 / 未命中 657 |

## 7. 待补的文档

- 评测侧大样例（2 万条录制 / 20 万次回放）的生成脚本不入库。
- 报告配色、文案与错误消息措辞自定，4.5 的元素与属性不能少；分片上限、跨进程共享留待后续。
