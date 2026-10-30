ci/job.txt 里只写一个 make 目标（不带 test- 之外的参数），runner 每次读到新 commit 就执行它。

可选值：
test-csv / test-pi / test-pdf / test-rerank / test-incremental
test-count-trigger / test-embedding / test-lifecycle / test-evidence / test-hybrid
test（全部，慢）

结果落在 ci/log 分支：ci/results/latest.md 与 ci/results/<sha8>-<target>.md
