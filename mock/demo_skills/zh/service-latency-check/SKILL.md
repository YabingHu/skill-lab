---
name: service-latency-check
description: 定位服务接口延迟升高的原因
---

# 服务延迟排查

## 第一步 查询链路指标

调用监控接口：

```
curl -s http://monitor/api/latency?service=<service>&window=30m
```

## 第二步 检查依赖服务

```
curl -s http://monitor/api/deps?service=<service>
```

## 第三步 查看慢日志

```
kubectl logs deploy/<service> -n prod --since=30m | grep slow
```

## 第四步 输出结论

列出延迟最高的接口、可疑的依赖，以及建议的处理动作。
