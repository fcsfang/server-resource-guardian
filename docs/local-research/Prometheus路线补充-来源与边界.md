# Prometheus路线补充：来源与设计边界

本次按已获同意的三页结构独立交付：职责分工、内存危机流程、与Beszel路线的同题比较。用户自行插入总稿，页脚使用补充1/3至3/3，不改总稿页序。公司已有Beszel或Prometheus均未被核实；能力说明来自官方文档，组合属于待验证建议。

## 官方来源

- [Prometheus概览](https://prometheus.io/docs/introduction/overview/)：拉取数值指标、时间序列存储、标签、PromQL、组件与自主运行边界。上一轮已通过Anysearch全文核对。
- [HTTP API](https://prometheus.io/docs/prometheus/latest/querying/api/)：即时与区间表达式查询；查询执行时间不等于原始样本采集时间，本机复核与数据年龄检查不能省略。
- [记录规则](https://prometheus.io/docs/prometheus/latest/configuration/recording_rules/)：定期计算并保存新的时间序列。它减少重复计算的能力不构成本项目的性能实测结论。
- [告警规则](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/)：根据表达式与持续条件产生告警，不是业务安全执行器。
- [Alertmanager](https://prometheus.io/docs/alerting/latest/alertmanager/)：分组、去重、路由、静默和抑制通知；不能代替动作互斥或冷却。
- [node_exporter](https://github.com/prometheus/node_exporter)：主机采集与pressure。此前官方核验。
- [cAdvisor指标](https://github.com/google/cadvisor/blob/master/docs/storage/prometheus.md)：容器用量、CPU限流。此前官方核验，目标Docker29/cgroup v2仍需测试。
- [Beszel API](https://beszel.dev/guide/rest-api)与[指标说明](https://www.beszel.dev/guide/what-is-beszel)：基础历史与程序访问均可复用；不把它描述为只能看图。

## 方案决定与验证

第一版先验证本机程序主动查询平台结果，告警通知联动按需加入。本机保留紧急检测、公司授权和身份复核、动作限制、资源和业务恢复检查。平台不可用时走已验证的本机判断或告警退出路径，不依据过期数据执行。

Grafana与Alertmanager按需加入，不是所有组件都必须部署。集中Prometheus是本例部署选择，不是产品唯一部署方式。平台持续采集可减少程序自行维护部分历史与计算，但不预判总开发量、时效、开销或可靠性优劣。统一对照试点应检查指标完整性、数据年龄、容器重建、权限、中心断连、Docker失效以及动作后的业务结果。
