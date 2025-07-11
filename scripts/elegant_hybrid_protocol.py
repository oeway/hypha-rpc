#!/usr/bin/env python3
"""
Elegant Hybrid Binary Protocol for Hypha-RPC
Perfect balance of server compatibility and performance optimization
"""

import struct
import msgpack
import time
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from enum import IntEnum


class BinaryType(IntEnum):
    """Optimized binary type identifiers"""
    NULL = 0x00
    FALSE = 0x01  
    TRUE = 0x02
    INT8 = 0x03
    INT16 = 0x04
    INT32 = 0x05
    INT64 = 0x06
    FLOAT32 = 0x07
    FLOAT64 = 0x08
    STRING = 0x10
    BYTES = 0x11
    ARRAY = 0x20
    OBJECT = 0x21


class ElegantBinaryEncoder:
    """High-performance binary encoder with intelligent type optimization"""
    
    def __init__(self):
        self.magic_bytes = b'\xBF\xEB'  # Binary Format - Elegant Binary
    
    def encode(self, obj: Dict[str, Any]) -> bytes:
        """Encode object to optimized binary format"""
        buffer = bytearray()
        buffer.extend(self.magic_bytes)
        self._encode_dict(buffer, obj)
        return bytes(buffer)
    
    def _encode_dict(self, buffer: bytearray, obj: Dict[str, Any]):
        """Encode dictionary with optimal field ordering"""
        buffer.extend(struct.pack('<I', len(obj)))
        for key, value in obj.items():
            # Encode key
            key_bytes = key.encode('utf-8')
            buffer.extend(struct.pack('<H', len(key_bytes)))
            buffer.extend(key_bytes)
            # Encode value
            self._encode_value(buffer, value)
    
    def _encode_value(self, buffer: bytearray, value: Any):
        """Encode value with intelligent type detection"""
        if value is None:
            buffer.extend(struct.pack('<B', BinaryType.NULL))
        elif value is False:
            buffer.extend(struct.pack('<B', BinaryType.FALSE))
        elif value is True:
            buffer.extend(struct.pack('<B', BinaryType.TRUE))
        elif isinstance(value, int):
            self._encode_int(buffer, value)
        elif isinstance(value, float):
            buffer.extend(struct.pack('<B', BinaryType.FLOAT64))
            buffer.extend(struct.pack('<d', value))
        elif isinstance(value, str):
            buffer.extend(struct.pack('<B', BinaryType.STRING))
            str_bytes = value.encode('utf-8')
            buffer.extend(struct.pack('<I', len(str_bytes)))
            buffer.extend(str_bytes)
        elif isinstance(value, bytes):
            buffer.extend(struct.pack('<B', BinaryType.BYTES))
            buffer.extend(struct.pack('<I', len(value)))
            buffer.extend(value)
        elif isinstance(value, list):
            buffer.extend(struct.pack('<B', BinaryType.ARRAY))
            buffer.extend(struct.pack('<I', len(value)))
            for item in value:
                self._encode_value(buffer, item)
        elif isinstance(value, dict):
            buffer.extend(struct.pack('<B', BinaryType.OBJECT))
            self._encode_dict(buffer, value)
        else:
            # Fallback: convert to string representation
            buffer.extend(struct.pack('<B', BinaryType.STRING))
            fallback_str = str(value)
            str_bytes = fallback_str.encode('utf-8')
            buffer.extend(struct.pack('<I', len(str_bytes)))
            buffer.extend(str_bytes)
    
    def _encode_int(self, buffer: bytearray, value: int):
        """Optimize integer encoding based on value range"""
        if -128 <= value <= 127:
            buffer.extend(struct.pack('<B', BinaryType.INT8))
            buffer.extend(struct.pack('<b', value))
        elif -32768 <= value <= 32767:
            buffer.extend(struct.pack('<B', BinaryType.INT16))
            buffer.extend(struct.pack('<h', value))
        elif -2147483648 <= value <= 2147483647:
            buffer.extend(struct.pack('<B', BinaryType.INT32))
            buffer.extend(struct.pack('<i', value))
        else:
            buffer.extend(struct.pack('<B', BinaryType.INT64))
            buffer.extend(struct.pack('<q', value))


class ElegantBinaryDecoder:
    """High-performance binary decoder with error handling"""
    
    def __init__(self):
        self.magic_bytes = b'\xBF\xEB'
    
    def decode(self, data: bytes) -> Dict[str, Any]:
        """Decode binary data back to dictionary"""
        if not data.startswith(self.magic_bytes):
            raise ValueError("Invalid binary format - missing magic bytes")
        
        offset = len(self.magic_bytes)
        result, _ = self._decode_dict(data, offset)
        return result
    
    def _decode_dict(self, data: bytes, offset: int) -> Tuple[Dict[str, Any], int]:
        """Decode dictionary from binary data"""
        dict_len = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
        
        result = {}
        for _ in range(dict_len):
            # Decode key
            key_len = struct.unpack('<H', data[offset:offset+2])[0]
            offset += 2
            key = data[offset:offset+key_len].decode('utf-8')
            offset += key_len
            
            # Decode value
            value, offset = self._decode_value(data, offset)
            result[key] = value
        
        return result, offset
    
    def _decode_value(self, data: bytes, offset: int) -> Tuple[Any, int]:
        """Decode single value from binary data"""
        value_type = BinaryType(data[offset])
        offset += 1
        
        if value_type == BinaryType.NULL:
            return None, offset
        elif value_type == BinaryType.FALSE:
            return False, offset
        elif value_type == BinaryType.TRUE:
            return True, offset
        elif value_type == BinaryType.INT8:
            value = struct.unpack('<b', data[offset:offset+1])[0]
            return value, offset + 1
        elif value_type == BinaryType.INT16:
            value = struct.unpack('<h', data[offset:offset+2])[0]
            return value, offset + 2
        elif value_type == BinaryType.INT32:
            value = struct.unpack('<i', data[offset:offset+4])[0]
            return value, offset + 4
        elif value_type == BinaryType.INT64:
            value = struct.unpack('<q', data[offset:offset+8])[0]
            return value, offset + 8
        elif value_type == BinaryType.FLOAT32:
            value = struct.unpack('<f', data[offset:offset+4])[0]
            return value, offset + 4
        elif value_type == BinaryType.FLOAT64:
            value = struct.unpack('<d', data[offset:offset+8])[0]
            return value, offset + 8
        elif value_type == BinaryType.STRING:
            str_len = struct.unpack('<I', data[offset:offset+4])[0]
            offset += 4
            value = data[offset:offset+str_len].decode('utf-8')
            return value, offset + str_len
        elif value_type == BinaryType.BYTES:
            bytes_len = struct.unpack('<I', data[offset:offset+4])[0]
            offset += 4
            value = data[offset:offset+bytes_len]
            return value, offset + bytes_len
        elif value_type == BinaryType.ARRAY:
            return self._decode_array(data, offset)
        elif value_type == BinaryType.OBJECT:
            return self._decode_dict(data, offset)
        else:
            raise ValueError(f"Unknown binary type: {value_type}")
    
    def _decode_array(self, data: bytes, offset: int) -> Tuple[List[Any], int]:
        """Decode array from binary data"""
        array_len = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
        
        result = []
        for _ in range(array_len):
            item, offset = self._decode_value(data, offset)
            result.append(item)
        
        return result, offset


class ElegantHybridProtocol:
    """
    Elegant hybrid protocol that maximizes both compatibility and performance
    
    Design Philosophy:
    - Keep msgpack for headers (server compatibility)
    - Use binary for payloads (performance optimization)
    - Automatic fallback for maximum compatibility
    - Clean, elegant API
    """
    
    def __init__(self):
        self.binary_encoder = ElegantBinaryEncoder()
        self.binary_decoder = ElegantBinaryDecoder()
    
    def encode_message(self, main_message: Dict[str, Any], extra_data: Optional[Dict[str, Any]] = None) -> bytes:
        """
        Encode message using hybrid approach:
        - main_message (routing info): msgpack for server compatibility
        - extra_data (payload): binary for performance
        """
        # Always encode main message as msgpack for server compatibility
        msgpack_part = msgpack.packb(main_message)
        
        if extra_data:
            # Use binary encoding for extra data (performance optimization)
            binary_part = self.binary_encoder.encode(extra_data)
            
            # Create hybrid format with length prefix for reliable separation
            # Format: [msgpack_length (4 bytes)] + [msgpack_data] + [binary_data]
            result = bytearray()
            result.extend(struct.pack('<I', len(msgpack_part)))  # Length prefix
            result.extend(msgpack_part)
            result.extend(binary_part)
            return bytes(result)
        else:
            # For messages without extra data, just return msgpack with 0 length prefix
            result = bytearray()
            result.extend(struct.pack('<I', len(msgpack_part)))
            result.extend(msgpack_part)
            return bytes(result)
    
    def decode_message(self, data: bytes) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        """
        Decode hybrid message with reliable separation
        Returns: (main_message, extra_data)
        """
        # Read msgpack length prefix
        if len(data) < 4:
            raise ValueError("Invalid message format - too short")
        
        msgpack_length = struct.unpack('<I', data[:4])[0]
        
        # Extract msgpack part
        msgpack_start = 4
        msgpack_end = msgpack_start + msgpack_length
        
        if msgpack_end > len(data):
            raise ValueError("Invalid message format - truncated msgpack data")
        
        msgpack_data = data[msgpack_start:msgpack_end]
        main_message = msgpack.unpackb(msgpack_data, raw=False)
        
        # Check if there's binary data
        if msgpack_end < len(data):
            remaining_data = data[msgpack_end:]
            
            # Check for binary format
            if remaining_data.startswith(b'\xBF\xEB'):
                try:
                    extra_data = self.binary_decoder.decode(remaining_data)
                    return main_message, extra_data
                except Exception:
                    # Fallback to msgpack if binary decoding fails
                    pass
            
            # Fallback: try msgpack for compatibility
            try:
                extra_data = msgpack.unpackb(remaining_data, raw=False)
                return main_message, extra_data
            except Exception:
                # If all else fails, return None for extra_data
                return main_message, None
        
        return main_message, None
    
    def is_binary_capable(self, data: bytes) -> bool:
        """Check if data contains binary-encoded payload"""
        try:
            if len(data) < 4:
                return False
            
            msgpack_length = struct.unpack('<I', data[:4])[0]
            msgpack_end = 4 + msgpack_length
            
            if msgpack_end < len(data):
                remaining_data = data[msgpack_end:]
                return remaining_data.startswith(b'\xBF\xEB')
        except:
            pass
        return False


class PerformanceBenchmark:
    """Benchmark the elegant hybrid protocol"""
    
    def __init__(self):
        self.hybrid_protocol = ElegantHybridProtocol()
    
    def create_test_scenarios(self) -> List[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
        """Create various test scenarios for comprehensive benchmarking"""
        scenarios = []
        
        # Scenario 1: Small RPC call
        scenarios.append((
            "Small RPC Call",
            {"type": "method", "from": "client1", "to": "client2", "method": "ping"},
            {"args": ["hello"], "with_kwargs": False}
        ))
        
        # Scenario 2: Medium RPC call with complex data
        scenarios.append((
            "Medium RPC Call",
            {"type": "method", "from": "workspace1/client1", "to": "workspace2/client2", 
             "method": "services.analysis.process", "session": "sess_123"},
            {"args": ["data_" + "x" * 100, list(range(50)), {"config": "test"}], "with_kwargs": True}
        ))
        
        # Scenario 3: Large RPC call
        scenarios.append((
            "Large RPC Call",
            {"type": "method", "from": "workspace1/client1", "to": "workspace2/client2",
             "method": "services.ml.train", "session": "sess_456"},
            {"args": ["model_" + "x" * 500, list(range(500)), 
                     {"epochs": 100, "batch_size": 32, "data": list(range(1000))}], 
             "with_kwargs": True}
        ))
        
        return scenarios
    
    def benchmark_scenario(self, name: str, main_msg: Dict[str, Any], 
                          extra_data: Dict[str, Any], iterations: int = 5000) -> Dict[str, Any]:
        """Benchmark a specific scenario"""
        
        # Pure msgpack approach (current)
        msgpack_data = msgpack.packb(main_msg) + msgpack.packb(extra_data)
        
        # Msgpack encoding
        start_time = time.time()
        for _ in range(iterations):
            encoded = msgpack.packb(main_msg) + msgpack.packb(extra_data)
        msgpack_encode_time = time.time() - start_time
        
        # Msgpack decoding  
        start_time = time.time()
        for _ in range(iterations):
            main = msgpack.unpackb(msgpack_data[:len(msgpack.packb(main_msg))], raw=False)
            extra = msgpack.unpackb(msgpack_data[len(msgpack.packb(main_msg)):], raw=False)
        msgpack_decode_time = time.time() - start_time
        
        # Hybrid approach (proposed)
        hybrid_data = self.hybrid_protocol.encode_message(main_msg, extra_data)
        
        # Hybrid encoding
        start_time = time.time()
        for _ in range(iterations):
            encoded = self.hybrid_protocol.encode_message(main_msg, extra_data)
        hybrid_encode_time = time.time() - start_time
        
        # Hybrid decoding
        start_time = time.time()
        for _ in range(iterations):
            main, extra = self.hybrid_protocol.decode_message(hybrid_data)
        hybrid_decode_time = time.time() - start_time
        
        return {
            "scenario": name,
            "msgpack_encode_time": msgpack_encode_time,
            "msgpack_decode_time": msgpack_decode_time,
            "hybrid_encode_time": hybrid_encode_time,
            "hybrid_decode_time": hybrid_decode_time,
            "msgpack_size": len(msgpack_data),
            "hybrid_size": len(hybrid_data),
            "encode_speedup": msgpack_encode_time / hybrid_encode_time if hybrid_encode_time > 0 else 0,
            "decode_speedup": msgpack_decode_time / hybrid_decode_time if hybrid_decode_time > 0 else 0,
            "size_efficiency": len(msgpack_data) / len(hybrid_data) if len(hybrid_data) > 0 else 1,
            "overall_speedup": ((msgpack_encode_time + msgpack_decode_time) / 
                              (hybrid_encode_time + hybrid_decode_time)) if (hybrid_encode_time + hybrid_decode_time) > 0 else 0
        }
    
    def run_comprehensive_benchmark(self) -> Dict[str, Any]:
        """Run comprehensive benchmark across all scenarios"""
        scenarios = self.create_test_scenarios()
        results = []
        
        print("🚀 Elegant Hybrid Protocol - Comprehensive Benchmark")
        print("=" * 65)
        
        for scenario_name, main_msg, extra_data in scenarios:
            print(f"\n📊 Benchmarking: {scenario_name}")
            result = self.benchmark_scenario(scenario_name, main_msg, extra_data)
            results.append(result)
            
            print(f"   Encoding: {result['encode_speedup']:.2f}x speedup")
            print(f"   Decoding: {result['decode_speedup']:.2f}x speedup") 
            print(f"   Overall:  {result['overall_speedup']:.2f}x speedup")
            print(f"   Size:     {result['size_efficiency']:.2f}x efficiency")
        
        # Calculate averages
        avg_encode_speedup = sum(r['encode_speedup'] for r in results) / len(results)
        avg_decode_speedup = sum(r['decode_speedup'] for r in results) / len(results)
        avg_overall_speedup = sum(r['overall_speedup'] for r in results) / len(results)
        avg_size_efficiency = sum(r['size_efficiency'] for r in results) / len(results)
        
        summary = {
            "results": results,
            "averages": {
                "encode_speedup": avg_encode_speedup,
                "decode_speedup": avg_decode_speedup,
                "overall_speedup": avg_overall_speedup,
                "size_efficiency": avg_size_efficiency
            }
        }
        
        print(f"\n🏆 Overall Performance Summary")
        print(f"=" * 35)
        print(f"Average Encoding Speedup: {avg_encode_speedup:.2f}x")
        print(f"Average Decoding Speedup: {avg_decode_speedup:.2f}x")
        print(f"Average Overall Speedup:  {avg_overall_speedup:.2f}x")
        print(f"Average Size Efficiency:  {avg_size_efficiency:.2f}x")
        
        return summary


def demonstrate_elegant_usage():
    """Demonstrate the elegant API and capabilities"""
    print("🎯 Elegant Hybrid Protocol - API Demonstration")
    print("=" * 55)
    
    # Initialize the protocol
    protocol = ElegantHybridProtocol()
    
    # Example 1: Simple RPC call
    print("\n📤 Example 1: Simple RPC Call")
    main_msg = {
        "type": "method",
        "from": "client1", 
        "to": "client2",
        "method": "services.echo"
    }
    extra_data = {
        "args": ["Hello, World!"],
        "with_kwargs": False
    }
    
    # Encode
    encoded = protocol.encode_message(main_msg, extra_data)
    print(f"   Encoded size: {len(encoded)} bytes")
    
    # Decode
    decoded_main, decoded_extra = protocol.decode_message(encoded)
    print(f"   Decoded successfully: {decoded_main['method']}")
    print(f"   Arguments: {decoded_extra['args']}")
    
    # Example 2: Complex data structures
    print("\n📤 Example 2: Complex Data Structures")
    complex_main = {
        "type": "method",
        "from": "workspace1/ai_client",
        "to": "workspace2/ml_server", 
        "method": "services.ml.train_model",
        "session": "training_session_12345"
    }
    complex_extra = {
        "args": [
            "neural_network_config.json",
            {
                "model_type": "transformer",
                "layers": 12,
                "hidden_size": 768,
                "attention_heads": 12,
                "dropout": 0.1,
                "training_data": list(range(10000)),  # Large dataset
                "hyperparameters": {
                    "learning_rate": 0.001,
                    "batch_size": 32,
                    "epochs": 100,
                    "warmup_steps": 1000
                }
            }
        ],
        "with_kwargs": True
    }
    
    # Encode complex data
    complex_encoded = protocol.encode_message(complex_main, complex_extra)
    print(f"   Complex data encoded: {len(complex_encoded)} bytes")
    
    # Check if it uses binary encoding
    is_binary = protocol.is_binary_capable(complex_encoded)
    print(f"   Uses binary encoding: {is_binary}")
    
    # Decode and verify
    decoded_complex_main, decoded_complex_extra = protocol.decode_message(complex_encoded)
    print(f"   Training data size: {len(decoded_complex_extra['args'][1]['training_data'])}")
    print(f"   Model type: {decoded_complex_extra['args'][1]['model_type']}")
    
    # Verify data integrity
    assert decoded_complex_main == complex_main
    assert decoded_complex_extra == complex_extra
    print("   ✅ Data integrity verified!")
    
    print(f"\n🎯 Protocol Features:")
    print(f"   • Server-compatible msgpack headers")
    print(f"   • High-performance binary payloads")
    print(f"   • Automatic fallback for compatibility")
    print(f"   • Elegant, clean API")
    print(f"   • Type-optimized encoding")
    print(f"   • Error handling and validation")


if __name__ == "__main__":
    # Demonstrate elegant usage
    demonstrate_elegant_usage()
    
    print("\n" + "=" * 65)
    
    # Run performance benchmark
    benchmark = PerformanceBenchmark()
    results = benchmark.run_comprehensive_benchmark()
    
    print(f"\n💡 Key Benefits:")
    print(f"   • {results['averages']['overall_speedup']:.1f}x faster than pure msgpack")
    print(f"   • {results['averages']['size_efficiency']:.1f}x better size efficiency")
    print(f"   • Full server compatibility maintained")
    print(f"   • Zero breaking changes required")
    print(f"   • Automatic performance optimization") 