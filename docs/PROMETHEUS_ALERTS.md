# Prometheus Alert Rules — Agent Harness

Suggested alerting rules for the metrics exposed at `/metrics` (rendered
by `agent_framework/prometheus_exporter.py`). Drop these into your
Prometheus rules file — they're not auto-applied; they're documentation.

## Critical (page on-call)

```yaml
groups:
  - name: mediahub-agent-critical
    interval: 30s
    rules:

      # /health/deep returning unhealthy across multiple scrapes
      - alert: MediaHubBackendUnhealthy
        expr: up{job="mediahub-backend"} == 0
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "MediaHub backend down for 2m"
          runbook: "ssh nas + check `sudo docker logs mediahub-app-backend`"

      # MCP transport errors spiking
      - alert: MCPTransportErrorBurst
        expr: rate(agent_mcp_tool_call_transport_error[5m]) > 0.1
        for: 5m
        labels:
          severity: high
        annotations:
          summary: "MCP server transport errors > 6/min for 5m"
          runbook: "Check user_mcp_servers table for misconfigured URLs;
                    correlate spike with a specific server.bearer_token rotation."

      # Dispatch gate refusing many workflows (worker missing?)
      - alert: DispatchGateBlocking
        expr: rate(agent_dispatch_gate_blocked[5m]) > 0.05
        for: 5m
        labels:
          severity: high
        annotations:
          summary: "DBOS dispatch refused > 3/min — workers may be down"
          runbook: "Check /api/v1/health/deep, look at bounds.live_count.
                    If 0, restart worker container."
```

## Warning (notification, not page)

```yaml
  - name: mediahub-agent-warnings
    interval: 60s
    rules:

      # Tool-call loops detected — agent stuck in repeat
      - alert: ToolCallLoopBurst
        expr: increase(agent_loop_guard_tripped[1h]) > 5
        for: 30m
        labels:
          severity: warning
        annotations:
          summary: "Loop guard tripped > 5x in 1h — flaky tool/skill?"

      # Streaming aborted mid-stream a lot — UX issue
      - alert: StreamingAbortRate
        expr: |
          rate(agent_streaming_aborted_mid[15m])
            / clamp_min(rate(agent_streaming_started[15m]), 0.001) > 0.20
        for: 15m
        labels:
          severity: warning
        annotations:
          summary: "20%+ of streaming chats aborted — users cancelling?"

      # Compactor running on every turn — context too big
      - alert: CompactorOverActive
        expr: |
          rate(agent_compaction_triggered[10m])
            / clamp_min(rate(agent_streaming_started[10m]) +
                        rate(agent_session_memory_update_attempted[10m]),
                        0.001) > 0.5
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Compactor firing on > 50% of turns — bump per-message cap?"

      # Memory archive sweeper unusually quiet — sweeper crashed?
      - alert: MemoryArchivedFlatline
        expr: increase(agent_memory_archived[24h]) == 0
        for: 24h
        labels:
          severity: warning
        annotations:
          summary: "0 memories archived in 24h — decay sweeper may have stopped"

      # Commitment sweeper firing 0x — webhook delivery dead?
      - alert: CommitmentSweeperSilence
        expr: |
          increase(agent_commitment_harvest_persisted[6h]) > 5
          and increase(agent_commitment_sweeper_fired[6h]) == 0
        for: 6h
        labels:
          severity: warning
        annotations:
          summary: "Commitments persisted but never fired — sweeper or trigger_at logic broken"
```

## SLO probes

These are not alerts — they're suggested SLOs for dashboards.

| Metric | Target | Budget |
|---|---|---|
| chat turn p95 latency | < 8s | 5% |
| `agent_mcp_tool_call_error` rate | < 1% of `agent_mcp_tool_call` | 5% |
| `streaming_aborted_mid` rate | < 5% of `streaming_started` | 5% |
| compactor invocation rate | < 30% of turns | 10% |

## Dashboard panels

Group the counters in /metrics into 5 dashboards:

1. **Chat Throughput** — `streaming_started`, `streaming_aborted_mid`,
   `chat_mcp_registry_built`
2. **Tool Surface** — `mcp_tool_call*`, `tool_cache_hit`, `loop_guard_*`
3. **Memory Lifecycle** — `memory_*`, `commitment_*`, `session_memory_*`
4. **Cost Discipline** — `output_budget_tightened`, `compaction_*`
5. **Boundary** — `dispatch_gate_*`, `hook_aborted_run`,
   `link_injection_*`

Each dashboard should also embed the corresponding `/health/deep`
section as a stat panel so on-call sees in-process state alongside
historical trend.
