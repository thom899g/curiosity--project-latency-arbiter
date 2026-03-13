"""
Satellite Network - Distributed data acquisition layer
Geographically positioned to capture latency differentials
"""

import asyncio
import time
import logging
from typing import Dict, List, Optional, Tuple
import ccxt.async_support as ccxt
import aiohttp
from web3 import Web3
from firebase_admin import firestore
import firebase_admin
from firebase_admin import credentials
import json
from datetime import datetime
import psutil

from config import CONFIG

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("satellite")

# Initialize Firebase
try:
    cred = credentials.Certificate('firebase_config.json')
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    logger.info("Firebase initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize Firebase: {e}")
    raise

class CEXDataCollector:
    """Collects real-time data from centralized exchanges"""
    
    def __init__(self, exchange_name: str):
        self.exchange_name = exchange_name
        self.exchange = None
        self.last_prices: Dict[str, Tuple[float, float]] = {}
        self.latency_stats: Dict[str, List[float]] = {}
        
    async def initialize(self):
        """Initialize exchange connection"""
        try:
            exchange_class = getattr(ccxt, self.exchange_name)
            self.exchange = exchange_class({
                'enableRateLimit': True,
                'timeout': 5000,
            })
            logger.info(f"Initialized {self.exchange_name} collector")
        except Exception as e:
            logger.error(f"Failed to initialize {self.exchange_name}: {e}")
            raise
    
    async def fetch_ticker_with_latency(self, symbol: str = "ETH/USDC") -> Optional[Dict]:
        """Fetch ticker data and measure round-trip latency"""
        if not self.exchange:
            await self.initialize()
        
        start_time = time.time() * 1000  # milliseconds
        
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            
            end_time = time.time() * 1000
            latency_ms = end_time - start_time
            
            data = {
                "bid": float(ticker['bid']),
                "ask": float(ticker['ask']),
                "last": float(ticker['last']),
                "volume": float(ticker['baseVolume']),
                "timestamp": ticker['timestamp'],
                "latency_ms": latency_ms,
                "exchange": self.exchange_name,
                "symbol": symbol,
                "region": CONFIG.satellite.region
            }
            
            # Update latency stats
            if symbol not in self.latency_stats:
                self.latency_stats[symbol] = []
            self.latency_stats[symbol].append(latency_ms)
            if len(self.latency_stats[symbol]) > 100:
                self.latency_stats[symbol].pop(0)
            
            logger.debug(f"{self.exchange_name} {symbol}: bid={data['bid']}, latency={latency_ms:.1f}ms")
            return data
            
        except ccxt.NetworkError as e:
            logger.warning(f"Network error for {self.exchange_name}: {e}")
            return None
        except ccxt.ExchangeError as e:
            logger.error(f"Exchange error for {self.exchange_name}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in {self.exchange_name}: {e}")
            return None
    
    def get_latency_percentile(self, symbol: str, percentile: int = 90) -> float:
        """Calculate latency percentile for a symbol"""
        if symbol not in self.latency_stats or not self.latency_stats[symbol]:
            return float('inf')
        
        sorted_latencies = sorted(self.latency_stats[symbol])
        idx = int(len(sorted_latencies) * percentile / 100)
        return sorted_latencies[idx]
    
    async def close(self):
        """Cleanup exchange connection"""
        if self.exchange:
            await self.exchange.close()

class DEXDataCollector:
    """Collects real-time data from decentralized exchanges"""
    
    def __init__(self, chain: str = "ethereum"):
        self.chain = chain
        self.web3 = None
        self.pools: Dict[str, Dict] = {}
        
        # Common Uniswap V3 pools (ETH/USDC)
        self.pool_configs = {
            "ethereum": {
                "ETH/USDC": "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640",  # 0.05% fee
            },
            "base": {
                "ETH/USDC": "0x4C36388bE6F416A29C8d8Eee81C771cE6bE14B18",  # 0.05% fee
            }
        }
    
    async def initialize(self):
        """Initialize Web3 connection with multiple RPC endpoints"""
        rpc_endpoints = {
            "ethereum": [
                os.getenv("ETH_RPC_1", "https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY"),
                os.getenv("ETH_RPC_2", "https://mainnet.infura.io/v3/YOUR_KEY"),
            ],
            "base": [
                os.getenv("BASE_RPC", "https://mainnet.base.org"),
            ]
        }
        
        for endpoint in rpc_endpoints.get(self.chain, []):
            try:
                self.web3 = Web3(Web3.HTTPProvider(endpoint, request_kwargs={'timeout': 2}))
                if self.web3.is_connected():
                    logger.info(f"Connected to {self.chain} via {endpoint[:30]}...")
                    break
            except Exception as e:
                logger.warning(f"Failed to connect to {endpoint[:30]}: {e}")
                continue
        
        if not self.web3 or not self.web3.is_connected():
            raise ConnectionError(f"Could not connect to any {self.chain} RPC")
    
    async def fetch_pool_state(self, pool_address: str) -> Optional[Dict]:
        """Fetch current pool state from blockchain"""
        if not self.web3:
            await self.initialize()
        
        start_time = time.time() * 1000
        
        try:
            # Minimal ABI for slot0 (sqrtPriceX96, tick, observationIndex, observationCardinality, feeProtocol)
            slot0_abi = '[{"constant":true,"inputs":[],"name":"slot0","outputs":[{"internalType":"uint160","name":"sqrtPriceX96","type":"uint160"},{"internalType":"int24","name":"tick","type":"int24"},{"internalType":"uint16","name":"observationIndex","type":"uint16"},{"internalType":"uint16","name":"observationCardinality","type":"uint16"},{"internalType":"uint16","name":"observationCardinalityNext","type":"uint16"},{"internalType":"uint8","name":"feeProtocol","type":"uint8"},{"internalType":"bool","name":"unlocked","type":"bool"}],"type":"function"}]'
            
            pool_contract = self.web3.eth.contract(
                address=self.web3.to_checksum_address(pool_address),
                abi=slot0_abi
            )
            
            slot0 = pool_contract.functions.slot0().call()
            sqrt_price_x96 = slot0[0]
            
            # Convert sqrtPriceX96 to actual price
            price = (sqrt_price_x96 ** 2) / (2 ** 192)
            
            # Get block timestamp for latency calculation
            block = self.web3.eth.get_block('latest')
            
            end_time = time.time() * 1000
            latency_ms = end_time - start_time
            
            data = {
                "price": float(price),
                "tick": int(slot0[1]),
                "block_number": block['number'],
                "timestamp": block['timestamp'],
                "latency_ms": latency_ms,
                "chain": self.chain,
                "pool_address": pool_address,
                "region": CONFIG.satellite.region
            }
            
            logger.debug(f"{self.chain} pool: price={price:.2f}, latency={latency_ms:.1f}ms")
            return data
            
        except Exception as e:
            logger.error(f"Failed to fetch pool state on {self.chain}: {e}")
            return None

class SatelliteNode:
    """Main satellite node coordinating CEX and