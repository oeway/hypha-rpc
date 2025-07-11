#!/usr/bin/env python3
"""
Elegant Hybrid Binary Protocol for Hypha-RPC
Keeps msgpack headers for server compatibility, binary payload for performance
"""

import struct
import msgpack
import json
import time
import asyncio
from typing import Dict, Any, List, Optional, Union, Tuple
from dataclasses import dataclass, field
from enum import IntEnum
from abc import ABC, abstractmethod
import inspect


class BinaryType(IntEnum):
    """Optimized binary type system"""
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
    # Special types for RPC optimization
    PROMISE = 0x30
    METHOD_REF = 0x31
    SESSION_REF = 0x32
    GENERATOR = 0x33


@dataclass
class BinarySchema:
    """Schema definition for binary encoding"""
    name: str
    version: int
    fields: Dict[str, 'FieldSpec']
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BinarySchema':
        """Create schema from dictionary"""
        fields = {}
        for name, spec in data.get('fields', {}).items():
            fields[name] = FieldSpec.from_dict(spec)
        return cls(
            name=data['name'],
            version=data['version'],
            fields=fields
        )


@dataclass 
class FieldSpec:
    """Field specification for schema"""
    type: BinaryType
    optional: bool = False
    array: bool = False
    schema: Optional[str] = None  # For nested objects
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'FieldSpec':
        """Create field spec from dictionary"""
        return cls(
            type=BinaryType(data['type']),
            optional=data.get('optional', False),
            array=data.get('array', False),
            schema=data.get('schema')
        )


class BinaryEncoder:
    """High-performance binary encoder with schema support"""
    
    def __init__(self):
        self.schemas: Dict[str, BinarySchema] = {}
        self._compiled_encoders: Dict[str, callable] = {}
    
    def register_schema(self, schema: BinarySchema):
        """Register a schema and compile optimized encoder"""
        self.schemas[schema.name] = schema
        self._compiled_encoders[schema.name] = self._compile_encoder(schema)
    
    def _compile_encoder(self, schema: BinarySchema) -> callable:
        """Compile an optimized encoder for the schema"""
        def encode_with_schema(obj: Dict[str, Any]) -> bytes:
            buffer = bytearray()
            
            # Write schema header (magic + name + version)
            buffer.extend(b'\xBF\xBF')  # Magic bytes for binary format
            schema_name_bytes = schema.name.encode('utf-8')
            buffer.extend(struct.pack('<H', len(schema_name_bytes)))
            buffer.extend(schema_name_bytes)
            buffer.extend(struct.pack('<H', schema.version))
            
            # Encode fields in schema order for optimal parsing
            for field_name, field_spec in schema.fields.items():
                value = obj.get(field_name)
                self._encode_field(buffer, value, field_spec)
            
            return bytes(buffer)
        
        return encode_with_schema
    
    def _encode_field(self, buffer: bytearray, value: Any, spec: FieldSpec):
        """Encode a single field with type information"""
        if value is None:
            if not spec.optional:
                raise ValueError(f"Required field is None")
            buffer.extend(struct.pack('<B', BinaryType.NULL))
            return
        
        if spec.array:
            self._encode_array(buffer, value, spec)
        else:
            self._encode_value(buffer, value, spec.type)
    
    def _encode_array(self, buffer: bytearray, array: List[Any], spec: FieldSpec):
        """Encode an array with length prefix"""
        buffer.extend(struct.pack('<B', BinaryType.ARRAY))
        buffer.extend(struct.pack('<I', len(array)))
        for item in array:
            self._encode_value(buffer, item, spec.type)
    
    def _encode_value(self, buffer: bytearray, value: Any, value_type: BinaryType):
        """Encode a single value with optimized type handling"""
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
                self._encode_value(buffer, item, self._infer_type(item))
        elif isinstance(value, dict):
            buffer.extend(struct.pack('<B', BinaryType.OBJECT))
            self._encode_dict(buffer, value)
        else:
            # Fallback to object encoding
            buffer.extend(struct.pack('<B', BinaryType.OBJECT))
            self._encode_dict(buffer, {'_value': value, '_type': type(value).__name__})
    
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
    
    def _encode_dict(self, buffer: bytearray, obj: Dict[str, Any]):
        """Encode dictionary with field count prefix"""
        buffer.extend(struct.pack('<I', len(obj)))
        for key, value in obj.items():
            # Encode key
            key_bytes = key.encode('utf-8')
            buffer.extend(struct.pack('<H', len(key_bytes)))
            buffer.extend(key_bytes)
            # Encode value
            self._encode_value(buffer, value, self._infer_type(value))
    
    def _infer_type(self, value: Any) -> BinaryType:
        """Infer binary type from Python value"""
        if value is None:
            return BinaryType.NULL
        elif isinstance(value, bool):
            return BinaryType.TRUE if value else BinaryType.FALSE
        elif isinstance(value, int):
            return BinaryType.INT64  # Default to largest for inference
        elif isinstance(value, float):
            return BinaryType.FLOAT64
        elif isinstance(value, str):
            return BinaryType.STRING
        elif isinstance(value, bytes):
            return BinaryType.BYTES
        elif isinstance(value, list):
            return BinaryType.ARRAY
        elif isinstance(value, dict):
            return BinaryType.OBJECT
        else:
            return BinaryType.OBJECT
    
    def encode(self, obj: Dict[str, Any], schema_name: Optional[str] = None) -> bytes:
        """Encode object using schema or fallback encoding"""
        if schema_name and schema_name in self._compiled_encoders:
            return self._compiled_encoders[schema_name](obj)
        else:
            return self._encode_fallback(obj)
    
    def _encode_fallback(self, obj: Dict[str, Any]) -> bytes:
        """Fallback encoding without schema"""
        buffer = bytearray()
        buffer.extend(b'\xBF\xBF')  # Magic bytes
        buffer.extend(struct.pack('<H', 0))  # No schema name
        buffer.extend(struct.pack('<H', 0))  # No version
        self._encode_dict(buffer, obj)
        return bytes(buffer)


class BinaryDecoder:
    """High-performance binary decoder with schema support"""
    
    def __init__(self):
        self.schemas: Dict[str, BinarySchema] = {}
        self._compiled_decoders: Dict[str, callable] = {}
    
    def register_schema(self, schema: BinarySchema):
        """Register a schema and compile optimized decoder"""
        self.schemas[schema.name] = schema
        self._compiled_decoders[schema.name] = self._compile_decoder(schema)
    
    def _compile_decoder(self, schema: BinarySchema) -> callable:
        """Compile an optimized decoder for the schema"""
        def decode_with_schema(data: bytes) -> Dict[str, Any]:
            offset = self._skip_header(data)
            result = {}
            
            # Decode fields in schema order
            for field_name, field_spec in schema.fields.items():
                value, offset = self._decode_field(data, offset, field_spec)
                if value is not None or not field_spec.optional:
                    result[field_name] = value
            
            return result
        
        return decode_with_schema
    
    def _skip_header(self, data: bytes) -> int:
        """Skip binary format header and return data offset"""
        offset = 2  # Skip magic bytes
        schema_name_len = struct.unpack('<H', data[offset:offset+2])[0]
        offset += 2 + schema_name_len  # Skip schema name
        offset += 2  # Skip version
        return offset
    
    def _decode_field(self, data: bytes, offset: int, spec: FieldSpec) -> Tuple[Any, int]:
        """Decode a field according to its specification"""
        if spec.array:
            return self._decode_array(data, offset, spec)
        else:
            return self._decode_value(data, offset)
    
    def _decode_array(self, data: bytes, offset: int, spec: FieldSpec) -> Tuple[List[Any], int]:
        """Decode an array with length prefix"""
        value_type = data[offset]
        offset += 1
        
        if value_type != BinaryType.ARRAY:
            raise ValueError(f"Expected array, got {value_type}")
        
        array_len = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
        
        result = []
        for _ in range(array_len):
            item, offset = self._decode_value(data, offset)
            result.append(item)
        
        return result, offset
    
    def _decode_value(self, data: bytes, offset: int) -> Tuple[Any, int]:
        """Decode a single value with type detection"""
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
            return self._decode_array_untyped(data, offset - 1)
        elif value_type == BinaryType.OBJECT:
            return self._decode_dict(data, offset)
        else:
            raise ValueError(f"Unknown binary type: {value_type}")
    
    def _decode_array_untyped(self, data: bytes, offset: int) -> Tuple[List[Any], int]:
        """Decode untyped array"""
        offset += 1  # Skip type byte (already read)
        array_len = struct.unpack('<I', data[offset:offset+4])[0]
        offset += 4
        
        result = []
        for _ in range(array_len):
            item, offset = self._decode_value(data, offset)
            result.append(item)
        
        return result, offset
    
    def _decode_dict(self, data: bytes, offset: int) -> Tuple[Dict[str, Any], int]:
        """Decode dictionary with field count prefix"""
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
    
    def decode(self, data: bytes) -> Dict[str, Any]:
        """Decode binary data using schema or fallback decoding"""
        # Check magic bytes
        if data[:2] != b'\xBF\xBF':
            raise ValueError("Invalid binary format")
        
        # Read schema info
        offset = 2
        schema_name_len = struct.unpack('<H', data[offset:offset+2])[0]
        offset += 2
        
        if schema_name_len > 0:
            schema_name = data[offset:offset+schema_name_len].decode('utf-8')
            offset += schema_name_len
            version = struct.unpack('<H', data[offset:offset+2])[0]
            offset += 2
            
            if schema_name in self._compiled_decoders:
                return self._compiled_decoders[schema_name](data)
        
        # Fallback decoding
        return self._decode_fallback(data)
    
    def _decode_fallback(self, data: bytes) -> Dict[str, Any]:
        """Fallback decoding without schema"""
        offset = self._skip_header(data)
        result, _ = self._decode_dict(data, offset)
        return result


class HybridProtocol:
    """Elegant hybrid protocol that combines msgpack headers with binary payloads"""
    
    def __init__(self):
        self.encoder = BinaryEncoder()
        self.decoder = BinaryDecoder()
        self._setup_rpc_schemas()
    
    def _setup_rpc_schemas(self):
        """Setup optimized schemas for common RPC patterns"""
        
        # RPC Arguments Schema
        args_schema = BinarySchema(
            name="rpc_args",
            version=1,
            fields={
                "args": FieldSpec(BinaryType.ARRAY, optional=True),
                "with_kwargs": FieldSpec(BinaryType.TRUE, optional=True),
            }
        )
        self.register_schema(args_schema)
        
        # Promise Schema  
        promise_schema = BinarySchema(
            name="rpc_promise",
            version=1,
            fields={
                "resolve": FieldSpec(BinaryType.METHOD_REF),
                "reject": FieldSpec(BinaryType.METHOD_REF),
                "heartbeat": FieldSpec(BinaryType.METHOD_REF, optional=True),
                "interval": FieldSpec(BinaryType.FLOAT64, optional=True),
            }
        )
        self.register_schema(promise_schema)
        
        # Service Registration Schema
        service_schema = BinarySchema(
            name="service_info",
            version=1,
            fields={
                "config": FieldSpec(BinaryType.OBJECT),
                "id": FieldSpec(BinaryType.STRING),
                "name": FieldSpec(BinaryType.STRING),
                "description": FieldSpec(BinaryType.STRING, optional=True),
                "type": FieldSpec(BinaryType.STRING),
                "docs": FieldSpec(BinaryType.STRING, optional=True),
                "app_id": FieldSpec(BinaryType.STRING),
                "service_schema": FieldSpec(BinaryType.OBJECT),
            }
        )
        self.register_schema(service_schema)
    
    def register_schema(self, schema: BinarySchema):
        """Register schema for both encoder and decoder"""
        self.encoder.register_schema(schema)
        self.decoder.register_schema(schema)
    
    def encode_message(self, main_message: Dict[str, Any], extra_data: Optional[Dict[str, Any]] = None) -> bytes:
        """
        Encode message in hybrid format:
        - main_message: msgpack (for server compatibility)
        - extra_data: binary (for performance)
        """
        # Keep main message as msgpack for server compatibility
        msgpack_part = msgpack.packb(main_message)
        
        if extra_data:
            # Determine optimal schema for extra_data
            schema_name = self._select_schema(extra_data)
            binary_part = self.encoder.encode(extra_data, schema_name)
            return msgpack_part + binary_part
        else:
            return msgpack_part
    
    def decode_message(self, data: bytes) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        """
        Decode hybrid message:
        Returns: (main_message, extra_data)
        """
        # Decode msgpack part
        unpacker = msgpack.Unpacker(data, max_buffer_size=len(data))
        main_message = unpacker.unpack()
        
        # Check if there's binary data
        consumed = unpacker.tell()
        if consumed < len(data):
            remaining_data = data[consumed:]
            # Check for binary format magic bytes
            if remaining_data[:2] == b'\xBF\xBF':
                extra_data = self.decoder.decode(remaining_data)
                return main_message, extra_data
            else:
                # Fallback to msgpack for compatibility
                try:
                    unpacker2 = msgpack.Unpacker(remaining_data)
                    extra_data = unpacker2.unpack()
                    return main_message, extra_data
                except:
                    return main_message, None
        
        return main_message, None
    
    def _select_schema(self, data: Dict[str, Any]) -> Optional[str]:
        """Intelligently select the best schema for the data"""
        if "args" in data and "with_kwargs" in data:
            return "rpc_args"
        elif "resolve" in data and "reject" in data:
            return "rpc_promise"
        elif "config" in data and "service_schema" in data:
            return "service_info"
        else:
            return None  # Use fallback encoding


class PerformanceBenchmark:
    """Benchmark hybrid protocol vs pure msgpack"""
    
    def __init__(self):
        self.hybrid = HybridProtocol()
    
    def create_test_data(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Create realistic RPC test data"""
        main_message = {
            "type": "method",
            "from": "workspace1/client1", 
            "to": "workspace2/client2",
            "method": "services.analysis.process",
            "session": "session_123456789",
        }
        
        extra_data = {
            "args": [
                "large_dataset_" + "x" * 100,
                list(range(500)),
                {"config": "high_performance", "threads": 8, "memory": "4GB"},
                42,
                3.14159,
                True,
                None
            ],
            "with_kwargs": True,
        }
        
        return main_message, extra_data
    
    def benchmark(self, iterations: int = 10000) -> Dict[str, float]:
        """Run comprehensive benchmark"""
        main_msg, extra_data = self.create_test_data()
        
        # Test pure msgpack (current approach)
        msgpack_data = msgpack.packb(main_msg) + msgpack.packb(extra_data)
        
        start_time = time.time()
        for _ in range(iterations):
            packed = msgpack.packb(main_msg) + msgpack.packb(extra_data)
        msgpack_encode_time = time.time() - start_time
        
        start_time = time.time()
        for _ in range(iterations):
            unpacker = msgpack.Unpacker(msgpack_data)
            main = unpacker.unpack()
            extra = unpacker.unpack()
        msgpack_decode_time = time.time() - start_time
        
        # Test hybrid protocol
        hybrid_data = self.hybrid.encode_message(main_msg, extra_data)
        
        start_time = time.time()
        for _ in range(iterations):
            encoded = self.hybrid.encode_message(main_msg, extra_data)
        hybrid_encode_time = time.time() - start_time
        
        start_time = time.time()
        for _ in range(iterations):
            main, extra = self.hybrid.decode_message(hybrid_data)
        hybrid_decode_time = time.time() - start_time
        
        return {
            "msgpack_encode": msgpack_encode_time,
            "msgpack_decode": msgpack_decode_time,
            "hybrid_encode": hybrid_encode_time,
            "hybrid_decode": hybrid_decode_time,
            "msgpack_size": len(msgpack_data),
            "hybrid_size": len(hybrid_data),
            "encode_speedup": msgpack_encode_time / hybrid_encode_time,
            "decode_speedup": msgpack_decode_time / hybrid_decode_time,
            "size_ratio": len(hybrid_data) / len(msgpack_data),
        }


def demo_elegant_usage():
    """Demonstrate elegant usage of the hybrid protocol"""
    print("🎯 Elegant Hybrid Binary Protocol Demo")
    print("=" * 50)
    
    # Initialize protocol
    protocol = HybridProtocol()
    
    # Create realistic RPC data
    main_message = {
        "type": "method",
        "from": "workspace1/client1",
        "to": "workspace2/client2", 
        "method": "services.ai.train_model",
    }
    
    extra_data = {
        "args": [
            "model_config.json",
            {"epochs": 100, "batch_size": 32, "learning_rate": 0.001},
            list(range(1000)),  # Large array
        ],
        "with_kwargs": True,
    }
    
    print("📤 Encoding message...")
    encoded = protocol.encode_message(main_message, extra_data)
    print(f"   Encoded size: {len(encoded)} bytes")
    
    print("📥 Decoding message...")
    decoded_main, decoded_extra = protocol.decode_message(encoded)
    print(f"   Decoded main: {decoded_main}")
    print(f"   Decoded extra keys: {list(decoded_extra.keys())}")
    
    # Verify integrity
    assert decoded_main == main_message
    assert decoded_extra == extra_data
    print("✅ Data integrity verified!")
    
    print("\n🚀 Performance Comparison")
    benchmark = PerformanceBenchmark()
    results = benchmark.benchmark()
    
    print(f"Encoding Performance:")
    print(f"   Hybrid: {results['hybrid_encode']:.4f}s")
    print(f"   msgpack: {results['msgpack_encode']:.4f}s")
    print(f"   Speedup: {results['encode_speedup']:.2f}x")
    
    print(f"Decoding Performance:")
    print(f"   Hybrid: {results['hybrid_decode']:.4f}s") 
    print(f"   msgpack: {results['msgpack_decode']:.4f}s")
    print(f"   Speedup: {results['decode_speedup']:.2f}x")
    
    print(f"Size Efficiency:")
    print(f"   Hybrid: {results['hybrid_size']} bytes")
    print(f"   msgpack: {results['msgpack_size']} bytes")
    print(f"   Size ratio: {results['size_ratio']:.2f}")
    
    overall_speedup = (results['encode_speedup'] + results['decode_speedup']) / 2
    print(f"\n🏆 Overall Performance: {overall_speedup:.2f}x improvement")


if __name__ == "__main__":
    demo_elegant_usage() 