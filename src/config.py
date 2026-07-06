"""PAKT network and runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class NetworkConfig:
    id: str = "testnet-12"
    wrpc_url: str = "wss://testnet-12.kaspa.org"
    wrpc_port: int = 443
    daa_score_poll_interval: float = 0.5
    tx_confirmation_timeout: float = 60.0
    default_fee_sompi: int = 10_000

    @property
    def wrpc_endpoint(self) -> str:
        return f"{self.wrpc_url}:{self.wrpc_port}"


@dataclass
class CovenantConfig:
    timeout_daa_delta: int = 100
    arb_timeout_daa_delta: int = 50
    max_lock_amount_sompi: int = 1_000_000_000_000
    min_lock_amount_sompi: int = 1_000_000


@dataclass
class AgentConfig:
    model: str = "gpt-4o"
    temperature: float = 0.7
    max_negotiation_rounds: int = 5
    response_timeout_s: float = 30.0


@dataclass
class PactConfig:
    max_active_pacts: int = 10
    default_arb_share_pct: float = 0.5
    dispute_wait_blocks: int = 200


@dataclass
class DemoConfig:
    terminal_theme: str = "dark"
    show_agent_logs: bool = True
    show_raw_tx: bool = False
    auto_advance: bool = True
    step_delay_s: float = 1.5


@dataclass
class AppConfig:
    network: NetworkConfig = field(default_factory=NetworkConfig)
    covenant: CovenantConfig = field(default_factory=CovenantConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    pact: PactConfig = field(default_factory=PactConfig)
    demo: DemoConfig = field(default_factory=DemoConfig)
    data_dir: str = field(default_factory=lambda: os.path.expanduser("~/.pakt"))

    @classmethod
    def default(cls) -> AppConfig:
        return cls()

    @classmethod
    def testnet(cls) -> AppConfig:
        cfg = cls.default()
        cfg.network = NetworkConfig(
            id="testnet-12",
            wrpc_url="wss://testnet-12.kaspa.org",
        )
        cfg.covenant.timeout_daa_delta = 100
        return cfg

    @classmethod
    def mainnet(cls) -> AppConfig:
        cfg = cls.default()
        cfg.network = NetworkConfig(
            id="mainnet",
            wrpc_url="wss://wrpc.kaspa.org",
        )
        cfg.covenant.timeout_daa_delta = 1000
        return cfg


CONFIG = AppConfig.default()
