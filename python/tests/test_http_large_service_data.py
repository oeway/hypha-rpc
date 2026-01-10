"""Test HTTP transmission with large datasets in service registration and calling.

NOTE: These tests use a module-scoped isolated server (http_test_server) to prevent
HTTP transmission operations from affecting other test modules.
"""

import asyncio
import pytest
import time
import numpy as np

from hypha_rpc import connect_to_server
from .conftest import HTTP_TEST_PORT


# Use isolated server URL for HTTP tests
HTTP_SERVER_URL = f"http://127.0.0.1:{HTTP_TEST_PORT}"


def generate_large_dataset(size_mb):
    """Generate large test dataset."""
    size_bytes = int(size_mb * 1024 * 1024)
    return b"x" * size_bytes


def generate_large_numpy_dataset(shape):
    """Generate large numpy dataset."""
    return np.random.random(shape).astype(np.float32)


@pytest.mark.asyncio
async def test_large_dataset_service_workflow(http_test_server, http_test_user_token):
    """Test complete workflow with large datasets: register service and call with large data."""
    print("\n=== TESTING LARGE DATASET SERVICE WORKFLOW ===")
    
    # Connect client
    client = await connect_to_server({
        "name": "large-dataset-client",
        "server_url": HTTP_SERVER_URL,
        "token": http_test_user_token,
    })
    
    try:
        # Track HTTP transmission events
        http_transmission_events = []
        
        def on_http_transmission(data):
            http_transmission_events.append(data)
            print(f"🔗 HTTP transmission: {data['content_length'] / (1024*1024):.1f}MB "
                  f"via {data['transmission_method']} "
                  f"({'multipart' if data['used_multipart'] else 'single'} upload)")
        
        client.rpc.on("http_transmission_stats", on_http_transmission)
        
        # Register a data processing service
        def process_large_data(data, operation="analyze"):
            """Process large data and return analysis."""
            if isinstance(data, np.ndarray):
                return {
                    "operation": operation,
                    "data_type": "numpy",
                    "shape": data.shape,
                    "dtype": str(data.dtype),
                    "size_mb": data.nbytes / (1024 * 1024),
                    "mean": float(data.mean()),
                    "std": float(data.std()),
                    "min": float(data.min()),
                    "max": float(data.max()),
                    "processed": True
                }
            else:
                return {
                    "operation": operation,
                    "data_type": "bytes",
                    "size_mb": len(data) / (1024 * 1024),
                    "first_bytes": data[:10].hex() if len(data) >= 10 else data.hex(),
                    "last_bytes": data[-10:].hex() if len(data) >= 10 else data.hex(),
                    "processed": True
                }
        
        def transform_data(data, factor=2.0):
            """Transform data by applying operations."""
            if isinstance(data, np.ndarray):
                transformed = data * factor
                return {
                    "transformed": transformed,
                    "original_shape": data.shape,
                    "factor": factor,
                    "transformed_mean": float(transformed.mean()),
                    "size_mb": transformed.nbytes / (1024 * 1024)
                }
            else:
                # For bytes data, duplicate it
                transformed = data * int(factor)
                return {
                    "original_size_mb": len(data) / (1024 * 1024),
                    "transformed_size_mb": len(transformed) / (1024 * 1024),
                    "factor": factor,
                    "sample": transformed[:50].hex() if len(transformed) >= 50 else transformed.hex()
                }
        
        # Register the service
        service_info = await client.register_service({
            "id": "large-data-processor",
            "name": "Large Data Processing Service",
            "description": "Service for processing large datasets with HTTP transmission",
            "config": {"visibility": "public"},
            "process_large_data": process_large_data,
            "transform_data": transform_data
        })
        
        print(f"✅ Service registered: {service_info['id']}")
        
        # Test 1: Process medium-sized binary data (5MB - single upload)
        print(f"\n--- Test 1: Medium binary data (5MB) ---")
        http_transmission_events.clear()
        
        medium_data = generate_large_dataset(5)  # 5MB
        print(f"📤 Processing {len(medium_data) / (1024*1024):.1f}MB binary data...")
        
        start_time = time.time()
        service = await client.get_service("large-data-processor")
        result = await service.process_large_data(medium_data, operation="analyze")
        processing_time = time.time() - start_time
        
        print(f"✅ Processed in {processing_time:.2f}s")
        print(f"   Result: {result['data_type']} data, {result['size_mb']:.1f}MB processed")
        assert result["processed"] is True
        assert result["size_mb"] == 5.0
        
        # Verify HTTP transmission was used
        assert len(http_transmission_events) == 1
        assert http_transmission_events[0]["transmission_method"] == "single_upload"
        
        # Test 2: Process large numpy array (20MB - multipart upload)
        print(f"\n--- Test 2: Large numpy array (20MB) ---")
        http_transmission_events.clear()
        
        large_array = generate_large_numpy_dataset((1024, 1024, 5))  # ~20MB
        print(f"📤 Processing {large_array.nbytes / (1024*1024):.1f}MB numpy array...")
        
        start_time = time.time()
        result = await service.process_large_data(large_array, operation="statistical_analysis")
        processing_time = time.time() - start_time
        
        print(f"✅ Processed in {processing_time:.2f}s")
        print(f"   Result: {result['data_type']} array shape {result['shape']}, {result['size_mb']:.1f}MB")
        print(f"   Stats: mean={result['mean']:.3f}, std={result['std']:.3f}")
        assert result["processed"] is True
        assert result["data_type"] == "numpy"
        
        # Verify HTTP transmission was used with multipart
        assert len(http_transmission_events) == 1
        assert http_transmission_events[0]["transmission_method"] == "multipart_upload"
        assert http_transmission_events[0]["used_multipart"] is True
        
        # Test 3: Transform data and return large result (bidirectional large data)
        print(f"\n--- Test 3: Bidirectional large data transformation ---")
        http_transmission_events.clear()
        
        input_array = generate_large_numpy_dataset((512, 512, 10))  # ~10MB
        print(f"📤 Transforming {input_array.nbytes / (1024*1024):.1f}MB array...")
        
        start_time = time.time()
        transform_result = await service.transform_data(input_array, factor=3.0)
        processing_time = time.time() - start_time
        
        print(f"✅ Transformed in {processing_time:.2f}s")
        print(f"   Input: {input_array.nbytes / (1024*1024):.1f}MB")
        print(f"   Result: {transform_result['size_mb']:.1f}MB")
        
        # Should have HTTP transmission for both input and output
        assert len(http_transmission_events) >= 1  # At least input transmission
        
        # Verify the transformed data came back correctly
        transformed_array = transform_result["transformed"]
        assert isinstance(transformed_array, np.ndarray)
        assert transformed_array.shape == input_array.shape
        assert abs(transform_result["transformed_mean"] - (input_array.mean() * 3.0)) < 0.01
        
        # Test 4: Multiple concurrent large data operations
        print(f"\n--- Test 4: Concurrent large data operations ---")
        http_transmission_events.clear()
        
        # Prepare multiple datasets
        datasets = [
            generate_large_dataset(3),  # 3MB
            generate_large_dataset(7),  # 7MB  
            generate_large_numpy_dataset((400, 400, 4)),  # ~2.5MB
        ]
        
        print(f"📤 Processing {len(datasets)} datasets concurrently...")
        
        # Process all datasets concurrently
        start_time = time.time()
        tasks = []
        for i, data in enumerate(datasets):
            task = service.process_large_data(data, operation=f"concurrent_batch_{i}")
            tasks.append(task)
        
        results = await asyncio.gather(*tasks)
        total_time = time.time() - start_time
        
        print(f"✅ All {len(datasets)} datasets processed in {total_time:.2f}s")
        
        # Verify all results
        for i, result in enumerate(results):
            assert result["processed"] is True
            print(f"   Dataset {i}: {result['data_type']} data processed")
        
        # Should have multiple HTTP transmissions
        assert len(http_transmission_events) >= len(datasets)
        print(f"   HTTP transmissions: {len(http_transmission_events)}")
        
        print(f"\n🎉 SUCCESS: All large dataset operations completed successfully!")
        print(f"   - Medium data (5MB): Single upload HTTP transmission")
        print(f"   - Large array (20MB): Multipart HTTP transmission") 
        print(f"   - Bidirectional transformation: Both input/output via HTTP")
        print(f"   - Concurrent operations: Multiple HTTP transmissions")
        
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_mixed_small_large_data_service(http_test_server, http_test_user_token):
    """Test service with mixed small and large data to verify HTTP transmission only for large data."""
    print("\n=== TESTING MIXED SMALL/LARGE DATA SERVICE ===")
    
    client = await connect_to_server({
        "name": "mixed-data-client",
        "server_url": HTTP_SERVER_URL,
        "token": http_test_user_token,
    })
    
    try:
        # Track HTTP transmission events
        http_transmission_events = []
        
        def on_http_transmission(data):
            http_transmission_events.append(data)
            print(f"🔗 HTTP: {data['content_length'] / (1024*1024):.1f}MB via {data['transmission_method']}")
        
        client.rpc.on("http_transmission_stats", on_http_transmission)
        
        # Register service that handles both small and large data
        def flexible_processor(data, metadata=None):
            """Process data of any size."""
            size_mb = len(data) / (1024 * 1024) if isinstance(data, (bytes, bytearray)) else data.nbytes / (1024 * 1024)
            return {
                "size_mb": size_mb,
                "transmission_type": "large" if size_mb >= 1.0 else "small",
                "metadata": metadata,
                "processed": True
            }
        
        await client.register_service({
            "id": "flexible-processor",
            "name": "Flexible Data Processor",
            "config": {"visibility": "public"},
            "process": flexible_processor
        })
        
        service = await client.get_service("flexible-processor")
        
        # Test small data (should use WebSocket)
        print("📤 Testing small data (100KB)...")
        http_transmission_events.clear()
        small_data = generate_large_dataset(0.1)  # 100KB
        result = await service.process(small_data, metadata="small_test")
        
        assert result["transmission_type"] == "small"
        assert len(http_transmission_events) == 0  # No HTTP transmission
        print("✅ Small data used WebSocket transmission")
        
        # Test large data (should use HTTP)
        print("📤 Testing large data (8MB)...")
        http_transmission_events.clear()
        large_data = generate_large_dataset(8)  # 8MB
        result = await service.process(large_data, metadata="large_test")
        
        assert result["transmission_type"] == "large"
        assert len(http_transmission_events) == 1  # HTTP transmission used
        assert http_transmission_events[0]["transmission_method"] == "single_upload"
        print("✅ Large data used HTTP transmission")
        
        print("🎉 SUCCESS: Mixed data sizes handled correctly!")
        
    finally:
        await client.disconnect()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])