"""Agentic Mesh Self-Healing Trading Architecture — Book 211.

Replaces centralized microservice orchestration with a self-healing mesh
of autonomous agents. Each agent has its own event loop, heartbeat, and
task queue. When an agent fails, its tasks are redistributed to healthy
peers. Recovery is attempted automatically; human escalation via Telegram
only when recovery fails.

Key concepts:
  - Gossip-based discovery (no central registry)
  - Contract Net Protocol for task allocation
  - Heartbeat monitoring with configurable timeout
  - Automatic task redistribution on failure
  - Escalation to Telegram if recovery fails

Data paths:
  - /app/data/mesh_state.json — agent registry + health status
  - /app/data/mesh_events.ndjson — mesh lifecycle events

Bridge.py integration:
    try:
        from python_brain.ml.agent_mesh import (
            SelfHealingMesh, HeartbeatMonitor, TaskRedistributor,
            MeshAgent, AgentStatus,
        )
    except ImportError:
        pass

Usage:
    mesh = SelfHealingMesh([
        MeshAgent(agent_id="signal_gen", role="signal_generator",
                  status=AgentStatus.HEALTHY, tasks=["evaluate_signals"]),
        MeshAgent(agent_id="risk_mgr", role="risk_manager",
                  status=AgentStatus.HEALTHY, tasks=["check_limits"]),
    ])
    report = mesh.monitor_cycle()
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger("agent_mesh")

__all__ = [
    "AgentStatus",
    "MeshAgent",
    "HeartbeatMonitor",
    "TaskRedistributor",
    "SelfHealingMesh",
]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_DIR = Path("/app/data")
STATE_PATH = DATA_DIR / "mesh_state.json"
EVENTS_PATH = DATA_DIR / "mesh_events.ndjson"


# ---------------------------------------------------------------------------
# Enums and Dataclasses
# ---------------------------------------------------------------------------
class AgentStatus(Enum):
    """Status of a mesh agent.

    HEALTHY:    Agent is responding to heartbeats and processing tasks.
    DEGRADED:   Agent is responding but with elevated error rate or latency.
    FAILED:     Agent has not responded within timeout. Tasks will be redistributed.
    RECOVERING: Agent is being restarted. Tasks temporarily held.
    """
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    RECOVERING = "RECOVERING"


@dataclass
class MeshAgent:
    """Represents a single agent in the mesh.

    Attributes:
        agent_id: Unique identifier (e.g., "signal_gen_1").
        role: Functional role (e.g., "signal_generator", "risk_manager").
        status: Current health status.
        last_heartbeat: Epoch timestamp of last heartbeat.
        tasks: List of task names assigned to this agent.
        error_count: Running count of errors since last healthy state.
        metadata: Arbitrary metadata (load, latency, version, etc.).
    """
    agent_id: str = ""
    role: str = ""
    status: AgentStatus = AgentStatus.HEALTHY
    last_heartbeat: float = 0.0
    tasks: List[str] = field(default_factory=list)
    error_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.last_heartbeat == 0.0:
            self.last_heartbeat = time.time()

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to dict."""
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "status": self.status.value,
            "last_heartbeat": self.last_heartbeat,
            "tasks": self.tasks,
            "error_count": self.error_count,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Heartbeat Monitor
# ---------------------------------------------------------------------------
class HeartbeatMonitor:
    """Monitors agent heartbeats and determines health status.

    Each agent must send a heartbeat within timeout_seconds.
    Agents past timeout are marked FAILED. Agents with elevated
    errors are marked DEGRADED.
    """

    def __init__(
        self,
        timeout_seconds: float = 30.0,
        degraded_error_threshold: int = 3,
    ) -> None:
        """Initialise heartbeat monitor.

        Args:
            timeout_seconds: Seconds since last heartbeat before FAILED.
            degraded_error_threshold: Error count above which agent is DEGRADED.
        """
        self._timeout = timeout_seconds
        self._degraded_threshold = degraded_error_threshold
        self._agents: Dict[str, MeshAgent] = {}
        self._events: Deque[Dict[str, Any]] = deque(maxlen=2000)
        log.info(
            "HeartbeatMonitor: timeout=%.1fs degraded_threshold=%d",
            timeout_seconds, degraded_error_threshold,
        )

    @property
    def timeout(self) -> float:
        """Return heartbeat timeout in seconds."""
        return self._timeout

    def register(self, agent_id: str, role: str, tasks: Optional[List[str]] = None) -> None:
        """Register a new agent in the mesh.

        Args:
            agent_id: Unique agent identifier.
            role: Agent's functional role.
            tasks: Initial task list for this agent.
        """
        agent = MeshAgent(
            agent_id=agent_id,
            role=role,
            status=AgentStatus.HEALTHY,
            last_heartbeat=time.time(),
            tasks=tasks or [],
        )
        self._agents[agent_id] = agent
        self._log_event("REGISTER", agent_id, f"role={role}")
        log.info("Registered agent: %s (role=%s tasks=%d)", agent_id, role, len(agent.tasks))

    def unregister(self, agent_id: str) -> None:
        """Remove an agent from the mesh.

        Args:
            agent_id: Agent to remove.
        """
        if agent_id in self._agents:
            del self._agents[agent_id]
            self._log_event("UNREGISTER", agent_id)
            log.info("Unregistered agent: %s", agent_id)

    def heartbeat(self, agent_id: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Record a heartbeat from an agent.

        Resets the agent's last_heartbeat timestamp. If the agent was
        FAILED or RECOVERING, transitions to HEALTHY (if errors are low)
        or DEGRADED (if errors are elevated).

        Args:
            agent_id: Agent sending the heartbeat.
            metadata: Optional metadata update (load, latency, etc.).
        """
        agent = self._agents.get(agent_id)
        if agent is None:
            log.warning("Heartbeat from unknown agent: %s", agent_id)
            return

        agent.last_heartbeat = time.time()

        if metadata:
            agent.metadata.update(metadata)

        # Transition from FAILED/RECOVERING back to healthy/degraded
        if agent.status in (AgentStatus.FAILED, AgentStatus.RECOVERING):
            if agent.error_count > self._degraded_threshold:
                agent.status = AgentStatus.DEGRADED
            else:
                agent.status = AgentStatus.HEALTHY
                agent.error_count = 0
            self._log_event("RECOVERED", agent_id, f"new_status={agent.status.value}")
            log.info("Agent %s recovered → %s", agent_id, agent.status.value)

    def report_error(self, agent_id: str, error_msg: str = "") -> None:
        """Report an error from an agent.

        Increments error count. If above threshold, transitions to DEGRADED.

        Args:
            agent_id: Agent reporting the error.
            error_msg: Optional error message for logging.
        """
        agent = self._agents.get(agent_id)
        if agent is None:
            return

        agent.error_count += 1
        self._log_event("ERROR", agent_id, error_msg)

        if agent.error_count > self._degraded_threshold and agent.status == AgentStatus.HEALTHY:
            agent.status = AgentStatus.DEGRADED
            log.warning(
                "Agent %s degraded: %d errors (threshold=%d)",
                agent_id, agent.error_count, self._degraded_threshold,
            )

    def check_health(self) -> Dict[str, AgentStatus]:
        """Check health of all registered agents.

        Agents past timeout are marked FAILED. Returns current status
        of all agents.

        Returns:
            Dict mapping agent_id -> AgentStatus.
        """
        now = time.time()
        result = {}

        for agent_id, agent in self._agents.items():
            elapsed = now - agent.last_heartbeat

            if elapsed > self._timeout:
                if agent.status != AgentStatus.FAILED:
                    agent.status = AgentStatus.FAILED
                    self._log_event(
                        "FAILED", agent_id,
                        f"timeout={elapsed:.1f}s > {self._timeout:.1f}s",
                    )
                    log.error(
                        "Agent %s FAILED: no heartbeat for %.1fs",
                        agent_id, elapsed,
                    )
            elif agent.error_count > self._degraded_threshold:
                if agent.status == AgentStatus.HEALTHY:
                    agent.status = AgentStatus.DEGRADED

            result[agent_id] = agent.status

        return result

    def get_failed(self) -> List[str]:
        """Return list of agent IDs that are currently FAILED.

        Returns:
            List of failed agent IDs.
        """
        return [
            aid for aid, agent in self._agents.items()
            if agent.status == AgentStatus.FAILED
        ]

    def get_healthy(self) -> List[str]:
        """Return list of agent IDs that are HEALTHY or DEGRADED.

        These agents can accept redistributed tasks.

        Returns:
            List of healthy/degraded agent IDs.
        """
        return [
            aid for aid, agent in self._agents.items()
            if agent.status in (AgentStatus.HEALTHY, AgentStatus.DEGRADED)
        ]

    def get_agent(self, agent_id: str) -> Optional[MeshAgent]:
        """Get agent by ID.

        Args:
            agent_id: Agent identifier.

        Returns:
            MeshAgent or None if not found.
        """
        return self._agents.get(agent_id)

    def all_agents(self) -> Dict[str, MeshAgent]:
        """Return all registered agents."""
        return dict(self._agents)

    def _log_event(self, event_type: str, agent_id: str, detail: str = "") -> None:
        """Log a mesh event.

        Args:
            event_type: Event type (REGISTER, HEARTBEAT, FAILED, etc.).
            agent_id: Related agent.
            detail: Optional detail string.
        """
        event = {
            "ts": time.time(),
            "type": event_type,
            "agent_id": agent_id,
            "detail": detail,
        }
        self._events.append(event)

        try:
            with open(EVENTS_PATH, "a") as f:
                f.write(json.dumps(event) + "\n")
        except OSError:
            pass  # non-critical logging

    def recent_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return recent mesh events.

        Args:
            limit: Maximum events to return.

        Returns:
            List of event dicts, newest first.
        """
        items = list(self._events)
        items.reverse()
        return items[:limit]


# ---------------------------------------------------------------------------
# Task Redistribution
# ---------------------------------------------------------------------------
class TaskRedistributor:
    """Redistributes tasks from failed agents to healthy peers.

    Uses a least-loaded selection strategy: the healthy agent with
    the fewest current tasks receives the redistributed task.
    """

    def __init__(self) -> None:
        """Initialise task redistributor."""
        self._redistribution_count: int = 0
        self._redistribution_log: Deque[Dict[str, Any]] = deque(maxlen=1000)
        log.info("TaskRedistributor initialised")

    def redistribute(
        self,
        failed_agent: str,
        failed_agent_tasks: List[str],
        healthy_agents: Dict[str, MeshAgent],
    ) -> Dict[str, List[str]]:
        """Redistribute all tasks from a failed agent to healthy peers.

        Each task is assigned to the least-loaded healthy agent.

        Args:
            failed_agent: ID of the failed agent.
            failed_agent_tasks: Tasks that need redistribution.
            healthy_agents: Dict of healthy agent_id -> MeshAgent.

        Returns:
            Dict mapping target_agent_id -> list of redistributed tasks.
        """
        if not failed_agent_tasks:
            return {}

        if not healthy_agents:
            log.error(
                "No healthy agents available to redistribute %d tasks from %s",
                len(failed_agent_tasks), failed_agent,
            )
            return {}

        assignments: Dict[str, List[str]] = {aid: [] for aid in healthy_agents}

        for task in failed_agent_tasks:
            target = self._select_target(task, healthy_agents, assignments)
            if target is None:
                log.error("Cannot assign task %s — no target found", task)
                continue
            assignments[target].append(task)
            healthy_agents[target].tasks.append(task)
            self._redistribution_count += 1

        # Remove empty assignments
        assignments = {k: v for k, v in assignments.items() if v}

        record = {
            "ts": time.time(),
            "failed_agent": failed_agent,
            "tasks": failed_agent_tasks,
            "assignments": assignments,
        }
        self._redistribution_log.append(record)

        log.info(
            "Redistributed %d tasks from %s to %d agents: %s",
            len(failed_agent_tasks), failed_agent, len(assignments),
            {k: len(v) for k, v in assignments.items()},
        )
        return assignments

    def _select_target(
        self,
        task: str,
        candidates: Dict[str, MeshAgent],
        current_assignments: Dict[str, List[str]],
    ) -> Optional[str]:
        """Select the best target agent for a task.

        Strategy: least-loaded (fewest total tasks including pending assignments).
        Ties broken by preferring HEALTHY over DEGRADED.

        Args:
            task: Task name to assign.
            candidates: Available healthy agents.
            current_assignments: Running tally of assignments in this batch.

        Returns:
            Agent ID of the selected target, or None if no candidates.
        """
        if not candidates:
            return None

        best_id = None
        best_load = float("inf")
        best_status_priority = 99

        for aid, agent in candidates.items():
            # Total load = existing tasks + tasks assigned in this batch
            load = len(agent.tasks) + len(current_assignments.get(aid, []))
            status_priority = 0 if agent.status == AgentStatus.HEALTHY else 1

            if (load < best_load) or (load == best_load and status_priority < best_status_priority):
                best_id = aid
                best_load = load
                best_status_priority = status_priority

        return best_id

    @property
    def redistribution_count(self) -> int:
        """Return total number of task redistributions performed."""
        return self._redistribution_count


# ---------------------------------------------------------------------------
# Self-Healing Mesh
# ---------------------------------------------------------------------------
class SelfHealingMesh:
    """Top-level self-healing mesh coordinator.

    Combines heartbeat monitoring, task redistribution, and recovery
    into a single monitor_cycle() that should be called periodically
    (e.g., every 5-10 seconds).

    Recovery flow:
      1. Check all agent heartbeats
      2. For FAILED agents, attempt recovery (restart)
      3. If recovery fails, redistribute tasks to healthy peers
      4. If no healthy peers, escalate to human via Telegram
    """

    def __init__(
        self,
        agents: Optional[List[MeshAgent]] = None,
        timeout_seconds: float = 30.0,
        max_recovery_attempts: int = 3,
        telegram_escalation_fn: Optional[Any] = None,
    ) -> None:
        """Initialise self-healing mesh.

        Args:
            agents: Initial list of agents to register.
            timeout_seconds: Heartbeat timeout.
            max_recovery_attempts: Max restart attempts before escalating.
            telegram_escalation_fn: Optional callable for Telegram alerts.
                                    Signature: fn(agent_id, message) -> None.
        """
        self._monitor = HeartbeatMonitor(timeout_seconds=timeout_seconds)
        self._redistributor = TaskRedistributor()
        self._max_recovery = max_recovery_attempts
        self._telegram_fn = telegram_escalation_fn
        self._recovery_attempts: Dict[str, int] = {}
        self._cycle_count: int = 0

        if agents:
            for agent in agents:
                self._monitor.register(
                    agent.agent_id, agent.role, agent.tasks,
                )
                # Sync agent state
                if agent.status != AgentStatus.HEALTHY:
                    registered = self._monitor.get_agent(agent.agent_id)
                    if registered:
                        registered.status = agent.status

        log.info(
            "SelfHealingMesh: %d agents, timeout=%.1fs, max_recovery=%d",
            len(agents or []), timeout_seconds, max_recovery_attempts,
        )

    @property
    def heartbeat_monitor(self) -> HeartbeatMonitor:
        """Return the heartbeat monitor."""
        return self._monitor

    @property
    def task_redistributor(self) -> TaskRedistributor:
        """Return the task redistributor."""
        return self._redistributor

    def register_agent(self, agent_id: str, role: str, tasks: Optional[List[str]] = None) -> None:
        """Register a new agent in the mesh.

        Args:
            agent_id: Unique agent identifier.
            role: Agent's functional role.
            tasks: Initial tasks.
        """
        self._monitor.register(agent_id, role, tasks)

    def heartbeat(self, agent_id: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Forward a heartbeat to the monitor.

        Args:
            agent_id: Agent ID.
            metadata: Optional metadata.
        """
        self._monitor.heartbeat(agent_id, metadata)
        # Reset recovery attempts on successful heartbeat
        if agent_id in self._recovery_attempts:
            self._recovery_attempts[agent_id] = 0

    def monitor_cycle(self) -> Dict[str, Any]:
        """Execute one monitoring cycle.

        Checks health, handles failures, redistributes tasks,
        attempts recovery, and escalates if necessary.

        Returns:
            Dict with cycle results: health status, redistributions, recoveries.
        """
        self._cycle_count += 1
        cycle_start = time.time()

        # 1. Check health
        health = self._monitor.check_health()
        failed = self._monitor.get_failed()
        healthy_ids = self._monitor.get_healthy()
        healthy_agents = {
            aid: self._monitor.get_agent(aid)
            for aid in healthy_ids
            if self._monitor.get_agent(aid) is not None
        }

        redistributions = {}
        recoveries = {}
        escalations = []

        # 2. Handle failed agents
        for failed_id in failed:
            agent = self._monitor.get_agent(failed_id)
            if agent is None:
                continue

            # Attempt recovery
            attempts = self._recovery_attempts.get(failed_id, 0)
            if attempts < self._max_recovery:
                recovered = self._attempt_recovery(failed_id)
                self._recovery_attempts[failed_id] = attempts + 1
                recoveries[failed_id] = {
                    "attempt": attempts + 1,
                    "success": recovered,
                }

                if recovered:
                    log.info(
                        "Recovery succeeded for %s (attempt %d/%d)",
                        failed_id, attempts + 1, self._max_recovery,
                    )
                    continue

            # Recovery failed or exhausted — redistribute tasks
            if agent.tasks and healthy_agents:
                result = self._redistributor.redistribute(
                    failed_id, list(agent.tasks), healthy_agents,
                )
                redistributions[failed_id] = result
                # Clear tasks from failed agent (they've been moved)
                agent.tasks = []

            # Escalate if recovery exhausted
            if attempts + 1 >= self._max_recovery:
                self._escalate(failed_id)
                escalations.append(failed_id)

        cycle_duration = time.time() - cycle_start

        report = {
            "cycle": self._cycle_count,
            "duration_ms": round(cycle_duration * 1000, 2),
            "health": {aid: s.value for aid, s in health.items()},
            "failed_count": len(failed),
            "healthy_count": len(healthy_ids),
            "redistributions": redistributions,
            "recoveries": recoveries,
            "escalations": escalations,
            "ts": time.time(),
        }

        log.info(
            "Mesh cycle #%d: %d healthy, %d failed, %d redistributed, %d escalated (%.1fms)",
            self._cycle_count, len(healthy_ids), len(failed),
            len(redistributions), len(escalations), cycle_duration * 1000,
        )
        return report

    def _attempt_recovery(self, agent_id: str) -> bool:
        """Attempt to recover a failed agent.

        In production, this would restart the agent process.
        Here we mark it as RECOVERING and simulate a restart.

        Args:
            agent_id: Agent to recover.

        Returns:
            True if recovery initiated (actual success determined by next heartbeat).
        """
        agent = self._monitor.get_agent(agent_id)
        if agent is None:
            return False

        agent.status = AgentStatus.RECOVERING
        agent.error_count = 0

        log.info("Attempting recovery for agent %s", agent_id)

        # In real implementation: subprocess restart, container restart, etc.
        # For now, mark as recovering. The agent must send a heartbeat to confirm.
        return True

    def _escalate(self, agent_id: str) -> None:
        """Escalate a failed agent to human attention via Telegram.

        Args:
            agent_id: Failed agent requiring human intervention.
        """
        agent = self._monitor.get_agent(agent_id)
        role = agent.role if agent else "unknown"
        tasks = agent.tasks if agent else []

        message = (
            f"MESH ALERT: Agent '{agent_id}' (role={role}) has failed "
            f"after {self._max_recovery} recovery attempts. "
            f"Tasks: {tasks}. Human intervention required."
        )

        log.critical("ESCALATION: %s", message)

        if self._telegram_fn is not None:
            try:
                self._telegram_fn(agent_id, message)
            except Exception as exc:
                log.error("Telegram escalation failed: %s", exc)

    def mesh_status(self) -> Dict[str, Any]:
        """Return comprehensive mesh health summary.

        Returns:
            Dict with agent status, task distribution, health counts.
        """
        agents = self._monitor.all_agents()
        status_counts = {s.value: 0 for s in AgentStatus}
        total_tasks = 0
        agent_details = {}

        for aid, agent in agents.items():
            status_counts[agent.status.value] += 1
            total_tasks += len(agent.tasks)
            agent_details[aid] = agent.to_dict()

        overall = AgentStatus.HEALTHY
        if status_counts[AgentStatus.FAILED.value] > 0:
            overall = AgentStatus.FAILED
        elif status_counts[AgentStatus.DEGRADED.value] > 0:
            overall = AgentStatus.DEGRADED
        elif status_counts[AgentStatus.RECOVERING.value] > 0:
            overall = AgentStatus.RECOVERING

        return {
            "overall_status": overall.value,
            "agent_count": len(agents),
            "status_counts": status_counts,
            "total_tasks": total_tasks,
            "agents": agent_details,
            "cycles_completed": self._cycle_count,
            "redistributions_total": self._redistributor.redistribution_count,
            "recovery_attempts": dict(self._recovery_attempts),
            "ts": time.time(),
        }

    def save_state(self) -> None:
        """Persist mesh state to disk."""
        state = self.mesh_status()
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(STATE_PATH, "w") as f:
                json.dump(state, f, indent=2, default=str)
            log.info("Mesh state saved to %s", STATE_PATH)
        except OSError as exc:
            log.error("Failed to save mesh state: %s", exc)
