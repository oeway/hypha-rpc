"""Test HTTP message transmission using S3 storage for large messages."""

import asyncio
import httpx
import pytest
import msgpack
import math

from hypha_rpc import connect_to_server
from . import WS_SERVER_URL

pytestmark = pytest.mark.asyncio


async def test_s3_availability_and_http_transmission_setup(minio_server, fastapi_server, test_user_token):
    """Test that S3 service is available and HTTP transmission can be initialized."""
    
    # Connect to the real server with S3 support
    api = await connect_to_server({
        "name": "http transmission test client",
        "server_url": WS_SERVER_URL,
        "token": test_user_token,
    })
    
    # Get the S3 controller service
    s3controller = await api.get_service("public/s3-storage")
    
    # Verify required methods are available
    required_methods = ["put_file", "get_file", "put_file_start_multipart", "put_file_complete_multipart"]
    for method_name in required_methods:
        assert hasattr(s3controller, method_name), f"S3 controller missing {method_name}"
        method = getattr(s3controller, method_name)
        assert callable(method), f"S3 controller {method_name} is not callable"
    
    print("✅ S3 controller is available with all required methods")
    
    # Test HTTP message transmission availability on RPC instance
    rpc_instance = api.rpc
    assert hasattr(rpc_instance, '_http_message_transmission_available'), "RPC missing HTTP transmission flag"
    
    # Initialize HTTP transmission
    try:
        await rpc_instance._initialize_http_message_transmission()
        assert rpc_instance._http_message_transmission_available, "HTTP transmission should be available"
        print("✅ HTTP message transmission initialized successfully")
    except Exception as e:
        pytest.fail(f"Failed to initialize HTTP message transmission: {e}")
    
    await api.disconnect()


async def test_small_message_http_transmission(minio_server, fastapi_server, test_user_token):
    """Test HTTP transmission of small messages (<10MB) using real S3."""
    
    # Create two RPC instances to simulate sender and receiver
    sender_api = await connect_to_server({
        "name": "sender client",
        "server_url": WS_SERVER_URL,
        "token": test_user_token,
    })
    
    receiver_api = await connect_to_server({
        "name": "receiver client", 
        "server_url": WS_SERVER_URL,
        "token": test_user_token,
    })
    
    # Initialize HTTP transmission on sender
    sender_rpc = sender_api.rpc
    await sender_rpc._initialize_http_message_transmission()
    assert sender_rpc._http_message_transmission_available
    
    # Create test message (1MB)
    test_message = b"A" * (1024 * 1024)  # 1MB
    
    # Test HTTP transmission
    s3controller = await sender_api.get_service("public/s3-storage")
    
    # Generate unique file path
    import uuid
    message_id = str(uuid.uuid4())
    file_path = f"tmp/{message_id}"
    
    # Upload using put_file (< 10MB strategy)
    put_url = await s3controller.put_file(file_path, ttl=30)
    assert put_url.startswith("http"), "Put URL should be HTTP"
    
    # Upload the message content using httpx
    async with httpx.AsyncClient(timeout=30) as client:
        upload_response = await client.put(put_url, content=test_message)
        assert upload_response.status_code == 200, f"Upload failed: {upload_response.text}"
    
    # Get download URL
    get_url = await s3controller.get_file(file_path)
    assert get_url.startswith("http"), "Get URL should be HTTP"
    
    # Download and verify content
    async with httpx.AsyncClient(timeout=30) as client:
        download_response = await client.get(get_url)
        assert download_response.status_code == 200, "Download failed"
        assert download_response.content == test_message, "Content mismatch"
    
    print("✅ Small message HTTP transmission test passed")
    
    await sender_api.disconnect()
    await receiver_api.disconnect()


async def test_large_message_multipart_transmission(minio_server, fastapi_server, test_user_token):
    """Test HTTP transmission of large messages (>=10MB) using multipart upload."""
    
    api = await connect_to_server({
        "name": "multipart test client",
        "server_url": WS_SERVER_URL,
        "token": test_user_token,
    })
    
    rpc_instance = api.rpc
    await rpc_instance._initialize_http_message_transmission()
    assert rpc_instance._http_message_transmission_available
    
    s3controller = await api.get_service("public/s3-storage")
    
    # Create large test message (12MB = 3 parts of 5MB + 2MB)
    part_size = 5 * 1024 * 1024  # 5MB per part (S3 minimum)
    parts_data = [
        b"A" * part_size,           # Part 1: 5MB
        b"B" * part_size,           # Part 2: 5MB  
        b"C" * (2 * 1024 * 1024)    # Part 3: 2MB
    ]
    total_content = b"".join(parts_data)
    content_length = len(total_content)
    part_count = len(parts_data)
    
    # Generate unique file path
    import uuid
    message_id = str(uuid.uuid4())
    file_path = f"tmp/{message_id}"
    
    # Start multipart upload
    multipart_info = await s3controller.put_file_start_multipart(
        file_path=file_path,
        part_count=part_count,
        expires_in=3600,
        ttl=30
    )
    
    assert "upload_id" in multipart_info
    assert "parts" in multipart_info
    assert len(multipart_info["parts"]) == part_count
    
    upload_id = multipart_info["upload_id"]
    part_urls = multipart_info["parts"]
    
    # Upload each part
    uploaded_parts = []
    for i, (part_info, part_data) in enumerate(zip(part_urls, parts_data)):
        assert part_info["part_number"] == i + 1
        assert "url" in part_info
        
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.put(part_info["url"], content=part_data)
            assert response.status_code == 200, f"Failed to upload part {i+1}: {response.text}"
            
            # Extract ETag for completion
            etag = response.headers["ETag"].strip('"').strip("'")
            uploaded_parts.append({
                "part_number": part_info["part_number"],
                "etag": etag
            })
    
    # Complete multipart upload
    result = await s3controller.put_file_complete_multipart(
        upload_id=upload_id,
        parts=uploaded_parts
    )
    assert result["success"] is True, f"Multipart completion failed: {result}"
    
    # Verify the uploaded file by downloading
    get_url = await s3controller.get_file(file_path)
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(get_url)
        assert response.status_code == 200, "Download failed"
        assert response.content == total_content, "Multipart content mismatch"
        assert len(response.content) == content_length, "Content length mismatch"
    
    print("✅ Large message multipart transmission test passed")
    
    await api.disconnect()


async def test_parallel_range_download(minio_server, fastapi_server, test_user_token):
    """Test parallel range download for large files."""
    
    api = await connect_to_server({
        "name": "parallel download test client",
        "server_url": WS_SERVER_URL,
        "token": test_user_token,
    })
    
    s3controller = await api.get_service("public/s3-storage")
    
    # Create and upload a large file (8MB)
    content_length = 8 * 1024 * 1024  # 8MB
    test_content = b"Z" * content_length
    
    import uuid
    message_id = str(uuid.uuid4())
    file_path = f"tmp/{message_id}"
    
    # Upload using simple put_file (since < 10MB)
    put_url = await s3controller.put_file(file_path, ttl=30)
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.put(put_url, content=test_content)
        assert response.status_code == 200
    
    # Test parallel range download (simulate >= 5MB download strategy)
    get_url = await s3controller.get_file(file_path)
    
    # Calculate range requests (max 10 parallel)
    max_requests = min(10, (content_length // (1024 * 1024)) + 1)  # 8MB = 8 requests
    chunk_size = content_length // max_requests
    
    # Create range requests
    download_tasks = []
    for i in range(max_requests):
        start = i * chunk_size
        if i == max_requests - 1:
            # Last chunk gets remainder
            end = content_length - 1
        else:
            end = start + chunk_size - 1
        
        download_tasks.append(_download_range(get_url, start, end))
    
    # Execute parallel downloads
    chunks = await asyncio.gather(*download_tasks)
    
    # Combine and verify
    downloaded_content = b"".join(chunks)
    assert downloaded_content == test_content, "Parallel download content mismatch"
    assert len(downloaded_content) == content_length, "Parallel download length mismatch"
    
    print("✅ Parallel range download test passed")
    
    await api.disconnect()


async def _download_range(url, start, end):
    """Helper to download a specific byte range."""
    headers = {"Range": f"bytes={start}-{end}"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.content


async def test_rpc_http_transmission_integration(minio_server, fastapi_server, test_user_token):
    """Test integration of HTTP transmission with RPC._send_chunks method."""
    
    # Create sender and receiver clients
    sender_api = await connect_to_server({
        "name": "integration sender", 
        "server_url": WS_SERVER_URL,
        "token": test_user_token,
    })
    
    receiver_api = await connect_to_server({
        "name": "integration receiver",
        "server_url": WS_SERVER_URL,
        "token": test_user_token,
    })
    
    # Initialize HTTP transmission on sender
    await sender_api._initialize_http_message_transmission()
    assert sender_api._http_message_transmission_available
    
    # Create a large test message that would trigger HTTP transmission
    large_message = {
        "type": "test_message",
        "data": "X" * (2 * 1024 * 1024),  # 2MB of data
        "metadata": {"test": True}
    }
    
    # Serialize the message
    serialized_message = msgpack.packb(large_message)
    
    # Test HTTP transmission through _send_chunks
    target_id = receiver_api._client_id
    session_id = "test-session-123"
    
    # Mock the _send_chunks_http method to verify it gets called
    original_send_chunks_http = sender_api._send_chunks_http
    http_transmission_called = False
    
    async def mock_send_chunks_http(package, target_id, session_id):
        nonlocal http_transmission_called
        http_transmission_called = True
        # Call the original implementation
        return await original_send_chunks_http(package, target_id, session_id)
    
    sender_api._send_chunks_http = mock_send_chunks_http
    
    # Trigger _send_chunks with large message
    try:
        await sender_api._send_chunks(serialized_message, target_id, session_id)
        assert http_transmission_called, "HTTP transmission should have been called for large message"
        print("✅ RPC HTTP transmission integration test passed")
    except Exception as e:
        pytest.fail(f"HTTP transmission integration failed: {e}")
    
    await sender_api.disconnect()
    await receiver_api.disconnect()


async def test_http_transmission_ttl_cleanup(minio_server, fastapi_server, test_user_token):
    """Test that files uploaded with TTL are properly cleaned up."""
    
    api = await connect_to_server({
        "name": "ttl test client",
        "server_url": WS_SERVER_URL,
        "token": test_user_token,
    })
    
    s3controller = await api.get_service("public/s3-storage")
    
    # Upload a small file with very short TTL
    test_content = b"This file should expire quickly"
    ttl_seconds = 3  # Very short TTL
    
    import uuid
    message_id = str(uuid.uuid4())
    file_path = f"tmp/{message_id}"
    
    # Upload with TTL
    put_url = await s3controller.put_file(file_path, ttl=ttl_seconds)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.put(put_url, content=test_content)
        assert response.status_code == 200
    
    # Verify file exists immediately
    get_url = await s3controller.get_file(file_path)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(get_url)
        assert response.status_code == 200
        assert response.content == test_content
    
    print(f"File uploaded with TTL={ttl_seconds}s, waiting for cleanup...")
    
    # Wait for TTL to expire plus cleanup period
    wait_time = ttl_seconds + 3  # TTL + cleanup buffer
    await asyncio.sleep(wait_time)
    
    # Verify file is deleted
    get_url_after_ttl = await s3controller.get_file(file_path)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(get_url_after_ttl)
        assert response.status_code == 404, f"File should be deleted but got status {response.status_code}"
    
    print("✅ TTL cleanup test passed")
    
    await api.disconnect()


async def test_http_transmission_error_handling(minio_server, fastapi_server, test_user_token):
    """Test error handling in HTTP message transmission."""
    
    api = await connect_to_server({
        "name": "error handling test client",
        "server_url": WS_SERVER_URL,
        "token": test_user_token,
    })
    
    # Test behavior when S3 is not available (simulate by not initializing)
    api._http_message_transmission_available = False
    
    # Should fall back to regular message_cache transmission
    test_message = b"Small message for fallback test"
    target_id = "test-target"
    session_id = "test-session"
    
    # This should not raise an error and should fall back to message_cache
    try:
        await api._send_chunks(test_message, target_id, session_id)
        print("✅ Fallback to message_cache works when HTTP transmission unavailable")
    except Exception as e:
        # This is expected since target doesn't exist, but it shouldn't be HTTP-related
        assert "HTTP" not in str(e), f"Should not be HTTP error: {e}"
    
    # Test with valid HTTP transmission but invalid S3 operation
    await api._initialize_http_message_transmission()
    assert api._http_message_transmission_available
    
    s3controller = await api.get_service("public/s3-storage")
    
    # Test multipart completion with invalid upload_id
    try:
        result = await s3controller.put_file_complete_multipart(
            upload_id="invalid-upload-id",
            parts=[{"part_number": 1, "etag": "fake-etag"}]
        )
        assert result["success"] is False, "Should fail with invalid upload_id"
    except Exception as e:
        # Expected S3 error
        assert "invalid" in str(e).lower() or "not found" in str(e).lower()
    
    print("✅ Error handling test passed")
    
    await api.disconnect()