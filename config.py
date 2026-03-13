"""
Configuration manager for Project Latency Arbiter
Centralizes all system parameters with validation
"""

import os
from dataclasses import dataclass
from typing import Dict, List, Optional
from pydantic import BaseModel, Field, validator
import json
import logging

logger = logging.getLogger(__name__)

class SatelliteConfig(BaseModel):
    """Configuration for satellite data acquisition nodes"""
    region: str
    vps_provider: str = "aws"
    cex_exchanges: List[str] = ["coinbase", "kraken", "binance"]
    dex_chains: List[str] = ["ethereum", "arbitrum", "base"]
    update_interval_ms: int = 100
    max_retries: int = 3
    retry_delay_seconds: float = 1.0
    
    @validator('region')
    def validate_region(cls, v):
        valid_regions = ["us-east-1", "eu-west-1", "ap-northeast-1"]
        if v not in valid_regions:
            raise ValueError(f"Region must be one of {valid_regions}")
        return v

class CoreConfig(BaseModel):
    """Configuration for core decision engine"""
    arbitration_threshold_ms: int = 200
    min_profit_bps: int = 5  # 0.05%
    max_daily_loss_bps: int = 10  # 0.1%
    max_position_size_usd: float = 100.0
    enable_ml_models: bool = False
    parallel_models: int = 3
    circuit_breaker_enabled: bool = True
    
    @validator('min_profit_bps')
    def validate_min_profit(cls, v):
        if v < 1:
            raise ValueError("Minimum profit too low - consider transaction costs")
        return v

class ExecutionConfig(BaseModel):
    """Configuration for execution layer"""
    dex_slippage_tolerance_bps: int = 20  # 0.2%
    max_gas_price_gwei: int = 50
    flashbots_enabled: bool = True
    private_rpc_endpoints: bool = True
    api_key_rotation_hours: int = 24
    human_mimicry_delay_ms: Dict[str, tuple] = {"min": 10, "max": 50}
    
    @validator('max_gas_price_gwei')
    def validate_gas_price(cls, v):
        if v > 200:
            logger.warning("Gas price very high - may impact profitability")
        return v

class ObservabilityConfig(BaseModel):
    """Configuration for monitoring and alerts"""
    telegram_enabled: bool = True
    health_check_interval_seconds: int = 30
    performance_tracing: bool = True
    profit_sweep_threshold_eth: float = 0.0005
    hardware_wallet_address: str = "0xYOUR_HARDWARE_WALLET"
    
    @validator('hardware_wallet_address')
    def validate_address(cls, v):
        if not v.startswith("0x") or len(v) != 42:
            raise ValueError("Invalid Ethereum address format")
        return v

@dataclass
class SystemConfig:
    """Main configuration container"""
    satellite: SatelliteConfig
    core: CoreConfig
    execution: ExecutionConfig
    observability: ObservabilityConfig
    firebase_project_id: str = "latency-arbiter"
    environment: str = "production"
    
    @classmethod
    def from_env(cls):
        """Load configuration from environment variables with defaults"""
        env = os.getenv("ENVIRONMENT", "production")
        
        satellite = SatelliteConfig(
            region=os.getenv("SATELLITE_REGION", "us-east-1"),
            cex_exchanges=json.loads(os.getenv("CEX_EXCHANGES", '["coinbase", "kraken"]')),
            dex_chains=json.loads(os.getenv("DEX_CHAINS", '["ethereum", "base"]'))
        )
        
        core = CoreConfig(
            arbitration_threshold_ms=int(os.getenv("ARB_THRESHOLD_MS", "200")),
            min_profit_bps=int(os.getenv("MIN_PROFIT_BPS", "5")),
            max_daily_loss_bps=int(os.getenv("MAX_LOSS_BPS", "10"))
        )
        
        execution = ExecutionConfig(
            dex_slippage_tolerance_bps=int(os.getenv("SLIPPAGE_BPS", "20")),
            max_gas_price_gwei=int(os.getenv("MAX_GAS_GWEI", "50")),
            flashbots_enabled=os.getenv("FLASHBOTS_ENABLED", "true").lower() == "true"
        )
        
        observability = ObservabilityConfig(
            telegram_enabled=os.getenv("TELEGRAM_ENABLED", "true").lower() == "true",
            hardware_wallet_address=os.getenv("HARDWARE_WALLET", "0xYOUR_HARDWARE_WALLET")
        )
        
        return cls(
            satellite=satellite,
            core=core,
            execution=execution,
            observability=observability,
            environment=env,
            firebase_project_id=os.getenv("FIREBASE_PROJECT_ID", "latency-arbiter")
        )
    
    def validate(self) -> bool:
        """Validate entire configuration"""
        try:
            self.satellite.validate(self.satellite.dict())
            self.core.validate(self.core.dict())
            self.execution.validate(self.execution.dict())
            self.observability.validate(self.observability.dict())
            logger.info("Configuration validated successfully")
            return True
        except Exception as e:
            logger.error(f"Configuration validation failed: {e}")
            return False

# Global configuration instance
CONFIG = SystemConfig.from_env()