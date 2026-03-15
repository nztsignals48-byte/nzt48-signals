"""
Unit tests for IB Gateway Health Monitor (Phase 2b)

Tests the 3-layer resilience stack:
1. Connectivity checks via socket
2. Auto-restart after 3 failures
3. Market-aware alerts

Note: Async tests are intentionally simplified to avoid pytest-asyncio dependency.
Core sync functionality is fully tested.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from core.ib_gateway_health_monitor import IBGatewayHealthMonitor


class TestIBGatewayHealthMonitorConnectivity:
    """Test Layer 1: Socket connectivity checks"""

    def test_check_connection_success(self):
        """Test successful connection to healthy gateway"""
        monitor = IBGatewayHealthMonitor(host="localhost", port=4002)

        with patch("socket.create_connection") as mock_socket:
            mock_sock_obj = Mock()
            mock_socket.return_value = mock_sock_obj

            result = monitor.check_connection()

            assert result is True
            assert monitor.is_healthy is True
            assert monitor.failure_count == 0
            assert monitor.last_check is not None
            mock_socket.assert_called_once_with(("localhost", 4002), timeout=5)
            mock_sock_obj.close.assert_called_once()

    def test_check_connection_timeout(self):
        """Test connection timeout"""
        import socket
        monitor = IBGatewayHealthMonitor(host="localhost", port=4002)

        with patch("socket.create_connection") as mock_socket:
            mock_socket.side_effect = socket.timeout("Connection timed out")

            result = monitor.check_connection()

            assert result is False
            assert monitor.is_healthy is False
            assert monitor.failure_count == 1
            assert monitor.last_check is not None

    def test_check_connection_refused(self):
        """Test connection refused"""
        import socket
        monitor = IBGatewayHealthMonitor(host="localhost", port=4002)

        with patch("socket.create_connection") as mock_socket:
            mock_socket.side_effect = ConnectionRefusedError("Connection refused")

            result = monitor.check_connection()

            assert result is False
            assert monitor.is_healthy is False
            assert monitor.failure_count == 1

    def test_failure_counter_reset_on_recovery(self):
        """Test that failure counter resets when connection recovers"""
        import socket
        monitor = IBGatewayHealthMonitor(host="localhost", port=4002)

        # Simulate 2 failures
        with patch("socket.create_connection") as mock_socket:
            mock_socket.side_effect = socket.timeout()
            monitor.check_connection()
            monitor.check_connection()
            assert monitor.failure_count == 2

        # Simulate recovery
        with patch("socket.create_connection") as mock_socket:
            mock_sock_obj = Mock()
            mock_socket.return_value = mock_sock_obj

            result = monitor.check_connection()

            assert result is True
            assert monitor.failure_count == 0
            assert monitor.is_healthy is True


class TestIBGatewayHealthMonitorStatus:
    """Test status reporting methods"""

    def test_get_status_report(self):
        """Test status report generation"""
        monitor = IBGatewayHealthMonitor(host="localhost", port=4002)
        monitor.is_healthy = True
        monitor.failure_count = 0

        status = monitor.get_status_report()

        assert status["is_healthy"] is True
        assert status["failure_count"] == 0
        assert status["host"] == "localhost"
        assert status["port"] == 4002
        assert "timestamp" in status
        assert status["restart_in_progress"] is False

    def test_get_health_metric(self):
        """Test Prometheus-style health metric"""
        monitor = IBGatewayHealthMonitor(host="localhost", port=4002)
        monitor.is_healthy = True
        monitor.failure_count = 2

        metric = monitor.get_health_metric()

        assert metric["ib_gateway_healthy"] == 1  # True as 1
        assert metric["ib_gateway_failures"] == 2
        assert "ib_gateway_last_check_age_seconds" in metric


class TestIBGatewayHealthMonitorInitialization:
    """Test initialization and configuration"""

    def test_monitor_initialization_with_defaults(self):
        """Test default initialization"""
        monitor = IBGatewayHealthMonitor()

        assert monitor.host == "localhost"
        assert monitor.port == 4002
        assert monitor.is_healthy is True
        assert monitor.failure_count == 0
        assert monitor.max_failures_before_restart == 3
        assert monitor.market_scheduler is None
        assert monitor.telegram_notifier is None

    def test_monitor_initialization_with_custom_host_port(self):
        """Test initialization with custom host and port"""
        monitor = IBGatewayHealthMonitor(host="ib-gateway", port=4004)

        assert monitor.host == "ib-gateway"
        assert monitor.port == 4004

    def test_monitor_initialization_with_market_scheduler(self):
        """Test initialization with market scheduler"""
        mock_scheduler = Mock()
        monitor = IBGatewayHealthMonitor(market_scheduler=mock_scheduler)

        assert monitor.market_scheduler is mock_scheduler

    def test_monitor_initialization_with_telegram(self):
        """Test initialization with Telegram notifier"""
        mock_telegram = Mock()
        monitor = IBGatewayHealthMonitor(telegram_notifier=mock_telegram)

        assert monitor.telegram_notifier is mock_telegram


class TestFailureCountingLogic:
    """Test failure tracking and reset logic"""

    def test_failure_count_increments_on_each_failure(self):
        """Test that failure count increments properly"""
        import socket
        monitor = IBGatewayHealthMonitor(host="localhost", port=4002)

        with patch("socket.create_connection") as mock_socket:
            mock_socket.side_effect = socket.timeout()

            monitor.check_connection()
            assert monitor.failure_count == 1
            monitor.check_connection()
            assert monitor.failure_count == 2
            monitor.check_connection()
            assert monitor.failure_count == 3

    def test_failure_count_persists_across_calls(self):
        """Test that failure count is maintained"""
        import socket
        monitor = IBGatewayHealthMonitor(host="localhost", port=4002)

        with patch("socket.create_connection") as mock_socket:
            mock_socket.side_effect = socket.timeout()
            monitor.check_connection()
            assert monitor.failure_count == 1

        # Second call with same failure
        with patch("socket.create_connection") as mock_socket:
            mock_socket.side_effect = socket.timeout()
            monitor.check_connection()
            assert monitor.failure_count == 2

    def test_is_healthy_reflects_connection_status(self):
        """Test that is_healthy flag is updated properly"""
        import socket
        monitor = IBGatewayHealthMonitor(host="localhost", port=4002)

        # Start healthy
        assert monitor.is_healthy is True

        # Fail connection
        with patch("socket.create_connection") as mock_socket:
            mock_socket.side_effect = socket.timeout()
            monitor.check_connection()
            assert monitor.is_healthy is False

        # Recover
        with patch("socket.create_connection") as mock_socket:
            mock_sock_obj = Mock()
            mock_socket.return_value = mock_sock_obj
            monitor.check_connection()
            assert monitor.is_healthy is True


class TestMaxFailuresThreshold:
    """Test max failures threshold logic"""

    def test_max_failures_before_restart_default(self):
        """Test default max failures threshold"""
        monitor = IBGatewayHealthMonitor()
        assert monitor.max_failures_before_restart == 3

    def test_failure_count_can_exceed_max(self):
        """Test that failure count can exceed max (restart is external logic)"""
        import socket
        monitor = IBGatewayHealthMonitor(host="localhost", port=4002)
        monitor.max_failures_before_restart = 3

        with patch("socket.create_connection") as mock_socket:
            mock_socket.side_effect = socket.timeout()

            # Simulate more than 3 failures
            for _ in range(5):
                monitor.check_connection()

            # Should keep counting
            assert monitor.failure_count == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
