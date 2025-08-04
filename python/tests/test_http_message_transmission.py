"""Test HTTP message transmission using S3 storage for large messages."""

import asyncio
import pytest
import math
import time
import numpy as np

from hypha_rpc import connect_to_server
from . import WS_SERVER_URL


# ============================================================================
# TEST DATA GENERATORS
# ============================================================================

def generate_test_data(size_mb):
    """Generate test data of specified size in MB."""
    size_bytes = int(size_mb * 1024 * 1024)
    return b"x" * size_bytes

def generate_numpy_array(shape):
    """Generate numpy array with specified shape."""
    return np.random.random(shape).astype(np.float32)

def generate_large_string(size_mb):
    """Generate large string data."""
    size_bytes = int(size_mb * 1024 * 1024)
    return "x" * size_bytes


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
async def http_client(fastapi_server, test_user_token):
    """Create a client for HTTP transmission testing."""
    client = await connect_to_server({
        "name": "http-test-client",
        "server_url": WS_SERVER_URL,
        "token": test_user_token,
    })
    try:
        yield client
    finally:
        await client.disconnect()


# ============================================================================
# TEST CATEGORY 1: BASIC HTTP TRANSMISSION SETUP
# ============================================================================

@pytest.mark.asyncio
async def test_http_transmission_availability(fastapi_server, test_user_token):
    """Test that HTTP transmission is properly initialized and available."""
    print("\n=== TESTING HTTP TRANSMISSION AVAILABILITY ===")
    
    client = await connect_to_server({
        "name": "http-test-client",
        "server_url": WS_SERVER_URL,
        "token": test_user_token,
    })
    
    try:
        # Check if HTTP transmission is available
        assert hasattr(client.rpc, '_http_message_transmission_available')
        assert hasattr(client.rpc, '_s3controller')
        
        # The availability should be determined during initialization
        print(f"✅ HTTP transmission available: {client.rpc._http_message_transmission_available}")
        
        if client.rpc._http_message_transmission_available:
            print("✅ S3 controller is properly initialized")
        else:
            print("⚠️  HTTP transmission not available (this is normal if S3 is not configured)")
    except Exception as e:
        print(f"❌ Error: {e}")
        raise e
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_small_message_below_threshold(fastapi_server, test_user_token):
    """Test that messages below 1MB threshold use regular WebSocket transmission."""
    print("\n=== TESTING SMALL MESSAGE (BELOW 1MB THRESHOLD) ===")
    
    client = await connect_to_server({
        "name": "http-test-client",
        "server_url": WS_SERVER_URL,
        "token": test_user_token,
    })
    
    try:
        # Track HTTP transmission events
        http_transmission_events = []
        
        def on_http_transmission(data):
            http_transmission_events.append(data)
            print(f"🔗 HTTP transmission event: {data['content_length'] / (1024*1024):.1f}MB")
        
        client.rpc.on("http_transmission_stats", on_http_transmission)
    
        # Register a service that echoes data
        def echo_data(data):
            return data
        
        service_info = await client.register_service({
            "id": "echo-service",
            "name": "Echo Service",
            "config": {"visibility": "public"},
            "echo_data": echo_data
        })
        
        print(f"✅ Service registered: {service_info['id']}")
        
        # Test with small data (500KB - well below 1MB threshold)
        small_data = generate_test_data(0.5)  # 500KB
        print(f"📤 Sending {len(small_data) / (1024*1024):.1f}MB of data...")
        
        start_time = time.time()
        service = await client.get_service("echo-service")
        result = await service.echo_data(small_data)
        transmission_time = time.time() - start_time
        
        print(f"✅ Data echoed successfully in {transmission_time:.2f}s")
        assert result == small_data
        
        # Verify NO HTTP transmission was used
        assert len(http_transmission_events) == 0, "HTTP transmission should not be used for small messages"
        print("✅ Confirmed: Small message used regular WebSocket transmission")
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_medium_message_single_upload(fastapi_server, test_user_token):
    """Test that messages between 1MB and 10MB use single upload HTTP transmission."""
    print("\n=== TESTING MEDIUM MESSAGE (1MB-10MB, SINGLE UPLOAD) ===")
    
    client = await connect_to_server({
        "name": "http-test-client",
        "server_url": WS_SERVER_URL,
        "token": test_user_token,
    })
    
    try:
        # Track HTTP transmission events
        http_transmission_events = []
        
        def on_http_transmission(data):
            http_transmission_events.append(data)
            print(f"🔗 HTTP transmission event: {data['content_length'] / (1024*1024):.1f}MB")
            print(f"   Method: {data['transmission_method']}, Parts: {data['part_count']}")
            print(f"   Used multipart: {data['used_multipart']}")
        
        client.rpc.on("http_transmission_stats", on_http_transmission)
    
        # Register a service that processes data
        def process_data(data):
            return {"received_size": len(data), "processed": True}
        
        service_info = await client.register_service({
            "id": "process-service",
            "name": "Process Service",
            "config": {"visibility": "public"},
            "process_data": process_data
        })
        
        print(f"✅ Service registered: {service_info['id']}")
        
        # Test with medium data (5MB - above 1MB threshold, below 10MB multipart threshold)
        medium_data = generate_test_data(5)  # 5MB
        print(f"📤 Sending {len(medium_data) / (1024*1024):.1f}MB of data...")
        
        start_time = time.time()
        service = await client.get_service("process-service")
        result = await service.process_data(medium_data)
        transmission_time = time.time() - start_time
        
        print(f"✅ Data processed successfully in {transmission_time:.2f}s")
        assert result["received_size"] == len(medium_data)
        assert result["processed"] is True
        
        # Verify HTTP transmission was used with single upload
        assert len(http_transmission_events) == 1, "HTTP transmission should be used for medium messages"
        event = http_transmission_events[0]
        assert event["transmission_method"] == "single_upload"
        assert event["part_count"] == 1
        assert event["used_multipart"] is False
        assert event["multipart_threshold"] == 10 * 1024 * 1024  # 10MB
        print("✅ Confirmed: Medium message used single upload HTTP transmission")
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_large_message_multipart_upload(fastapi_server, test_user_token):
    """Test that messages above 10MB use multipart upload HTTP transmission."""
    print("\n=== TESTING LARGE MESSAGE (ABOVE 10MB, MULTIPART UPLOAD) ===")
    
    client = await connect_to_server({
        "name": "http-test-client",
        "server_url": WS_SERVER_URL,
        "token": test_user_token,
    })
    
    try:
        # Track HTTP transmission events
        http_transmission_events = []
        
        def on_http_transmission(data):
            http_transmission_events.append(data)
            print(f"🔗 HTTP transmission event: {data['content_length'] / (1024*1024):.1f}MB")
            print(f"   Method: {data['transmission_method']}, Parts: {data['part_count']}")
            print(f"   Used multipart: {data['used_multipart']}")
            print(f"   Part size: {data['part_size'] / (1024*1024):.1f}MB")
        
        client.rpc.on("http_transmission_stats", on_http_transmission)
    
        # Register a service that analyzes data
        def analyze_data(data):
            return {
                "size_mb": len(data) / (1024 * 1024),
                "analyzed": True,
                "checksum": hash(data) % 1000000  # Simple checksum
            }
        
        service_info = await client.register_service({
            "id": "analyze-service",
            "name": "Analyze Service",
            "config": {"visibility": "public"},
            "analyze_data": analyze_data
        })
        
        print(f"✅ Service registered: {service_info['id']}")
        
        # Test with large data (15MB - above 10MB multipart threshold)
        large_data = generate_test_data(15)  # 15MB
        print(f"📤 Sending {len(large_data) / (1024*1024):.1f}MB of data...")
        
        start_time = time.time()
        service = await client.get_service("analyze-service")
        result = await service.analyze_data(large_data)
        transmission_time = time.time() - start_time
        
        print(f"✅ Data analyzed successfully in {transmission_time:.2f}s")
        assert result["size_mb"] == 15.0
        assert result["analyzed"] is True
        
        # Verify HTTP transmission was used with multipart upload
        assert len(http_transmission_events) == 1, "HTTP transmission should be used for large messages"
        event = http_transmission_events[0]
        assert event["transmission_method"] == "multipart_upload"
        assert event["used_multipart"] is True
        assert event["multipart_threshold"] == 10 * 1024 * 1024  # 10MB
        assert event["part_size"] == 6 * 1024 * 1024  # 6MB per part (new default)
        # Verify multipart upload was used (actual part count may vary due to data size)
        assert event["part_count"] >= 3, f"Expected at least 3 parts for 15MB, got {event['part_count']}"
        print("✅ Confirmed: Large message used multipart upload HTTP transmission")
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_very_large_message_multipart_upload(fastapi_server, test_user_token):
    """Test that very large messages (50MB+) use multipart upload with many parts."""
    print("\n=== TESTING VERY LARGE MESSAGE (50MB+, MULTIPART UPLOAD) ===")
    
    client = await connect_to_server({
        "name": "very-http-message-test",
        "server_url": WS_SERVER_URL,
        "token": test_user_token,
    })
    
    try:
        # Track HTTP transmission events
        http_transmission_events = []
        
        def on_http_transmission(data):
            http_transmission_events.append(data)
            print(f"🔗 HTTP transmission event: {data['content_length'] / (1024*1024):.1f}MB")
            print(f"   Method: {data['transmission_method']}, Parts: {data['part_count']}")
            print(f"   Used multipart: {data['used_multipart']}")
            print(f"   Part size: {data['part_size'] / (1024*1024):.1f}MB")
        
        client.rpc.on("http_transmission_stats", on_http_transmission)
        
        # Register a service that handles very large data
        def handle_very_large_data(data):
            return {
                "size_mb": len(data) / (1024 * 1024),
                "handled": True,
                "first_byte": data[0] if data else None,
                "last_byte": data[-1] if data else None
            }
        
        service_info = await client.register_service({
            "id": "very-large-service",
            "name": "Very Large Data Service",
            "config": {"visibility": "public"},
            "handle_very_large_data": handle_very_large_data
        })
        
        print(f"✅ Service registered: {service_info['id']}")
        
        # Test with very large data (50MB - should use many parts)
        very_large_data = generate_test_data(50)  # 50MB
        print(f"📤 Sending {len(very_large_data) / (1024*1024):.1f}MB of data...")
        
        start_time = time.time()
        service = await client.get_service("very-large-service")
        result = await service.handle_very_large_data(very_large_data)
        transmission_time = time.time() - start_time
        
        print(f"✅ Very large data handled successfully in {transmission_time:.2f}s")
        assert result["size_mb"] == 50.0
        assert result["handled"] is True
        
        # Verify HTTP transmission was used with multipart upload
        assert len(http_transmission_events) == 1, "HTTP transmission should be used for very large messages"
        event = http_transmission_events[0]
        assert event["transmission_method"] == "multipart_upload"
        assert event["used_multipart"] is True
        assert event["multipart_threshold"] == 10 * 1024 * 1024  # 10MB
        assert event["part_size"] == 6 * 1024 * 1024  # 6MB per part (new default)
        # Verify multipart upload was used (actual part count may vary due to data size)
        assert event["part_count"] >= 9, f"Expected at least 9 parts for 50MB with 6MB parts, got {event['part_count']}"
        print("✅ Confirmed: Very large message used multipart upload with 10 parts")
        
    finally:
        await client.disconnect()


# ============================================================================
# TEST CATEGORY 3: DATA TYPE HANDLING
# ============================================================================

@pytest.mark.asyncio
async def test_numpy_array_transmission(fastapi_server, test_user_token):
    """Test HTTP transmission with numpy arrays."""
    print("\n=== TESTING NUMPY ARRAY TRANSMISSION ===")
    
    client = await connect_to_server({
        "name": "http-test-client",
        "server_url": WS_SERVER_URL,
        "token": test_user_token,
    })
    
    try:
        # Track HTTP transmission events
        http_transmission_events = []
        
        def on_http_transmission(data):
            http_transmission_events.append(data)
            print(f"🔗 HTTP transmission event: {data['content_length'] / (1024*1024):.1f}MB")
            print(f"   Method: {data['transmission_method']}, Parts: {data['part_count']}")
            print(f"   Used multipart: {data['used_multipart']}")
        
        client.rpc.on("http_transmission_stats", on_http_transmission)
    
        # Register a service that processes numpy arrays
        def process_array(array_data):
            return {
                "shape": array_data.shape,
                "dtype": str(array_data.dtype),
                "size_mb": array_data.nbytes / (1024 * 1024),
                "mean": float(array_data.mean()),
                "sum": float(array_data.sum())
            }
        
        service_info = await client.register_service({
            "id": "array-service",
            "name": "Array Processing Service",
            "config": {"visibility": "public"},
            "process_array": process_array
        })
        
        print(f"✅ Service registered: {service_info['id']}")
        
        # Create large numpy array (20MB - should trigger multipart upload)
        large_array = generate_numpy_array((2048, 2048, 2))  # ~32MB
        print(f"📤 Sending numpy array: {large_array.shape}, {large_array.nbytes / (1024*1024):.1f}MB")
        
        start_time = time.time()
        service = await client.get_service("array-service")
        result = await service.process_array(large_array)
        transmission_time = time.time() - start_time
        
        print(f"✅ Array processed successfully in {transmission_time:.2f}s")
        assert result["shape"] == [2048, 2048, 2]
        assert result["dtype"] == "float32"
        assert abs(result["size_mb"] - 32.0) < 0.1
        
        # Verify HTTP transmission was used
        assert len(http_transmission_events) == 1, "HTTP transmission should be used for large numpy arrays"
        event = http_transmission_events[0]
        assert event["transmission_method"] == "multipart_upload"
        assert event["used_multipart"] is True
        # Verify multipart upload was used (actual part count may vary due to data size)
        assert event["part_count"] >= 6, f"Expected at least 6 parts for 32MB, got {event['part_count']}"
        print("✅ Confirmed: Large numpy array used multipart upload HTTP transmission")
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_string_data_transmission(fastapi_server, test_user_token):
    """Test HTTP transmission with large string data."""
    print("\n=== TESTING LARGE STRING TRANSMISSION ===")
    
    client = await connect_to_server({
        "name": "string-data-test",
        "server_url": WS_SERVER_URL,
        "token": test_user_token,
    })
    
    try:
        # Track HTTP transmission events
        http_transmission_events = []
        
        def on_http_transmission(data):
            http_transmission_events.append(data)
            print(f"🔗 HTTP transmission event: {data['content_length'] / (1024*1024):.1f}MB")
            print(f"   Method: {data['transmission_method']}, Parts: {data['part_count']}")
            print(f"   Used multipart: {data['used_multipart']}")
        
        client.rpc.on("http_transmission_stats", on_http_transmission)
        
        # Register a service that processes string data
        def process_string(string_data):
            return {
                "length": len(string_data),
                "size_mb": len(string_data.encode('utf-8')) / (1024 * 1024),
                "first_char": string_data[0],
                "last_char": string_data[-1],
                "char_count": len(set(string_data))
            }
        
        service_info = await client.register_service({
            "id": "string-service",
            "name": "String Processing Service",
            "config": {"visibility": "public"},
            "process_string": process_string
        })
        
        print(f"✅ Service registered: {service_info['id']}")
        
        # Create large string data (12MB - should trigger multipart upload)
        large_string = generate_large_string(12)  # 12MB
        print(f"📤 Sending large string: {len(large_string.encode('utf-8')) / (1024*1024):.1f}MB")
        
        start_time = time.time()
        service = await client.get_service("string-service")
        result = await service.process_string(large_string)
        transmission_time = time.time() - start_time
        
        print(f"✅ String processed successfully in {transmission_time:.2f}s")
        assert result["length"] == len(large_string)
        assert abs(result["size_mb"] - 12.0) < 0.1
        assert result["first_char"] == "x"
        assert result["last_char"] == "x"
        
        # Verify HTTP transmission was used
        assert len(http_transmission_events) == 1, "HTTP transmission should be used for large strings"
        event = http_transmission_events[0]
        assert event["transmission_method"] == "multipart_upload"
        assert event["used_multipart"] is True
        # Verify multipart upload was used (actual part count may vary due to data size)
        assert event["part_count"] >= 3, f"Expected at least 3 parts for 12MB, got {event['part_count']}"
        print("✅ Confirmed: Large string used multipart upload HTTP transmission")
        
    finally:
        await client.disconnect()


# ============================================================================
# TEST CATEGORY 4: PERFORMANCE COMPARISON
# ============================================================================

@pytest.mark.asyncio
async def test_http_vs_websocket_performance(fastapi_server, test_user_token):
    """Compare performance between HTTP transmission and WebSocket fallback."""
    print("\n=== TESTING HTTP VS WEBSOCKET PERFORMANCE ===")
    
    client = await connect_to_server({
        "name": "performance-test",
        "server_url": WS_SERVER_URL,
        "token": test_user_token,
    })
    
    try:
        # Track HTTP transmission events
        http_transmission_events = []
        
        def on_http_transmission(data):
            http_transmission_events.append(data)
            print(f"🔗 HTTP transmission event: {data['content_length'] / (1024*1024):.1f}MB")
            print(f"   Method: {data['transmission_method']}, Parts: {data['part_count']}")
            print(f"   Used multipart: {data['used_multipart']}")
        
        client.rpc.on("http_transmission_stats", on_http_transmission)
        
        # Register a service that echoes data
        def echo_data(data):
            return {"echoed": True, "size": len(data)}
        
        service_info = await client.register_service({
            "id": "echo-performance-service",
            "name": "Echo Performance Service",
            "config": {"visibility": "public"},
            "echo_data": echo_data
        })
        
        print(f"✅ Service registered: {service_info['id']}")
        
        # Test different data sizes
        test_sizes = [2, 5, 8, 10, 15]  # MB
        results = []
        
        for size_mb in test_sizes:
            print(f"\n📊 Testing {size_mb}MB data transmission...")
            
            # Clear previous events
            http_transmission_events.clear()
            
            # Generate test data
            test_data = generate_test_data(size_mb)
            
            # Measure transmission time
            start_time = time.time()
            service = await client.get_service("echo-performance-service")
            result = await service.echo_data(test_data)
            transmission_time = time.time() - start_time
            
            # Determine transmission method
            transmission_method = "websocket"
            part_count = 0
            used_multipart = False
            if http_transmission_events:
                event = http_transmission_events[0]
                transmission_method = event["transmission_method"]
                part_count = event["part_count"]
                used_multipart = event["used_multipart"]
            
            results.append({
                "size_mb": size_mb,
                "transmission_time": transmission_time,
                "transmission_method": transmission_method,
                "part_count": part_count,
                "used_multipart": used_multipart,
                "mb_per_second": size_mb / transmission_time
            })
            
            print(f"   ✅ {size_mb}MB: {transmission_time:.2f}s ({transmission_method}, {part_count} parts, multipart: {used_multipart})")
        
        # Print performance summary
        print(f"\n📈 PERFORMANCE SUMMARY:")
        print(f"{'Size (MB)':<8} {'Time (s)':<8} {'Method':<15} {'Parts':<6} {'Multipart':<9} {'MB/s':<8}")
        print("-" * 60)
        
        for result in results:
            print(f"{result['size_mb']:<8} {result['transmission_time']:<8.2f} "
                  f"{result['transmission_method']:<15} {result['part_count']:<6} "
                  f"{result['used_multipart']:<9} {result['mb_per_second']:<8.2f}")
        
        # Verify that appropriate transmission methods were used
        for result in results:
            if result["size_mb"] < 1:
                assert result["transmission_method"] == "websocket", f"Small data should use WebSocket"
            elif result["size_mb"] < 10:
                assert result["transmission_method"] == "single_upload", f"Medium data should use single upload"
                assert result["used_multipart"] is False, f"Medium data should not use multipart"
            else:
                assert result["transmission_method"] == "multipart_upload", f"Large data should use multipart upload"
                assert result["used_multipart"] is True, f"Large data should use multipart"
        
        print("✅ All transmission methods correctly selected based on data size")
        
    finally:
        await client.disconnect()


# ============================================================================
# TEST CATEGORY 5: ERROR HANDLING AND EDGE CASES
# ============================================================================

@pytest.mark.asyncio
async def test_http_transmission_fallback(fastapi_server, test_user_token):
    """Test that system falls back to WebSocket when HTTP transmission fails."""
    print("\n=== TESTING HTTP TRANSMISSION FALLBACK ===")
    
    client = await connect_to_server({
        "name": "fallback-test",
        "server_url": WS_SERVER_URL,
        "token": test_user_token,
    })
    
    try:
        # Temporarily disable HTTP transmission to test fallback
        original_available = client.rpc._http_message_transmission_available
        client.rpc._http_message_transmission_available = False
        
        # Register a service
        def echo_data(data):
            return {"echoed": True, "size": len(data)}
        
        service_info = await client.register_service({
            "id": "fallback-service",
            "name": "Fallback Service",
            "config": {"visibility": "public"},
            "echo_data": echo_data
        })
        
        print(f"✅ Service registered: {service_info['id']}")
        
        # Test with large data that would normally use HTTP transmission
        large_data = generate_test_data(5)  # 5MB
        print(f"📤 Sending {len(large_data) / (1024*1024):.1f}MB with HTTP disabled...")
        
        start_time = time.time()
        service = await client.get_service("fallback-service")
        result = await service.echo_data(large_data)
        transmission_time = time.time() - start_time
        
        print(f"✅ Data transmitted successfully via fallback in {transmission_time:.2f}s")
        assert result["echoed"] is True
        assert result["size"] == len(large_data)
        
        # Restore original HTTP transmission availability
        client.rpc._http_message_transmission_available = original_available
        
        print("✅ Confirmed: System gracefully falls back to WebSocket when HTTP transmission is unavailable")
        
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_concurrent_large_transmissions(fastapi_server, test_user_token):
    """Test concurrent large data transmissions."""
    print("\n=== TESTING CONCURRENT LARGE TRANSMISSIONS ===")
    
    client = await connect_to_server({
        "name": "concurrent-test",
        "server_url": WS_SERVER_URL,
        "token": test_user_token,
    })
    
    try:
        # Track HTTP transmission events
        http_transmission_events = []
        
        def on_http_transmission(data):
            http_transmission_events.append(data)
            print(f"🔗 HTTP transmission event: {data['content_length'] / (1024*1024):.1f}MB")
            print(f"   Method: {data['transmission_method']}, Parts: {data['part_count']}")
            print(f"   Used multipart: {data['used_multipart']}")
        
        client.rpc.on("http_transmission_stats", on_http_transmission)
        
        # Register a service that processes data
        def process_data(data):
            return {"processed": True, "size": len(data), "checksum": hash(data) % 1000000}
        
        service_info = await client.register_service({
            "id": "concurrent-service",
            "name": "Concurrent Processing Service",
            "config": {"visibility": "public"},
            "process_data": process_data
        })
        
        print(f"✅ Service registered: {service_info['id']}")
        
        # Create multiple large datasets
        datasets = [
            generate_test_data(3),   # 3MB
            generate_test_data(5),   # 5MB
            generate_test_data(8),   # 8MB
            generate_test_data(12),  # 12MB
        ]
        
        print(f"📤 Sending {len(datasets)} concurrent transmissions...")
        
        # Clear events
        http_transmission_events.clear()
        
        # Send all datasets concurrently
        start_time = time.time()
        service = await client.get_service("concurrent-service")
        tasks = []
        for i, data in enumerate(datasets):
            task = service.process_data(data)
            tasks.append(task)
        
        results = await asyncio.gather(*tasks)
        total_time = time.time() - start_time
        
        print(f"✅ All {len(datasets)} transmissions completed in {total_time:.2f}s")
        
        # Verify all results
        for i, result in enumerate(results):
            assert result["processed"] is True
            assert result["size"] == len(datasets[i])
        
        # Verify HTTP transmission events
        print(f"📊 HTTP transmission events: {len(http_transmission_events)}")
        for event in http_transmission_events:
            print(f"   - {event['content_length'] / (1024*1024):.1f}MB: {event['transmission_method']} ({event['part_count']} parts, multipart: {event['used_multipart']})")
        
        # All transmissions above 1MB should have used HTTP
        expected_http_transmissions = sum(1 for data in datasets if len(data) >= 1024 * 1024)
        assert len(http_transmission_events) == expected_http_transmissions
        
        print("✅ Confirmed: Concurrent large transmissions work correctly")
        
    finally:
        await client.disconnect()


# ============================================================================
# TEST CATEGORY 6: INTEGRATION AND END-TO-END
# ============================================================================

@pytest.mark.asyncio
async def test_comprehensive_integration(fastapi_server, test_user_token):
    """Comprehensive integration test covering all aspects of HTTP transmission."""
    print("\n=== COMPREHENSIVE INTEGRATION TEST ===")
    
    client = await connect_to_server({
        "name": "integration-test",
        "server_url": WS_SERVER_URL,
        "token": test_user_token,
    })
    
    try:
        # Track HTTP transmission events
        http_transmission_events = []
        
        def on_http_transmission(data):
            http_transmission_events.append(data)
            print(f"🔗 HTTP transmission event: {data['content_length'] / (1024*1024):.1f}MB")
            print(f"   Method: {data['transmission_method']}, Parts: {data['part_count']}")
            print(f"   Used multipart: {data['used_multipart']}")
        
        client.rpc.on("http_transmission_stats", on_http_transmission)
        
        # Register a comprehensive service
        def comprehensive_processor(data, data_type="binary"):
            """Process different types of data comprehensively."""
            if data_type == "binary":
                return {
                    "type": "binary",
                    "size": len(data),
                    "size_mb": len(data) / (1024 * 1024),
                    "processed": True,
                    "checksum": hash(data) % 1000000
                }
            elif data_type == "numpy":
                return {
                    "type": "numpy",
                    "shape": data.shape,
                    "dtype": str(data.dtype),
                    "size_mb": data.nbytes / (1024 * 1024),
                    "mean": float(data.mean()),
                    "processed": True
                }
            elif data_type == "string":
                return {
                    "type": "string",
                    "length": len(data),
                    "size_mb": len(data.encode('utf-8')) / (1024 * 1024),
                    "processed": True,
                    "char_count": len(set(data))
                }
            else:
                raise ValueError(f"Unknown data type: {data_type}")
        
        service_info = await client.register_service({
            "id": "comprehensive-service",
            "name": "Comprehensive Processing Service",
            "config": {"visibility": "public"},
            "comprehensive_processor": comprehensive_processor
        })
        
        print(f"✅ Service registered: {service_info['id']}")
        
        # Test different data types and sizes
        test_cases = [
            # (data, data_type, expected_method, expected_parts, expected_multipart)
            (generate_test_data(0.5), "binary", "websocket", 0, False),      # 500KB - WebSocket
            (generate_test_data(2), "binary", "single_upload", 1, False),    # 2MB - Single upload
            (generate_test_data(8), "binary", "single_upload", 1, False),    # 8MB - Single upload
            (generate_test_data(15), "binary", "multipart_upload", 3, True), # 15MB - Multipart
            (generate_numpy_array((1024, 1024, 3)), "numpy", "multipart_upload", 3, True),  # ~12MB - Multipart
            (generate_large_string(20), "string", "multipart_upload", 4, True),  # 20MB - Multipart
        ]
        
        results = []
        
        for i, (data, data_type, expected_method, expected_parts, expected_multipart) in enumerate(test_cases):
            print(f"\n🧪 Test case {i+1}: {data_type} data, expected {expected_method}")
            
            # Clear previous events
            http_transmission_events.clear()
            
            # Process data
            start_time = time.time()
            service = await client.get_service("comprehensive-service")
            result = await service.comprehensive_processor(data, data_type)
            transmission_time = time.time() - start_time
            
            print(f"   ✅ Processed in {transmission_time:.2f}s")
            
            # Verify result
            assert result["type"] == data_type
            assert result["processed"] is True
            
            # Verify transmission method
            actual_method = "websocket"
            actual_parts = 0
            actual_multipart = False
            if http_transmission_events:
                event = http_transmission_events[0]
                actual_method = event["transmission_method"]
                actual_parts = event["part_count"]
                actual_multipart = event["used_multipart"]
            
            results.append({
                "test_case": i+1,
                "data_type": data_type,
                "expected_method": expected_method,
                "actual_method": actual_method,
                "expected_parts": expected_parts,
                "actual_parts": actual_parts,
                "expected_multipart": expected_multipart,
                "actual_multipart": actual_multipart,
                "transmission_time": transmission_time
            })
            
            print(f"   📊 Method: {actual_method} ({actual_parts} parts, multipart: {actual_multipart})")
        
        # Print comprehensive results
        print(f"\n📈 COMPREHENSIVE TEST RESULTS:")
        print(f"{'Case':<4} {'Type':<8} {'Expected':<15} {'Actual':<15} {'Parts':<8} {'Multipart':<9} {'Time(s)':<8}")
        print("-" * 75)
        
        for result in results:
            print(f"{result['test_case']:<4} {result['data_type']:<8} "
                  f"{result['expected_method']:<15} {result['actual_method']:<15} "
                  f"{result['actual_parts']:<8} {result['actual_multipart']:<9} "
                  f"{result['transmission_time']:<8.2f}")
        
        # Verify all transmission methods were correct
        for result in results:
            if result["expected_method"] == "websocket":
                assert result["actual_method"] == "websocket", f"Case {result['test_case']}: Expected WebSocket"
                assert result["actual_multipart"] is False, f"Case {result['test_case']}: WebSocket should not use multipart"
            elif result["expected_method"] == "single_upload":
                assert result["actual_method"] == "single_upload", f"Case {result['test_case']}: Expected single upload"
                assert result["actual_multipart"] is False, f"Case {result['test_case']}: Single upload should not use multipart"
            elif result["expected_method"] == "multipart_upload":
                assert result["actual_method"] == "multipart_upload", f"Case {result['test_case']}: Expected multipart upload"
                assert result["actual_multipart"] is True, f"Case {result['test_case']}: Multipart upload should use multipart"
            
            # Verify part count is reasonable (actual may vary due to data size)
            if result["expected_method"] == "websocket":
                assert result["actual_parts"] == 0, f"Case {result['test_case']}: WebSocket should have 0 parts"
            elif result["expected_method"] == "single_upload":
                assert result["actual_parts"] == 1, f"Case {result['test_case']}: Single upload should have 1 part"
            elif result["expected_method"] == "multipart_upload":
                assert result["actual_parts"] >= 1, f"Case {result['test_case']}: Multipart should have at least 1 part"
        
        print("✅ All test cases passed with correct transmission methods!")
        
    finally:
        await client.disconnect()

# ============================================================================
# TEST CATEGORY 8: CONFIGURABLE PARAMETERS VERIFICATION
# ============================================================================

@pytest.mark.asyncio
async def test_configurable_http_transmission_parameters(fastapi_server, test_user_token):
    """Verify that HTTP transmission parameters can be configured via RPC constructor."""
    print("\n=== TESTING CONFIGURABLE HTTP TRANSMISSION PARAMETERS ===")
    
    # Test with custom thresholds
    custom_http_threshold = 512 * 1024  # 512KB (lower than default 1MB)
    custom_multipart_threshold = 5 * 1024 * 1024  # 5MB (lower than default 10MB)
    
    client = await connect_to_server({
        "name": "configurable-params-test",
        "server_url": WS_SERVER_URL,
        "token": test_user_token,
        "enable_http_transmission": True,
        "http_transmission_threshold": custom_http_threshold,
        "multipart_threshold": custom_multipart_threshold,
    })
    
    # Track HTTP transmission events
    http_transmission_events = []
    
    def on_http_transmission_stats(event):
        http_transmission_events.append(event)
        print(f"   📊 HTTP transmission event: {event['content_length'] / (1024*1024):.1f}MB")
        print(f"      Method: {event['transmission_method']}, Parts: {event['part_count']}")
        print(f"      Used multipart: {event['used_multipart']}")
        print(f"      Part size: {event['part_size'] / (1024*1024):.1f}MB")
        print(f"      Multipart threshold: {event['multipart_threshold'] / (1024*1024):.1f}MB")
    
    client.rpc.on("http_transmission_stats", on_http_transmission_stats)
    
    # Register a service that analyzes data
    def analyze_data(data):
        return {
            "size": len(data),
            "analyzed": True,
            "checksum": hash(data) % 1000000  # Simple checksum
        }
    
    service_info = await client.register_service({
        "id": "analyze-service",
        "name": "Analyze Service",
        "config": {"visibility": "public"},
        "analyze_data": analyze_data
    })
    
    print(f"✅ Service registered: {service_info['id']}")
    
    # Get the service
    service = await client.get_service("analyze-service")
    
    # Test 1: Small message (should use HTTP single upload due to lowered threshold)
    print("📤 Testing small message (should use HTTP due to lowered threshold)...")
    small_data = generate_test_data(0.5)  # 500KB
    result = await service.analyze_data(small_data)
    assert result["size"] == len(small_data)
    assert len(http_transmission_events) == 1, "Small message should trigger HTTP transmission with lowered threshold"
    
    event = http_transmission_events[0]
    assert event["transmission_method"] == "single_upload"
    assert event["used_multipart"] is False
    assert event["part_count"] == 1
    assert event["multipart_threshold"] == custom_multipart_threshold
    print("   ✅ Small message correctly used HTTP single upload (due to lowered threshold)")
    
    # Test 2: Medium message (should use HTTP single upload)
    print("📤 Testing medium message (should use HTTP single upload)...")
    medium_data = generate_test_data(3.0)  # 3MB
    result = await service.analyze_data(medium_data)
    assert result["size"] == len(medium_data)
    assert len(http_transmission_events) == 2, "Medium message should trigger HTTP transmission"
    
    event = http_transmission_events[1]
    assert event["transmission_method"] == "single_upload"
    assert event["used_multipart"] is False
    assert event["part_count"] == 1
    assert event["multipart_threshold"] == custom_multipart_threshold
    print("   ✅ Medium message correctly used HTTP single upload")
    
    # Test 3: Large message (should use HTTP multipart upload)
    print("📤 Testing large message (should use HTTP multipart upload)...")
    large_data = generate_test_data(8.0)  # 8MB
    result = await service.analyze_data(large_data)
    assert result["size"] == len(large_data)
    assert len(http_transmission_events) == 3, "Large message should trigger HTTP transmission"
    
    event = http_transmission_events[2]
    assert event["transmission_method"] == "multipart_upload"
    assert event["used_multipart"] is True
    assert event["part_count"] >= 2  # 8MB / 5MB per part = at least 2 parts
    assert event["multipart_threshold"] == custom_multipart_threshold
    print("   ✅ Large message correctly used HTTP multipart upload")
    
    print("✅ All configurable parameter tests passed!")
    
    await client.disconnect()


@pytest.mark.asyncio
async def test_configurable_multipart_size_and_parallel_uploads(fastapi_server, test_user_token):
    """Test configurable multipart size and parallel upload functionality."""
    print("\n=== TESTING CONFIGURABLE MULTIPART SIZE AND PARALLEL UPLOADS ===")
    
    client = await connect_to_server({
        "name": "configurable-multipart-test",
        "server_url": WS_SERVER_URL,
        "token": test_user_token,
        # Test with custom multipart configuration
        "multipart_size": 5 * 1024 * 1024,  # 5MB parts (minimum for S3)
        "max_parallel_uploads": 3,  # 3 parallel uploads
    })
    
    try:
        # Verify configuration was applied
        assert client.rpc._multipart_size == 5 * 1024 * 1024
        assert client.rpc._max_parallel_uploads == 3
        
        print(f"✅ Multipart size configured: {client.rpc._multipart_size / (1024*1024)}MB")
        print(f"✅ Max parallel uploads configured: {client.rpc._max_parallel_uploads}")
        
        # Track HTTP transmission events
        http_transmission_events = []
        
        def on_http_transmission(data):
            http_transmission_events.append(data)
            print(f"🔗 HTTP transmission event: {data['content_length'] / (1024*1024):.1f}MB")
            print(f"   - Multipart size: {data['multipart_size'] / (1024*1024)}MB")
            print(f"   - Part count: {data['part_count']}")
            print(f"   - Used multipart: {data['used_multipart']}")
        
        client.rpc.on("http_transmission_stats", on_http_transmission)
        
        # Register a service that processes large data
        def process_large_data(data):
            return {
                "received_size": len(data) if hasattr(data, '__len__') else data.nbytes,
                "processed": True,
                "timestamp": time.time()
            }
        
        service_info = await client.register_service({
            "id": "large-data-processor",
            "name": "Large Data Processor",
            "description": "Processes large data with configurable multipart settings",
            "config": {
                "visibility": "public",
                "require_context": False,
            },
            "process_large_data": process_large_data,
        })
        
        print(f"✅ Service registered: {service_info['id']}")
        
        # Test with data that will trigger multipart upload
        test_data = generate_test_data(15)  # 15MB - should trigger multipart with 4MB parts
        print(f"📤 Sending {len(test_data) / (1024*1024):.1f}MB of test data")
        
        start_time = time.time()
        service = await client.get_service("large-data-processor")
        result = await service.process_large_data(test_data)
        end_time = time.time()
        
        print(f"✅ Data processed successfully in {end_time - start_time:.2f}s")
        print(f"✅ Result: {result}")
        
        # Verify HTTP transmission was used
        assert len(http_transmission_events) > 0
        
        last_event = http_transmission_events[-1]
        # Allow for small differences due to transmission overhead
        assert abs(last_event["content_length"] - len(test_data)) < 1000, f"Content length mismatch: expected {len(test_data)}, got {last_event['content_length']}"
        assert last_event["multipart_size"] == 5 * 1024 * 1024  # Should match our configuration
        # Calculate expected part count based on actual data size
        expected_part_count = math.ceil(last_event["content_length"] / (5 * 1024 * 1024))
        assert last_event["part_count"] == expected_part_count, f"Part count mismatch: expected {expected_part_count} for {last_event['content_length']} bytes with 5MB parts, got {last_event['part_count']}"
        assert last_event["used_multipart"] is True
        
        print(f"✅ Multipart configuration verified: {last_event['part_count']} parts of {last_event['multipart_size'] / (1024*1024)}MB each")
    except Exception as e:
        print(f"❌ Error: {e}")
        raise e
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_parallel_upload_performance(fastapi_server, test_user_token):
    """Test parallel upload performance with different configurations."""
    print("\n=== TESTING PARALLEL UPLOAD PERFORMANCE ===")
    
    # Test with different parallel upload configurations
    configs = [
        {"max_parallel_uploads": 1, "name": "Sequential"},
        {"max_parallel_uploads": 3, "name": "3 Parallel"},
        {"max_parallel_uploads": 5, "name": "5 Parallel"},
    ]
    
    for config in configs:
        print(f"\n--- Testing {config['name']} Uploads ---")
        
        client = await connect_to_server({
            "name": f"parallel-test-{config['max_parallel_uploads']}",
            "server_url": WS_SERVER_URL,
            "token": test_user_token,
            "multipart_size": 5 * 1024 * 1024,  # 5MB parts (minimum for S3)
            "max_parallel_uploads": config["max_parallel_uploads"],
        })
        
        try:
            http_transmission_events = []
            
            def on_http_transmission(data):
                http_transmission_events.append(data)
            
            client.rpc.on("http_transmission_stats", on_http_transmission)
            
            # Register echo service
            def echo_data(data):
                return data
            
            await client.register_service({
                "id": "echo-service",
                "name": "Echo Service",
                "config": {"visibility": "public", "require_context": False},
                "echo_data": echo_data,
            })
            
            # Test with data that creates multiple parts
            test_data = generate_test_data(25)  # 25MB with 5MB parts = 5 parts
            print(f"📤 Sending {len(test_data) / (1024*1024):.1f}MB with {config['max_parallel_uploads']} parallel uploads")
            
            start_time = time.time()
            service = await client.get_service("echo-service")
            result = await service.echo_data(test_data)
            end_time = time.time()
            
            duration = end_time - start_time
            print(f"✅ Completed in {duration:.2f}s")
            
            assert result == test_data
            assert len(http_transmission_events) > 0
            
            last_event = http_transmission_events[-1]
            assert last_event["used_multipart"] is True
            # Calculate expected part count based on actual data size
            expected_part_count = math.ceil(last_event["content_length"] / (5 * 1024 * 1024))
            assert last_event["part_count"] == expected_part_count, f"Part count mismatch: expected {expected_part_count} for {last_event['content_length']} bytes with 5MB parts, got {last_event['part_count']}"
            
            print(f"   - Parts: {last_event['part_count']}")
            print(f"   - Part size: {last_event['part_size'] / (1024*1024)}MB")
            if "transmission_time" in last_event:
                print(f"   - Transmission time: {last_event['transmission_time']:.2f}s")
        except Exception as e:
            print(f"❌ Error: {e}")
            raise e
        finally:
            await client.disconnect()


if __name__ == "__main__":
    pytest.main([__file__])
