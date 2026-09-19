# EXP-011：本地可丢弃测试镜像准备

- 实验 ID：`EXP-011`
- 状态：`PASSED`
- 创建日期：2026-09-19
- 最近更新：2026-09-19
- 关联 Goal：`Goal 4 / G4-T05`
- 实验负责人：当前 Agent

## 1. 实验目的

在 Docker Hub 出网不稳定的情况下，为 `guardian-ubuntu` 准备一个不依赖外网的 ARM64 本地测试镜像，为后续获得明确授权后的可丢弃 `graceful_stop` 实验提供基础设施。

## 2. 授权与安全边界

- 环境：Mac Multipass `guardian-ubuntu`，Ubuntu 22.04.5 ARM64。
- 来源：虚拟机自带的静态链接 `/usr/bin/busybox`，未连接生产，也未拉取生产镜像。
- 允许动作：构造本地 Docker 镜像；不创建、启动或停止容器。
- 停止条件：不执行 `docker stop`、`docker restart`、`docker kill`；不接触生产 Docker socket。
- 清理方式：后续可删除本地镜像 `guardian-test-base:local`；当前不影响任何容器运行态。

## 3. 实验步骤

1. 尝试 `docker pull busybox:1.36`，设置 20 秒超时。
2. 验证超时后没有留下镜像或容器。
3. 将静态 BusyBox 复制到临时 rootfs，通过 `docker import` 创建 `guardian-test-base:local`。
4. 检查镜像 ID、大小和容器列表。

## 4. 结果与核心数据

- Docker Hub 拉取：20 秒超时，退出码 124。
- 本地镜像：`guardian-test-base:local`。
- 镜像 ID：`sha256:fefb056e381dbf251ddaf830472403da13cb0be0f33c29193e7c180029b43327`。
- 镜像大小：`1,005,785` bytes。
- 容器状态：创建前后均为 0 个；没有启动任何测试容器。
- 结论类型：观测结果 + 工程判断。

## 5. 结论

- 验收状态：通过（本地测试镜像准备完成）。
- 已验证：后续测试不必依赖 Docker Hub，可在本地构造 ARM64 BusyBox 测试对象。
- 尚未验证：容器启动、真实 `graceful_stop`、动作后恢复、失败升级。
- 下一步：获得明确的本地可丢弃对象授权后，创建并启动唯一测试容器，再执行一次受控 `graceful_stop` 闭环。

## 6. 更新记录

- 2026-09-19：Docker Hub 拉取超时；使用虚拟机自带静态 BusyBox 成功导入本地测试镜像，未创建或启动容器。
