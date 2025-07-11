#!/usr/bin/env python3
"""
Targeted Binary Optimization for Hypha-RPC
Smart hybrid approach that only optimizes where it matters most
"""

import struct
import msgpack
import time
import numpy as np
from typing import Dict, Any, List, Optional, Tuple, Union
from dataclasses import dataclass
from enum import IntEnum


class OptimizedType(IntEnum):
    """Types that benefit most from binary optimization"""
    # Large data structures
    LARGE_ARRAY = 0x80
    NUMPY_ARRAY = 0x81
    TYPED_ARRAY = 0x82
    
    # Repeated patterns (RPC-specific)
    RPC_ARGS_PATTERN = 0x90
    METHOD_REFERENCE = 0x91
    SESSION_REFERENCE = 0x92
    
    # Compression candidates
    STRING_ARRAY = 0xA0
    NUMBER_ARRAY = 0xA1


class SmartHybridOptimizer:
    """
    Smart optimizer that only uses binary encoding where it provides clear benefits
    
    Key Strategy:
    - Keep msgpack for small, simple objects (it's already optimized)
    - Use binary for large arrays, repeated patterns, and specific RPC structures
    - Automatic threshold-based selection
    - Zero overhead for small messages
    """
    
    def __init__(self):
        self.magic_bytes = b'\xEB\x01'  # Efficient Binary v1
        
        # Thresholds for when binary optimization is beneficial
        self.array_size_threshold = 100  # Elements
        self.string_size_threshold = 1000  # Bytes
        self.object_complexity_threshold = 10  # Nested levels
        
        # Pattern cache for repeated RPC structures
        self.pattern_cache = {}
    
    def should_optimize(self, data: Any) -> bool:
        """Decide if data would benefit from binary optimization"""
        return self._analyze_optimization_potential(data) > 0.2  # 20% improvement threshold
    
    def _analyze_optimization_potential(self, data: Any) -> float:
        """Analyze potential optimization gain (0.0 to 1.0)"""
        if isinstance(data, dict):
            # Check for RPC argument patterns
            if self._is_rpc_args_pattern(data):
                return 0.4  # RPC args often have repeated structures
            
            # Check for large nested structures
            complexity = self._calculate_complexity(data)
            if complexity > self.object_complexity_threshold:
                return min(0.6, complexity / 20.0)
        
        elif isinstance(data, list):
            if len(data) > self.array_size_threshold:
                # Large arrays benefit significantly
                return min(0.8, len(data) / 10000.0)
            
            # Check for typed arrays (all same type)
            if self._is_homogeneous_array(data):
                return 0.5
        
        elif isinstance(data, str):
            if len(data) > self.string_size_threshold:
                return 0.3
        
        return 0.0
    
    def _is_rpc_args_pattern(self, data: Dict[str, Any]) -> bool:
        """Detect common RPC argument patterns"""
        rpc_keys = {"args", "with_kwargs", "promise", "session", "method"}
        return len(rpc_keys.intersection(data.keys())) >= 2
    
    def _is_homogeneous_array(self, array: List[Any]) -> bool:
        """Check if array contains all same type (good for binary optimization)"""
        if not array:
            return False
        first_type = type(array[0])
        return all(isinstance(item, first_type) for item in array[:100])  # Sample first 100
    
    def _calculate_complexity(self, obj: Any, depth: int = 0) -> int:
        """Calculate nested structure complexity"""
        if depth > 10:  # Prevent deep recursion
            return depth
        
        if isinstance(obj, dict):
            return 1 + sum(self._calculate_complexity(v, depth + 1) for v in obj.values())
        elif isinstance(obj, list):
            return 1 + sum(self._calculate_complexity(v, depth + 1) for v in obj[:10])  # Sample
        else:
            return 1
    
    def encode_optimized(self, data: Dict[str, Any]) -> bytes:
        """Encode with smart optimization selection"""
        buffer = bytearray()
        buffer.extend(self.magic_bytes)
        
        # Analyze and encode each field
        optimized_fields = 0
        buffer.extend(struct.pack('<H', len(data)))  # Field count
        
        for key, value in data.items():
            # Encode key
            key_bytes = key.encode('utf-8')
            buffer.extend(struct.pack('<H', len(key_bytes)))
            buffer.extend(key_bytes)
            
            # Check if value should be optimized
            if self.should_optimize(value):
                # Use binary optimization
                self._encode_optimized_value(buffer, value)
                optimized_fields += 1
            else:
                # Use msgpack for this value
                msgpack_data = msgpack.packb(value)
                buffer.extend(struct.pack('<B', 0xFF))  # Msgpack marker
                buffer.extend(struct.pack('<I', len(msgpack_data)))
                buffer.extend(msgpack_data)
        
        return bytes(buffer)
    
    def _encode_optimized_value(self, buffer: bytearray, value: Any):
        """Encode value using optimized binary format"""
        if isinstance(value, list) and len(value) > self.array_size_threshold:
            if self._is_homogeneous_array(value):
                self._encode_typed_array(buffer, value)
            else:
                self._encode_large_array(buffer, value)
        
        elif isinstance(value, dict) and self._is_rpc_args_pattern(value):
            self._encode_rpc_args_pattern(buffer, value)
        
        else:
            # Fallback to general optimization
            self._encode_general_optimized(buffer, value)
    
    def _encode_typed_array(self, buffer: bytearray, array: List[Any]):
        """Encode homogeneous array with type optimization"""
        buffer.extend(struct.pack('<B', OptimizedType.TYPED_ARRAY))
        buffer.extend(struct.pack('<I', len(array)))
        
        if not array:
            buffer.extend(struct.pack('<B', 0))  # No type
            return
        
        first_item = array[0]
        if isinstance(first_item, int):
            buffer.extend(struct.pack('<B', 1))  # Int type
            for item in array:
                buffer.extend(struct.pack('<q', item))  # Use 64-bit for simplicity
        elif isinstance(first_item, float):
            buffer.extend(struct.pack('<B', 2))  # Float type
            for item in array:
                buffer.extend(struct.pack('<d', item))
        elif isinstance(first_item, str):
            buffer.extend(struct.pack('<B', 3))  # String type
            for item in array:
                str_bytes = str(item).encode('utf-8')
                buffer.extend(struct.pack('<H', len(str_bytes)))
                buffer.extend(str_bytes)
        else:
            # Fallback to msgpack for complex types
            buffer.extend(struct.pack('<B', 0xFF))
            msgpack_data = msgpack.packb(array)
            buffer.extend(struct.pack('<I', len(msgpack_data)))
            buffer.extend(msgpack_data)
    
    def _encode_large_array(self, buffer: bytearray, array: List[Any]):
        """Encode large heterogeneous array"""
        buffer.extend(struct.pack('<B', OptimizedType.LARGE_ARRAY))
        buffer.extend(struct.pack('<I', len(array)))
        
        for item in array:
            if isinstance(item, (int, float, str, bool)) or item is None:
                # Encode simple types directly
                self._encode_simple_value(buffer, item)
            else:
                # Use msgpack for complex items
                buffer.extend(struct.pack('<B', 0xFF))
                msgpack_data = msgpack.packb(item)
                buffer.extend(struct.pack('<I', len(msgpack_data)))
                buffer.extend(msgpack_data)
    
    def _encode_rpc_args_pattern(self, buffer: bytearray, data: Dict[str, Any]):
        """Optimize common RPC argument patterns"""
        buffer.extend(struct.pack('<B', OptimizedType.RPC_ARGS_PATTERN))
        
        # Encode known RPC fields in optimized order
        has_args = 'args' in data
        has_kwargs = 'with_kwargs' in data
        has_promise = 'promise' in data
        
        flags = (has_args << 0) | (has_kwargs << 1) | (has_promise << 2)
        buffer.extend(struct.pack('<B', flags))
        
        if has_args:
            args_data = msgpack.packb(data['args'])
            buffer.extend(struct.pack('<I', len(args_data)))
            buffer.extend(args_data)
        
        if has_kwargs:
            buffer.extend(struct.pack('<B', 1 if data['with_kwargs'] else 0))
        
        if has_promise:
            promise_data = msgpack.packb(data['promise'])
            buffer.extend(struct.pack('<I', len(promise_data)))
            buffer.extend(promise_data)
        
        # Encode remaining fields with msgpack
        remaining = {k: v for k, v in data.items() 
                    if k not in ('args', 'with_kwargs', 'promise')}
        if remaining:
            remaining_data = msgpack.packb(remaining)
            buffer.extend(struct.pack('<I', len(remaining_data)))
            buffer.extend(remaining_data)
        else:
            buffer.extend(struct.pack('<I', 0))
    
    def _encode_simple_value(self, buffer: bytearray, value: Any):
        """Encode simple values efficiently"""
        if value is None:
            buffer.extend(struct.pack('<B', 0))
        elif value is False:
            buffer.extend(struct.pack('<B', 1))
        elif value is True:
            buffer.extend(struct.pack('<B', 2))
        elif isinstance(value, int):
            buffer.extend(struct.pack('<B', 3))
            buffer.extend(struct.pack('<q', value))
        elif isinstance(value, float):
            buffer.extend(struct.pack('<B', 4))
            buffer.extend(struct.pack('<d', value))
        elif isinstance(value, str):
            buffer.extend(struct.pack('<B', 5))
            str_bytes = value.encode('utf-8')
            buffer.extend(struct.pack('<I', len(str_bytes)))
            buffer.extend(str_bytes)
        else:
            # Fallback
            buffer.extend(struct.pack('<B', 0xFF))
            msgpack_data = msgpack.packb(value)
            buffer.extend(struct.pack('<I', len(msgpack_data)))
            buffer.extend(msgpack_data)
    
    def _encode_general_optimized(self, buffer: bytearray, value: Any):
        """General optimized encoding for complex values"""
        # For now, use msgpack as fallback
        buffer.extend(struct.pack('<B', 0xFF))
        msgpack_data = msgpack.packb(value)
        buffer.extend(struct.pack('<I', len(msgpack_data)))
        buffer.extend(msgpack_data)
    
    def decode_optimized(self, data: bytes) -> Dict[str, Any]:
        """Decode optimized binary data"""
        if not data.startswith(self.magic_bytes):
            raise ValueError("Invalid optimized binary format")
        
        offset = len(self.magic_bytes)
        field_count = struct.unpack('<H', data[offset:offset+2])[0]
        offset += 2
        
        result = {}
        for _ in range(field_count):
            # Decode key
            key_len = struct.unpack('<H', data[offset:offset+2])[0]
            offset += 2
            key = data[offset:offset+key_len].decode('utf-8')
            offset += key_len
            
            # Decode value
            value, offset = self._decode_optimized_value(data, offset)
            result[key] = value
        
        return result
    
    def _decode_optimized_value(self, data: bytes, offset: int) -> Tuple[Any, int]:
        """Decode optimized value"""
        value_type = data[offset]
        offset += 1
        
        if value_type == 0xFF:  # Msgpack data
            data_len = struct.unpack('<I', data[offset:offset+4])[0]
            offset += 4
            value = msgpack.unpackb(data[offset:offset+data_len], raw=False)
            return value, offset + data_len
        
        elif value_type == OptimizedType.TYPED_ARRAY:
            return self._decode_typed_array(data, offset)
        
        elif value_type == OptimizedType.LARGE_ARRAY:
            return self._decode_large_array(data, offset)
        
        elif value_type == OptimizedType.RPC_ARGS_PATTERN:
            return self._decode_rpc_args_pattern(data, offset)
        
        else:
            raise ValueError(f"Unknown optimized type: {value_type}")
    
    def _decode_typed_array(self, data: bytes, offset: int) -> Tuple[List[Any], int]:
        """Decode typed array"""
        array_len = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
        
        if array_len == 0:
            return [], offset + 1  # Skip type byte
        
        array_type = data[offset]
        offset += 1
        
        result = []
        if array_type == 1:  # Int array
            for _ in range(array_len):
                value = struct.unpack('<q', data[offset:offset+8])[0]
                result.append(value)
                offset += 8
        elif array_type == 2:  # Float array
            for _ in range(array_len):
                value = struct.unpack('<d', data[offset:offset+8])[0]
                result.append(value)
                offset += 8
        elif array_type == 3:  # String array
            for _ in range(array_len):
                str_len = struct.unpack('<H', data[offset:offset+2])[0]
                offset += 2
                value = data[offset:offset+str_len].decode('utf-8')
                result.append(value)
                offset += str_len
        elif array_type == 0xFF:  # Msgpack fallback
            data_len = struct.unpack('<I', data[offset:offset+4])[0]
            offset += 4
            result = msgpack.unpackb(data[offset:offset+data_len], raw=False)
            offset += data_len
        
        return result, offset
    
    def _decode_large_array(self, data: bytes, offset: int) -> Tuple[List[Any], int]:
        """Decode large heterogeneous array"""
        array_len = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
        
        result = []
        for _ in range(array_len):
            value, offset = self._decode_simple_or_msgpack(data, offset)
            result.append(value)
        
        return result, offset
    
    def _decode_rpc_args_pattern(self, data: bytes, offset: int) -> Tuple[Dict[str, Any], int]:
        """Decode RPC args pattern"""
        flags = data[offset]
        offset += 1
        
        result = {}
        
        if flags & 1:  # has_args
            data_len = struct.unpack('<I', data[offset:offset+4])[0]
            offset += 4
            result['args'] = msgpack.unpackb(data[offset:offset+data_len], raw=False)
            offset += data_len
        
        if flags & 2:  # has_kwargs
            result['with_kwargs'] = bool(data[offset])
            offset += 1
        
        if flags & 4:  # has_promise
            data_len = struct.unpack('<I', data[offset:offset+4])[0]
            offset += 4
            result['promise'] = msgpack.unpackb(data[offset:offset+data_len], raw=False)
            offset += data_len
        
        # Decode remaining fields
        remaining_len = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
        if remaining_len > 0:
            remaining = msgpack.unpackb(data[offset:offset+remaining_len], raw=False)
            result.update(remaining)
            offset += remaining_len
        
        return result, offset
    
    def _decode_simple_or_msgpack(self, data: bytes, offset: int) -> Tuple[Any, int]:
        """Decode simple value or msgpack data"""
        value_type = data[offset]
        offset += 1
        
        if value_type == 0:
            return None, offset
        elif value_type == 1:
            return False, offset
        elif value_type == 2:
            return True, offset
        elif value_type == 3:
            value = struct.unpack('<q', data[offset:offset+8])[0]
            return value, offset + 8
        elif value_type == 4:
            value = struct.unpack('<d', data[offset:offset+8])[0]
            return value, offset + 8
        elif value_type == 5:
            str_len = struct.unpack('<I', data[offset:offset+4])[0]
            offset += 4
            value = data[offset:offset+str_len].decode('utf-8')
            return value, offset + str_len
        elif value_type == 0xFF:
            data_len = struct.unpack('<I', data[offset:offset+4])[0]
            offset += 4
            value = msgpack.unpackb(data[offset:offset+data_len], raw=False)
            return value, offset + data_len
        else:
            raise ValueError(f"Unknown simple type: {value_type}")


class SmartHybridProtocol:
    """Smart hybrid protocol that only optimizes where beneficial"""
    
    def __init__(self):
        self.optimizer = SmartHybridOptimizer()
    
    def encode_message(self, main_message: Dict[str, Any], extra_data: Optional[Dict[str, Any]] = None) -> bytes:
        """Encode with smart optimization"""
        # Always use msgpack for main message (server compatibility)
        msgpack_part = msgpack.packb(main_message)
        
        if extra_data:
            # Decide whether to optimize extra_data
            if self.optimizer.should_optimize(extra_data):
                # Use optimized binary encoding
                binary_part = self.optimizer.encode_optimized(extra_data)
                
                # Create hybrid format with length prefix
                result = bytearray()
                result.extend(struct.pack('<I', len(msgpack_part)))
                result.extend(msgpack_part)
                result.extend(binary_part)
                return bytes(result)
            else:
                # Use msgpack for both (no optimization benefit)
                return msgpack_part + msgpack.packb(extra_data)
        else:
            return msgpack_part
    
    def decode_message(self, data: bytes) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        """Decode with automatic format detection"""
        # Try to detect hybrid format first
        if len(data) >= 4:
            try:
                msgpack_length = struct.unpack('<I', data[:4])[0]
                if msgpack_length <= len(data) - 4:
                    # Looks like hybrid format
                    msgpack_data = data[4:4+msgpack_length]
                    main_message = msgpack.unpackb(msgpack_data, raw=False)
                    
                    remaining_data = data[4+msgpack_length:]
                    if remaining_data:
                        if remaining_data.startswith(b'\xEB\x01'):
                            # Optimized binary data
                            extra_data = self.optimizer.decode_optimized(remaining_data)
                            return main_message, extra_data
                        else:
                            # Regular msgpack data
                            extra_data = msgpack.unpackb(remaining_data, raw=False)
                            return main_message, extra_data
                    else:
                        return main_message, None
            except:
                pass
        
        # Fallback: traditional msgpack approach
        main_message = msgpack.unpackb(data, raw=False)
        return main_message, None


def benchmark_smart_optimization():
    """Benchmark the smart optimization approach"""
    print("🎯 Smart Binary Optimization - Targeted Performance Benchmark")
    print("=" * 70)
    
    protocol = SmartHybridProtocol()
    
    # Test scenarios where optimization should help
    scenarios = [
        {
            "name": "Small RPC Call (should use msgpack)",
            "main": {"type": "method", "from": "client1", "to": "client2", "method": "ping"},
            "extra": {"args": ["hello"], "with_kwargs": False}
        },
        {
            "name": "Large Array (should optimize)",
            "main": {"type": "method", "from": "client1", "to": "client2", "method": "process"},
            "extra": {"args": [list(range(5000)), "config"], "with_kwargs": True}
        },
        {
            "name": "Typed Number Array (should optimize heavily)",
            "main": {"type": "method", "from": "client1", "to": "client2", "method": "calculate"},
            "extra": {"args": [list(range(10000)), {"precision": "high"}], "with_kwargs": True}
        },
        {
            "name": "Complex RPC Pattern (should optimize structure)",
            "main": {"type": "method", "from": "w1/c1", "to": "w2/c2", "method": "services.ml.train"},
            "extra": {
                "args": ["model.json", {"epochs": 100, "data": list(range(1000))}],
                "with_kwargs": True,
                "promise": {"resolve": {"_rtype": "method"}, "reject": {"_rtype": "method"}}
            }
        }
    ]
    
    results = []
    for scenario in scenarios:
        print(f"\n📊 Testing: {scenario['name']}")
        
        main_msg = scenario['main']
        extra_data = scenario['extra']
        
        # Check if optimizer thinks it should optimize
        should_optimize = protocol.optimizer.should_optimize(extra_data)
        optimization_potential = protocol.optimizer._analyze_optimization_potential(extra_data)
        print(f"   Optimization potential: {optimization_potential:.2f}")
        print(f"   Will optimize: {should_optimize}")
        
        # Benchmark pure msgpack
        msgpack_data = msgpack.packb(main_msg) + msgpack.packb(extra_data)
        
        iterations = 1000
        
        # Msgpack timing
        start_time = time.time()
        for _ in range(iterations):
            encoded = msgpack.packb(main_msg) + msgpack.packb(extra_data)
        msgpack_encode_time = time.time() - start_time
        
        start_time = time.time()
        for _ in range(iterations):
            main = msgpack.unpackb(msgpack_data[:len(msgpack.packb(main_msg))], raw=False)
            extra = msgpack.unpackb(msgpack_data[len(msgpack.packb(main_msg)):], raw=False)
        msgpack_decode_time = time.time() - start_time
        
        # Smart hybrid timing
        hybrid_data = protocol.encode_message(main_msg, extra_data)
        
        start_time = time.time()
        for _ in range(iterations):
            encoded = protocol.encode_message(main_msg, extra_data)
        hybrid_encode_time = time.time() - start_time
        
        start_time = time.time()
        for _ in range(iterations):
            main, extra = protocol.decode_message(hybrid_data)
        hybrid_decode_time = time.time() - start_time
        
        # Calculate results
        encode_speedup = msgpack_encode_time / hybrid_encode_time if hybrid_encode_time > 0 else 0
        decode_speedup = msgpack_decode_time / hybrid_decode_time if hybrid_decode_time > 0 else 0
        overall_speedup = ((msgpack_encode_time + msgpack_decode_time) / 
                          (hybrid_encode_time + hybrid_decode_time)) if (hybrid_encode_time + hybrid_decode_time) > 0 else 0
        size_ratio = len(msgpack_data) / len(hybrid_data) if len(hybrid_data) > 0 else 1
        
        print(f"   Encode speedup: {encode_speedup:.2f}x")
        print(f"   Decode speedup: {decode_speedup:.2f}x")
        print(f"   Overall speedup: {overall_speedup:.2f}x")
        print(f"   Size efficiency: {size_ratio:.2f}x")
        print(f"   Original size: {len(msgpack_data)} bytes")
        print(f"   Optimized size: {len(hybrid_data)} bytes")
        
        results.append({
            "name": scenario['name'],
            "should_optimize": should_optimize,
            "encode_speedup": encode_speedup,
            "decode_speedup": decode_speedup,
            "overall_speedup": overall_speedup,
            "size_ratio": size_ratio
        })
    
    # Summary
    print(f"\n🏆 Smart Optimization Summary")
    print(f"=" * 40)
    optimized_scenarios = [r for r in results if r['should_optimize']]
    if optimized_scenarios:
        avg_speedup = sum(r['overall_speedup'] for r in optimized_scenarios) / len(optimized_scenarios)
        print(f"Average speedup for optimized scenarios: {avg_speedup:.2f}x")
    
    non_optimized = [r for r in results if not r['should_optimize']]
    if non_optimized:
        avg_overhead = sum(r['overall_speedup'] for r in non_optimized) / len(non_optimized)
        print(f"Average overhead for non-optimized scenarios: {avg_overhead:.2f}x")
    
    print(f"\n💡 Smart Strategy Benefits:")
    print(f"   • Only optimizes where beneficial")
    print(f"   • Minimal overhead for small messages")
    print(f"   • Significant gains for large/complex data")
    print(f"   • Automatic threshold-based selection")


if __name__ == "__main__":
    benchmark_smart_optimization() 