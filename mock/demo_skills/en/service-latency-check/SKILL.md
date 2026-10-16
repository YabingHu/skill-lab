---
name: service-latency-check
description: Find the cause of increased service API latency
---

# Service Latency Check

## Step 1 Query trace metrics

Call the monitoring API:

```
curl -s http://monitor/api/latency?service=<service>&window=30m
```

## Step 2 Check dependencies

```
curl -s http://monitor/api/deps?service=<service>
```

## Step 3 Read slow logs

```
kubectl logs deploy/<service> -n prod --since=30m | grep slow
```

## Step 4 Conclude

List the slowest APIs, suspicious dependencies, and recommended actions.
