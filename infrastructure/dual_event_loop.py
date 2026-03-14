"""
Phase Q4: Dual Event Loop Orchestrator
Separates slow I/O (data pipeline) from fast execution (trading logic)
Maintains <10ms execution latency SLA via independent async loops
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional, Dict, List
from dataclasses import dataclass, field

logger = logging.getLogger("nzt48.dual_event_loop")

@dataclass
class PerformanceMetrics:
    """Track latency and throughput metrics"""
    data_latencies: List[float] = field(default_factory=list)
    exec_latencies: List[float] = field(default_factory=list)
    data_throughput: int = 0
    exec_throughput: int = 0
    
    def record_data_latency(self, ms: float):
        self.data_latencies.append(ms)
        if len(self.data_latencies) > 1000:
            self.data_latencies.pop(0)
    
    def record_exec_latency(self, ms: float):
        self.exec_latencies.append(ms)
        if len(self.exec_latencies) > 10000:
            self.exec_latencies.pop(0)
    
    def get_summary(self) -> Dict:
        """Return performance summary"""
        if not self.data_latencies:
            data_avg = data_max = 0
        else:
            data_avg = sum(self.data_latencies) / len(self.data_latencies)
            data_max = max(self.data_latencies)
        
        if not self.exec_latencies:
            exec_avg = exec_max = exec_p99 = 0
        else:
            exec_avg = sum(self.exec_latencies) / len(self.exec_latencies)
            exec_max = max(self.exec_latencies)
            sorted_exec = sorted(self.exec_latencies)
            exec_p99 = sorted_exec[int(len(sorted_exec) * 0.99)]
        
        return {
            "data_pipeline_avg_ms": round(data_avg, 2),
            "data_pipeline_max_ms": round(data_max, 2),
            "exec_avg_ms": round(exec_avg, 3),
            "exec_max_ms": round(exec_max, 3),
            "exec_p99_ms": round(exec_p99, 3),
            "exec_sla_ok": exec_avg < 10.0,
        }

class DualEventLoopOrchestrator:
    """
    Orchestrates two independent event loops:
    1. Data Loop: Handles slow I/O (API calls, DB queries) at 0.5-1s cadence
    2. Execution Loop: Handles fast trading logic at 10-100ms cadence
    
    This separation prevents slow I/O from blocking execution decisions.
    """
    
    def __init__(self, data_workers: int = 4, exec_workers: int = 1):
        self.data_loop = asyncio.new_event_loop()
        self.exec_loop = asyncio.new_event_loop()
        
        self.data_executor = ThreadPoolExecutor(
            max_workers=data_workers, 
            thread_name_prefix="nzt48_data_"
        )
        self.exec_executor = ThreadPoolExecutor(
            max_workers=exec_workers,
            thread_name_prefix="nzt48_exec_"
        )
        
        self.metrics = PerformanceMetrics()
        self.shared_market_data = {}
        self.shared_signals = {}
        self.is_running = False
        
        logger.info(f"DualEventLoopOrchestrator initialized: "
                   f"data_workers={data_workers}, exec_workers={exec_workers}")
    
    async def run_data_pipeline(
        self,
        scan_func: Callable,
        signal_func: Callable,
        interval: float = 0.5
    ):
        """
        Run data acquisition and signal generation.
        Safe to be slow; runs in isolated loop.
        
        Args:
            scan_func: Callable that fetches market data (blocks OK)
            signal_func: Callable that computes signals from market data
            interval: Minimum time between pipeline runs (seconds)
        """
        logger.info(f"Data pipeline starting (interval={interval}s)")
        pipeline_errors = 0
        
        while self.is_running:
            try:
                start_time = time.time()
                
                # Run market scan in executor (blocks OK here)
                market_data = await self.data_loop.run_in_executor(
                    self.data_executor,
                    scan_func
                )
                self.shared_market_data = market_data or {}
                
                # Generate signals
                signals = await self.data_loop.run_in_executor(
                    self.data_executor,
                    signal_func,
                    market_data
                )
                self.shared_signals = signals or {}
                
                elapsed_ms = (time.time() - start_time) * 1000
                self.metrics.record_data_latency(elapsed_ms)
                self.metrics.data_throughput += 1
                
                if elapsed_ms > 1000:
                    logger.warning(f"Data pipeline slow: {elapsed_ms:.0f}ms")
                else:
                    logger.debug(f"Data pipeline: {elapsed_ms:.0f}ms")
                
                # Sleep remaining interval
                sleep_time = max(0, interval - (time.time() - start_time))
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                
                pipeline_errors = 0
                
            except Exception as e:
                pipeline_errors += 1
                logger.error(f"Data pipeline error (#{pipeline_errors}): {e}")
                if pipeline_errors >= 5:
                    logger.critical("Data pipeline failed 5x, halting")
                    self.is_running = False
                await asyncio.sleep(interval)
    
    async def run_execution_pipeline(
        self,
        gate_func: Callable,
        order_func: Callable,
        exit_func: Callable,
        interval: float = 0.010
    ):
        """
        Run trading logic: gate check → order placement → exit management.
        Must maintain <10ms latency SLA.
        
        Args:
            gate_func: Callable that checks risk gates (returns bool)
            order_func: Callable that places new orders
            exit_func: Callable that manages exits
            interval: Target execution interval (seconds, 10ms = 100 Hz)
        """
        logger.info(f"Execution pipeline starting (target={interval*1000:.1f}ms)")
        exec_errors = 0
        sla_violations = 0
        
        while self.is_running:
            try:
                start_time = time.time()
                
                # 1. Gate check (fast: <2ms)
                gate_start = time.time()
                passed = await self.exec_loop.run_in_executor(
                    self.exec_executor,
                    gate_func
                )
                gate_elapsed = (time.time() - gate_start) * 1000
                
                # 2. Order placement (if gates pass)
                if passed:
                    order_start = time.time()
                    await self.exec_loop.run_in_executor(
                        self.exec_executor,
                        order_func
                    )
                    order_elapsed = (time.time() - order_start) * 1000
                else:
                    order_elapsed = 0
                
                # 3. Exit management (fast: <3ms)
                exit_start = time.time()
                await self.exec_loop.run_in_executor(
                    self.exec_executor,
                    exit_func
                )
                exit_elapsed = (time.time() - exit_start) * 1000
                
                total_elapsed_ms = (time.time() - start_time) * 1000
                self.metrics.record_exec_latency(total_elapsed_ms)
                self.metrics.exec_throughput += 1
                
                if total_elapsed_ms > interval * 1000:
                    sla_violations += 1
                    logger.warning(
                        f"Execution SLA violation: {total_elapsed_ms:.1f}ms "
                        f"(gate={gate_elapsed:.1f}ms, order={order_elapsed:.1f}ms, exit={exit_elapsed:.1f}ms)"
                    )
                
                logger.debug(
                    f"Execution cycle: {total_elapsed_ms:.2f}ms "
                    f"[gate={gate_elapsed:.1f}ms, order={order_elapsed:.1f}ms, exit={exit_elapsed:.1f}ms]"
                )
                
                # Sleep remaining time to maintain interval
                sleep_time = max(0, interval - (time.time() - start_time))
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                
                exec_errors = 0
                
            except Exception as e:
                exec_errors += 1
                logger.error(f"Execution pipeline error (#{exec_errors}): {e}")
                if exec_errors >= 10:
                    logger.critical("Execution pipeline failed 10x, emergency halt")
                    self.is_running = False
                await asyncio.sleep(interval)
        
        logger.info(f"Execution pipeline stopped. SLA violations: {sla_violations}")
    
    def start(
        self,
        scan_func: Callable,
        signal_func: Callable,
        gate_func: Callable,
        order_func: Callable,
        exit_func: Callable,
        data_interval: float = 0.5,
        exec_interval: float = 0.010
    ):
        """
        Start both pipelines.
        
        Args:
            scan_func: Market data scanner
            signal_func: Signal generator
            gate_func: Risk gate checker
            order_func: Order placement logic
            exit_func: Exit management logic
            data_interval: Data pipeline interval (seconds)
            exec_interval: Execution pipeline interval (seconds)
        """
        self.is_running = True
        
        # Schedule both coroutines
        asyncio.set_event_loop(self.data_loop)
        data_task = self.data_loop.create_task(
            self.run_data_pipeline(scan_func, signal_func, data_interval)
        )
        
        asyncio.set_event_loop(self.exec_loop)
        exec_task = self.exec_loop.create_task(
            self.run_execution_pipeline(gate_func, order_func, exit_func, exec_interval)
        )
        
        logger.info("Both pipelines started")
        return data_task, exec_task
    
    def stop(self):
        """Stop both pipelines gracefully"""
        self.is_running = False
        logger.info("Orchestrator stop signal sent")
    
    def get_metrics(self) -> Dict:
        """Return performance metrics"""
        return self.metrics.get_summary()
    
    def get_shared_state(self) -> Dict:
        """Return current shared market data and signals"""
        return {
            "market_data": self.shared_market_data.copy(),
            "signals": self.shared_signals.copy(),
            "metrics": self.get_metrics()
        }


# Example usage and testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    
    # Mock functions for testing
    def mock_scan():
        time.sleep(0.1)  # Simulate slow API call
        return {"QQQ3.L": 100.5, "TSL3.L": 50.2}
    
    def mock_signal(data):
        return {"signal_strength": 0.75, "confidence": 85}
    
    def mock_gate():
        return True
    
    def mock_order():
        pass
    
    def mock_exit():
        pass
    
    print("✅ Q4: Dual Event Loop Orchestrator ready")
